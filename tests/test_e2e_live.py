"""End-to-end live testing across all dynamically discovered Smartsheet RM MCP tools."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import pytest
from mcp.types import CallToolResult

import smartsheet_rm_mcp.server as server
from smartsheet_rm_mcp.client import SmartsheetRMClient
from smartsheet_rm_mcp.server import _redact_secrets, mcp

SAFE_TOOL_FIXTURES: dict[str, dict[str, Any]] = {
    # Destructive tools (tested safely with confirm=False)
    "rm_delete_time_entry": {"entry_id": 999999, "confirm": False},
    "rm_delete_project": {"project_id": 999999, "confirm": False},
    "rm_delete_project_phase": {"project_id": 999999, "phase_id": 999999, "confirm": False},
    "rm_delete_assignment": {"assignment_id": 999999, "confirm": False},
    "rm_delete_user": {"user_id": 999999, "confirm": False},
    "rm_delete_role": {"role_id": 999999, "confirm": False},
    "rm_delete_discipline": {"discipline_id": 999999, "confirm": False},
    "rm_delete_client": {"client_id": 999999, "confirm": False},
    "rm_delete_client_contact": {"client_id": 999999, "contact_id": 999999, "confirm": False},
    "rm_delete_leave_type": {"leave_type_id": 999999, "confirm": False},
    "rm_delete_holiday": {"holiday_id": 999999, "confirm": False},
    "rm_delete_expense": {"expense_id": 999999, "confirm": False},
    "rm_delete_expense_category": {"category_id": 999999, "confirm": False},
    "rm_delete_tag": {"tag_id": 999999, "confirm": False},
    "rm_delete_custom_field": {"custom_field_id": 999999, "confirm": False},
    "rm_delete_approval": {"approval_id": 999999, "confirm": False},
    "rm_delete_placeholder_resource": {"placeholder_id": 999999, "confirm": False},
    "rm_delete_assignment_subtask": {
        "project_id": 999999,
        "assignment_id": 999999,
        "subtask_id": 999999,
        "confirm": False,
    },
    "rm_delete_webhook": {"webhook_id": 999999, "confirm": False},
    "rm_bulk_delete_time_entries": {"entry_ids": [999999], "confirm": False},
    "rm_bulk_delete_assignments": {"assignment_ids": [999999], "confirm": False},
    # Parameterized read tools with probe IDs
    "rm_get_time_entry": {"entry_id": 999999},
    "rm_list_user_suggestions": {"user_id": 999999},
    "rm_get_project": {"project_id": 999999},
    "rm_list_project_users": {"project_id": 999999},
    "rm_list_project_phases": {"project_id": 999999},
    "rm_get_project_phase": {"project_id": 999999, "phase_id": 999999},
    "rm_get_assignment": {"assignment_id": 999999},
    "rm_get_user": {"user_id": 999999},
    "rm_list_user_bill_rates": {"user_id": 999999},
    "rm_get_user_availability": {"user_id": 999999},
    "rm_get_user_utilization": {"user_id": 999999},
    "rm_get_client": {"client_id": 999999},
    "rm_list_client_contacts": {"client_id": 999999},
    "rm_get_leave_type": {"leave_type_id": 999999},
    "rm_get_holiday": {"holiday_id": 999999},
    "rm_get_expense": {"expense_id": 999999},
    "rm_get_custom_field": {"custom_field_id": 999999},
    "rm_get_user_statuses": {"user_id": 999999},
    "rm_list_assignment_subtasks": {"project_id": 999999, "assignment_id": 999999},
    "rm_get_report_rows": {"report_parameters": {"from": "2026-08-01", "to": "2026-08-07"}},
    "rm_get_report_totals": {"report_parameters": {"from": "2026-08-01", "to": "2026-08-07"}},
}

SAFE_ARGUMENTLESS_LIST_TOOLS: set[str] = {
    "rm_list_time_entries",
    "rm_list_projects",
    "rm_list_assignments",
    "rm_list_users",
    "rm_list_roles",
    "rm_list_disciplines",
    "rm_list_clients",
    "rm_list_leave_types",
    "rm_list_holidays",
    "rm_list_expenses",
    "rm_list_expense_categories",
    "rm_list_tags",
    "rm_list_custom_fields",
    "rm_list_custom_field_values",
    "rm_list_approvals",
    "rm_list_status_options",
    "rm_list_placeholder_resources",
    "rm_list_webhooks",
}

SAFE_PROBE_EXPECTED_NOT_FOUND_TOOLS: set[str] = {
    "rm_get_time_entry",
    "rm_list_user_suggestions",
    "rm_get_project",
    "rm_list_project_users",
    "rm_list_project_phases",
    "rm_get_project_phase",
    "rm_get_assignment",
    "rm_get_user",
    "rm_list_user_bill_rates",
    "rm_get_user_availability",
    "rm_get_user_utilization",
    "rm_get_client",
    "rm_list_client_contacts",
    "rm_get_leave_type",
    "rm_get_holiday",
    "rm_get_expense",
    "rm_get_custom_field",
    "rm_get_user_statuses",
    "rm_list_assignment_subtasks",
}

INTENTIONALLY_SKIPPED_MUTATING_TOOLS: set[str] = {
    "rm_create_time_entry",
    "rm_update_time_entry",
    "rm_update_time_approval_status",
    "rm_lock_timesheet",
    "rm_create_project",
    "rm_update_project",
    "rm_create_project_phase",
    "rm_update_project_phase",
    "rm_create_assignment",
    "rm_update_assignment",
    "rm_create_user",
    "rm_update_user",
    "rm_create_user_bill_rate",
    "rm_create_role",
    "rm_update_role",
    "rm_create_discipline",
    "rm_update_discipline",
    "rm_create_client",
    "rm_update_client",
    "rm_create_client_contact",
    "rm_create_leave_type",
    "rm_update_leave_type",
    "rm_create_holiday",
    "rm_update_holiday",
    "rm_create_expense",
    "rm_update_expense",
    "rm_create_expense_category",
    "rm_create_tag",
    "rm_create_custom_field",
    "rm_update_custom_field",
    "rm_set_custom_field_values",
    "rm_fill_weekly_timesheet",
    "rm_confirm_suggested_hours",
    "rm_reconcile_and_submit_week",
    "rm_clone_project_schedule",
    "rm_create_approval",
    "rm_set_user_status",
    "rm_create_placeholder_resource",
    "rm_create_assignment_subtask",
    "rm_create_webhook",
}


async def dispatch_tool_call(
    tool_name: str,
    is_destructive: bool,
    required_args: list[str] | None = None,
) -> tuple[str, bool, str | None]:
    """Execute a single tool call and return (status, is_error, error_message)."""
    try:
        if tool_name in SAFE_TOOL_FIXTURES:
            res = await mcp.call_tool(tool_name, SAFE_TOOL_FIXTURES[tool_name])
        elif tool_name in SAFE_ARGUMENTLESS_LIST_TOOLS:
            res = await mcp.call_tool(tool_name, {"per_page": 5})
        elif tool_name in INTENTIONALLY_SKIPPED_MUTATING_TOOLS:
            return ("SKIP", False, "Intentionally skipped mutating tool to protect live state")
        else:
            return ("FAIL", True, f"Unclassified tool '{tool_name}' is not in a safety allowlist")

        if isinstance(res, CallToolResult):
            content_str = res.content[0].text if res.content and hasattr(res.content[0], "text") else ""
            if "requires explicit confirmation" in content_str:
                return ("PASS", False, None)

            try:
                data = json.loads(content_str)
                if isinstance(data, dict) and "error" in data:
                    err_obj = data["error"]
                    if isinstance(err_obj, dict):
                        status_code = err_obj.get("status_code")
                        if tool_name in SAFE_PROBE_EXPECTED_NOT_FOUND_TOOLS and status_code == 404:
                            return ("PASS", False, None)
                    return ("FAIL", True, _redact_secrets(content_str))
            except Exception:
                pass

            is_err = res.is_error
            status = "FAIL" if is_err else "PASS"
            return (status, is_err, None if not is_err else _redact_secrets(content_str))

        return ("PASS", False, None)
    except Exception as exc:
        return ("FAIL", True, _redact_secrets(str(exc)))


@pytest.mark.asyncio
async def test_dispatch_tool_call_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify tool invocation logic and dispatch behavior using HTTP transport mocking."""
    request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        url_str = str(request.url)
        if "users/999999" in url_str:
            return httpx.Response(200, json={"id": 999999, "first_name": "Jane"})
        if "users" in url_str:
            return httpx.Response(200, json={"data": [{"id": 1, "first_name": "Jane"}]})
        if "projects" in url_str:
            return httpx.Response(200, json={"data": [{"id": 100, "name": "Apollo"}]})
        return httpx.Response(200, json={"data": []})

    mock_transport = httpx.MockTransport(mock_handler)
    mock_http = httpx.AsyncClient(transport=mock_transport, base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=mock_http))

    # 1. Successful non-destructive parameterized read tool
    status, is_err, err = await dispatch_tool_call("rm_get_user", is_destructive=False, required_args=["user_id"])
    assert status == "PASS"
    assert not is_err
    assert err is None

    # 2. Destructive tool intercepted by safety confirmation gate
    requests_before_delete = request_count
    status, is_err, err = await dispatch_tool_call(
        "rm_delete_project", is_destructive=True, required_args=["project_id"]
    )
    assert status == "PASS"
    assert not is_err
    assert request_count == requests_before_delete

    # 3. List tool from positive allowlist with per_page pagination argument
    status, is_err, err = await dispatch_tool_call("rm_list_projects", is_destructive=False)
    assert status == "PASS"
    assert not is_err

    # 4. Intentionally skipped mutating tool
    status, is_err, err = await dispatch_tool_call("rm_create_project", is_destructive=False, required_args=["name"])
    assert status == "SKIP"
    assert not is_err

    # 5. Unclassified unknown tool returns FAIL rather than executing unchecked
    status, is_err, err = await dispatch_tool_call(
        "rm_unknown_future_tool", is_destructive=False, required_args=["foo"]
    )
    assert status == "FAIL"
    assert is_err
    assert "is not in a safety allowlist" in str(err)

    # 6. Expected 404 on synthetic probe tool returns PASS
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found", "status_code": 404})

    nf_transport = httpx.MockTransport(not_found_handler)
    nf_http = httpx.AsyncClient(transport=nf_transport, base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=nf_http))

    status, is_err, err = await dispatch_tool_call("rm_get_user", is_destructive=False, required_args=["user_id"])
    assert status == "PASS"
    assert not is_err

    # 7. 404 on non-probe report endpoint returns FAIL (reports must return valid report documents)
    status, is_err, err = await dispatch_tool_call("rm_get_report_rows", is_destructive=False)
    assert status == "FAIL"
    assert is_err

    # 8. Unexpected 400 Bad Request error response fails
    def bad_request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "Bad Request", "status_code": 400})

    br_transport = httpx.MockTransport(bad_request_handler)
    br_http = httpx.AsyncClient(transport=br_transport, base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=br_http))

    status, is_err, err = await dispatch_tool_call("rm_get_user", is_destructive=False, required_args=["user_id"])
    assert status == "FAIL"
    assert is_err

    # 9. Error response with 500 status code fails
    def err_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "Internal Server Error", "status_code": 500})

    err_transport = httpx.MockTransport(err_handler)
    err_http = httpx.AsyncClient(transport=err_transport, base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=err_http))

    status, is_err, err = await dispatch_tool_call("rm_get_user", is_destructive=False, required_args=["user_id"])
    assert status == "FAIL"
    assert is_err

    # 10. Unexpected exception with credential redaction
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", "e2e-secret-token")

    def exc_handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("transport broken with SMARTSHEET_RM_API_TOKEN=e2e-secret-token")

    exc_transport = httpx.MockTransport(exc_handler)
    exc_http = httpx.AsyncClient(transport=exc_transport, base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="e2e-secret-token", http_client=exc_http))

    status, is_err, err = await dispatch_tool_call("rm_get_user", is_destructive=False, required_args=["user_id"])
    assert status == "FAIL"
    assert is_err
    assert err is not None
    assert "e2e-secret-token" not in err
    assert "***REDACTED***" in err


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_all_discovered_tools_live() -> None:
    """Dynamically discover and exercise registered tools against live endpoints."""
    if not os.environ.get("SMARTSHEET_RM_API_TOKEN"):
        pytest.skip("SMARTSHEET_RM_API_TOKEN not set; skipping live endpoint verification")

    tools = await mcp.list_tools()
    assert len(tools) > 0, "No tools registered in MCPServer"

    results: list[dict[str, Any]] = []

    for tool in tools:
        t0 = time.perf_counter()
        tool_name = tool.name
        annotations = tool.annotations
        is_destructive = getattr(annotations, "destructive_hint", False)
        required_args = tool.input_schema.get("required", []) if tool.input_schema else []

        status, is_err, err_msg = await dispatch_tool_call(tool_name, is_destructive, required_args)
        latency_ms = (time.perf_counter() - t0) * 1000

        res_entry: dict[str, Any] = {
            "tool": tool_name,
            "status": status,
            "latency_ms": latency_ms,
            "is_error": is_err,
        }
        if err_msg:
            res_entry["error"] = err_msg
        results.append(res_entry)

    failed = [r for r in results if r["status"] == "FAIL"]
    assert not failed, f"E2E live tool verification failed for: {failed}"
    executed = [r for r in results if r["status"] == "PASS"]
    assert len(executed) > 0, "Expected at least one tool to be safely executed"
