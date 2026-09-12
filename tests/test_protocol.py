"""Protocol integration tests for stdio and stateless Streamable HTTP transports."""

from pathlib import Path

import httpx
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.client import SmartsheetRMClient


@pytest.mark.asyncio
async def test_stdio_jsonrpc_handshake_and_dispatch() -> None:
    """Verify standard stdio JSON-RPC handshake and tool discovery over subprocess pipes."""
    repo_dir = Path(__file__).resolve().parents[1]
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "--directory", str(repo_dir), "smartsheet-rm-mcp"],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result.server_info.name == "mcp-server-smartsheet-rm"
            assert init_result.protocol_version in ("2026-07-28", "2025-11-25", "2024-11-05")

            # Tool listing
            tools_result = await session.list_tools()
            tool_names = {t.name for t in tools_result.tools}
            assert "rm_list_time_entries" in tool_names
            assert "rm_list_projects" in tool_names

            # Resource listing
            resources_result = await session.list_resources()
            resource_uris = {str(r.uri) for r in resources_result.resources}
            assert "rm://capabilities" in resource_uris

            # Prompt listing
            prompts_result = await session.list_prompts()
            prompt_names = {p.name for p in prompts_result.prompts}
            assert "timesheet_reconciliation" in prompt_names


@pytest.mark.asyncio
async def test_stateless_streamable_http_standalone_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify stateless Streamable HTTP requests using modern 2026-07-28 wire framing."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        """Simulate Smartsheet RM API response for role listing."""
        return httpx.Response(
            200,
            json={"data": [{"id": 101, "name": "Software Engineer"}], "total_count": 1},
        )

    mock_transport = httpx.MockTransport(mock_handler)
    mock_http = httpx.AsyncClient(transport=mock_transport, base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=mock_http))

    app = server.mcp.streamable_http_app(stateless_http=True, json_response=True)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as client:
            meta = {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }

            # 1. Modern discovery entrypoint: server/discover
            discover_payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "server/discover",
                "params": {"_meta": meta},
            }
            res = await client.post(
                "/mcp",
                json=discover_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "server/discover",
                },
            )
            assert res.status_code == 200
            assert "Mcp-Session-Id" not in res.headers
            data = res.json()
            assert "result" in data
            assert "supportedVersions" in data["result"]
            assert "2026-07-28" in data["result"]["supportedVersions"]

            # 2. Standalone tools/list with routing headers and _meta
            list_payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {"_meta": meta},
            }
            res_list = await client.post(
                "/mcp",
                json=list_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                },
            )
            assert res_list.status_code == 200
            assert "Mcp-Session-Id" not in res_list.headers
            list_data = res_list.json()
            assert "tools" in list_data["result"]
            assert len(list_data["result"]["tools"]) > 0

            # 3. Standalone tools/call with Mcp-Name and _meta
            tool_payload = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "rm_list_roles",
                    "arguments": {},
                    "_meta": meta,
                },
            }
            res_tool = await client.post(
                "/mcp",
                json=tool_payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": "rm_list_roles",
                },
            )
            assert res_tool.status_code == 200
            assert "Mcp-Session-Id" not in res_tool.headers
            tool_data = res_tool.json()
            assert "result" in tool_data
            assert "content" in tool_data["result"]
