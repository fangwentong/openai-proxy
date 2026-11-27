from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

import config
import log as proxy_log
import main as proxy_main
from utils import PathMatchingTree, filter_hop_by_hop_headers


class _TrackingAsyncStream(httpx.AsyncByteStream):
    """Stream that yields explicit chunks and tracks aclose() invocations."""

    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.close_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.close_count += 1


@pytest.fixture
async def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_openai_log.db"
    proxy_log.configure_database(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(config, "LOG_ENABLED", True)
    await proxy_log.create_tables()
    yield db_file
    await proxy_log.dispose_engine()


def test_filter_hop_by_hop_headers_respects_connection_header():
    headers = [
        ("Connection", "keep-alive, X-Custom-Hop"),
        ("Keep-Alive", "timeout=5"),
        ("X-Custom-Hop", "secret"),
        ("Transfer-Encoding", "chunked"),
        ("Content-Type", "application/json"),
        ("Set-Cookie", "a=1"),
        ("Set-Cookie", "b=2"),
    ]
    filtered = filter_hop_by_hop_headers(headers, extra_strip=("content-length",))
    assert filtered == [
        ("Content-Type", "application/json"),
        ("Set-Cookie", "a=1"),
        ("Set-Cookie", "b=2"),
    ]


@pytest.mark.asyncio
async def test_sse_streaming_chunks_and_sqlite_wal_log(isolated_db: Path, monkeypatch: pytest.MonkeyPatch):
    stream = _TrackingAsyncStream([b"data: chunk1\n\n", b"data: chunk2\n\n", b"data: [DONE]\n\n"])
    captured_requests: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_requests.append(req)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/event-stream",
                "Connection": "keep-alive, X-Hop",
                "X-Hop": "drop-me",
                "X-Request-Id": "req_123",
            },
            stream=stream,
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            received: list[bytes] = []
            async with cli.stream(
                "POST",
                "/v1/chat/completions",
                headers={"Authorization": "Bearer sk-test", "Content-Type": "application/json"},
                json={"model": "gpt-4o-mini", "stream": True},
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("x-request-id") == "req_123"
                assert "content-encoding" not in resp.headers
                assert "x-hop" not in resp.headers
                async for chunk in resp.aiter_bytes():
                    received.append(chunk)
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert b"".join(received) == b"data: chunk1\n\ndata: chunk2\n\ndata: [DONE]\n\n"
    assert stream.close_count == 1
    assert captured_requests[0].headers["authorization"] == "Bearer sk-test"

    conn = sqlite3.connect(isolated_db)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    rows = conn.execute(
        "SELECT request_method, status_code, request_content, response_content, response_duration FROM openai_logs"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    method, status, req_content, resp_content, duration = rows[0]
    assert method == "POST"
    assert status == 200
    assert json.loads(req_content)["model"] == "gpt-4o-mini"
    assert "data: chunk1" in resp_content and "[DONE]" in resp_content
    assert isinstance(duration, float) and duration >= 0.0


@pytest.mark.asyncio
async def test_sse_chunks_yielded_incrementally_at_asgi_layer(monkeypatch: pytest.MonkeyPatch):
    stream = _TrackingAsyncStream([b"data: 1\n\n", b"data: 2\n\n", b"data: [DONE]\n\n"])
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)
        )
    )
    monkeypatch.setattr(config, "LOG_ENABLED", False)
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    sent_bodies: list[bytes] = []

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            sent_bodies.append(message["body"])  # type: ignore[arg-type]

    first_receive = True
    never_disconnect = asyncio.Event()

    async def receive() -> dict[str, object]:
        nonlocal first_receive
        if first_receive:
            first_receive = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await never_disconnect.wait()
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/stream",
        "raw_path": b"/v1/stream",
        "query_string": b"",
        "headers": [],
        "app": proxy_main.app,
    }
    try:
        req = proxy_main.Request(scope, receive)
        resp = await proxy_main.proxy_openai_api(req)
        await resp(scope, receive, send)
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert sent_bodies == [b"data: 1\n\n", b"data: 2\n\n", b"data: [DONE]\n\n"]
    assert stream.close_count == 1


@pytest.mark.asyncio
async def test_gzip_upstream_response_is_decoded_and_encoding_header_stripped(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    compressed = gzip.compress(b'{"id":"cmpl-1","object":"chat.completion"}')

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "Content-Length": str(len(compressed)),
            },
            stream=_TrackingAsyncStream([compressed]),
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            resp = await cli.get("/v1/models")
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers
    assert resp.json() == {"id": "cmpl-1", "object": "chat.completion"}


@pytest.mark.asyncio
async def test_multipart_bytes_forwarded_verbatim(isolated_db: Path, monkeypatch: pytest.MonkeyPatch):
    received_upstream_body: bytes = b""
    received_content_type: str = ""

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal received_upstream_body, received_content_type
        received_upstream_body = req.read()
        received_content_type = req.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "hello audio"})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    raw_multipart = (
        b"--boundary123\r\n"
        b'Content-Disposition: form-data; name="file"; filename="clip.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        b"\x00\x01\x02\xff\xfeaudio-bytes\r\n"
        b"--boundary123--\r\n"
    )

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            resp = await cli.post(
                "/v1/audio/transcriptions",
                headers={"Content-Type": "multipart/form-data; boundary=boundary123"},
                content=raw_multipart,
            )
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert resp.status_code == 200
    assert resp.json() == {"text": "hello audio"}
    assert received_upstream_body == raw_multipart
    assert "multipart/form-data; boundary=boundary123" in received_content_type

    # Non-JSON multipart body should not be persisted as request_content
    conn = sqlite3.connect(isolated_db)
    req_content = conn.execute("SELECT request_content FROM openai_logs").fetchone()[0]
    conn.close()
    assert req_content is None


