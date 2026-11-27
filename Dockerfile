FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml LICENSE README.md main.py config.py log.py utils.py ./
RUN pip install --no-cache-dir . && \
    mkdir -p /app/data && \
    adduser --disabled-password --gecos "" appuser && \
    chown -R appuser:appuser /app

USER appuser
WORKDIR /app/data

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
