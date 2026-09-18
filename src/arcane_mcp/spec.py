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