@pytest.mark.asyncio
async def test_raw_path_preserves_encoded_slash(isolated_db: Path, monkeypatch: pytest.MonkeyPatch):
    captured_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_urls.append(str(req.url))
        return httpx.Response(200, json={"ok": True})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            resp = await cli.get("/v1/models/org%2Fmodel-1?version=2")
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert resp.status_code == 200
    assert captured_urls == ["https://api.openai.com/v1/models/org%2Fmodel-1?version=2"]


@pytest.mark.asyncio
async def test_upstream_connect_error_returns_502_without_leaking_details(monkeypatch: pytest.MonkeyPatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("internal secret socket error")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            resp = await cli.get("/v1/models")
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert resp.status_code == 502
    assert "internal secret" not in resp.text


@pytest.mark.asyncio
async def test_upstream_timeout_returns_504(monkeypatch: pytest.MonkeyPatch):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            resp = await cli.get("/v1/models")
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert resp.status_code == 504


@pytest.mark.asyncio
async def test_request_body_over_limit_returns_413(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "MAX_REQUEST_BODY_BYTES", 16)
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))

    transport = httpx.ASGITransport(app=proxy_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
        resp = await cli.post("/v1/chat/completions", content=b"x" * 32)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_oversized_response_is_truncated_in_log_but_streamed_in_full(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(config, "MAX_LOG_BODY_BYTES", 16)
    full_payload = b"0123456789abcdef" * 4

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_TrackingAsyncStream([full_payload[:32], full_payload[32:]]))

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        transport = httpx.ASGITransport(app=proxy_main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as cli:
            resp = await cli.get("/v1/files/content")
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert resp.status_code == 200
    assert resp.content == full_payload

    conn = sqlite3.connect(isolated_db)
    logged = conn.execute("SELECT response_content FROM openai_logs").fetchone()[0]
    conn.close()
    assert logged.startswith("0123456789abcdef")
    assert logged.endswith("[truncated]")


@pytest.mark.asyncio
async def test_disconnect_before_first_chunk_still_closes_upstream(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    stream = _TrackingAsyncStream([b"chunk-1", b"chunk-2"])

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    try:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/v1/models",
            "raw_path": b"/v1/models",
            "query_string": b"",
            "headers": [],
            "app": proxy_main.app,
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b"", "more_body": False}

        request = proxy_main.Request(scope, receive)
        streaming_resp = await proxy_main.proxy_openai_api(request)
        # Simulate client disconnecting before generator iteration starts:
        # BackgroundTask still runs and must close the upstream stream idempotently.
        assert streaming_resp.background is not None
        await streaming_resp.background()
        # Closing again must remain idempotent.
        await streaming_resp.background()
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert stream.close_count == 1
    conn = sqlite3.connect(isolated_db)
    rows = conn.execute("SELECT response_content FROM openai_logs").fetchall()
    conn.close()
    assert any("[client_disconnected]" in row[0] for row in rows)


@pytest.mark.asyncio
async def test_client_cancel_mid_stream_shields_upstream_aclose_and_strips_accept_encoding(
    isolated_db: Path, monkeypatch: pytest.MonkeyPatch
):
    first_chunk_sent = asyncio.Event()
    never_release = asyncio.Event()
    seen_accept_encoding: list[str] = []

    class _SlowClosableStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.close_completed = 0

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"data: part1\n\n"
            await never_release.wait()
            yield b"data: part2\n\n"

        async def aclose(self) -> None:
            # Yield control to verify CancelScope(shield=True) protects async aclose().
            await asyncio.sleep(0.01)
            self.close_completed += 1

    stream = _SlowClosableStream()

    def handler(req: httpx.Request) -> httpx.Response:
        seen_accept_encoding.append(req.headers.get("accept-encoding", ""))
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=stream)

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(proxy_main, "proxied_hosts", PathMatchingTree({"/": "https://api.openai.com"}))
    proxy_main.app.state.http_client = mock_client

    disconnect_event = asyncio.Event()
    first_receive = True

    async def receive() -> dict[str, object]:
        nonlocal first_receive
        if first_receive:
            first_receive = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect_event.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk_sent.set()
            # Trigger real ASGI client disconnect right after first chunk!
            disconnect_event.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/stream",
        "raw_path": b"/v1/stream",
        "query_string": b"",
        "headers": [(b"accept-encoding", b"gzip, deflate, br, zstd")],
        "app": proxy_main.app,
    }
    try:
        req = proxy_main.Request(scope, receive)
        resp = await proxy_main.proxy_openai_api(req)
        await resp(scope, receive, send)
    finally:
        await mock_client.aclose()
        proxy_main.app.state.http_client = None

    assert first_chunk_sent.is_set()
    assert stream.close_completed == 1
    assert "br" not in seen_accept_encoding[0] and "zstd" not in seen_accept_encoding[0]
    conn = sqlite3.connect(isolated_db)
    logged = conn.execute("SELECT response_content FROM openai_logs").fetchone()[0]
    conn.close()
    assert "data: part1" in logged and "[client_disconnected]" in logged
