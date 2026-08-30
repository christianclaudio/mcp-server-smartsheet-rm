"""Unit and integration tests for Smartsheet RM MCP Server."""

from __future__ import annotations

import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import smartsheet_rm_mcp.server as srv
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError


@pytest.fixture(autouse=True)
def setup_mock_client():
    mock_client = AsyncMock()
    # Default returns
    mock_client.list_time_entries.return_value = {"data": []}
    mock_client.get_time_entry.return_value = {"id": 1, "hours": 8}
    mock_client.create_time_entry.return_value = {"id": 2, "hours": 8}
    mock_client.update_time_entry.return_value = {"id": 1, "hours": 4}
    mock_client.delete_time_entry.return_value = {"status": "deleted"}
    mock_client.list_project_time_entries.return_value = {"data": []}
    mock_client.list_user_time_entries.return_value = {"data": []}
    mock_client.update_user_approval_status.return_value = {"status": "approved"}
    mock_client.lock_user_timesheet.return_value = {"status": "locked"}

    mock_client.list_projects.return_value = {"data": []}
    mock_client.get_project.return_value = {"id": 100, "name": "Apollo", "client_id": 5}
    mock_client.create_project.return_value = {"id": 101, "name": "New Project"}
    mock_client.update_project.return_value = {"id": 100, "name": "Updated Apollo"}
    mock_client.delete_project.return_value = {"status": "deleted"}
    mock_client.list_project_phases.return_value = {"data": []}
    mock_client.get_project_phase.return_value = {"id": 10, "name": "Phase 1"}
    mock_client.create_project_phase.return_value = {"id": 11, "name": "Phase 2"}
    mock_client.update_project_phase.return_value = {"id": 10, "name": "Phase 1b"}
    mock_client.delete_project_phase.return_value = {"status": "deleted"}

    mock_client.list_assignments.return_value = {"data": []}
    mock_client.get_assignment.return_value = {"id": 200, "percent": 100}
    mock_client.create_assignment.return_value = {"id": 201}
    mock_client.update_assignment.return_value = {"id": 200, "percent": 50}
    mock_client.delete_assignment.return_value = {"status": "deleted"}
    mock_client.list_project_assignments.return_value = {"data": []}
    mock_client.list_user_assignments.return_value = {"data": [{"project_id": 100}]}

    mock_client.list_users.return_value = {"data": []}
    mock_client.get_user.return_value = {"id": 1, "first_name": "Jane"}
    mock_client.create_user.return_value = {"id": 2, "first_name": "John"}
    mock_client.update_user.return_value = {"id": 1, "first_name": "Janet"}
    mock_client.delete_user.return_value = {"status": "deleted"}
    mock_client.list_user_bill_rates.return_value = [{"rate": 150}]
    mock_client.create_user_bill_rate.return_value = {"id": 1, "rate": 150}
    mock_client.get_user_availability.return_value = {"availability": 40}
    mock_client.get_user_utilization.return_value = {"utilization": 85}
    mock_client.list_roles.return_value = [{"id": 1, "name": "Engineer"}]
    mock_client.create_role.return_value = {"id": 2, "name": "Designer"}
    mock_client.update_role.return_value = {"id": 1, "name": "Lead Engineer"}
    mock_client.delete_role.return_value = {"status": "deleted"}
    mock_client.list_disciplines.return_value = [{"id": 1, "name": "Dev"}]
    mock_client.create_discipline.return_value = {"id": 2, "name": "QA"}
    mock_client.update_discipline.return_value = {"id": 1, "name": "Fullstack"}
    mock_client.delete_discipline.return_value = {"status": "deleted"}

    mock_client.list_clients.return_value = [{"id": 1, "name": "Acme"}]
    mock_client.get_client.return_value = {"id": 1, "name": "Acme"}
    mock_client.create_client.return_value = {"id": 2, "name": "Beta"}
    mock_client.update_client.return_value = {"id": 1, "name": "Acme Inc"}
    mock_client.delete_client.return_value = {"status": "deleted"}
    mock_client.list_client_contacts.return_value = [{"id": 1, "name": "Bob"}]
    mock_client.create_client_contact.return_value = {"id": 2, "first_name": "Alice"}
    mock_client.delete_client_contact.return_value = {"status": "deleted"}

    mock_client.list_leave_types.return_value = [{"id": 1, "name": "Vacation"}]
    mock_client.get_leave_type.return_value = {"id": 1, "name": "Vacation"}
    mock_client.create_leave_type.return_value = {"id": 2, "name": "Sick"}
    mock_client.update_leave_type.return_value = {"id": 1, "name": "PTO"}
    mock_client.delete_leave_type.return_value = {"status": "deleted"}
    mock_client.list_holidays.return_value = [{"id": 1, "name": "Labor Day"}]
    mock_client.get_holiday.return_value = {"id": 1, "name": "Labor Day"}
    mock_client.create_holiday.return_value = {"id": 2, "name": "New Year"}
    mock_client.update_holiday.return_value = {"id": 1, "name": "Holiday"}
    mock_client.delete_holiday.return_value = {"status": "deleted"}

    mock_client.list_expenses.return_value = {"data": []}
    mock_client.get_expense.return_value = {"id": 1, "amount": 100}
    mock_client.create_expense.return_value = {"id": 2, "amount": 150}
    mock_client.update_expense.return_value = {"id": 1, "amount": 120}
    mock_client.delete_expense.return_value = {"status": "deleted"}
    mock_client.list_project_expenses.return_value = {"data": []}
    mock_client.list_user_expenses.return_value = {"data": []}
    mock_client.list_expense_categories.return_value = [{"id": 1, "name": "Travel"}]
    mock_client.create_expense_category.return_value = {"id": 2, "name": "Meals"}
    mock_client.delete_expense_category.return_value = {"status": "deleted"}

    mock_client.list_tags.return_value = [{"id": 1, "name": "Priority"}]
    mock_client.create_tag.return_value = {"id": 2, "name": "VIP"}
    mock_client.delete_tag.return_value = {"status": "deleted"}
    mock_client.list_custom_fields.return_value = [{"id": 1, "name": "Region"}]
    mock_client.get_custom_field.return_value = {"id": 1, "name": "Region"}
    mock_client.create_custom_field.return_value = {"id": 2, "name": "Cost Center"}
    mock_client.update_custom_field.return_value = {"id": 1, "name": "Region V2"}
    mock_client.delete_custom_field.return_value = {"status": "deleted"}
    mock_client.list_custom_field_values.return_value = [{"id": 1, "value": "NA"}]
    mock_client.set_custom_field_values.return_value = {"status": "updated"}

    mock_client.list_approvals.return_value = {"data": []}
    mock_client.create_approval.return_value = {"status": "approved"}
    mock_client.delete_approval.return_value = {"status": "deleted"}
    mock_client.list_status_options.return_value = [{"id": 1, "name": "Active"}]
    mock_client.get_user_statuses.return_value = [{"status": "WFH"}]
    mock_client.set_user_status.return_value = {"status": "WFH"}
    mock_client.list_placeholder_resources.return_value = {"data": []}
    mock_client.create_placeholder_resource.return_value = {"id": 1, "title": "Dev"}
    mock_client.delete_placeholder_resource.return_value = {"status": "deleted"}
    mock_client.list_subtasks.return_value = [{"id": 1, "description": "Task"}]
    mock_client.create_subtask.return_value = {"id": 2, "description": "Task"}
    mock_client.delete_subtask.return_value = {"status": "deleted"}
    mock_client.get_report_rows.return_value = {"rows": []}
    mock_client.get_report_totals.return_value = {"totals": {}}
    mock_client.list_webhooks.return_value = [{"id": 1, "url": "https://test.com"}]
    mock_client.create_webhook.return_value = {"id": 2, "url": "https://test.com"}
    mock_client.delete_webhook.return_value = {"status": "deleted"}

    old_client = srv._client
    srv._client = mock_client
    yield mock_client
    srv._client = old_client


