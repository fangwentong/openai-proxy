from __future__ import annotations

import typing
from functools import partial

import anyio
from fastapi.responses import StreamingResponse
from starlette.types import Receive, Scope, Send


class PathMatchingTree:
    """Prefix tree for mapping request paths to upstream base URLs.

    Supports exact segments, single-segment `*` wildcards, and longest-prefix
    fallback when a deeper path does not match a more specific route.
    """

    def __init__(self, config: dict[str, str]):
        self.child: dict[str, PathMatchingTree] = {}
        self.value: str | None = None
        self._build_tree(config)

    def _build_tree(self, config: dict[str, str]) -> None:
        for k, v in config.items():
            parts = [part for part in k.split("/") if part]
            self._add(parts, v)

    def _add(self, parts: list[str], value: str) -> None:
        node = self
        for part in parts:
            if part not in node.child:
                node.child[part] = PathMatchingTree({})
            node = node.child[part]
        node.value = value

    def _match_best(self, parts: list[str], idx: int, exact_count: int) -> tuple[int, int, str] | None:
        best: tuple[int, int, str] | None = (idx, exact_count, self.value) if self.value is not None else None
        if idx < len(parts):
            part = parts[idx]
            if part in self.child:
                cand = self.child[part]._match_best(parts, idx + 1, exact_count + 1)
                if cand is not None and (best is None or cand[:2] > best[:2]):
                    best = cand
            if "*" in self.child:
                cand = self.child["*"]._match_best(parts, idx + 1, exact_count)
                if cand is not None and (best is None or cand[:2] > best[:2]):
                    best = cand
        return best

    def get_matching(self, path: str) -> str | None:
        parts = [part for part in path.split("/") if part]
        matched = self._match_best(parts, 0, 0)
        if matched is None:
            return None
        return matched[2].rstrip("/")


class OverrideStreamResponse(StreamingResponse):
    """Override StreamingResponse to lazily send response status_code and headers."""

    async def stream_response(self, send: Send) -> None:
        first_chunk = True
        async for chunk in self.body_iterator:
            if first_chunk:
                await self.send_request_header(send)
                first_chunk = False
            if not isinstance(chunk, bytes):
                chunk = chunk.encode(self.charset)
            await send({"type": "http.response.body", "body": chunk, "more_body": True})

        if first_chunk:
            await self.send_request_header(send)
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send_request_header(self, send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            }
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async with anyio.create_task_group() as task_group:
            async def wrap(func: typing.Callable[[], typing.Coroutine[typing.Any, typing.Any, typing.Any]]) -> None:
                await func()
                task_group.cancel_scope.cancel()

            task_group.start_soon(wrap, partial(self.stream_response, send))
            await wrap(partial(self.listen_for_disconnect, receive))

        if self.background is not None:
            await self.background()
