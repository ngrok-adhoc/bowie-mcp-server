FROM python:3.15.2-alpine
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev && install -d -o 10001 -g 10001 /app/data

COPY mcp_server.py ./
COPY --chown=10001:10001 data/places.json ./data/

USER 10001:10001
CMD ["/app/.venv/bin/python", "mcp_server.py"]