def test_logging_and_formatter() -> None:
    formatter = srv.StructuredJSONFormatter()
    record = logging.LogRecord("test", logging.INFO, "path.py", 10, "Hello Log", (), None)
    record.tool_name = "rm_test_tool"
    record.duration_ms = 45.6
    res = formatter.format(record)
    data = json.loads(res)
    assert data["message"] == "Hello Log"
    assert data["mcp_tool"] == "rm_test_tool"
    assert data["duration_ms"] == 45.6

    # With exception info
    try:
        raise ValueError("Boom")
    except ValueError:
        import sys

        record.exc_info = sys.exc_info()
        res_exc = formatter.format(record)
        data_exc = json.loads(res_exc)
        assert "Boom" in data_exc["exception"]

    with patch.dict(os.environ, {"SMARTSHEET_RM_LOG_FORMAT": "json"}):
        srv.configure_logging()


def test_redact_secrets() -> None:
    assert srv._redact_secrets("") == ""
    with patch.dict(os.environ, {"SMARTSHEET_RM_API_TOKEN": "my-secret-key"}):
        text = 'Failed with token my-secret-key and extra-sec and auth: 987654321 and api_token="abc12345"'
        redacted = srv._redact_secrets(text, extra_secret="extra-sec")
        assert "my-secret-key" not in redacted
        assert "extra-sec" not in redacted
        assert "***REDACTED***" in redacted


