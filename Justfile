set dotenv-load

_default:
    @just --list


docker-build:
  docker build -t ghcr.io/ngrok-adhoc/bowie-mcp-server:latest .

mcp:
    uv run --locked mcp_server.py

# Start the ngrok OAuth provider after starting the MCP server.
oauth:
    npx --yes --package='git+ssh://git@github.com/ngrok-adhoc/mcp-oauth-provider.git#main' mcp-oauth-provider
