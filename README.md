# OpenAI API Proxy

OpenAI API Proxy is a lightweight transparent middleware service built with Python and FastAPI, designed to sit between
clients and OpenAI-compatible APIs. It forwards raw HTTP requests (JSON, multipart file/audio uploads, and binary
payloads), streams SSE responses back in real time, and logs metadata and bounded payloads to SQLite (WAL mode).

> [!NOTE]
> By default, JSON request bodies and decoded response bodies (up to `OPENAI_PROXY_MAX_LOG_BODY_BYTES`) are persisted in
> plain text to `./openai_log.db`. Set `OPENAI_PROXY_LOG_ENABLED=false` to disable database logging or protect access to
> the database file if payloads contain sensitive prompts or user data.

## Features

1. **Transparent Proxy**: Forwards `GET/POST/PUT/PATCH/DELETE/OPTIONS`, raw paths (including `%2F`), query parameters,
   headers (stripping hop-by-hop headers), and raw request bodies (JSON, multipart, binary) via longest-prefix routing.
2. **Real-Time Streaming**: Connects to upstream before returning `StreamingResponse` (clean `502`/`504` on upstream
   connect/timeout failures), streams chunks with zero line buffering, and guarantees upstream stream closure on client
   disconnects.
3. **Bounded SQLite Logging**: Persists request timestamp, duration (float seconds), status code, response headers, and
   bounded bodies to SQLite in WAL mode.
4. **Configurable via Environment Variables**: Tune upstream host, DB URL, body limits, timeouts, and logging toggle
   without editing source code.

## Configuration

| Environment Variable | Default | Description |
| --- | --- | --- |
| `OPENAI_PROXY_UPSTREAM` | `https://api.openai.com` | Default upstream base URL for `/` |
| `OPENAI_PROXY_DATABASE_URL` | `sqlite+aiosqlite:///./openai_log.db` | Async SQLAlchemy database URL |
| `OPENAI_PROXY_LOG_ENABLED` | `true` | Enable/disable DB logging (`true`/`false`) |
| `OPENAI_PROXY_MAX_REQUEST_BODY_BYTES` | `10485760` (10 MiB) | Max request body size (`413` if exceeded) |
| `OPENAI_PROXY_MAX_LOG_BODY_BYTES` | `1048576` (1 MiB) | Max logged body size (truncated in DB only) |
| `OPENAI_PROXY_CONNECT_TIMEOUT` | `10.0` | Upstream connect timeout in seconds |
| `OPENAI_PROXY_READ_TIMEOUT` | `600.0` | Upstream read timeout in seconds (`none`/`0` disables) |

## Installation

Requires Python 3.10+.

```bash
pip install .
```

For local development and running tests:

```bash
pip install -e ".[dev]"
ruff check .
pytest -v
```

## Usage

### Run locally

```bash
uvicorn main:app --host 127.0.0.1 --port 8000
```

> If you place Nginx in front of this proxy for SSE streaming, set `proxy_buffering off;` so chunks flush immediately.

### Run with Docker

```bash
docker build -t openai-proxy .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" openai-proxy
```

Mount a host volume to `/app/data` to persist `openai_log.db` across container restarts.

### Send a streaming request

```bash
curl -N -X POST "http://127.0.0.1:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your_openai_api_key>" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}], "stream": true}'
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) file for details.
