# OpenAI API Proxy

OpenAI API Proxy is a lightweight transparent middleware service built with Python and FastAPI, designed to sit between
clients and OpenAI-compatible APIs. It forwards requests, streams responses back to clients in real time, and logs
request/response metadata and payloads to a local SQLite database.

> [!NOTE]
> By default, request and response bodies are persisted in plain text to `./openai_log.db` in the working directory.
> Protect access to that database file if requests contain sensitive prompts or user data.

## Features

1. **Transparent Proxy**: Forwards requests, paths, and `Authorization` headers to configured upstream hosts via
   longest-prefix path routing.
2. **Streaming**: Streams upstream responses back to clients in real time.
3. **Logging**: Records request timestamps, response durations, status codes, headers, and payloads to SQLite.
4. **Error Handling**: Returns clean HTTP errors on invalid payloads or unmatched upstream routes.

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

### Run with Docker

```bash
docker build -t openai-proxy .
docker run --rm -p 8000:8000 -v "$(pwd)/data:/app/data" openai-proxy
```

Mount a host volume if you want to persist `./openai_log.db` across container restarts.

### Send a request

```bash
curl -N -X POST "http://127.0.0.1:8000/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <your_openai_api_key>" \
  -d '{"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "Hello!"}], "stream": true}'
```

Replace `<your_openai_api_key>` with your actual OpenAI API key.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) file for details.
