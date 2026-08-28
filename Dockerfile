# Multi-stage Docker build for mcp-server-smartsheet-rm
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/dist/*.whl ./
RUN pip install --no-cache-dir *.whl && rm -f *.whl

ENTRYPOINT ["mcp-server-smartsheet-rm"]
