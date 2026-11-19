set dotenv-load

_default:
    @just --list


docker-build:
  docker build -t ghcr.io/ngrok-adhoc/bowie-mcp-server:latest .

mcp:
    uv run --locked mcp_server.py

# Start the ngrok OAuth provider after starting Bowie.
oauth:
    node ../mcp-oauth-provider/dist/bin/mcp-oauth-provider.js
