#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

import config
from log import OpenAILog, create_tables, dispose_engine, save_log
from utils import BoundedBodyCapture, PathMatchingTree, filter_hop_by_hop_headers

logger = logging.getLogger("openai_proxy")

proxied_hosts = PathMatchingTree(config.proxied_hosts_config)

# Strip accept-encoding so httpx negotiates only encodings it can decode (gzip/deflate),
# avoiding br/zstd corruption when stripping content-encoding on decoded streaming bytes.
_REQUEST_EXTRA_STRIP = ("host", "content-length", "x-forwarded-for", "x-real-ip", "accept-encoding")
_RESPONSE_EXTRA_STRIP = ("content-length", "content-encoding", "alt-svc")


def _build_http_client() -> httpx.AsyncClient:
    timeout = httpx.Timeout(
        connect=config.UPSTREAM_CONNECT_TIMEOUT,
        read=config.UPSTREAM_READ_TIMEOUT,
        write=config.UPSTREAM_WRITE_TIMEOUT,
        pool=config.UPSTREAM_POOL_TIMEOUT,
    )
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=50)
    return httpx.AsyncClient(timeout=timeout, limits=limits)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if config.LOG_ENABLED:
        await create_tables()
    owns_client = not hasattr(app.state, "http_client") or app.state.http_client is None
    if owns_client:
        app.state.http_client = _build_http_client()
    try:
        yield
    finally:
        if owns_client and app.state.http_client is not None:
            await app.state.http_client.aclose()
            app.state.http_client = None
        await dispose_engine()


app = FastAPI(lifespan=lifespan)


def _extract_raw_path(request: Request) -> str:
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes) and raw_path:
        return raw_path.decode("latin-1")
    return request.url.path


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    buf = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(buf) + len(chunk) > max_bytes:
            raise HTTPException(status_code=413, detail="Request body too large")
        buf.extend(chunk)
    return bytes(buf)


def _capture_json_request_content(content_type: str | None, body: bytes, max_log_bytes: int) -> str | None:
    if not body or len(body) > max_log_bytes:
        return None
    if content_type and "json" not in content_type.lower():
        return None
    try:
        text = body.decode("utf-8")
        json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return text


async def proxy_openai_api(request: Request) -> StreamingResponse:
    matched_host = proxied_hosts.get_matching(request.url.path)
    if matched_host is None:
        raise HTTPException(status_code=404, detail="No matching upstream host")

    raw_path = _extract_raw_path(request)
    url = f"{matched_host}{raw_path}"

    req_headers = filter_hop_by_hop_headers(
        request.headers.items(),
        extra_strip=_REQUEST_EXTRA_STRIP,
    )
    body = await _read_bounded_body(request, config.MAX_REQUEST_BODY_BYTES)

    http_client: httpx.AsyncClient | None = getattr(request.app.state, "http_client", None)
    if http_client is None:
        http_client = _build_http_client()
        request.app.state.http_client = http_client

    start_time_secs = time.time()
    upstream_req = http_client.build_request(
        method=request.method,
        url=url,
        headers=req_headers,
        params=request.query_params,
        content=body if body else None,
    )

    try:
        upstream_res = await http_client.send(upstream_req, stream=True)
    except httpx.TimeoutException as exc:
        logger.warning("Upstream timeout for %s %s: %s", request.method, url, exc)
        raise HTTPException(status_code=504, detail="Upstream request timed out") from exc
    except httpx.RequestError as exc:
        logger.warning("Upstream request error for %s %s: %s", request.method, url, exc)
        raise HTTPException(status_code=502, detail="Bad gateway: upstream request failed") from exc

    response_headers = filter_hop_by_hop_headers(
        upstream_res.headers.multi_items(),
        extra_strip=_RESPONSE_EXTRA_STRIP,
    )
    capture = BoundedBodyCapture(config.MAX_LOG_BODY_BYTES)
    stream_completed = False
    client_disconnected = False
    upstream_stream_error = False
    upstream_closed = False

    async def close_upstream() -> None:
        nonlocal upstream_closed
        if upstream_closed:
            return
        with anyio.CancelScope(shield=True):
            await upstream_res.aclose()
            upstream_closed = True

    async def stream_api_response() -> AsyncIterator[bytes]:
        nonlocal stream_completed, client_disconnected, upstream_stream_error
        try:
            async for chunk in upstream_res.aiter_bytes():
                capture.append(chunk)
                yield chunk
            stream_completed = True
        except (asyncio.CancelledError, GeneratorExit):
            client_disconnected = True
            raise
        except httpx.HTTPError as exc:
            upstream_stream_error = True
            logger.warning("Upstream stream error for %s %s: %s", request.method, url, exc)
            raise
        finally:
            if not stream_completed and not upstream_stream_error:
                client_disconnected = True
            await close_upstream()

    async def finalize_and_log() -> None:
        await close_upstream()
        if not config.LOG_ENABLED:
            return
        log_entry = OpenAILog(
            request_url=url,
            request_method=request.method,
            request_time=int(start_time_secs * 1000),
            response_duration=time.time() - start_time_secs,
            status_code=upstream_res.status_code,
            request_content=_capture_json_request_content(
                request.headers.get("content-type"),
                body,
                config.MAX_LOG_BODY_BYTES,
            ),
            response_header=json.dumps([[k, v] for k, v in upstream_res.headers.multi_items()]),
            response_content=capture.decode(
                disconnected=client_disconnected or (not stream_completed and not upstream_stream_error),
                upstream_error=upstream_stream_error,
            ),
        )
        try:
            await save_log(log_entry)
        except Exception:
            logger.exception("Failed to save proxy log entry")

    response = StreamingResponse(
        stream_api_response(),
        status_code=upstream_res.status_code,
        background=BackgroundTask(finalize_and_log),
    )
    for k, v in response_headers:
        response.headers.append(k, v)
    return response


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def request_handler(request: Request) -> StreamingResponse:
    return await proxy_openai_api(request)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="info")
