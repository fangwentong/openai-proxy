# Changelog

## 0.2.0

### Breaking / Behavioral Changes
- Replaced Poetry `pyproject.toml` and `requirements.txt` with PEP 621 `pyproject.toml` (requires Python 3.10+).
- Removed obsolete `https://chat.openai.com` default route; default `/` upstream is configurable via `OPENAI_PROXY_UPSTREAM`.
- Non-JSON request bodies (such as `multipart/form-data` audio/file uploads) are now forwarded verbatim instead of returning `400`.
- Requests exceeding `OPENAI_PROXY_MAX_REQUEST_BODY_BYTES` return `413 Payload Too Large`.
- Upstream connection errors now return `502` and timeouts return `504` without exposing raw internal exception text.

### Fixes & Improvements
- Fixed `PathMatchingTree` longest-prefix fallback, wildcard backtracking, and `None` handling (returning `404` when unmatched).
- Removed `OverrideStreamResponse` (incompatible with `anyio 4`) and switched to `httpx.AsyncClient.send(..., stream=True)` + `StreamingResponse` with idempotent upstream closure on client disconnect.
- Added RFC 9110 hop-by-hop header filtering, `%2F` `raw_path` preservation, bounded response log capture, and SQLite WAL mode.
- Upgraded `pytest>=9.0.3` (closing Dependabot alert #3 / CVE-2025-71176) and enforced safe lower bounds for `starlette` and `h11`.
- Added GitHub Actions CI and non-root `Dockerfile`.
