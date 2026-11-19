# MCP server

The MCP server helps you find dog-friendly cafés and off-leash dog parks near Holland Park, Brisbane.

Pass `{"radius_km": 3}` to either `find_dog_parks` or `find_dog_cafes` to search within 3 km of Holland Park, nearest first. Omit `suburb` to include adjacent suburbs. The search uses straight-line distance from Queensland's published Holland Park locality point to each saved pin. Places without coordinates are still available in searches without a radius.

## Run locally

Install `just`, [uv](https://docs.astral.sh/uv/), and Python 3.11 or newer. From the repository root, run:

```sh
just mcp
```

Connect a local MCP client to `http://127.0.0.1:3001/mcp`. Local runs read and write `data/places.json`. Press Ctrl+C to stop the server.

`just mcp` reads `BIND_HOST` and `ALLOWED_HOSTS` from the environment or the repo's `.env`. The bind address defaults to `127.0.0.1`. `ALLOWED_HOSTS` is a JSON list of additional HTTP hostnames. The MCP server always accepts `127.0.0.1`.

## Run the ngrok OAuth provider

Keep the `mcp-oauth-provider` checkout beside this repo. The MCP server and the provider run as separate processes. The provider reads `.env` from this repository's root.

With Node.js 26 or newer, install and build the provider once from the repository root:

```sh
npm --prefix ../mcp-oauth-provider ci
```

If you haven't created `.env`, copy `.env.example` to `.env`. Set `NGROK_AUTHTOKEN` to your ngrok agent authtoken and `USERS` to a JSON array containing your Google sign-in email. Set `NGROK_URL` to the HTTPS origin of your ngrok development domain or custom domain, or leave it blank to let the provider allocate one.

Set `MCP_SERVER_URL=http://127.0.0.1:3001/mcp` for local runs. When the MCP server and the provider run on separate hosts, use a private network or an upstream with its own access controls and include its hostname in the server's `ALLOWED_HOSTS`. The MCP server serves its tools without authentication, so expose the OAuth provider's ngrok endpoint to clients and keep the upstream restricted.

To show the bundled icon on the OAuth consent screen, set `MCP_SERVER_ICON=./bowie-icon.png` when using Just. Docker Compose mounts that icon into the provider automatically. The provider limits local icons to 256 KiB.

Leave `just mcp` running and start the provider from a second terminal in the repository root:

```sh
just oauth
```

Connect your MCP client to the public **MCP** URL printed by the provider and sign in with Google. Press Ctrl+C in the provider's terminal to stop it.

## Run with Docker

To start the MCP server on its own:

```sh
docker compose up --build
```

Connect to `http://127.0.0.1:3001/mcp`.

The image bundles `data/places.json`. Docker copies it into the `bowie-data` volume the first time an empty volume is mounted. Catalog edits in Docker persist across rebuilds and `docker compose down`. Running `docker compose down -v` deletes the volume and its edits.

To build and start both services from their local checkouts, use the same root `.env`:

```sh
docker compose --profile oauth up --build
```

Compose connects the provider to `http://bowie:3001/mcp` on its Docker network. Your local `MCP_SERVER_URL` and `ALLOWED_HOSTS` settings apply to the Just workflow. Connect to the public **MCP** URL printed by the provider.

The Compose provider uses `STORAGE=local` and saves OAuth signing keys, client registrations, and grants in the `oauth-state` named volume. Keep that volume across container replacements. `docker compose down -v` deletes it along with the catalog volume.

For a separately deployed provider, use `STORAGE=s3` with a private bucket and AWS credentials to persist OAuth state without a local volume. S3 mode fails startup if the state object is inaccessible. See the [OAuth provider's storage configuration](https://github.com/ngrok-adhoc/mcp-oauth-provider#store-oauth-state-in-s3).
