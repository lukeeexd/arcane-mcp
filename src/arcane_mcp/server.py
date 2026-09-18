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
    'environment "0" and remote agents have UUIDs (see list_environments). Destructive tools return '
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

    verifier = (
        StaticTokenVerifier(tokens={settings.mcp_auth_token: {"client_id": "arcane-mcp-client", "scopes": []}})
        if auth
        else None
    )

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
        return JSONResponse(
            {"status": "ok", "version": __version__, "arcane": settings.arcane_base_url, "tools": len(tools)}
        )

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