@pytest.mark.asyncio
async def test_get_client_resolution_and_cache() -> None:
    srv._client = None
    with patch.dict(os.environ, {"SMARTSHEET_RM_API_TOKEN": "env-token"}):
        c = await srv.get_client()
        assert c.api_token == "env-token"

    # Missing env token
    srv._client = None
    with patch.dict(os.environ, {"SMARTSHEET_RM_API_TOKEN": ""}):
        with pytest.raises(ValueError) as exc:
            await srv.get_client()
        assert "SMARTSHEET_RM_API_TOKEN" in str(exc.value)

    # Per-request context header resolution
    srv._HEADER_CLIENT_CACHE.clear()
    ctx = {"headers": {"x-smartsheet-rm-token": "header-token", "x-smartsheet-rm-base-url": "https://api.custom.com"}}
    client_ctx = await srv.get_client(ctx)
    assert client_ctx.api_token == "header-token"
    assert client_ctx.base_url == "https://api.custom.com"

    # Context with request_context object
    req_ctx_mock = MagicMock()
    req_ctx_mock.request_context.headers = {"auth": "auth-header-token"}
    client_ctx2 = await srv.get_client(req_ctx_mock)
    assert client_ctx2.api_token == "auth-header-token"

    # Cache limit eviction (>100)
    srv._HEADER_CLIENT_CACHE.clear()
    for i in range(105):
        m = MagicMock()
        m.aclose = AsyncMock()
        srv._HEADER_CLIENT_CACHE[(f"tok-{i}", "url")] = m
    await srv.get_client({"headers": {"x-smartsheet-rm-token": "new-tok"}})
    assert len(srv._HEADER_CLIENT_CACHE) == 105


@pytest.mark.asyncio
async def test_decorator_error_handling() -> None:
    @srv.rm_tool
    async def failing_api_tool():
        raise SmartsheetRMAPIError(404, "/items/1", "GET", "Resource not found token=123")

    @srv.rm_tool
    async def failing_generic_tool():
        raise RuntimeError("Unexpected failure with token=secret")

    res1 = await failing_api_tool()
    data1 = json.loads(res1)
    assert data1["error"]["status_code"] == 404
    assert "Resource not found" in data1["error"]["detail"]

    res2 = await failing_generic_tool()
    data2 = json.loads(res2)
    assert data2["error"]["type"] == "internal"


