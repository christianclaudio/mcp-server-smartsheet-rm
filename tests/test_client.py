"""Unit and integration tests for SmartsheetRMClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from smartsheet_rm_mcp.client import SmartsheetRMClient
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError


@pytest.mark.asyncio
async def test_client_init_and_context_manager() -> None:
    client = SmartsheetRMClient("test-token", "https://api.rm.smartsheet.com/api/v1/")
    assert client.api_token == "test-token"
    assert client.base_url == "https://api.rm.smartsheet.com/api/v1"
    assert client._owns_http is True

    async with client as c:
        assert c is client

    # Test with custom external httpx client
    custom_http = httpx.AsyncClient()
    client2 = SmartsheetRMClient("test-token-2", http_client=custom_http)
    assert client2._owns_http is False
    await client2.aclose()  # Shouldn't close custom_http
    await custom_http.aclose()


def test_client_headers() -> None:
    client = SmartsheetRMClient("my-secret-token")
    headers = client._headers()
    assert headers["auth"] == "my-secret-token"
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    assert "mcp-server-smartsheet-rm" in headers["User-Agent"]


def test_parse_retry_after() -> None:
    assert SmartsheetRMClient._parse_retry_after(None) is None
    assert SmartsheetRMClient._parse_retry_after("") is None
    assert SmartsheetRMClient._parse_retry_after("5") == 5.0
    assert SmartsheetRMClient._parse_retry_after("2.5") == 2.5
    assert SmartsheetRMClient._parse_retry_after("invalid") is None
    assert SmartsheetRMClient._parse_retry_after("-10") == 0.0


@pytest.mark.asyncio
async def test_request_success_and_path_encoding() -> None:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": 123, "name": "Test Project"}
    mock_resp.content = b'{"id": 123, "name": "Test Project"}'
    mock_http.request.return_value = mock_resp

    client = SmartsheetRMClient("token", http_client=mock_http)
    res = await client.get_project("proj/with special")
    assert res["id"] == 123
    mock_http.request.assert_called_once()
    call_args = mock_http.request.call_args
    assert "with%20special" in call_args[0][1]


@pytest.mark.asyncio
async def test_request_error_handling_json_and_text() -> None:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {"x-request-id": "req-404"}
    mock_resp.json.return_value = {"message": "Not Found"}
    mock_http.request.return_value = mock_resp

    client = SmartsheetRMClient("token", http_client=mock_http)
    with pytest.raises(SmartsheetRMAPIError) as exc_info:
        await client.get_project(999)
    assert exc_info.value.status_code == 404
    assert exc_info.value.request_id == "req-404"

    # Non-json response
    mock_resp.json.side_effect = Exception("Not JSON")
    mock_resp.text = "Raw error text"
    with pytest.raises(SmartsheetRMAPIError) as exc_info2:
        await client.get_project(999)
    assert exc_info2.value.detail == "Raw error text"


@pytest.mark.asyncio
async def test_request_429_rate_limit_retry_and_exhaustion() -> None:
    mock_http = AsyncMock()
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "0.01", "x-request-id": "req-429"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": []}
    mock_resp_200.content = b'{"data": []}'

    # Case 1: Succeed on retry 2
    mock_http.request.side_effect = [mock_resp_429, mock_resp_200]
    client = SmartsheetRMClient("token", max_retries=2, base_delay=0.01, http_client=mock_http)
    res = await client.list_projects()
    assert res == {"data": []}
    assert mock_http.request.call_count == 2

    # Case 2: Exhaust retries (without Retry-After header)
    mock_resp_429.headers = {}
    mock_http.request.side_effect = [mock_resp_429, mock_resp_429, mock_resp_429]
    mock_http.request.reset_mock()
    with pytest.raises(SmartsheetRMAPIError) as exc:
        await client.list_projects()
    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded after max retries"


@pytest.mark.asyncio
async def test_all_client_api_methods() -> None:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True}
    mock_resp.content = b'{"success": True}'
    mock_http.request.return_value = mock_resp

    client = SmartsheetRMClient("token", http_client=mock_http)

    # 1. Time tracking
    assert await client.list_time_entries({"page": 1}) == {"success": True}
    assert await client.get_time_entry(1) == {"success": True}
    assert await client.create_time_entry({"hours": 8}) == {"success": True}
    assert await client.update_time_entry(1, {"hours": 7}) == {"success": True}
    assert await client.delete_time_entry(1) == {"success": True}
    assert await client.list_project_time_entries(10) == {"success": True}
    assert await client.create_project_time_entry(10, {"hours": 4}) == {"success": True}
    assert await client.list_user_time_entries(20) == {"success": True}
    assert await client.create_user_time_entry(20, {"hours": 5}) == {"success": True}
    assert await client.update_user_approval_status(20, {"status": "approved"}) == {"success": True}
    assert await client.lock_user_timesheet(20, {"date": "2026-08-01"}) == {"success": True}

    # Empty content delete returns dict
    mock_resp.content = b""
    assert await client.delete_time_entry(2) == {"status": "deleted", "id": 2}
    assert await client.update_user_approval_status(20, {}) == {"status": "updated", "user_id": 20}
    assert await client.lock_user_timesheet(20, {}) == {"status": "locked", "user_id": 20}
    mock_resp.content = b'{"success": True}'

    # 2. Projects & phases
    assert await client.list_projects() == {"success": True}
    assert await client.get_project(100) == {"success": True}
    assert await client.create_project({"name": "P"}) == {"success": True}
    assert await client.update_project(100, {"name": "P2"}) == {"success": True}
    assert await client.delete_project(100) == {"success": True}
    assert await client.list_project_users(100) == {"success": True}
    assert await client.list_project_phases(100) == {"success": True}
    assert await client.get_project_phase(100, 101) == {"success": True}
    assert await client.create_project_phase(100, {"name": "Phase 1"}) == {"success": True}
    assert await client.update_project_phase(100, 101, {"name": "Phase 1b"}) == {"success": True}
    assert await client.delete_project_phase(100, 101) == {"success": True}

    # 3. Assignments
    assert await client.list_assignments() == {"success": True}
    assert await client.get_assignment(200) == {"success": True}
    assert await client.create_assignment({"user_id": 1}) == {"success": True}
    assert await client.update_assignment(200, {"percent": 50}) == {"success": True}
    assert await client.delete_assignment(200) == {"success": True}
    assert await client.list_project_assignments(100) == {"success": True}
    assert await client.create_project_assignment(100, {}) == {"success": True}
    assert await client.list_user_assignments(1) == {"success": True}
    assert await client.create_user_assignment(1, {}) == {"success": True}

    # 4. Users, Roles & Disciplines
    assert await client.list_users() == {"success": True}
    assert await client.get_user(1) == {"success": True}
    assert await client.create_user({"email": "a@b.com"}) == {"success": True}
    assert await client.update_user(1, {"first_name": "F"}) == {"success": True}
    assert await client.delete_user(1) == {"success": True}
    assert await client.list_user_bill_rates(1) == {"success": True}
    assert await client.create_user_bill_rate(1, {"rate": 150}) == {"success": True}
    assert await client.get_user_availability(1) == {"success": True}
    assert await client.get_user_utilization(1) == {"success": True}
    assert await client.list_roles() == {"success": True}
    assert await client.create_role({"name": "Dev"}) == {"success": True}
    assert await client.update_role(1, {"name": "Dev Senior"}) == {"success": True}
    assert await client.delete_role(1) == {"success": True}
    assert await client.list_disciplines() == {"success": True}
    assert await client.create_discipline({"name": "Eng"}) == {"success": True}
    assert await client.update_discipline(1, {"name": "Eng Core"}) == {"success": True}
    assert await client.delete_discipline(1) == {"success": True}

    # 5. Clients & Contacts
    assert await client.list_clients() == {"success": True}
    assert await client.get_client(1) == {"success": True}
    assert await client.create_client({"name": "C"}) == {"success": True}
    assert await client.update_client(1, {"name": "C2"}) == {"success": True}
    assert await client.delete_client(1) == {"success": True}
    assert await client.list_client_contacts(1) == {"success": True}
    assert await client.create_client_contact(1, {"first_name": "Bob"}) == {"success": True}
    assert await client.delete_client_contact(1, 10) == {"success": True}

    # 6. Leaves & Holidays
    assert await client.list_leave_types() == {"success": True}
    assert await client.get_leave_type(1) == {"success": True}
    assert await client.create_leave_type({"name": "Vacation"}) == {"success": True}
    assert await client.update_leave_type(1, {"name": "PTO"}) == {"success": True}
    assert await client.delete_leave_type(1) == {"success": True}
    assert await client.list_holidays() == {"success": True}
    assert await client.get_holiday(1) == {"success": True}
    assert await client.create_holiday({"name": "New Year"}) == {"success": True}
    assert await client.update_holiday(1, {"name": "Holiday"}) == {"success": True}
    assert await client.delete_holiday(1) == {"success": True}

    # 7. Expenses
    assert await client.list_expenses() == {"success": True}
    assert await client.get_expense(1) == {"success": True}
    assert await client.create_expense({"amount": 50}) == {"success": True}
    assert await client.update_expense(1, {"amount": 75}) == {"success": True}
    assert await client.delete_expense(1) == {"success": True}
    assert await client.list_project_expenses(100) == {"success": True}
    assert await client.list_user_expenses(1) == {"success": True}
    assert await client.list_expense_categories() == {"success": True}
    assert await client.create_expense_category({"name": "Travel"}) == {"success": True}
    assert await client.delete_expense_category(1) == {"success": True}

    # 8. Tags & Custom Fields
    assert await client.list_tags() == {"success": True}
    assert await client.create_tag({"name": "VIP"}) == {"success": True}
    assert await client.delete_tag(1) == {"success": True}
    assert await client.list_custom_fields() == {"success": True}
    assert await client.get_custom_field(1) == {"success": True}
    assert await client.create_custom_field({"name": "Tier"}) == {"success": True}
    assert await client.update_custom_field(1, {"name": "Tier 2"}) == {"success": True}
    assert await client.delete_custom_field(1) == {"success": True}
    assert await client.list_custom_field_values() == {"success": True}
    assert await client.set_custom_field_values({"1": "A"}) == {"success": True}

    # 9. OpenAPI Extension Entities
    assert await client.list_approvals() == {"success": True}
    assert await client.create_approval({"status": "approved"}) == {"success": True}
    assert await client.delete_approval(1) == {"success": True}
    assert await client.list_status_options() == {"success": True}
    assert await client.get_user_statuses(1) == {"success": True}
    assert await client.set_user_status(1, {"status": "WFH"}) == {"success": True}
    assert await client.list_placeholder_resources() == {"success": True}
    assert await client.create_placeholder_resource({"title": "Dev"}) == {"success": True}
    assert await client.delete_placeholder_resource(1) == {"success": True}
    assert await client.list_subtasks(100, 10) == {"success": True}
    assert await client.create_subtask(100, 10, {"description": "Task"}) == {"success": True}
    assert await client.delete_subtask(100, 10, 5) == {"success": True}
    assert await client.get_report_rows({"report_type": "time"}) == {"success": True}
    assert await client.get_report_totals({"report_type": "time"}) == {"success": True}
    assert await client.list_webhooks() == {"success": True}
    assert await client.create_webhook({"url": "https://test.com"}) == {"success": True}
    assert await client.delete_webhook(1) == {"success": True}
