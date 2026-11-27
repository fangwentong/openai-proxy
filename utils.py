from __future__ import annotations

from collections.abc import Iterable

HOP_BY_HOP_HEADERS: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


def filter_hop_by_hop_headers(
    headers: Iterable[tuple[str, str]],
    *,
    extra_strip: Iterable[str] = (),
) -> list[tuple[str, str]]:
    """Strip RFC 9110 hop-by-hop headers (including those nominated by Connection)."""
    raw_items = list(headers)
    strip = set(HOP_BY_HOP_HEADERS)
    strip.update(h.lower() for h in extra_strip)

    for key, value in raw_items:
        if key.lower() == "connection":
            for token in value.split(","):
                name = token.strip().lower()
                if name:
                    strip.add(name)

    return [(k, v) for k, v in raw_items if k.lower() not in strip]


class BoundedBodyCapture:
    """Capture up to `max_bytes` from a byte stream without unbounded memory growth."""

    def __init__(self, max_bytes: int):
        self.max_bytes = max(0, max_bytes)
        self._buf = bytearray()
        self.truncated = False

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        remaining = self.max_bytes - len(self._buf)
        if remaining <= 0:
            self.truncated = True
            return
        if len(chunk) > remaining:
            self._buf.extend(chunk[:remaining])
            self.truncated = True
        else:
            self._buf.extend(chunk)

    def decode(self, *, disconnected: bool = False, upstream_error: bool = False) -> str:
        text = self._buf.decode("utf-8", errors="replace")
        if self.truncated:
            text += "\n...[truncated]"
        if upstream_error:
            text += "\n...[upstream_error]"
        elif disconnected:
            text += "\n...[client_disconnected]"
        return text


class PathMatchingTree:
    """Prefix tree for mapping request paths to upstream base URLs.

    Supports exact segments, single-segment `*` wildcards, and longest-prefix
    fallback (deeper match wins; exact segment wins over `*` at equal depth).
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