@pytest.mark.asyncio
async def test_time_tracking_tools(setup_mock_client) -> None:
    # list_time_entries
    res = await srv.rm_list_time_entries(
        project_id=10, from_date="2026-08-01", to_date="2026-08-07", with_suggestions=True
    )
    assert "data" in json.loads(res)
    await srv.rm_list_time_entries(user_id=20)
    await srv.rm_list_time_entries()

    # get_time_entry
    await srv.rm_get_time_entry(1)

    # create_time_entry
    await srv.rm_create_time_entry(1, 100, "2026-08-10", 8.0, notes="Work", phase_id=10, custom_field_values={"1": "v"})

    # update_time_entry
    await srv.rm_update_time_entry(1, hours=7.5, notes="N", date="2026-08-11", is_billable=True)
    res_empty_up = await srv.rm_update_time_entry(1)
    assert "invalid_request" in json.loads(res_empty_up)["error"]["type"]

    # delete_time_entry
    gate_res = await srv.rm_delete_time_entry(1, confirm=False)
    assert "requires explicit confirmation" in json.loads(gate_res)["error"]["message"]
    await srv.rm_delete_time_entry(1, confirm=True)

    # suggestions & approval & locks
    await srv.rm_list_user_suggestions(1, from_date="2026-08-01", to_date="2026-08-07")
    await srv.rm_update_time_approval_status(1, [10, 11], "approved", approver_notes="Approved")
    invalid_status = await srv.rm_update_time_approval_status(1, [10], "unknown")
    assert "Status must be one of" in json.loads(invalid_status)["error"]["message"]

    await srv.rm_lock_timesheet(1, "2026-08-01", unlock=False)


