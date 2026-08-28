#!/usr/bin/env python3
"""End-to-End JSON-RPC stdio smoke test for Smartsheet RM MCP Server."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SMOKE_CLIENT_SCRIPT = """
import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def run_smoke():
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "smartsheet_rm_mcp.server"],
        env={
            **os.environ,
            "SMARTSHEET_RM_API_TOKEN": "mock-smoke-test-token",
            "SMARTSHEET_RM_BASE_URL": "https://api.rm.smartsheet.com/api/v1",
            "PYTHONPATH": "src",
        },
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Initialize
            init_res = await session.initialize()
            print("1. [INIT] Protocol Initialized:", init_res.server_info.name, init_res.server_info.version)

            # 2. List Tools
            tools_res = await session.list_tools()
            print(f"2. [TOOLS] Discovered {len(tools_res.tools)} tools.")
            assert len(tools_res.tools) >= 80

            # 3. List Resources
            resources_res = await session.list_resources()
            print(f"3. [RESOURCES] Discovered {len(resources_res.resources)} resources.")
            assert any(r.uri == "rm://capabilities" for r in resources_res.resources)

            # 4. List Prompts
            prompts_res = await session.list_prompts()
            print(f"4. [PROMPTS] Discovered {len(prompts_res.prompts)} prompts.")
            assert any(p.name == "timesheet_reconciliation" for p in prompts_res.prompts)

            # 5. Read Resource
            resource_content = await session.read_resource("rm://capabilities")
            print("5. [RESOURCE READ] Successfully read 'rm://capabilities'")
            assert "Time Tracking" in str(resource_content)

            # 6. Execute Tool Call (Safety validation test)
            res = await session.call_tool("rm_delete_project", {"project_id": 999, "confirm": False})
            content_text = res.content[0].text
            print("6. [TOOL CALL] Destructive Gate Check Response:", content_text)
            assert "requires explicit confirmation" in content_text

            # 7. Execute Tool Call (Validation test)
            res_up = await session.call_tool("rm_update_project", {"project_id": 999})
            up_text = res_up.content[0].text
            print("7. [TOOL CALL] Parameter Validation Check:", up_text)
            assert "invalid_request" in up_text

            # 8. Execute Prompt
            prompt_res = await session.get_prompt(
                "timesheet_reconciliation",
                {"user_id": "123", "week_start_date": "2026-08-10"},
            )
            prompt_text = prompt_res.messages[0].content.text
            print("8. [PROMPT] Timesheet Prompt Generated:", prompt_text[:60], "...")
            assert "user ID 123" in prompt_text

            print("\\n>>> ALL STDIO PROTOCOL SMOKE TESTS PASSED CLEANLY <<<")

asyncio.run(run_smoke())
"""


def main() -> int:
    print("Launching MCP Server stdio smoke test...")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", SMOKE_CLIENT_SCRIPT],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"ERROR: stdio smoke test timed out after {exc.timeout} seconds.", file=sys.stderr)
        return 1

    print(proc.stdout)
    if proc.returncode != 0:
        print("STDERR:", proc.stderr, file=sys.stderr)
        return proc.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
