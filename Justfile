set dotenv-load

_default:
    @just --list

docker-build:
    docker build -t bowie-mcp-server:dev .

mcp:
    uv run --locked mcp_server.py

test:
    uv run --locked python -m unittest discover -s tests

check:
    uv run --locked ruff check .
    uv run --locked ruff format --check .
    uv run --locked mypy mcp_server.py tests

# Start the ngrok OAuth provider after starting the MCP server.
oauth:
    env -u STORAGE -u S3_BUCKET -u S3_KEY npx --yes --package='git+https://github.com/ngrok-adhoc/mcp-oauth-provider.git#43f36581d06a42cc80c444fb6cd5295f06ff4629' mcp-oauth-provider
