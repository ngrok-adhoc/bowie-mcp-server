# Bowie MCP server

![Bowie](./bowie-icon.png)

Use this demo to learn how to restrict access to an MCP server over an ngrok tunnel. Clients like Claude and ChatGPT use the standard MCP OAuth flow through a drop-in proxy. The MCP server serves its tools, and authentication runs outside the app in the [companion MCP OAuth provider](https://github.com/ngrok-adhoc/mcp-oauth-provider).

The server comes with seed data for dog-friendly cafés and off-leash dog parks around Holland Park in Brisbane.

Once connected, try asking your MCP client “Find off-leash dog parks within 3 km of Holland Park, nearest first.” Radius searches include nearby suburbs and measure straight-line distance to saved locations.

## MCP tools

Your MCP client discovers these tools when it connects. FastMCP generates the input and output JSON schemas from Python signatures and Pydantic models. The MCP `tools/list` response includes the exact `inputSchema` and `outputSchema` for each tool.

| Tool | What it does | Input | Output |
| --- | --- | --- | --- |
| `find_dog_parks` | Find saved off-leash dog parks | [Location, radius, and drinking-water filters](mcp_server.py#L331-L336) | [Matching parks](mcp_server.py#L218-L219) |
| `find_dog_cafes` | Find saved dog-friendly cafés | [Location, radius, and puppuccino filters](mcp_server.py#L360-L365) | [Matching cafés](mcp_server.py#L222-L223) |
| `add_dog_park` | Save a dog park | [Park details](mcp_server.py#L117-L120) | [Saved park](mcp_server.py#L199-L201) |
| `add_dog_cafe` | Save a dog-friendly café | [Café details](mcp_server.py#L123-L130) | [Saved café](mcp_server.py#L204-L206) |
| `review_dog_park` | Append a review to a saved park | [Place ID, stars, optional comment and source](mcp_server.py#L445-L450) | [Updated park](mcp_server.py#L199-L201) |
| `review_dog_cafe` | Append a review to a saved café | [Place ID, stars, optional comment and source](mcp_server.py#L464-L469) | [Updated café](mcp_server.py#L204-L206) |

Both place types share [location and contact fields](mcp_server.py#L79-L114) and [ratings and comments](mcp_server.py#L157-L183).

## Choose how to run it

| Setup | What runs | Where the client connects |
| --- | --- | --- |
| [Local without authentication](#run-locally-without-authentication) | MCP server on your machine | `http://127.0.0.1:3001/mcp` |
| [Local with ngrok OAuth](#run-locally-with-ngrok-oauth) | MCP server and OAuth provider as two local processes | Public ngrok `/mcp` URL with Google sign-in |
| [Docker Compose stack](#run-the-docker-compose-stack) | MCP server and OAuth provider as two containers | Public ngrok `/mcp` URL with Google sign-in |

## Run locally without authentication

Install `just`, [uv](https://docs.astral.sh/uv/), and Python 3.11 or newer. From the repository root, run:

```sh
just mcp
```

Connect a local MCP client directly to `http://127.0.0.1:3001/mcp`. This endpoint requires no sign-in. The server reads and writes `data/places.json` by default. Press Ctrl+C to stop the server.

`just mcp` reads `BIND_HOST` and `ALLOWED_HOSTS` from the environment or the repo's `.env`. The bind address defaults to `127.0.0.1`. `ALLOWED_HOSTS` is a JSON list of additional HTTP hostnames. The MCP server always accepts `127.0.0.1`.

## How the ngrok OAuth proxy works

The OAuth provider and MCP server run on your machine or in Docker Compose. The client connects to ngrok's public HTTPS endpoint.

```mermaid
flowchart LR
    client["MCP client"] -->|"HTTPS"| publicEndpoint["Public ngrok endpoint"]
    subgraph upstream["Local machine or Docker network"]
        oauthProvider["MCP OAuth provider"] -->|"Private HTTP"| mcpServer["MCP server"]
    end
    publicEndpoint -->|"Authorized MCP requests"| oauthProvider
```

The provider issues an access token after Google sign-in and consent. For `/mcp` calls, ngrok Traffic Policy validates the token before the provider forwards the request to the MCP server.

### When to use the proxy

Share an MCP server with your team the same way you’d put ngrok OAuth in front of an internal website. Allow a company email domain or specific addresses. Users sign in with Google without configuring API keys or custom headers. ngrok Traffic Policy gates access, and the MCP server receives ordinary requests without needing auth code or seeing who signed in.

For production apps, configure OAuth in your MCP framework with a registered client and application-level identity and permissions.

### OAuth setup for local runs and Docker Compose

Both setups fetch the provider from its `main` branch on GitHub. Use a GitHub SSH key with access to the private `ngrok-adhoc/mcp-oauth-provider` repository and an ngrok account with access to internal endpoints and the required Traffic Policy actions.

In this repository's root, copy `.env.example` to `.env` if needed. Set `NGROK_AUTHTOKEN` to your ngrok agent authtoken and `USERS` to a JSON array of allowed Google email addresses or patterns. Set `NGROK_URL` to the HTTPS origin of your ngrok development domain or custom domain, or leave it blank to let the provider allocate one. See the [provider's email allowlist examples](https://github.com/ngrok-adhoc/mcp-oauth-provider#share-with-your-company).

## Run locally with ngrok OAuth

Install Node.js 26 or newer with npm and Git. Complete the [shared OAuth setup](#oauth-setup-for-local-runs-and-docker-compose), then start the MCP server with `just mcp`.

Set `MCP_SERVER_URL=http://127.0.0.1:3001/mcp` in `.env`. When the MCP server and the provider run on separate hosts, use a private network or an upstream with its own access controls and include its hostname in the server's `ALLOWED_HOSTS`.

To show the bundled icon on the OAuth consent screen, set `MCP_SERVER_ICON=./bowie-icon.png` when using Just. Docker Compose mounts that icon into the provider automatically. The provider limits local icons to 256 KiB.

Leave `just mcp` running. In a second terminal at the repository root, run `just oauth` to fetch and run the provider with `npx`:

```sh
just oauth
```

Connect your MCP client to the public **MCP** URL printed by the provider and sign in with Google. Press Ctrl+C in the provider's terminal to stop it.

## Run the Docker Compose stack

Complete the [shared OAuth setup](#oauth-setup-for-local-runs-and-docker-compose) and load your GitHub key into your SSH agent with `ssh-add`. Docker Compose forwards the agent during the provider build. Build and start both services from the repository root:

```sh
docker compose --profile oauth up --build
```

Compose starts the MCP server and OAuth provider as separate containers. The provider reaches the server at `http://bowie:3001/mcp` on the Docker network. Your local `MCP_SERVER_URL` and `ALLOWED_HOSTS` settings apply to the Just workflow. Connect to the public **MCP** URL printed by the provider and sign in with Google.

By default, the server image seeds the `bowie-data` named volume with `data/places.json`. Local edits to places and reviews persist across rebuilds and `docker compose down`.

The Compose provider uses `STORAGE=local` and saves OAuth signing keys, client registrations, and grants in the `oauth-state` named volume. Keep that volume across container replacements. `docker compose down -v` deletes it along with the Bowie data volume.

For a separately deployed provider, use `STORAGE=s3` with a private bucket and AWS credentials to persist OAuth state without a local volume. S3 mode fails startup if the state object is inaccessible. See the [OAuth provider's storage configuration](https://github.com/ngrok-adhoc/mcp-oauth-provider#store-oauth-state-in-s3).

### Run only the MCP container

For direct access from a local client without authentication, run `docker compose up --build` and connect to `http://127.0.0.1:3001/mcp`.

## Pull a released Docker image

Bowie releases are available for Linux amd64 and arm64. Sign in to GHCR with a GitHub personal access token (classic) with `read:packages` and access to the package, then pull an exact version.

```sh
docker login ghcr.io
docker pull ghcr.io/ngrok-adhoc/bowie-mcp-server:v0.1.0
```

### Cut a Docker release

Set the release version in `pyproject.toml` and run `uv lock` if the version changes. Once the version is on `main`, tag that commit. For example:

```sh
git fetch origin main --tags
git tag -a v0.1.0 origin/main -m "Bowie MCP server 0.1.0"
git push origin refs/tags/v0.1.0
```

The [Publish Bowie image workflow](.github/workflows/publish-image.yml) publishes `v0.1.0`, `0.1.0`, and `sha-<full-commit-sha>` tags to GHCR. Pushes to `main` that change image inputs warm the build cache. To retry a failed release, rerun the workflow or run `gh workflow run publish-image.yml --ref v0.1.0`.

## Store places and reviews in S3

For a hosted MCP server, use a private Amazon S3 bucket to keep places and reviews across deployments. Set these environment variables on the MCP server, or in `.env` for Just or Docker Compose:

```dotenv
STORAGE=s3
S3_BUCKET=your-private-bucket
S3_KEY=bowie/places.json
AWS_DEFAULT_REGION=ap-southeast-2
```

`S3_KEY` defaults to `bowie/places.json`. Supply AWS credentials through the SDK's standard credential chain, such as the host's AWS role or `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `AWS_SESSION_TOKEN` for temporary credentials.

Grant `s3:GetObject` and `s3:PutObject` on that object. For a missing key, the server also needs `s3:ListBucket` so S3 can report that it does not exist. On startup the server seeds a missing object from `data/places.json`. To migrate existing local data or Docker volume, upload its `places.json` to the intended key before starting the S3-backed server.

S3 startup fails if the object cannot be read or initialized. A conditional write rejects an edit if another server has updated the data since it was read. Retry the edit to apply it to the latest data. [Amazon S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)

Bowie's storage is configured independently of the OAuth provider's `STORAGE` setting. Keep Bowie's data and OAuth state in separate S3 objects.

## Host Bowie on Ship

Set Bowie's primary endpoint to an ngrok internal URL on port `3001`. Set `BIND_HOST=0.0.0.0`, configure S3 as above, and include the OAuth provider's public hostname in Bowie's `ALLOWED_HOSTS`.

Run the OAuth provider as a separate Ship app in the same ngrok account. The provider creates its own public sign-in endpoint. Set `NGROK_URL` to a fixed HTTPS origin for that endpoint, set `MCP_SERVER_BINDING=internal`, and point `MCP_SERVER_URL` at the Bowie internal URL including `/mcp`. Set the provider's `STORAGE=s3` and give its OAuth state a separate S3 key.

## Troubleshooting

If the MCP server logs repeated `GET /mcp` 404s after a restart, reconnect your MCP client. The server returns `Session not found` when a client tries to reuse a session ID from before the restart.
