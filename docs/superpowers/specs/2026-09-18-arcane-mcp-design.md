# Arcane MCP Server: Design

Date: 2026-09-18
Status: Approved

## Goal

A Docker-hostable MCP server that exposes the [Arcane](https://getarcane.app) Docker-management API to MCP clients (Claude Code, Claude Desktop, others) over Streamable HTTP. Tools are generated at container startup from the live Arcane instance's OpenAPI spec, so the tool set always matches the Arcane version in use.

## Why not the existing project

[MikeCase/arcane-mcp](https://github.com/MikeCase/arcane-mcp) is a hand-curated FastMCP server (125 tools) that runs only over stdio, has no Dockerfile, no HTTP transport and no endpoint auth. It also lags Arcane releases because every tool is hand-written. This project is a fresh build that generates tools from the spec instead.

## Decisions

| Question | Decision |
|---|---|
| Reuse vs fresh | Fresh build, generated from OpenAPI |
| When to generate | At container startup, from the live Arcane instance |
| Exposure | LAN / same Docker network; static bearer token on the MCP endpoint |
| Scope and safety | Tag allowlist; destructive operations require a two-step confirmation |
| Stack | Python 3.12, FastMCP 4.x (`OpenAPIProvider`), httpx, uv |

## 1. Architecture

A single container running FastMCP over Streamable HTTP at `/mcp`. On startup it:

1. Fetches `GET {ARCANE_BASE_URL}/api/openapi.json`, retrying until `ARCANE_MCP_SPEC_TIMEOUT` elapses, then exits non-zero (Docker restarts it) so it never serves an empty tool set.
2. Normalises the spec: assigns a synthetic `Jobs` tag to untagged operations, rewrites `servers[0].url` to `{ARCANE_BASE_URL}/api` (the spec advertises a LAN IP).
3. Builds route maps from the tag allowlist / exclusions and constructs an `OpenAPIProvider` with an `httpx.AsyncClient` carrying `X-API-Key`.
4. Wraps the server with a static-bearer `StaticTokenVerifier`, the safety middleware, and a `GET /health` custom route (unauthenticated).

Layout:

```
arcane-mcp/
  src/arcane_mcp/
    __init__.py
    config.py        # env var parsing, defaults, validation
    spec.py          # fetch + retry openapi.json, normalisation, route maps, naming
    safety.py        # destructive classification, confirmation token store, middleware
    server.py        # build_server(), main()
  tests/
    fixtures/openapi.json   # snapshot of Arcane 2.12.0 spec
  Dockerfile
  compose.yaml
  pyproject.toml
  README.md
```

## 2. Tool generation and filtering

- All Arcane operations carry kebab-case `operationId`s (e.g. `list-containers`). A `mcp_component_fn` converts them to snake_case tool names (`list_containers`) and sets the description to `"{summary}. {description}"` when both exist.
- Untagged operations (the seven `/environments/{id}/jobs/...` routes) receive a synthetic `Jobs` tag before filtering.
- Route map order (first match wins):
  1. EXCLUDE any path matching the hard-exclusion regex (streaming and binary downloads): `/stream`, `/logs/download`, `/export`, `/download`, `/attestations`, `/upload`, `/app-images/`.
  2. EXCLUDE any tag in `ARCANE_MCP_EXCLUDE_TAGS`.
  3. If `ARCANE_MCP_READ_ONLY=true`: EXCLUDE all non-GET methods.
  4. TOOL for any tag in `ARCANE_MCP_TAGS`.
  5. EXCLUDE everything else.
- Default `ARCANE_MCP_TAGS`:

  ```
  Containers, Images, Volumes, Networks, Projects, Project Workspace,
  Environments, System, Dashboard, Activities, Events, Ports, Updater,
  Image Updates, Vulnerabilities, Health, Version
  ```

  This yields on the order of 110 tools. Admin/identity tags (Users, Roles, API Keys, Auth, OIDC, Passkeys, MFA, Settings, System Backups, S3 Destinations, Federated Credentials) and large optional areas (Swarm, GitOps Syncs, Templates, Volume Backup, Volume Workspace, Builds, Container Registries, Notifications, Webhooks, Customize, Mobile Push, Application Images, Uploads, Variables, Diagnostics, JobSchedules, Jobs, Stream) are excluded unless added to the allowlist.
- All GET operations become TOOLs, not resources; MCP clients handle tools far more uniformly.

## 3. Safety gate

**Classification.** An operation is destructive if its HTTP method is `DELETE`, or its path (with parameters) matches the default regex:

```
/(prune|destroy|kill|down|restore|restore-files)$
```

`ARCANE_MCP_DESTRUCTIVE_PATTERNS` (comma-separated regexes) is appended to this list. Classification happens at startup using the spec, producing a set of destructive tool names.

**Middleware.** A FastMCP `Middleware` overriding `on_call_tool`:

- If the tool is not destructive, or `ARCANE_MCP_CONFIRM_DESTRUCTIVE=false`, pass through.
- Otherwise store `(tool_name, arguments, expires_at)` under a random 16-hex-char token and return, without calling Arcane:

  ```json
  {"requires_confirmation": true, "token": "…", "expires_in": 120,
   "operation": "remove_container", "arguments": {…},
   "message": "This operation is destructive. Call confirm_operation(token) to proceed."}
  ```

**`confirm_operation(token)`** is a single hand-written tool. It looks up the token, rejects unknown/expired/already-used tokens with a clear error, otherwise removes it from the store and invokes the original tool through the server with the stored arguments, returning Arcane's response. Store is in-memory, per-process, TTL 120 seconds, purged lazily on each access.

## 4. Configuration

| Variable | Required | Default | Notes |
|---|---|---|---|
| `ARCANE_BASE_URL` | yes | | e.g. `https://arcane.example.com` (no `/api`) |
| `ARCANE_API_KEY` | yes | | Sent as `X-API-Key` |
| `MCP_AUTH_TOKEN` | yes | | Bearer token clients must present |
| `MCP_HOST` | no | `0.0.0.0` | |
| `MCP_PORT` | no | `8000` | |
| `ARCANE_MCP_TAGS` | no | list in §2 | Comma-separated |
| `ARCANE_MCP_EXCLUDE_TAGS` | no | empty | Comma-separated |
| `ARCANE_MCP_READ_ONLY` | no | `false` | GET only |
| `ARCANE_MCP_CONFIRM_DESTRUCTIVE` | no | `true` | |
| `ARCANE_MCP_DESTRUCTIVE_PATTERNS` | no | empty | Extra regexes |
| `ARCANE_MCP_SPEC_TIMEOUT` | no | `60` | Seconds to keep retrying the spec fetch |
| `ARCANE_MCP_LOG_LEVEL` | no | `INFO` | |

Missing required variables cause an immediate exit with a message naming the variable.

## 5. Docker

- `Dockerfile`: two-stage build on `python:3.12-slim`; stage one uses `uv` to build a virtualenv from `uv.lock`; stage two copies the venv, runs as a non-root `app` user, `EXPOSE 8000`, `HEALTHCHECK` hitting `http://localhost:8000/health` via Python's `urllib` (no curl in slim image).
- `compose.yaml`: one `arcane-mcp` service with `env_file: .env`, `restart: unless-stopped`, port mapping `8000:8000`, and a commented example of joining an existing Arcane compose network.
- `.env.example` listing every variable.

Client connection example (Claude Code):

```
claude mcp add --transport http arcane http://<host>:8000/mcp \
  --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

## 6. Error handling

- Arcane 4xx/5xx responses propagate as tool errors containing status code and Arcane's JSON error body; the API key is never echoed.
- Spec fetch failures log each retry and the final failure reason.
- Unknown/expired confirmation tokens return a tool error, not an exception.
- FastMCP `mask_error_details` stays off so the model sees Arcane's actual message.

## 7. Testing

Pytest, `pytest-asyncio`, `pytest-httpx`. `tests/fixtures/openapi.json` is a committed snapshot of the Arcane 2.12.0 spec.

- `test_config.py`: required vars, defaults, list parsing, bool parsing.
- `test_spec.py`: fetch retry/backoff/timeout; synthetic `Jobs` tag; server URL rewrite; name conversion; route maps produce expected include/exclude decisions for representative operations (containers included, users excluded, `/stream` excluded, read-only removes POST).
- `test_safety.py`: classification of DELETE and pattern paths; token issue/confirm/expiry/reuse/unknown.
- `test_server.py`: boots `build_server()` in-process against the fixture spec with mocked Arcane; lists tools and asserts count and sample names; calls `list_containers` and sees mocked payload; calls `remove_container` and receives a confirmation payload; confirms and sees the mocked DELETE hit; request without bearer is rejected; `/health` returns 200 unauthenticated.
- Manual: build the image, run against `arcane.example.com` with a read-only key, connect from Claude Code, list environments.

## Out of scope

Swarm/GitOps/backup tools by default (available via tags), multi-user or OAuth auth, persistent audit logging, MCP resources and prompts, TLS termination (use a reverse proxy).
