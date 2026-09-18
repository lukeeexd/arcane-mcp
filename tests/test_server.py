import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from arcane_mcp.config import load_settings
from arcane_mcp.safety import CONFIRM_TOOL_NAME
from arcane_mcp.server import build_server, make_arcane_client

ENV = {"ARCANE_BASE_URL": "http://arcane.test", "ARCANE_API_KEY": "test-key", "MCP_AUTH_TOKEN": "bearer-secret"}
GENERATED_TOOL_COUNT = 137


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


def mock_client(seen: list) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url="http://arcane.test/api",
        headers={"X-API-Key": "test-key"},
        transport=httpx2.MockTransport(arcane_handler(seen)),
    )


@pytest.fixture
def server(spec):
    seen: list = []
    return build_server(load_settings(ENV), spec, mock_client(seen)), seen


async def test_tool_inventory(server):
    mcp, _ = server
    async with Client(mcp) as c:
        tools = {t.name: t for t in await c.list_tools()}
    assert len(tools) == GENERATED_TOOL_COUNT + 1, sorted(tools)
    for name in ["list_containers", "start_container", "delete_container", "list_images", "prune_images",
                 "list_environments", "get_dashboard", "get_version", CONFIRM_TOOL_NAME]:
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
    mcp = build_server(settings, spec, mock_client(seen))
    async with Client(mcp) as c:
        r = await c.call_tool("delete_container", {"id": "0", "containerId": "abc"})
    assert r.structured_content == {"success": True, "message": "deleted"}
    assert len(seen) == 1


async def test_confirmation_disabled_omits_confirm_tool(spec):
    settings = load_settings({**ENV, "ARCANE_MCP_CONFIRM_DESTRUCTIVE": "false"})
    async with Client(build_server(settings, spec, mock_client([]))) as c:
        names = {t.name for t in await c.list_tools()}
    assert CONFIRM_TOOL_NAME not in names
    assert len(names) == GENERATED_TOOL_COUNT


async def test_read_only_has_only_get_tools(spec):
    settings = load_settings({**ENV, "ARCANE_MCP_READ_ONLY": "true"})
    async with Client(build_server(settings, spec, mock_client([]))) as c:
        names = {t.name for t in await c.list_tools()}
    assert "list_containers" in names
    assert "start_container" not in names
    assert "delete_container" not in names


async def test_arcane_error_surfaces_as_tool_error(server):
    mcp, _ = server
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="500"):
            await c.call_tool("get_dashboard", {"id": "0"})


def test_make_arcane_client_sets_base_and_header():
    client = make_arcane_client(load_settings(ENV))
    assert str(client.base_url).rstrip("/") == "http://arcane.test/api"
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
            assert health.json()["tools"] == GENERATED_TOOL_COUNT + 1

            init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
            headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
            unauth = await http.post("/mcp", json=init, headers=headers)
            assert unauth.status_code == 401
            authed = await http.post("/mcp", json=init, headers={**headers, "Authorization": "Bearer bearer-secret"})
            assert authed.status_code == 200
