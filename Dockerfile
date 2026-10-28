FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MANDAT_DATA_DIR=/app/docs/data

WORKDIR /app
COPY pyproject.toml ./
COPY mandat ./mandat
RUN pip install ".[mcp]"
COPY docs/data ./docs/data

# журнал решений лежит в томе, сервер работает не от root
RUN useradd --system --uid 10001 mandat && mkdir /data && chown mandat /data
USER mandat
VOLUME /data

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8765), 2).close()"
CMD ["python", "-m", "mandat.mcp_server", "--transport", "http", "--host", "0.0.0.0", "--port", "8765", "--journal", "/data/journal.jsonl"]