@pytest.mark.asyncio
async def test_project_and_phase_tools(setup_mock_client) -> None:
    # list / get / create / update / delete project
    await srv.rm_list_projects(
        with_phases=True, archived=False, filter_field="name", filter_value="A", sort_field="name"
    )
    await srv.rm_get_project(100, with_phases=True)
    await srv.rm_create_project(
        "New",
        client_id=1,
        budget=1000.0,
        budget_type="Fee",
        start_date="2026-08-01",
        end_date="2026-08-31",
        description="D",
    )
    await srv.rm_update_project(
        100,
        name="Up",
        project_state="Confirmed",
        budget=2000.0,
        budget_type="Hours",
        start_date="2026-08-01",
        end_date="2026-08-31",
        description="D2",
        archived=False,
    )
    assert "invalid_request" in json.loads(await srv.rm_update_project(100))["error"]["type"]
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_project(100, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_project(100, confirm=True)
    await srv.rm_list_project_users(100, page=1, per_page=10)

    # phases
    await srv.rm_list_project_phases(100)
    await srv.rm_get_project_phase(100, 10)
    await srv.rm_create_project_phase(100, "Phase 1", "2026-08-01", "2026-08-15", budget=500.0, description="P desc")
    await srv.rm_update_project_phase(
        100, 10, name="Phase 1b", start_date="2026-08-02", end_date="2026-08-16", budget=600.0, description="Desc2"
    )
    assert "invalid_request" in json.loads(await srv.rm_update_project_phase(100, 10))["error"]["type"]
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_project_phase(100, 10, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_project_phase(100, 10, confirm=True)


@pytest.mark.asyncio
async def test_assignment_tools(setup_mock_client) -> None:
    await srv.rm_list_assignments(project_id=100, from_date="2026-08-01", to_date="2026-08-10")
    await srv.rm_list_assignments(user_id=1)
    await srv.rm_list_assignments()
    await srv.rm_get_assignment(200)
    await srv.rm_create_assignment(
        100, 1, "2026-08-01", "2026-08-15", percent=100, hours_per_day=8.0, fixed_hours=40, phase_id=10, note="Note"
    )
    await srv.rm_update_assignment(
        200,
        start_date="2026-08-02",
        end_date="2026-08-16",
        percent=50,
        hours_per_day=4.0,
        fixed_hours=20,
        note="Updated",
    )
    assert "invalid_request" in json.loads(await srv.rm_update_assignment(200))["error"]["type"]
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_assignment(200, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_assignment(200, confirm=True)


@pytest.mark.asyncio
async def test_user_role_discipline_tools(setup_mock_client) -> None:
    # Users
    await srv.rm_list_users(role="Engineer", discipline="Dev", archived=False, include_billability=True)
    await srv.rm_get_user(1)
    await srv.rm_create_user(
        "A",
        "B",
        "ab@test.com",
        role="Dev",
        discipline="Eng",
        billability_target=0.8,
        bill_rate=150.0,
        cost_rate=75.0,
        user_type_id=1,
        location="NYC",
    )
    await srv.rm_update_user(
        1,
        first_name="A2",
        last_name="B2",
        email="ab2@test.com",
        role="Dev2",
        discipline="Eng2",
        billability_target=0.9,
        bill_rate=160.0,
        cost_rate=80.0,
        archived=False,
    )
    assert "invalid_request" in json.loads(await srv.rm_update_user(1))["error"]["type"]
    assert (
        "requires explicit confirmation" in json.loads(await srv.rm_delete_user(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_user(1, confirm=True)

    # Rates & capacity
    await srv.rm_list_user_bill_rates(1)
    await srv.rm_create_user_bill_rate(1, 150.0, "2026-08-01", "2026-12-31")
    await srv.rm_get_user_availability(1, from_date="2026-08-01", to_date="2026-08-31")
    await srv.rm_get_user_utilization(1, from_date="2026-08-01", to_date="2026-08-31")

    # Roles
    await srv.rm_list_roles()
    await srv.rm_create_role("Architect")
    await srv.rm_update_role(1, "Principal Architect")
    assert (
        "requires explicit confirmation" in json.loads(await srv.rm_delete_role(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_role(1, confirm=True)

    # Disciplines
    await srv.rm_list_disciplines()
    await srv.rm_create_discipline("Design")
    await srv.rm_update_discipline(1, "Product Design")
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_discipline(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_discipline(1, confirm=True)


@pytest.mark.asyncio
async def test_client_and_contact_tools(setup_mock_client) -> None:
    await srv.rm_list_clients(archived=False)
    await srv.rm_get_client(1)
    await srv.rm_create_client("Beta", address="123 Main", city="Miami", state="FL", zipcode="33101", country="USA")
    await srv.rm_update_client(
        1, name="Beta Inc", address="124 Main", city="Tampa", state="FL", zipcode="33601", country="USA", archived=False
    )
    assert "invalid_request" in json.loads(await srv.rm_update_client(1))["error"]["type"]
    assert (
        "requires explicit confirmation" in json.loads(await srv.rm_delete_client(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_client(1, confirm=True)

    await srv.rm_list_client_contacts(1)
    await srv.rm_create_client_contact(1, "John", "Doe", email="j@doe.com", phone="555-1234", title="VP")
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_client_contact(1, 10, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_client_contact(1, 10, confirm=True)


@pytest.mark.asyncio
async def test_leave_and_holiday_tools(setup_mock_client) -> None:
    await srv.rm_list_leave_types()
    await srv.rm_get_leave_type(1)
    await srv.rm_create_leave_type("Sabbatical")
    await srv.rm_update_leave_type(1, "Extended Leave")
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_leave_type(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_leave_type(1, confirm=True)

    await srv.rm_list_holidays(from_date="2026-01-01", to_date="2026-12-31")
    await srv.rm_get_holiday(1)
    await srv.rm_create_holiday("Memorial Day", "2026-05-25", "2026-05-25")
    await srv.rm_update_holiday(1, name="Memorial Day Observed", date="2026-05-26", end_date="2026-05-26")
    assert "invalid_request" in json.loads(await srv.rm_update_holiday(1))["error"]["type"]
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_holiday(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_holiday(1, confirm=True)


@pytest.mark.asyncio
async def test_expense_tools(setup_mock_client) -> None:
    await srv.rm_list_expenses(project_id=100, from_date="2026-08-01", to_date="2026-08-31")
    await srv.rm_list_expenses(user_id=1)
    await srv.rm_list_expenses()
    await srv.rm_get_expense(1)
    await srv.rm_create_expense(100, 1, 1, 150.0, "2026-08-05", notes="Flight", is_billable=True)
    await srv.rm_update_expense(1, amount=175.0, notes="Flight upgrade", is_billable=True, date="2026-08-06")
    assert "invalid_request" in json.loads(await srv.rm_update_expense(1))["error"]["type"]
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_expense(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_expense(1, confirm=True)

    await srv.rm_list_expense_categories()
    await srv.rm_create_expense_category("Software")
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_expense_category(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_expense_category(1, confirm=True)


@pytest.mark.asyncio
async def test_tags_and_custom_fields_tools(setup_mock_client) -> None:
    await srv.rm_list_tags()
    await srv.rm_create_tag("High Priority")
    assert "requires explicit confirmation" in json.loads(await srv.rm_delete_tag(1, confirm=False))["error"]["message"]
    await srv.rm_delete_tag(1, confirm=True)

    await srv.rm_list_custom_fields()
    await srv.rm_get_custom_field(1)
    await srv.rm_create_custom_field("Department", "select", "User", options=["Sales", "Eng"])
    await srv.rm_update_custom_field(1, name="Department Name", options=["Sales", "Eng", "Ops"])
    assert "invalid_request" in json.loads(await srv.rm_update_custom_field(1))["error"]["type"]
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_custom_field(1, confirm=False))["error"]["message"]
    )
    await srv.rm_delete_custom_field(1, confirm=True)

    await srv.rm_list_custom_field_values(target_id=1, target_type="User")
    await srv.rm_set_custom_field_values(1, "User", {"Department": "Eng"})


@pytest.mark.asyncio
async def test_composite_workflow_recipes(setup_mock_client) -> None:
    # 1. rm_fill_weekly_timesheet
    res1 = await srv.rm_fill_weekly_timesheet(1, "2026-08-10", daily_hours=8.0, project_id=100)
    data1 = json.loads(res1)
    assert data1["status"] == "success"
    assert data1["total_hours"] == 40.0
    assert data1["created_count"] == 5

    # Auto-resolve assignment project
    res1_auto = await srv.rm_fill_weekly_timesheet(1, "2026-08-10", daily_hours=8.0)
    assert json.loads(res1_auto)["status"] == "success"

    # Auto-resolve failure when no assignments
    setup_mock_client.list_user_assignments.return_value = []
    res1_fail = await srv.rm_fill_weekly_timesheet(1, "2026-08-10")
    assert "No active assignments found" in json.loads(res1_fail)["error"]["message"]

    # Auto-resolve failure when assignment missing project_id
    setup_mock_client.list_user_assignments.return_value = [{}]
    res1_fail2 = await srv.rm_fill_weekly_timesheet(1, "2026-08-10")
    assert "Unable to resolve project_id" in json.loads(res1_fail2)["error"]["message"]

    # Invalid date format
    res1_bad_date = await srv.rm_fill_weekly_timesheet(1, "invalid-date")
    assert "Invalid start_date" in json.loads(res1_bad_date)["error"]["message"]

    # Partial creation failure
    setup_mock_client.create_time_entry.side_effect = [
        {"id": 1},
        SmartsheetRMAPIError(500, "/time_entries", "POST", "Error"),
        {"id": 3},
        {"id": 4},
        {"id": 5},
    ]
    res1_partial = await srv.rm_fill_weekly_timesheet(1, "2026-08-10", project_id=100)
    data1_partial = json.loads(res1_partial)
    assert data1_partial["status"] == "partial_success"
    assert data1_partial["created_count"] == 4
    assert data1_partial["failed_count"] == 1
    setup_mock_client.create_time_entry.side_effect = None

    # Weekend support (7 days with weekend_hours)
    res1_weekend = await srv.rm_fill_weekly_timesheet(
        1, "2026-08-10", daily_hours=8.0, project_id=100, include_weekends=True, weekend_hours=4.0
    )
    data1_weekend = json.loads(res1_weekend)
    assert data1_weekend["status"] == "success"
    assert data1_weekend["days_filled"] == 7
    assert data1_weekend["total_hours"] == 48.0

    # 2. rm_confirm_suggested_hours
    setup_mock_client.list_user_time_entries.return_value = {
        "data": [
            {"id": 10, "is_suggestion": True, "hours": 8.0, "date": "2026-08-10"},
            {"id": 11, "is_suggestion": False, "hours": 8.0, "date": "2026-08-11"},
            {"id": 12, "is_suggestion": True, "hours": 4.0, "date": "2026-08-12"},
        ]
    }
    setup_mock_client.update_time_entry.side_effect = [
        {"id": 10},
        SmartsheetRMAPIError(500, "/time_entries/12", "PUT", "Error"),
    ]
    res2 = await srv.rm_confirm_suggested_hours(1, "2026-08-10", "2026-08-16")
    data2 = json.loads(res2)
    assert data2["status"] == "partial_success"
    assert data2["confirmed_count"] == 1
    assert data2["failed_count"] == 1
    setup_mock_client.update_time_entry.side_effect = None

    # 3. rm_reconcile_and_submit_week
    # Invalid date
    res3_bad = await srv.rm_reconcile_and_submit_week(1, "not-a-date")
    assert "Invalid start_date" in json.loads(res3_bad)["error"]["message"]

    # Balanced (40h)
    setup_mock_client.list_user_time_entries.return_value = {"data": [{"id": i, "hours": 8.0} for i in range(5)]}
    res3 = await srv.rm_reconcile_and_submit_week(1, "2026-08-10", target_hours=40.0, auto_submit=True)
    data3 = json.loads(res3)
    assert data3["status"] == "balanced"
    assert data3["submitted"] is True

    # Variance detected (32h)
    setup_mock_client.list_user_time_entries.return_value = {"data": [{"id": i, "hours": 8.0} for i in range(4)]}
    res3_var = await srv.rm_reconcile_and_submit_week(1, "2026-08-10", target_hours=40.0, auto_submit=True)
    data3_var = json.loads(res3_var)
    assert data3_var["status"] == "variance_detected"
    assert data3_var["variance"] == -8.0

    # 4. rm_clone_project_schedule
    setup_mock_client.get_project.return_value = {
        "id": 100,
        "name": "Template",
        "project_state": "Confirmed",
        "client_id": 5,
    }
    setup_mock_client.list_project_phases.return_value = {
        "data": [{"name": "Phase 1", "starts_at": "2026-08-01", "ends_at": "2026-08-15"}]
    }
    res4 = await srv.rm_clone_project_schedule(100, "Cloned Project", new_start_date="2026-09-01", client_id=9)
    data4 = json.loads(res4)
    assert data4["status"] == "success"
    assert data4["cloned_phases_count"] == 1

    # Clone without explicit client_id (uses source client_id fallback)
    res4_fallback = await srv.rm_clone_project_schedule(100, "Cloned Project 2")
    assert json.loads(res4_fallback)["status"] == "success"

    # 5. Bulk destructive operations
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_bulk_delete_time_entries([1, 2], confirm=False))["error"]["message"]
    )
    res_b_time = await srv.rm_bulk_delete_time_entries([1, 2], confirm=True)
    assert json.loads(res_b_time)["deleted_count"] == 2

    # Partial failure in bulk delete
    setup_mock_client.delete_time_entry.side_effect = [
        {"status": "deleted"},
        SmartsheetRMAPIError(404, "/time_entries/2", "DELETE", "Not found"),
    ]
    res_b_time_partial = await srv.rm_bulk_delete_time_entries([1, 2], confirm=True)
    data_b_time_partial = json.loads(res_b_time_partial)
    assert data_b_time_partial["status"] == "partial_success"
    assert data_b_time_partial["deleted_count"] == 1
    assert data_b_time_partial["failed_count"] == 1
    setup_mock_client.delete_time_entry.side_effect = None

    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_bulk_delete_assignments([10, 20], confirm=False))["error"]["message"]
    )
    res_b_assign = await srv.rm_bulk_delete_assignments([10, 20], confirm=True)
    assert json.loads(res_b_assign)["deleted_count"] == 2

    setup_mock_client.delete_assignment.side_effect = [
        {"status": "deleted"},
        SmartsheetRMAPIError(404, "/assignments/20", "DELETE", "Not found"),
    ]
    res_b_assign_partial = await srv.rm_bulk_delete_assignments([10, 20], confirm=True)
    data_b_assign_partial = json.loads(res_b_assign_partial)
    assert data_b_assign_partial["status"] == "partial_success"
    setup_mock_client.delete_assignment.side_effect = None

    # 6. OpenAPI Extension Tools
    # Approvals
    assert "data" in json.loads(await srv.rm_list_approvals())
    assert json.loads(await srv.rm_create_approval("time_entries", [1, 2], notes="Ok"))["status"] == "approved"
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_approval(1, confirm=False))["error"]["message"]
    )
    assert json.loads(await srv.rm_delete_approval(1, confirm=True))["status"] == "deleted"

    # Status options
    assert len(json.loads(await srv.rm_list_status_options())) == 1

    # User statuses
    assert len(json.loads(await srv.rm_get_user_statuses(1))) == 1
    assert "Status must be one of" in json.loads(await srv.rm_set_user_status(1, "INVALID"))["error"]["message"]
    assert json.loads(await srv.rm_set_user_status(1, "WFH", notes="Remote"))["status"] == "WFH"

    # Placeholders
    assert "data" in json.loads(await srv.rm_list_placeholder_resources())
    assert (
        json.loads(
            await srv.rm_create_placeholder_resource("Engineer", role="Dev", discipline="Backend", location="Remote")
        )["id"]
        == 1
    )
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_placeholder_resource(1, confirm=False))["error"]["message"]
    )
    assert json.loads(await srv.rm_delete_placeholder_resource(1, confirm=True))["status"] == "deleted"

    # Subtasks
    assert len(json.loads(await srv.rm_list_assignment_subtasks(100, 10))) == 1
    assert json.loads(await srv.rm_create_assignment_subtask(100, 10, "Task 1", completed=True))["id"] == 2
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_assignment_subtask(100, 10, 5, confirm=False))["error"]["message"]
    )
    assert json.loads(await srv.rm_delete_assignment_subtask(100, 10, 5, confirm=True))["status"] == "deleted"

    # Reports
    assert "rows" in json.loads(await srv.rm_get_report_rows({"report_type": "time"}))
    assert "totals" in json.loads(await srv.rm_get_report_totals({"report_type": "time"}))

    # Webhooks
    assert len(json.loads(await srv.rm_list_webhooks())) == 1
    assert json.loads(await srv.rm_create_webhook("time.entry.created", "https://hook.test"))["id"] == 2
    assert (
        "requires explicit confirmation"
        in json.loads(await srv.rm_delete_webhook(1, confirm=False))["error"]["message"]
    )
    assert json.loads(await srv.rm_delete_webhook(1, confirm=True))["status"] == "deleted"


def test_resources_and_prompts() -> None:
    cap = srv.rm_capabilities_resource()
    assert "Time Tracking & Approvals" in cap
    assert "mcp-server-smartsheet-rm" in cap

    quick = srv.rm_quickstart_resource()
    assert "Timesheet Reconciliation" in quick

    p1 = srv.timesheet_reconciliation("123", "2026-08-10")
    assert "user ID 123" in p1

    p2 = srv.project_staffing_plan("456")
    assert "project ID 456" in p2


def test_main_cli_argparsing() -> None:
    with patch("sys.argv", ["mcp-server-smartsheet-rm", "--transport", "stdio"]):
        with patch.object(srv.mcp, "run") as mock_run:
            srv.main()
            mock_run.assert_called_once_with(transport="stdio")


def test_server_profile_and_readonly_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    try:
        monkeypatch.setenv("SMARTSHEET_RM_PROFILE", "invalid_profile")
        with pytest.raises(ValueError, match="Unknown SMARTSHEET_RM_PROFILE"):
            importlib.reload(srv)

        monkeypatch.setenv("SMARTSHEET_RM_PROFILE", "time")
        importlib.reload(srv)
        assert "rm_list_time_entries" in srv.mcp._tool_manager._tools

        monkeypatch.setenv("SMARTSHEET_RM_PROFILE", "full")
        monkeypatch.setenv("SMARTSHEET_RM_READONLY", "1")
        importlib.reload(srv)
        assert "rm_delete_project" not in srv.mcp._tool_manager._tools
        assert "rm_list_projects" in srv.mcp._tool_manager._tools

        monkeypatch.delenv("SMARTSHEET_RM_READONLY", raising=False)
        monkeypatch.setenv("SMARTSHEET_RM_PROFILE", "full")
        monkeypatch.setenv("SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE", "1")
        importlib.reload(srv)
        assert "rm_bulk_delete_time_entries" in srv.mcp._tool_manager._tools
    finally:
        monkeypatch.undo()
        importlib.reload(srv)


def test_handle_shutdown() -> None:
    with pytest.raises(SystemExit) as exc_info:
        srv._handle_shutdown(15, None)
    assert exc_info.value.code == 0
