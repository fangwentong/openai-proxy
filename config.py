from __future__ import annotations

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _env_optional_float(name: str, default: float | None) -> float | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    if raw.strip().lower() in {"none", "off"}:
        return None
    val = float(raw)
    if val <= 0:
        return None
    return val


proxied_hosts_config: dict[str, str] = {
    "/": os.getenv("OPENAI_PROXY_UPSTREAM", "https://api.openai.com"),
}

DATABASE_URL: str = os.getenv("OPENAI_PROXY_DATABASE_URL", "sqlite+aiosqlite:///./openai_log.db")
LOG_ENABLED: bool = _env_bool("OPENAI_PROXY_LOG_ENABLED", True)
MAX_REQUEST_BODY_BYTES: int = _env_int("OPENAI_PROXY_MAX_REQUEST_BODY_BYTES", 10 * 1024 * 1024)
MAX_LOG_BODY_BYTES: int = _env_int("OPENAI_PROXY_MAX_LOG_BODY_BYTES", 1 * 1024 * 1024)
UPSTREAM_CONNECT_TIMEOUT: float = float(os.getenv("OPENAI_PROXY_CONNECT_TIMEOUT", "10.0"))
UPSTREAM_READ_TIMEOUT: float | None = _env_optional_float("OPENAI_PROXY_READ_TIMEOUT", 600.0)
UPSTREAM_WRITE_TIMEOUT: float = float(os.getenv("OPENAI_PROXY_WRITE_TIMEOUT", "30.0"))
UPSTREAM_POOL_TIMEOUT: float = float(os.getenv("OPENAI_PROXY_POOL_TIMEOUT", "10.0"))
