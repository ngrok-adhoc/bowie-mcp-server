# Bowie MCP server

Build a small Python MCP server for an ngrok demo. An agent can find a dog park or dog-friendly café for Bowie, add a place it learned about, and record how the visit went.

## Tool contract

| Tools | Behavior |
| --- | --- |
| `find_dog_parks`, `find_dog_cafes` | Return matching records with IDs, a description, relevant amenities, public rating snapshots, the submitted-review average, and comments from submitted and public reviews. With no filters, list saved places of that type, 50 per page by default (`limit`, `offset`; `total` counts all matches). |
| `add_dog_park`, `add_dog_cafe` | Save a place from details supplied by the caller and return the saved record. |
| `review_dog_park`, `review_dog_cafe` | Take a place ID and one to five whole stars. Append a review with an optional comment and source URL, then return the place. |

- Team members share one ordered list per place. Each call appends a rating, so repeat visits can coexist. Only submitted reviews count toward `average_rating`. Use `public_ratings` for published scores and `research_notes` for the source behind a public comment. The server does not identify contributors or deduplicate submissions. A legacy single `rating` is read as the first entry in `ratings`.

- Give each place a stable ID, type, name, locality or suburb, and country. Keep street address, coordinates, public business phone, website, and a free-text description optional. Existing place notes are read as the description.
- Use the description for general details such as separate small-dog areas, treats, shade, and busy periods. Parks retain the optional drinking-water filter and cafés retain dog access and puppuccino fields.
- Find tools return up to 50 places of the requested type per page by default, with `limit` up to 100, `offset` to page, and `total` for the match count. Optional locality, country, and amenity filters narrow the result. A radius search uses the Holland Park locality point and returns places with saved coordinates, nearest first.
- Bound anonymous writes: at most 500 places, 100 reviews per place, 10 sources per place, capped text lengths, and no control, zero-width, or bidirectional-override characters in new input. Data saved by earlier versions still loads.
- Use the [version-controlled place data](02-holland-park-seed-data.md) by default. Serialize writes in a single server process and replace the file atomically. Compose seeds a named volume from the image's seed data. When [S3 storage](../README.md#store-places-and-reviews-in-s3) is configured, conditionally create and update a single S3 object.

## Build and verify

- [x] Use Python 3.11+ and FastMCP 4. Derive MCP input and structured output schemas from typed Python signatures and Pydantic models. Describe filters, IDs, and write effects in tool metadata.
- [x] Mount the FastMCP Streamable HTTP app in FastAPI at `http://127.0.0.1:3001/mcp` with its lifespan active. The [MCP OAuth provider](https://github.com/ngrok-adhoc/mcp-oauth-provider) reads its upstream from `MCP_SERVER_URL`. Use the loopback URL for local runs or `http://bowie:3001/mcp` inside Compose.
- [x] Manage Python tooling and the latest compatible stable dependencies in `pyproject.toml` and `uv.lock`. Use Ruff for formatting and linting and mypy for type checks.
- [x] Smoke-test tool discovery and both schemas over HTTP with MCP revision `2026-07-28`. Exercise find, add, and review on temporary data, including a rejected review and confirming writes survive a restart. Check the previous `2025-11-25` handshake against the same endpoint for client compatibility.
- [x] Document how to run the server and try the tools in the [README](../README.md).

## Demo client

- [ ] Run the OAuth provider locally from the Bowie repo root with `just oauth` while `just mcp` is running. Attach Codex CLI to the provider's public ngrok `/mcp` URL, complete Google sign-in, and verify that Codex can call a find tool through the public route.

FastMCP was the more-starred of the two Python projects checked, and v4 supports the current MCP revision and older clients. [FastMCP repository](https://github.com/PrefectHQ/fastmcp), [official Python SDK](https://github.com/modelcontextprotocol/python-sdk), [FastMCP 4 release](https://blog.gofastmcp.com/3mufbh2vcv22o), [MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/).
