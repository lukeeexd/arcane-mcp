# Arcane MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Docker-hosted MCP server that generates its tools at startup from a live Arcane instance's OpenAPI spec, exposes them over Streamable HTTP behind a static bearer token, and gates destructive operations behind a two-step confirmation.

**Architecture:** One Python package (`arcane_mcp`) with four modules: `config` (env parsing), `spec` (fetch, normalise, filter, name), `safety` (destructive classification, confirmation tokens, middleware), and `server` (assembly and entrypoint). FastMCP 4's `OpenAPIProvider` does the OpenAPI-to-tool conversion; our code only supplies a route-map function, a component-naming function, middleware, one hand-written tool and a health route. Packaged as a two-stage `python:3.12-slim` image.

**Tech Stack:** Python 3.12, FastMCP 4.0.x, httpx2 (the fork FastMCP 4 depends on; **not** `httpx`), uv, pytest + pytest-asyncio, Docker / Compose.

**Spec:** `docs/superpowers/specs/2026-09-18-arcane-mcp-design.md`

## Global Constraints

- Python `>=3.12`. FastMCP `>=4.0.5,<5`.
- All HTTP to Arcane goes through `httpx2.AsyncClient` (FastMCP 4 imports `httpx2`, a fork; plain `httpx` clients are rejected). Tests mock Arcane with `httpx2.MockTransport`. Do **not** add `pytest-httpx` (it patches `httpx`, not `httpx2`).
- `OpenAPIProvider(..., validate_output=False)` always. Arcane's response schemas are strict (`additionalProperties: false`) and validation makes real responses fail.
- Tool names are snake_case versions of Arcane `operationId`s (`list-containers` -> `list_containers`).
- Env var names exactly as in spec §4: `ARCANE_BASE_URL`, `ARCANE_API_KEY`, `MCP_AUTH_TOKEN`, `MCP_HOST`, `MCP_PORT`, `ARCANE_MCP_TAGS`, `ARCANE_MCP_EXCLUDE_TAGS`, `ARCANE_MCP_READ_ONLY`, `ARCANE_MCP_CONFIRM_DESTRUCTIVE`, `ARCANE_MCP_DESTRUCTIVE_PATTERNS`, `ARCANE_MCP_SPEC_TIMEOUT`, `ARCANE_MCP_LOG_LEVEL`.
- Never log or echo `ARCANE_API_KEY` or `MCP_AUTH_TOKEN`.
- Commit after every task with the attribution trailers:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi
  ```
- Run tests with `uv run pytest -q` from repo root. Repo root is `D:\ClaudeCode\arcane-mcp`.
- Windows host: use forward slashes in shell commands via Git Bash.

## Verified FastMCP 4.0.5 API facts (do not re-derive)

```python
from fastmcp import FastMCP, Client
from fastmcp.server.providers.openapi import OpenAPIProvider, MCPType   # MCPType.TOOL / EXCLUDE
from fastmcp.utilities.openapi.models import HTTPRoute                  # .path .method .operation_id .summary .description .tags
from fastmcp.server.middleware import Middleware, MiddlewareContext, CallNext
from fastmcp.tools import ToolResult                                    # ToolResult(content=None, structured_content=None, meta=None, is_error=False)
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier       # StaticTokenVerifier(tokens={token: {"client_id": str, "scopes": list[str]}})

OpenAPIProvider(openapi_spec: dict, client: httpx2.AsyncClient, *, route_map_fn=Callable[[HTTPRoute, MCPType], MCPType | None],
                mcp_component_fn=Callable[[HTTPRoute, component], None], validate_output: bool)
FastMCP(name, instructions=..., auth=AuthProvider|None, middleware=[...], providers=[...])
FastMCP.call_tool(name, arguments, *, run_middleware=True) -> ToolResult
FastMCP.custom_route(path, methods=[...]) decorator; handler(request: starlette Request) -> starlette Response
FastMCP.run(transport="http", host=..., port=..., path="/mcp")
FastMCP.http_app(path="/mcp") -> Starlette ASGI app
Middleware.on_call_tool(self, context: MiddlewareContext, call_next) -> ToolResult
    # context.message.name : str ; context.message.arguments : dict | None
Client(server_or_app)  # in-memory transport; bypasses HTTP auth (fine for tool tests)
```

Smoke-test results against the Arcane 2.12.0 spec with the default allowlist: **137 tools**; `list_containers` input schema has `id` (environment), `search`, `sort`, `order`, `start`, `limit`; requests hit `{base_url}/environments/0/containers` with header `X-API-Key`; Arcane 5xx surfaces as `ToolError("HTTP error 500: ... - {json body}")`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, deps, pytest config, entrypoint `arcane-mcp = arcane_mcp.server:main` |
| `src/arcane_mcp/__init__.py` | Version string only |
| `src/arcane_mcp/config.py` | `Settings` dataclass + `load_settings(env) -> Settings`; raises `ConfigError` |
| `src/arcane_mcp/spec.py` | `fetch_spec`, `normalise_spec`, `tool_name`, `build_route_map_fn`, `build_component_fn`, constants |
| `src/arcane_mcp/safety.py` | `is_destructive`, `destructive_tool_names`, `ConfirmationStore`, `ConfirmationMiddleware`, `register_confirm_tool` |
| `src/arcane_mcp/server.py` | `build_server(settings, spec, client) -> FastMCP`, `main()` |
| `tests/conftest.py` | Fixture: loaded spec, mock Arcane client factory |
| `tests/fixtures/openapi.json` | Snapshot of Arcane 2.12.0 spec |
| `tests/test_config.py`, `test_spec.py`, `test_safety.py`, `test_server.py` | Per-module tests |
| `Dockerfile`, `compose.yaml`, `.env.example`, `.dockerignore`, `.gitignore`, `README.md` | Packaging and docs |

---

### Task 1: Project scaffold and test fixture

**Files:**
- Create: `pyproject.toml`, `src/arcane_mcp/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/fixtures/openapi.json`, `.gitignore`, `.python-version`
- Modify: `docs/superpowers/specs/2026-09-18-arcane-mcp-design.md` (tool count line)

**Interfaces:**
- Produces: pytest fixture `spec` (dict, the loaded fixture JSON) and fixture `make_arcane_client(handler) -> httpx2.AsyncClient`.

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/arcane_mcp"]

[project]
name = "arcane-mcp"
version = "0.1.0"
description = "Docker-hostable MCP server for Arcane, generated from its OpenAPI spec"
readme = "README.md"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "fastmcp>=4.0.5,<5",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
]

[project.scripts]
arcane-mcp = "arcane_mcp.server:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Write package init, .python-version, .gitignore**

`src/arcane_mcp/__init__.py`:
```python
"""Arcane MCP server."""

__version__ = "0.1.0"
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
.venv/
__pycache__/
*.pyc
.pytest_cache/
.env
dist/
```

- [ ] **Step 3: Copy the OpenAPI fixture**

Copy the previously downloaded spec into place. If the scratchpad copy is gone, fetch it again:

```bash
mkdir -p tests/fixtures
curl -sL https://arcane.example.com/api/openapi.json -o tests/fixtures/openapi.json
python -c "import json;d=json.load(open('tests/fixtures/openapi.json'));print(d['info']['version'], len(d['paths']))"
```
Expected: `2.12.0 ` followed by a path count around 300.

- [ ] **Step 4: Write tests/conftest.py**

```python
import json
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def spec() -> dict:
    return json.loads((FIXTURES / "openapi.json").read_text(encoding="utf-8"))


@pytest.fixture
def make_arcane_client() -> Callable[[Callable[[httpx2.Request], httpx2.Response]], httpx2.AsyncClient]:
    def _make(handler):
        return httpx2.AsyncClient(
            base_url="http://arcane.test/api",
            headers={"X-API-Key": "test-key"},
            transport=httpx2.MockTransport(handler),
        )

    return _make
```

Also create empty `tests/__init__.py`.

- [ ] **Step 5: Install and verify pytest collects**

```bash
uv sync --extra dev
uv run pytest -q
```
Expected: `no tests ran` with exit code 5 and no import errors.

- [ ] **Step 6: Fix spec tool count**

In the spec, replace `This yields on the order of 110 tools.` with `This yields 137 tools against Arcane 2.12.0.`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Scaffold arcane-mcp package with pytest and OpenAPI fixture" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 2: Configuration loading

**Files:**
- Create: `src/arcane_mcp/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  ```python
  class ConfigError(ValueError): ...
  DEFAULT_TAGS: frozenset[str]
  @dataclass(frozen=True)
  class Settings:
      arcane_base_url: str          # trailing slash and trailing "/api" stripped
      arcane_api_key: str
      mcp_auth_token: str
      mcp_host: str = "0.0.0.0"
      mcp_port: int = 8000
      tags: frozenset[str] = DEFAULT_TAGS
      exclude_tags: frozenset[str] = frozenset()
      read_only: bool = False
      confirm_destructive: bool = True
      destructive_patterns: tuple[str, ...] = ()
      spec_timeout: float = 60.0
      log_level: str = "INFO"
  def load_settings(env: Mapping[str, str]) -> Settings
  ```

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:
```python
import pytest

from arcane_mcp.config import DEFAULT_TAGS, ConfigError, load_settings

REQUIRED = {
    "ARCANE_BASE_URL": "https://arcane.example.com/",
    "ARCANE_API_KEY": "arc_key",
    "MCP_AUTH_TOKEN": "secret",
}


def test_defaults():
    s = load_settings(REQUIRED)
    assert s.arcane_base_url == "https://arcane.example.com"
    assert s.arcane_api_key == "arc_key"
    assert s.mcp_auth_token == "secret"
    assert s.mcp_host == "0.0.0.0"
    assert s.mcp_port == 8000
    assert s.tags == DEFAULT_TAGS
    assert "Containers" in DEFAULT_TAGS and "Users" not in DEFAULT_TAGS
    assert s.exclude_tags == frozenset()
    assert s.read_only is False
    assert s.confirm_destructive is True
    assert s.destructive_patterns == ()
    assert s.spec_timeout == 60.0
    assert s.log_level == "INFO"


def test_strips_trailing_api_segment():
    s = load_settings({**REQUIRED, "ARCANE_BASE_URL": "http://h:3552/api/"})
    assert s.arcane_base_url == "http://h:3552"


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_names_variable(missing):
    env = {k: v for k, v in REQUIRED.items() if k != missing}
    with pytest.raises(ConfigError) as exc:
        load_settings(env)
    assert missing in str(exc.value)


def test_blank_required_is_missing():
    with pytest.raises(ConfigError):
        load_settings({**REQUIRED, "ARCANE_API_KEY": "  "})


def test_list_and_bool_parsing():
    s = load_settings({
        **REQUIRED,
        "ARCANE_MCP_TAGS": " Containers, Swarm ,,Images ",
        "ARCANE_MCP_EXCLUDE_TAGS": "Images",
        "ARCANE_MCP_READ_ONLY": "TRUE",
        "ARCANE_MCP_CONFIRM_DESTRUCTIVE": "0",
        "ARCANE_MCP_DESTRUCTIVE_PATTERNS": r"/reset$, /wipe",
        "ARCANE_MCP_SPEC_TIMEOUT": "5",
        "MCP_PORT": "9000",
        "MCP_HOST": "127.0.0.1",
        "ARCANE_MCP_LOG_LEVEL": "debug",
    })
    assert s.tags == frozenset({"Containers", "Swarm", "Images"})
    assert s.exclude_tags == frozenset({"Images"})
    assert s.read_only is True
    assert s.confirm_destructive is False
    assert s.destructive_patterns == (r"/reset$", "/wipe")
    assert s.spec_timeout == 5.0
    assert s.mcp_port == 9000
    assert s.mcp_host == "127.0.0.1"
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["yes", "true", "1", "on", "True"])
def test_truthy_values(value):
    assert load_settings({**REQUIRED, "ARCANE_MCP_READ_ONLY": value}).read_only is True


def test_bad_bool_raises():
    with pytest.raises(ConfigError):
        load_settings({**REQUIRED, "ARCANE_MCP_READ_ONLY": "maybe"})


def test_bad_port_raises():
    with pytest.raises(ConfigError):
        load_settings({**REQUIRED, "MCP_PORT": "abc"})
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'arcane_mcp.config'`.

- [ ] **Step 3: Implement config.py**

```python
"""Environment-variable configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_TAGS: frozenset[str] = frozenset({
    "Containers", "Images", "Volumes", "Networks", "Projects", "Project Workspace",
    "Environments", "System", "Dashboard", "Activities", "Events", "Ports", "Updater",
    "Image Updates", "Vulnerabilities", "Health", "Version",
})

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class ConfigError(ValueError):
    """Raised when required configuration is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    arcane_base_url: str
    arcane_api_key: str
    mcp_auth_token: str
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    tags: frozenset[str] = DEFAULT_TAGS
    exclude_tags: frozenset[str] = frozenset()
    read_only: bool = False
    confirm_destructive: bool = True
    destructive_patterns: tuple[str, ...] = ()
    spec_timeout: float = 60.0
    log_level: str = "INFO"


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Required environment variable {name} is not set")
    return value


def _csv(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    raw = env.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _number(env: Mapping[str, str], name: str, default: float, cast: type) -> float | int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return cast(default)
    try:
        return cast(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    return url


def load_settings(env: Mapping[str, str]) -> Settings:
    tags = _csv(env, "ARCANE_MCP_TAGS")
    return Settings(
        arcane_base_url=_base_url(_required(env, "ARCANE_BASE_URL")),
        arcane_api_key=_required(env, "ARCANE_API_KEY"),
        mcp_auth_token=_required(env, "MCP_AUTH_TOKEN"),
        mcp_host=env.get("MCP_HOST", "").strip() or "0.0.0.0",
        mcp_port=int(_number(env, "MCP_PORT", 8000, int)),
        tags=frozenset(tags) if tags else DEFAULT_TAGS,
        exclude_tags=frozenset(_csv(env, "ARCANE_MCP_EXCLUDE_TAGS")),
        read_only=_bool(env, "ARCANE_MCP_READ_ONLY", False),
        confirm_destructive=_bool(env, "ARCANE_MCP_CONFIRM_DESTRUCTIVE", True),
        destructive_patterns=_csv(env, "ARCANE_MCP_DESTRUCTIVE_PATTERNS"),
        spec_timeout=float(_number(env, "ARCANE_MCP_SPEC_TIMEOUT", 60.0, float)),
        log_level=(env.get("ARCANE_MCP_LOG_LEVEL", "").strip() or "INFO").upper(),
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/arcane_mcp/config.py tests/test_config.py
git commit -m "Add environment configuration loader" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 3: Spec fetch, normalisation and naming

**Files:**
- Create: `src/arcane_mcp/spec.py`
- Test: `tests/test_spec.py`

**Interfaces:**
- Consumes: `Settings` from Task 2.
- Produces:
  ```python
  class SpecFetchError(RuntimeError): ...
  SYNTHETIC_UNTAGGED_TAG = "Jobs"
  HARD_EXCLUDE = re.compile(r"(/stream|/logs/download|/export|/download|/attestations|/upload|/app-images/)")
  async def fetch_spec(client: httpx2.AsyncClient, *, timeout: float, sleep=asyncio.sleep, clock=time.monotonic) -> dict
  def normalise_spec(spec: dict, base_url: str) -> dict          # returns a deep copy
  def tool_name(operation_id: str) -> str
  def build_route_map_fn(settings: Settings) -> Callable[[HTTPRoute, MCPType], MCPType]
  def build_component_fn() -> Callable[[HTTPRoute, Any], None]
  ```

- [ ] **Step 1: Write failing tests**

`tests/test_spec.py`:
```python
import asyncio
import copy
from types import SimpleNamespace

import httpx2
import pytest
from fastmcp.server.providers.openapi import MCPType

from arcane_mcp.config import load_settings
from arcane_mcp.spec import (
    HARD_EXCLUDE,
    SYNTHETIC_UNTAGGED_TAG,
    SpecFetchError,
    build_component_fn,
    build_route_map_fn,
    fetch_spec,
    normalise_spec,
    tool_name,
)

BASE_ENV = {"ARCANE_BASE_URL": "http://arcane.test", "ARCANE_API_KEY": "k", "MCP_AUTH_TOKEN": "t"}


def route(method: str, path: str, tags: list[str] | None, operation_id: str = "x-y"):
    return SimpleNamespace(method=method, path=path, tags=tags, operation_id=operation_id,
                           summary="Sum", description="Desc")


# ---- tool_name ----

@pytest.mark.parametrize("op,expected", [
    ("list-containers", "list_containers"),
    ("get-image-by-id", "get_image_by_id"),
    ("already_snake", "already_snake"),
    ("Mixed-Case", "mixed_case"),
])
def test_tool_name(op, expected):
    assert tool_name(op) == expected


# ---- normalise_spec ----

def test_normalise_rewrites_server_and_tags_untagged(spec):
    original = copy.deepcopy(spec)
    out = normalise_spec(spec, "http://arcane.test")
    assert out["servers"] == [{"url": "http://arcane.test/api"}]
    assert spec == original, "input must not be mutated"
    jobs = out["paths"]["/environments/{id}/jobs/{jobId}/restart"]["post"]
    assert jobs["tags"] == [SYNTHETIC_UNTAGGED_TAG]
    containers = out["paths"]["/environments/{id}/containers"]["get"]
    assert containers["tags"] == ["Containers"]


# ---- route map ----

def test_route_map_default_allowlist():
    fn = build_route_map_fn(load_settings(BASE_ENV))
    assert fn(route("GET", "/environments/{id}/containers", ["Containers"]), MCPType.TOOL) is MCPType.TOOL
    assert fn(route("POST", "/environments/{id}/containers/{containerId}/start", ["Containers"]), MCPType.TOOL) is MCPType.TOOL
    assert fn(route("GET", "/users", ["Users"]), MCPType.TOOL) is MCPType.EXCLUDE
    assert fn(route("GET", "/environments/{id}/swarm/info", ["Swarm"]), MCPType.TOOL) is MCPType.EXCLUDE
    assert fn(route("GET", "/x", None), MCPType.TOOL) is MCPType.EXCLUDE


def test_route_map_hard_exclusions_beat_allowlist():
    fn = build_route_map_fn(load_settings(BASE_ENV))
    for path in [
        "/environments/{id}/containers/{containerId}/logs/download",
        "/environments/{id}/images/{name}/export",
        "/environments/{id}/images/{name}/attestations",
        "/environments/{id}/images/upload",
        "/environments/{id}/stream",
    ]:
        assert HARD_EXCLUDE.search(path)
        assert fn(route("GET", path, ["Containers", "Images"]), MCPType.TOOL) is MCPType.EXCLUDE


def test_route_map_exclude_tags_beats_allowlist():
    fn = build_route_map_fn(load_settings({**BASE_ENV, "ARCANE_MCP_EXCLUDE_TAGS": "Images"}))
    # Commit container is tagged both Containers and Images; any excluded tag removes it.
    assert fn(route("POST", "/environments/{id}/containers/{containerId}/commit", ["Containers", "Images"]), MCPType.TOOL) is MCPType.EXCLUDE


def test_route_map_read_only():
    fn = build_route_map_fn(load_settings({**BASE_ENV, "ARCANE_MCP_READ_ONLY": "true"}))
    assert fn(route("GET", "/environments/{id}/containers", ["Containers"]), MCPType.TOOL) is MCPType.TOOL
    assert fn(route("POST", "/environments/{id}/containers/{containerId}/start", ["Containers"]), MCPType.TOOL) is MCPType.EXCLUDE
    assert fn(route("DELETE", "/environments/{id}/containers/{containerId}", ["Containers"]), MCPType.TOOL) is MCPType.EXCLUDE


def test_route_map_custom_tags():
    fn = build_route_map_fn(load_settings({**BASE_ENV, "ARCANE_MCP_TAGS": "Swarm"}))
    assert fn(route("GET", "/environments/{id}/swarm/info", ["Swarm"]), MCPType.TOOL) is MCPType.TOOL
    assert fn(route("GET", "/environments/{id}/containers", ["Containers"]), MCPType.TOOL) is MCPType.EXCLUDE


# ---- component fn ----

def test_component_fn_renames_and_describes():
    comp = SimpleNamespace(name="list-containers", description=None)
    build_component_fn()(route("GET", "/c", ["Containers"], "list-containers"), comp)
    assert comp.name == "list_containers"
    assert comp.description == "Sum. Desc"


def test_component_fn_summary_only():
    comp = SimpleNamespace(name="x", description=None)
    r = route("GET", "/c", ["Containers"], "get-thing")
    r.description = None
    build_component_fn()(r, comp)
    assert comp.description == "Sum"


# ---- fetch_spec ----

async def test_fetch_spec_success(make_arcane_client, spec):
    client = make_arcane_client(lambda req: httpx2.Response(200, json={"openapi": "3.1.0", "paths": {}}))
    out = await fetch_spec(client, timeout=5)
    assert out["openapi"] == "3.1.0"


async def test_fetch_spec_retries_then_succeeds(make_arcane_client):
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx2.Response(503)
        return httpx2.Response(200, json={"openapi": "3.1.0", "paths": {}})

    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    out = await fetch_spec(make_arcane_client(handler), timeout=60, sleep=fake_sleep)
    assert out["paths"] == {}
    assert calls["n"] == 3
    assert slept == [1.0, 2.0]


async def test_fetch_spec_gives_up_after_timeout(make_arcane_client):
    def handler(req):
        raise httpx2.ConnectError("refused")

    now = {"t": 0.0}

    async def fake_sleep(s):
        now["t"] += s

    with pytest.raises(SpecFetchError) as exc:
        await fetch_spec(make_arcane_client(handler), timeout=5, sleep=fake_sleep, clock=lambda: now["t"])
    assert "refused" in str(exc.value)


async def test_fetch_spec_requests_openapi_path(make_arcane_client):
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx2.Response(200, json={"openapi": "3.1.0", "paths": {}})

    await fetch_spec(make_arcane_client(handler), timeout=5)
    assert seen == ["http://arcane.test/api/openapi.json"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_spec.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'arcane_mcp.spec'`.

- [ ] **Step 3: Implement spec.py**

```python
"""Fetch, normalise, filter and name Arcane's OpenAPI operations."""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
from fastmcp.server.providers.openapi import MCPType
from fastmcp.utilities.openapi.models import HTTPRoute

from arcane_mcp.config import Settings

logger = logging.getLogger(__name__)

SYNTHETIC_UNTAGGED_TAG = "Jobs"
HARD_EXCLUDE = re.compile(r"(/stream|/logs/download|/export|/download|/attestations|/upload|/app-images/)")
_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
_MAX_BACKOFF = 10.0


class SpecFetchError(RuntimeError):
    """Raised when the OpenAPI spec cannot be fetched within the timeout."""


async def fetch_spec(
    client: httpx2.AsyncClient,
    *,
    timeout: float,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """GET /openapi.json (relative to the client's base_url), retrying with backoff until timeout."""
    deadline = clock() + timeout
    delay = 1.0
    attempt = 0
    last_error = "unknown"
    while True:
        attempt += 1
        try:
            response = await client.get("/openapi.json")
            response.raise_for_status()
            spec = response.json()
            if not isinstance(spec, dict) or "paths" not in spec:
                raise SpecFetchError("Response is not an OpenAPI document")
            logger.info("Fetched OpenAPI spec (attempt %d, %d paths)", attempt, len(spec["paths"]))
            return spec
        except SpecFetchError:
            raise
        except Exception as exc:  # noqa: BLE001 - any transport/HTTP/JSON error is retryable
            last_error = f"{type(exc).__name__}: {exc}"
            if clock() >= deadline:
                raise SpecFetchError(f"Could not fetch OpenAPI spec after {attempt} attempts: {last_error}") from exc
            logger.warning("Spec fetch attempt %d failed (%s); retrying in %.0fs", attempt, last_error, delay)
            await sleep(delay)
            delay = min(delay * 2, _MAX_BACKOFF)


def normalise_spec(spec: dict[str, Any], base_url: str) -> dict[str, Any]:
    """Return a copy with servers pointed at base_url/api and untagged operations tagged."""
    out = copy.deepcopy(spec)
    out["servers"] = [{"url": f"{base_url}/api"}]
    for path_item in out.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() in _HTTP_METHODS and isinstance(operation, dict) and not operation.get("tags"):
                operation["tags"] = [SYNTHETIC_UNTAGGED_TAG]
    return out


def tool_name(operation_id: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", operation_id.lower())


def build_route_map_fn(settings: Settings) -> Callable[[HTTPRoute, MCPType], MCPType]:
    allow = set(settings.tags)
    deny = set(settings.exclude_tags)
    read_only = settings.read_only

    def route_map(route: HTTPRoute, _default: MCPType) -> MCPType:
        tags = set(route.tags or [])
        if HARD_EXCLUDE.search(route.path):
            return MCPType.EXCLUDE
        if tags & deny:
            return MCPType.EXCLUDE
        if read_only and route.method.upper() != "GET":
            return MCPType.EXCLUDE
        if tags & allow:
            return MCPType.TOOL
        return MCPType.EXCLUDE

    return route_map


def build_component_fn() -> Callable[[HTTPRoute, Any], None]:
    def component(route: HTTPRoute, comp: Any) -> None:
        if route.operation_id:
            comp.name = tool_name(route.operation_id)
        summary = (route.summary or "").strip().rstrip(".")
        description = (route.description or "").strip()
        if summary and description:
            comp.description = f"{summary}. {description}"
        elif summary or description:
            comp.description = summary or description

    return component
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_spec.py -q`
Expected: all PASS. If `test_fetch_spec_retries_then_succeeds` fails on the `slept` assertion, check the backoff sequence is `1.0, 2.0` (delay doubles after each sleep).

- [ ] **Step 5: Commit**

```bash
git add src/arcane_mcp/spec.py tests/test_spec.py
git commit -m "Add OpenAPI spec fetching, normalisation and route filtering" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 4: Safety: classification and confirmation store

**Files:**
- Create: `src/arcane_mcp/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Consumes: `tool_name` from Task 3, `Settings` from Task 2.
- Produces:
  ```python
  DEFAULT_DESTRUCTIVE_PATTERNS: tuple[str, ...] = (r"/(prune|destroy|kill|down|restore|restore-files)$",)
  TOKEN_TTL_SECONDS = 120
  def is_destructive(method: str, path: str, extra_patterns: Sequence[str] = ()) -> bool
  def destructive_tool_names(spec: dict, extra_patterns: Sequence[str] = ()) -> frozenset[str]   # snake_case names
  @dataclass class PendingOperation: tool: str; arguments: dict; expires_at: float
  class ConfirmationStore:
      def __init__(self, ttl: float = TOKEN_TTL_SECONDS, clock=time.monotonic, token_factory=lambda: secrets.token_hex(8))
      def issue(self, tool: str, arguments: dict) -> tuple[str, PendingOperation]
      def redeem(self, token: str) -> PendingOperation      # raises ConfirmationError(unknown/expired); single use
  class ConfirmationError(ValueError)
  ```
  (Middleware and the `confirm_operation` tool are Task 5.)

- [ ] **Step 1: Write failing tests**

`tests/test_safety.py`:
```python
import pytest

from arcane_mcp.safety import (
    ConfirmationError,
    ConfirmationStore,
    destructive_tool_names,
    is_destructive,
)


@pytest.mark.parametrize("method,path,expected", [
    ("DELETE", "/environments/{id}/containers/{containerId}", True),
    ("delete", "/api-keys/{id}", True),
    ("POST", "/environments/{id}/images/prune", True),
    ("POST", "/environments/{id}/projects/{projectId}/down", True),
    ("POST", "/environments/{id}/containers/{containerId}/kill", True),
    ("POST", "/backups/{id}/restore", True),
    ("POST", "/backups/{id}/restore-files", True),
    ("DELETE", "/environments/{id}/projects/{projectId}/destroy", True),
    ("POST", "/environments/{id}/containers/{containerId}/stop", False),
    ("POST", "/environments/{id}/containers/{containerId}/restart", False),
    ("GET", "/environments/{id}/containers", False),
    ("GET", "/environments/{id}/updater/history", False),   # contains no pattern word at end
    ("POST", "/environments/{id}/projects/{projectId}/downgrade", False),  # 'down' must be a full segment
])
def test_is_destructive(method, path, expected):
    assert is_destructive(method, path) is expected


def test_is_destructive_extra_patterns():
    assert is_destructive("POST", "/environments/{id}/system/reset", (r"/reset$",)) is True
    assert is_destructive("POST", "/environments/{id}/system/reset") is False


def test_destructive_tool_names_from_spec(spec):
    names = destructive_tool_names(spec)
    assert "delete_container" in names
    assert "prune_images" in names
    assert "destroy_project" in names
    assert "kill_container" in names
    assert "list_containers" not in names
    assert "stop_container" not in names
    assert all("-" not in n for n in names)


def test_destructive_tool_names_extra_pattern(spec):
    base = destructive_tool_names(spec)
    more = destructive_tool_names(spec, (r"/stop$",))
    assert "stop_container" in more and "stop_container" not in base


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_store(**kw):
    clock = FakeClock()
    tokens = iter(["tok1", "tok2", "tok3"])
    store = ConfirmationStore(clock=clock, token_factory=lambda: next(tokens), **kw)
    return store, clock


def test_issue_and_redeem():
    store, _ = make_store()
    token, pending = store.issue("delete_container", {"id": "0", "containerId": "abc"})
    assert token == "tok1"
    assert pending.tool == "delete_container"
    assert pending.expires_at == 1120.0
    got = store.redeem("tok1")
    assert got.arguments == {"id": "0", "containerId": "abc"}


def test_redeem_is_single_use():
    store, _ = make_store()
    token, _ = store.issue("delete_container", {})
    store.redeem(token)
    with pytest.raises(ConfirmationError, match="unknown|already"):
        store.redeem(token)


def test_redeem_unknown():
    store, _ = make_store()
    with pytest.raises(ConfirmationError, match="unknown"):
        store.redeem("nope")


def test_redeem_expired():
    store, clock = make_store(ttl=10)
    token, _ = store.issue("delete_container", {})
    clock.t += 11
    with pytest.raises(ConfirmationError, match="expired"):
        store.redeem(token)


def test_expired_tokens_are_purged_on_issue():
    store, clock = make_store(ttl=10)
    store.issue("a", {})
    clock.t += 11
    store.issue("b", {})
    assert store.pending_count() == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_safety.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement safety.py (classification + store only)**

```python
"""Destructive-operation classification and two-step confirmation."""

from __future__ import annotations

import logging
import re
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from arcane_mcp.spec import tool_name

logger = logging.getLogger(__name__)

DEFAULT_DESTRUCTIVE_PATTERNS: tuple[str, ...] = (r"/(prune|destroy|kill|down|restore|restore-files)$",)
TOKEN_TTL_SECONDS = 120
_HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


class ConfirmationError(ValueError):
    """Unknown, expired or already-used confirmation token."""


def is_destructive(method: str, path: str, extra_patterns: Sequence[str] = ()) -> bool:
    if method.upper() == "DELETE":
        return True
    return any(re.search(p, path) for p in (*DEFAULT_DESTRUCTIVE_PATTERNS, *extra_patterns))


def destructive_tool_names(spec: dict[str, Any], extra_patterns: Sequence[str] = ()) -> frozenset[str]:
    names: set[str] = set()
    for path, path_item in spec.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if op_id and is_destructive(method, path, extra_patterns):
                names.add(tool_name(op_id))
    return frozenset(names)


@dataclass
class PendingOperation:
    tool: str
    arguments: dict[str, Any]
    expires_at: float


class ConfirmationStore:
    def __init__(
        self,
        ttl: float = TOKEN_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._token_factory = token_factory
        self._pending: dict[str, PendingOperation] = {}

    @property
    def ttl(self) -> float:
        return self._ttl

    def _purge(self) -> None:
        now = self._clock()
        for token in [t for t, p in self._pending.items() if p.expires_at <= now]:
            del self._pending[token]

    def pending_count(self) -> int:
        self._purge()
        return len(self._pending)

    def issue(self, tool: str, arguments: dict[str, Any]) -> tuple[str, PendingOperation]:
        self._purge()
        token = self._token_factory()
        pending = PendingOperation(tool=tool, arguments=dict(arguments), expires_at=self._clock() + self._ttl)
        self._pending[token] = pending
        return token, pending

    def redeem(self, token: str) -> PendingOperation:
        pending = self._pending.pop(token, None)
        if pending is None:
            raise ConfirmationError("Confirmation token is unknown or already used")
        if pending.expires_at <= self._clock():
            raise ConfirmationError("Confirmation token has expired; call the operation again to get a new one")
        return pending
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_safety.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/arcane_mcp/safety.py tests/test_safety.py
git commit -m "Add destructive classification and confirmation token store" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 5: Safety middleware and confirm_operation tool

**Files:**
- Modify: `src/arcane_mcp/safety.py` (append)
- Test: `tests/test_safety.py` (append)

**Interfaces:**
- Consumes: `ConfirmationStore`, `ConfirmationError` from Task 4; FastMCP `Middleware`, `ToolResult`, `FastMCP.call_tool`.
- Produces:
  ```python
  class ConfirmationMiddleware(Middleware):
      def __init__(self, store: ConfirmationStore, destructive: frozenset[str]) -> None
  CONFIRM_TOOL_NAME = "confirm_operation"
  def register_confirm_tool(mcp: FastMCP, store: ConfirmationStore) -> None
  ```

- [ ] **Step 1: Write failing tests (append to tests/test_safety.py)**

```python
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from arcane_mcp.safety import CONFIRM_TOOL_NAME, ConfirmationMiddleware, register_confirm_tool


def build_gated_server(store: ConfirmationStore) -> tuple[FastMCP, list]:
    calls: list = []
    mcp = FastMCP("gated", middleware=[ConfirmationMiddleware(store, frozenset({"delete_thing"}))])

    @mcp.tool
    def delete_thing(name: str) -> dict:
        calls.append(("delete", name))
        return {"deleted": name}

    @mcp.tool
    def list_things() -> dict:
        calls.append(("list",))
        return {"things": []}

    register_confirm_tool(mcp, store)
    return mcp, calls


async def test_non_destructive_passes_through():
    store, _ = make_store()
    mcp, calls = build_gated_server(store)
    async with Client(mcp) as c:
        r = await c.call_tool("list_things", {})
    assert r.structured_content == {"things": []}
    assert calls == [("list",)]


async def test_destructive_returns_confirmation_and_does_not_run():
    store, _ = make_store()
    mcp, calls = build_gated_server(store)
    async with Client(mcp) as c:
        r = await c.call_tool("delete_thing", {"name": "web"})
    body = r.structured_content
    assert body["requires_confirmation"] is True
    assert body["token"] == "tok1"
    assert body["operation"] == "delete_thing"
    assert body["arguments"] == {"name": "web"}
    assert body["expires_in"] == 120
    assert CONFIRM_TOOL_NAME in body["message"]
    assert calls == []


async def test_confirm_runs_original_and_is_single_use():
    store, _ = make_store()
    mcp, calls = build_gated_server(store)
    async with Client(mcp) as c:
        first = await c.call_tool("delete_thing", {"name": "web"})
        token = first.structured_content["token"]
        second = await c.call_tool(CONFIRM_TOOL_NAME, {"token": token})
        assert second.structured_content == {"deleted": "web"}
        assert calls == [("delete", "web")]
        with pytest.raises(ToolError, match="unknown|already"):
            await c.call_tool(CONFIRM_TOOL_NAME, {"token": token})


async def test_confirm_unknown_token_is_tool_error():
    store, _ = make_store()
    mcp, _ = build_gated_server(store)
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="unknown"):
            await c.call_tool(CONFIRM_TOOL_NAME, {"token": "bogus"})


async def test_confirm_expired_token_is_tool_error():
    store, clock = make_store(ttl=10)
    mcp, _ = build_gated_server(store)
    async with Client(mcp) as c:
        first = await c.call_tool("delete_thing", {"name": "web"})
        clock.t += 11
        with pytest.raises(ToolError, match="expired"):
            await c.call_tool(CONFIRM_TOOL_NAME, {"token": first.structured_content["token"]})


async def test_confirm_tool_is_listed():
    store, _ = make_store()
    mcp, _ = build_gated_server(store)
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert CONFIRM_TOOL_NAME in names
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_safety.py -q`
Expected: FAIL with `ImportError: cannot import name 'CONFIRM_TOOL_NAME'`.

- [ ] **Step 3: Append middleware and tool to safety.py**

Add imports at top of `safety.py`:
```python
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools import ToolResult
```

Append:
```python
CONFIRM_TOOL_NAME = "confirm_operation"


class ConfirmationMiddleware(Middleware):
    """Intercepts destructive tool calls and returns a confirmation token instead of executing."""

    def __init__(self, store: ConfirmationStore, destructive: frozenset[str]) -> None:
        self._store = store
        self._destructive = destructive

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext) -> ToolResult:
        name = context.message.name
        if name not in self._destructive:
            return await call_next(context)
        arguments = dict(context.message.arguments or {})
        token, pending = self._store.issue(name, arguments)
        logger.info("Destructive tool %s requested; issued confirmation token", name)
        payload = {
            "requires_confirmation": True,
            "token": token,
            "expires_in": int(self._store.ttl),
            "operation": name,
            "arguments": arguments,
            "message": (
                f"'{name}' is destructive and was NOT executed. Review the arguments, then call "
                f"{CONFIRM_TOOL_NAME}(token=\"{token}\") within {int(self._store.ttl)} seconds to proceed."
            ),
        }
        return ToolResult(structured_content=payload)


def register_confirm_tool(mcp: FastMCP, store: ConfirmationStore) -> None:
    @mcp.tool(
        name=CONFIRM_TOOL_NAME,
        description=(
            "Execute a destructive operation that previously returned requires_confirmation=true. "
            "Pass the token from that response. Tokens are single-use and expire after 120 seconds."
        ),
    )
    async def confirm_operation(token: str) -> ToolResult:
        try:
            pending = store.redeem(token)
        except ConfirmationError as exc:
            raise ToolError(str(exc)) from exc
        logger.info("Confirmed destructive tool %s", pending.tool)
        return await mcp.call_tool(pending.tool, pending.arguments, run_middleware=False)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_safety.py -q`
Expected: all PASS. If `context.message.arguments` raises `AttributeError`, print `type(context.message)` and adapt; it should be `mcp.types.CallToolRequestParams` with `.name` and `.arguments`.

- [ ] **Step 5: Commit**

```bash
git add src/arcane_mcp/safety.py tests/test_safety.py
git commit -m "Add confirmation middleware and confirm_operation tool" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 6: Server assembly

**Files:**
- Create: `src/arcane_mcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  ```python
  def make_arcane_client(settings: Settings) -> httpx2.AsyncClient     # base_url=f"{base}/api", X-API-Key header, 60s timeout
  def build_server(settings: Settings, spec: dict, client: httpx2.AsyncClient, *, auth: bool = True) -> FastMCP
  async def build_server_from_live_spec(settings: Settings) -> FastMCP
  def main() -> None
  ```

- [ ] **Step 1: Write failing tests**

`tests/test_server.py`:
```python
import json

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from arcane_mcp.config import load_settings
from arcane_mcp.safety import CONFIRM_TOOL_NAME
from arcane_mcp.server import build_server, make_arcane_client

ENV = {"ARCANE_BASE_URL": "http://arcane.test", "ARCANE_API_KEY": "test-key", "MCP_AUTH_TOKEN": "bearer-secret"}


def arcane_handler(seen: list):
    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append((req.method, req.url.path, req.headers.get("x-api-key")))
        if req.method == "DELETE":
            return httpx2.Response(200, json={"success": True, "message": "deleted"})
        if req.url.path.endswith("/containers"):
            return httpx2.Response(200, json={"success": True, "data": [{"id": "abc", "names": ["/web"]}]})
        if req.url.path.endswith("/version"):
            return httpx2.Response(200, json={"version": "2.12.0"})
        return httpx2.Response(500, json={"success": False, "error": "boom"})

    return handler


@pytest.fixture
def server(spec):
    seen: list = []
    settings = load_settings(ENV)
    client = httpx2.AsyncClient(base_url="http://arcane.test/api", headers={"X-API-Key": "test-key"},
                                transport=httpx2.MockTransport(arcane_handler(seen)))
    return build_server(settings, spec, client), seen


async def test_tool_inventory(server):
    mcp, _ = server
    async with Client(mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert len(tools) == 138, sorted(tools)  # 137 generated + confirm_operation
    for name in ["list_containers", "start_container", "delete_container", "list_images", "prune_images",
                 "list_environments", "get_dashboard_snapshot", "get_version_information", CONFIRM_TOOL_NAME]:
        assert name in tools, name
    assert not any(n.startswith(("list_users", "create_user", "login")) for n in tools)
    assert "download_container_logs" not in tools
    assert all("-" not in n for n in tools)
    assert "id" in tools["list_containers"].inputSchema["properties"]


async def test_read_tool_proxies_to_arcane(server):
    mcp, seen = server
    async with Client(mcp) as c:
        r = await c.call_tool("list_containers", {"id": "0"})
    assert r.structured_content["data"][0]["id"] == "abc"
    assert seen == [("GET", "/api/environments/0/containers", "test-key")]


async def test_destructive_tool_requires_confirmation(server):
    mcp, seen = server
    async with Client(mcp) as c:
        first = await c.call_tool("delete_container", {"id": "0", "containerId": "abc"})
        assert first.structured_content["requires_confirmation"] is True
        assert seen == []
        second = await c.call_tool(CONFIRM_TOOL_NAME, {"token": first.structured_content["token"]})
    assert second.structured_content == {"success": True, "message": "deleted"}
    assert seen == [("DELETE", "/api/environments/0/containers/abc", "test-key")]


async def test_confirmation_disabled_runs_immediately(spec):
    seen: list = []
    settings = load_settings({**ENV, "ARCANE_MCP_CONFIRM_DESTRUCTIVE": "false"})
    client = httpx2.AsyncClient(base_url="http://arcane.test/api", transport=httpx2.MockTransport(arcane_handler(seen)))
    mcp = build_server(settings, spec, client)
    async with Client(mcp) as c:
        r = await c.call_tool("delete_container", {"id": "0", "containerId": "abc"})
    assert r.structured_content == {"success": True, "message": "deleted"}
    assert len(seen) == 1


async def test_confirmation_disabled_omits_confirm_tool(spec):
    settings = load_settings({**ENV, "ARCANE_MCP_CONFIRM_DESTRUCTIVE": "false"})
    client = httpx2.AsyncClient(base_url="http://arcane.test/api", transport=httpx2.MockTransport(arcane_handler([])))
    async with Client(build_server(settings, spec, client)) as c:
        names = {t.name for t in await c.list_tools()}
    assert CONFIRM_TOOL_NAME not in names
    assert len(names) == 137


async def test_read_only_has_only_get_tools(spec):
    settings = load_settings({**ENV, "ARCANE_MCP_READ_ONLY": "true"})
    client = httpx2.AsyncClient(base_url="http://arcane.test/api", transport=httpx2.MockTransport(arcane_handler([])))
    async with Client(build_server(settings, spec, client)) as c:
        names = {t.name for t in await c.list_tools()}
    assert "list_containers" in names
    assert "start_container" not in names
    assert "delete_container" not in names


async def test_arcane_error_surfaces_as_tool_error(server):
    mcp, _ = server
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="500"):
            await c.call_tool("get_dashboard_snapshot", {"id": "0"})


def test_make_arcane_client_sets_base_and_header():
    client = make_arcane_client(load_settings(ENV))
    assert str(client.base_url) == "http://arcane.test/api"
    assert client.headers["X-API-Key"] == "test-key"


async def test_health_route_and_bearer_auth(server):
    mcp, _ = server
    app = mcp.http_app(path="/mcp")
    transport = httpx2.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=transport, base_url="http://mcp.test") as http:
            health = await http.get("/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"
            assert health.json()["tools"] == 138

            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
            headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
            unauth = await http.post("/mcp", json=init, headers=headers)
            assert unauth.status_code == 401
            authed = await http.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer bearer-secret"})
            assert authed.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'arcane_mcp.server'`.

- [ ] **Step 3: Implement server.py**

```python
"""Assemble the FastMCP server and run it over Streamable HTTP."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import httpx2
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from fastmcp.server.providers.openapi import OpenAPIProvider
from starlette.requests import Request
from starlette.responses import JSONResponse

from arcane_mcp import __version__
from arcane_mcp.config import ConfigError, Settings, load_settings
from arcane_mcp.safety import ConfirmationMiddleware, ConfirmationStore, destructive_tool_names, register_confirm_tool
from arcane_mcp.spec import SpecFetchError, build_component_fn, build_route_map_fn, fetch_spec, normalise_spec

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Tools for managing Docker via Arcane. Most tools take an environment `id`; the local Docker host is "
    "environment \"0\" and remote agents have UUIDs (see list_environments). Destructive tools return "
    "requires_confirmation=true with a token instead of executing; call confirm_operation(token) to proceed."
)


def make_arcane_client(settings: Settings) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=f"{settings.arcane_base_url}/api",
        headers={"X-API-Key": settings.arcane_api_key, "Accept": "application/json"},
        timeout=60.0,
    )


def build_server(settings: Settings, spec: dict[str, Any], client: httpx2.AsyncClient, *, auth: bool = True) -> FastMCP:
    normalised = normalise_spec(spec, settings.arcane_base_url)
    provider = OpenAPIProvider(
        normalised,
        client,
        route_map_fn=build_route_map_fn(settings),
        mcp_component_fn=build_component_fn(),
        validate_output=False,
    )

    middleware = []
    store: ConfirmationStore | None = None
    if settings.confirm_destructive:
        store = ConfirmationStore()
        destructive = destructive_tool_names(normalised, settings.destructive_patterns)
        middleware.append(ConfirmationMiddleware(store, destructive))
        logger.info("Confirmation gate active for %d destructive tools", len(destructive))

    verifier = StaticTokenVerifier(tokens={settings.mcp_auth_token: {"client_id": "arcane-mcp-client", "scopes": []}}) if auth else None

    mcp = FastMCP(
        "arcane",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=verifier,
        middleware=middleware,
        providers=[provider],
    )
    if store is not None:
        register_confirm_tool(mcp, store)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        tools = await mcp.list_tools(run_middleware=False)
        return JSONResponse({"status": "ok", "version": __version__, "arcane": settings.arcane_base_url, "tools": len(tools)})

    return mcp


async def build_server_from_live_spec(settings: Settings) -> FastMCP:
    client = make_arcane_client(settings)
    spec = await fetch_spec(client, timeout=settings.spec_timeout)
    logger.info("Arcane %s at %s", spec.get("info", {}).get("version", "?"), settings.arcane_base_url)
    return build_server(settings, spec, client)


def main() -> None:
    try:
        settings = load_settings(os.environ)
    except ConfigError as exc:
        print(f"arcane-mcp: {exc}", file=sys.stderr)
        sys.exit(2)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        mcp = asyncio.run(build_server_from_live_spec(settings))
    except SpecFetchError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    tool_count = len(asyncio.run(mcp.list_tools(run_middleware=False)))
    logger.info("Serving %d tools on http://%s:%d/mcp", tool_count, settings.mcp_host, settings.mcp_port)
    mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port, path="/mcp", show_banner=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_server.py -q`
Expected: all PASS. Known things to check if not:
- Tool count differs from 137/138: print `sorted(tools)` and compare against the allowlist; adjust the assertion only if the difference is explained by the fixture, never by loosening the filter.
- `ASGITransport` missing from `httpx2`: fall back to `from starlette.testclient import TestClient` (sync) for the health/auth test.
- 401 assertion fails with 403 or 400: print `unauth.text`; FastMCP may answer 401 with a `WWW-Authenticate` header. Accept `401` only.
- If `mcp.list_tools` inside the health route raises because there is no request context, compute the count once in `build_server` after registration (`tool_count = len(asyncio.run(mcp.list_tools(run_middleware=False)))` is not usable inside a running loop, so do it in `main()` and pass it into `build_server` via a `tool_count_holder: list[int]` closure the route reads). Prefer fixing the call first.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/arcane_mcp/server.py tests/test_server.py
git commit -m "Assemble FastMCP server with auth, safety gate and health route" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 7: Docker packaging

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `compose.yaml`, `.env.example`

**Interfaces:**
- Consumes: `arcane-mcp` console script from `pyproject.toml`; `/health` route from Task 6.

- [ ] **Step 1: Write .dockerignore**

```
.venv
.git
.pytest_cache
__pycache__
*.pyc
tests
docs
.env
```

- [ ] **Step 2: Write Dockerfile**

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"MCP_PORT\",\"8000\")}/health', timeout=4).status==200 else 1)"
CMD ["arcane-mcp"]
```

`start-period=90s` covers the spec fetch retry window (default 60s).

- [ ] **Step 3: Write compose.yaml**

```yaml
services:
  arcane-mcp:
    build: .
    image: arcane-mcp:local
    container_name: arcane-mcp
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    # To reach Arcane over an internal Docker network instead of a public URL,
    # join Arcane's network and set ARCANE_BASE_URL=http://arcane:3552 in .env:
    # networks:
    #   - arcane_default
#
# networks:
#   arcane_default:
#     external: true
```

- [ ] **Step 4: Write .env.example**

```env
# Required
ARCANE_BASE_URL=https://arcane.example.com
ARCANE_API_KEY=arc_xxxxxxxxxxxxxxxxx
# Clients must send: Authorization: Bearer <MCP_AUTH_TOKEN>. Generate with: openssl rand -hex 32
MCP_AUTH_TOKEN=change-me

# Optional
#MCP_HOST=0.0.0.0
#MCP_PORT=8000
#ARCANE_MCP_TAGS=Containers,Images,Volumes,Networks,Projects,Project Workspace,Environments,System,Dashboard,Activities,Events,Ports,Updater,Image Updates,Vulnerabilities,Health,Version
#ARCANE_MCP_EXCLUDE_TAGS=
#ARCANE_MCP_READ_ONLY=false
#ARCANE_MCP_CONFIRM_DESTRUCTIVE=true
#ARCANE_MCP_DESTRUCTIVE_PATTERNS=
#ARCANE_MCP_SPEC_TIMEOUT=60
#ARCANE_MCP_LOG_LEVEL=INFO
```

- [ ] **Step 5: Ensure README.md exists (Dockerfile copies it) and lock file is current**

If `README.md` does not exist yet, create it with a single line `# arcane-mcp` (Task 8 fills it). Then:

```bash
uv lock
git add uv.lock
```

- [ ] **Step 6: Build the image and verify it fails cleanly without config**

```bash
docker build -t arcane-mcp:local .
docker run --rm arcane-mcp:local; echo "exit=$?"
```
Expected: image builds; container prints `arcane-mcp: Required environment variable ARCANE_BASE_URL is not set` and `exit=2`.

- [ ] **Step 7: Verify it retries and exits when Arcane is unreachable**

```bash
docker run --rm -e ARCANE_BASE_URL=http://127.0.0.1:9 -e ARCANE_API_KEY=x -e MCP_AUTH_TOKEN=y -e ARCANE_MCP_SPEC_TIMEOUT=3 arcane-mcp:local; echo "exit=$?"
```
Expected: two or three `Spec fetch attempt N failed` warnings, then `Could not fetch OpenAPI spec`, `exit=1`.

- [ ] **Step 8: Verify a live boot against the real instance**

Ask the user for a real Arcane API key if none is available in the environment; do not proceed with a fake one. Then:

```bash
docker run --rm -d --name arcane-mcp-test -p 8000:8000 -e ARCANE_BASE_URL=https://arcane.example.com -e ARCANE_API_KEY="$ARCANE_API_KEY" -e MCP_AUTH_TOKEN=devtoken arcane-mcp:local
sleep 8
curl -s http://localhost:8000/health
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
docker logs arcane-mcp-test | tail -5
docker rm -f arcane-mcp-test
```
Expected: health JSON with `"tools": 138`; the unauthenticated POST returns `401`; logs show `Serving 138 tools`.

- [ ] **Step 9: Commit**

```bash
git add Dockerfile .dockerignore compose.yaml .env.example uv.lock README.md
git commit -m "Add Docker image and compose file" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

### Task 8: README

**Files:**
- Create/overwrite: `README.md`

- [ ] **Step 1: Write README.md**

```markdown
# arcane-mcp

A Docker-hostable [MCP](https://modelcontextprotocol.io) server for [Arcane](https://getarcane.app).
It reads your Arcane instance's OpenAPI spec at startup and exposes the Docker-management API
(containers, images, volumes, networks, Compose projects, environments, system) as MCP tools over
Streamable HTTP. Because tools are generated from the live spec, they track your Arcane version automatically.

## Quick start

```bash
cp .env.example .env          # set ARCANE_BASE_URL, ARCANE_API_KEY, MCP_AUTH_TOKEN
docker compose up -d --build
curl http://localhost:8000/health
```

Create the Arcane API key in Arcane under your profile, or via `POST /api/auth/me/api-keys`.
Generate `MCP_AUTH_TOKEN` with `openssl rand -hex 32`.

## Connecting a client

Claude Code:

```bash
claude mcp add --transport http arcane http://<host>:8000/mcp --header "Authorization: Bearer <MCP_AUTH_TOKEN>"
```

Any other MCP client: Streamable HTTP endpoint `http://<host>:8000/mcp`, header `Authorization: Bearer <MCP_AUTH_TOKEN>`.

## How it works

1. On boot the server fetches `ARCANE_BASE_URL/api/openapi.json`, retrying for `ARCANE_MCP_SPEC_TIMEOUT` seconds and exiting non-zero if Arcane never answers (Docker restarts it).
2. Operations whose tag is in `ARCANE_MCP_TAGS` become tools named after their `operationId` in snake_case (`list_containers`, `start_container`, ...). Streaming and binary-download endpoints are always excluded.
3. Every tool call is proxied to Arcane with your `X-API-Key`.

Most tools take an environment `id`. The local Docker host is `"0"`; remote agents have UUIDs (`list_environments`).

## Safety

Destructive operations (any `DELETE`, plus paths ending in `prune`, `destroy`, `kill`, `down`, `restore`, `restore-files`)
do not execute on the first call. They return:

```json
{"requires_confirmation": true, "token": "3f9a...", "expires_in": 120, "operation": "delete_container", "arguments": {...}}
```

The client must then call `confirm_operation(token)`. Tokens are single-use and expire after 120 seconds.
Disable with `ARCANE_MCP_CONFIRM_DESTRUCTIVE=false`; add patterns with `ARCANE_MCP_DESTRUCTIVE_PATTERNS`.

## Configuration

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ARCANE_BASE_URL` | yes | | Arcane URL without `/api` |
| `ARCANE_API_KEY` | yes | | Arcane API key |
| `MCP_AUTH_TOKEN` | yes | | Bearer token clients must present |
| `MCP_HOST` | no | `0.0.0.0` | Bind address |
| `MCP_PORT` | no | `8000` | Bind port |
| `ARCANE_MCP_TAGS` | no | see `.env.example` | OpenAPI tags to expose |
| `ARCANE_MCP_EXCLUDE_TAGS` | no | | Tags to hide even if allowed |
| `ARCANE_MCP_READ_ONLY` | no | `false` | Expose only GET operations |
| `ARCANE_MCP_CONFIRM_DESTRUCTIVE` | no | `true` | Two-step confirmation gate |
| `ARCANE_MCP_DESTRUCTIVE_PATTERNS` | no | | Extra regexes matched against paths |
| `ARCANE_MCP_SPEC_TIMEOUT` | no | `60` | Seconds to retry the spec fetch |
| `ARCANE_MCP_LOG_LEVEL` | no | `INFO` | Log level |

Available tags in Arcane 2.12: Containers, Images, Volumes, Networks, Projects, Project Workspace, Environments, System,
Dashboard, Activities, Events, Ports, Updater, Image Updates, Vulnerabilities, Health, Version, Swarm, GitOps Syncs,
Templates, Volume Backup, Volume Workspace, Builds, Container Registries, Notifications, Webhooks, Customize, Users, Roles,
API Keys, Auth, OIDC, Passkeys, MFA, Settings, System Backups, S3 Destinations, Federated Credentials, Variables,
Diagnostics, JobSchedules, Jobs, Uploads, Mobile Push, Application Images, Stream.

## Security notes

Intended for a LAN or an internal Docker network. If you expose it publicly, put TLS in front of it,
keep the confirmation gate on, and consider `ARCANE_MCP_READ_ONLY=true` or a narrower tag list.

## Development

```bash
uv sync --extra dev
uv run pytest -q
```

Tests run against a committed snapshot of the Arcane 2.12.0 spec in `tests/fixtures/openapi.json` with a mocked Arcane backend.

## Credits

Inspired by [MikeCase/arcane-mcp](https://github.com/MikeCase/arcane-mcp), a hand-curated stdio server. This project instead
generates tools from the OpenAPI spec and adds HTTP transport, auth and Docker packaging. MIT licensed.
```

- [ ] **Step 2: Add LICENSE**

Create `LICENSE` with the standard MIT text, copyright `2026 Luke Madigan`.

- [ ] **Step 3: Commit**

```bash
git add README.md LICENSE
git commit -m "Add README and MIT license" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_011QJP2t7LUocthgTMzm2ysi"
```

---

## Self-review notes

- Spec §1 steps 1-4: Tasks 3 and 6. §2 filtering and naming: Task 3, verified in Task 6 inventory test. §3 gate: Tasks 4-5. §4 config: Task 2. §5 Docker: Task 7. §6 errors: Task 3 (fetch), Task 5 (tokens), Task 6 (Arcane 5xx test). §7 tests: each task; manual live check Task 7 step 8.
- Names used consistently: `load_settings`, `Settings`, `fetch_spec`, `normalise_spec`, `tool_name`, `build_route_map_fn`, `build_component_fn`, `is_destructive`, `destructive_tool_names`, `ConfirmationStore.issue/redeem`, `ConfirmationMiddleware`, `register_confirm_tool`, `CONFIRM_TOOL_NAME`, `build_server`, `make_arcane_client`.
- The spec's "~110 tools" estimate is corrected to 137 in Task 1.
