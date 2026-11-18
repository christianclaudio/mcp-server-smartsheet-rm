"""Offline calls for the coverage-gap tools, form bodies, CSV cap, and API-token guard.

Paths and bodies follow the published Resource Management OpenAPI document.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp.exceptions import ToolError

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.client import GOV_BASE_URL, GOV_HOST, SmartsheetRMClient
from smartsheet_rm_mcp.config import settings
from smartsheet_rm_mcp.errors import clear_remembered_secrets
from smartsheet_rm_mcp.server import create_server

TOKEN = "tok-test-0001"
OTHER = "tok-test-0002"


class Inbox:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responder: Callable[[httpx.Request], httpx.Response] = lambda _request: httpx.Response(
            200, json={"ok": True, "id": 1}
        )


@pytest.fixture
def inbox(monkeypatch: pytest.MonkeyPatch) -> Iterator[Inbox]:
    for var in (
        "SMARTSHEET_RM_PROFILE",
        "SMARTSHEET_RM_READONLY",
        "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE",
        "SMARTSHEET_RM_ENABLE_TOOL_SEARCH",
        "SMARTSHEET_RM_ENABLE_CODE_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "READONLY", False)
    box = Inbox()

    def handler(request: httpx.Request) -> httpx.Response:
        box.requests.append(request)
        return box.responder(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=http))
    clear_remembered_secrets()
    yield box
    clear_remembered_secrets()


async def _call(name: str, args: dict[str, Any] | None = None) -> Any:
    res = await create_server(profile="full").call_tool(name, args or {})
    assert not res.is_error, res.content[0].text if res.content else res
    return json.loads(res.content[0].text)  # type: ignore[union-attr]


async def _fail(name: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        res = await create_server(profile="full").call_tool(name, args)
    except ToolError as exc:
        text = str(exc)
    else:
        assert res.is_error
        text = res.content[0].text  # type: ignore[union-attr]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"error": {"message": text}}
    assert isinstance(parsed, dict)
    return parsed


def _body(req: httpx.Request) -> Any:
    return json.loads(req.content.decode() or "null")


def _form(req: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(req.content.decode())


def _query(req: httpx.Request) -> dict[str, list[str]]:
    raw = req.url.query.decode() if isinstance(req.url.query, bytes) else str(req.url.query)
    return parse_qs(raw)


GETS = [
    ("time_get_project_time_entry", {"project_id": 3, "entry_id": 4}, "/api/v1/projects/3/time_entries/4"),
    ("time_get_user_time_entry", {"user_id": 8, "entry_id": 4}, "/api/v1/users/8/time_entries/4"),
    ("projects_get_project_bill_rate", {"project_id": 3, "bill_rate_id": 9}, "/api/v1/projects/3/bill_rates/9"),
    ("projects_get_placeholder_resource", {"placeholder_resource_id": 6}, "/api/v1/placeholder_resources/6"),
    (
        "projects_list_placeholder_resource_assignments",
        {"placeholder_resource_id": 6},
        "/api/v1/placeholder_resources/6/assignments",
    ),
    ("projects_list_project_placeholder_resources", {"project_id": 3}, "/api/v1/projects/3/placeholder_resources"),
    (
        "projects_get_assignment_subtask",
        {"project_id": 3, "assignment_id": 4, "subtask_id": 5},
        "/api/v1/projects/3/assignments/4/subtasks/5",
    ),
    ("projects_get_project_assignment", {"project_id": 3, "assignment_id": 4}, "/api/v1/projects/3/assignments/4"),
    ("projects_get_user_assignment", {"user_id": 8, "assignment_id": 4}, "/api/v1/users/8/assignments/4"),
    ("projects_get_status_option", {"status_option_id": 2}, "/api/v1/status_options/2"),
    ("admin_get_bill_rate", {"bill_rate_id": 9}, "/api/v1/bill_rates/9"),
    ("admin_get_availability_block", {"user_id": 8, "availability_id": 2}, "/api/v1/users/8/availabilities/2"),
    ("admin_get_discipline", {"discipline_id": 2}, "/api/v1/disciplines/2"),
    ("admin_get_role", {"role_id": 2}, "/api/v1/roles/2"),
    ("admin_get_webhook", {"webhook_id": 2}, "/api/v1/webhooks/2"),
    (
        "admin_get_project_expense_item",
        {"project_id": 3, "expense_item_id": 7},
        "/api/v1/projects/3/expense_items/7",
    ),
    ("admin_get_user_expense_item", {"user_id": 8, "expense_item_id": 7}, "/api/v1/users/8/expense_items/7"),
    (
        "admin_get_project_custom_field_value",
        {"project_id": 3, "custom_field_value_id": 11},
        "/api/v1/projects/3/custom_field_values/11",
    ),
    (
        "admin_get_user_custom_field_value",
        {"user_id": 8, "custom_field_value_id": 11},
        "/api/v1/users/8/custom_field_values/11",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args", "path"), GETS)
async def test_new_gets_use_the_documented_path(inbox: Inbox, name: str, args: dict[str, Any], path: str) -> None:
    await _call(name, args)
    (req,) = inbox.requests
    assert (req.method, req.url.path) == ("GET", path)


@pytest.mark.asyncio
async def test_list_pages_and_placeholder_fields(inbox: Inbox) -> None:
    await _call("projects_list_project_bill_rates", {"project_id": 3, "page": 2, "per_page": 5})
    await _call("admin_list_bill_rates", {"page": 1, "per_page": 20})
    await _call("admin_list_availability_blocks", {"user_id": 8, "page": 3, "per_page": 10})
    await _call("projects_get_placeholder_resource", {"placeholder_resource_id": 6, "fields": "title"})
    assert [req.url.path for req in inbox.requests] == [
        "/api/v1/projects/3/bill_rates",
        "/api/v1/bill_rates",
        "/api/v1/users/8/availabilities",
        "/api/v1/placeholder_resources/6",
    ]
    assert _query(inbox.requests[0])["page"] == ["2"]
    assert _query(inbox.requests[2])["per_page"] == ["10"]
    assert _query(inbox.requests[3])["fields"] == ["title"]


@pytest.mark.asyncio
async def test_budget_item_create_sends_null_amount_and_rejects_a_bad_type(inbox: Inbox) -> None:
    await _call(
        "projects_create_project_budget_item",
        {"project_id": 3, "item_type": "Expenses", "amount": None, "category": "Travel"},
    )
    (req,) = inbox.requests
    assert (req.method, req.url.path) == ("POST", "/api/v1/projects/3/budget_items")
    assert _body(req) == {"item_type": "Expenses", "amount": None, "category": "Travel"}
    inbox.requests.clear()
    err = await _fail("projects_update_budget_item", {"budget_item_id": 4, "item_type": "Nope"})
    assert err["error"]["type"] == "invalid_request"
    assert inbox.requests == []
    err = await _fail("projects_update_project_budget_item", {"project_id": 3, "budget_item_id": 4})
    assert err["error"]["type"] == "invalid_request"


@pytest.mark.asyncio
async def test_budget_update_and_delete(inbox: Inbox) -> None:
    await _call("projects_update_budget_item", {"budget_item_id": 4, "amount": 12, "peritem_label": "mile"})
    await _call(
        "projects_update_project_budget_item",
        {"project_id": 3, "budget_item_id": 4, "assignable_id": 9, "peritem_amount": 2},
    )
    preview = await _call("projects_delete_budget_item", {"budget_item_id": 4})
    assert preview["status"] == "confirmation_required"
    assert len(inbox.requests) == 2
    await _call("projects_delete_budget_item", {"budget_item_id": 4, "confirm": True})
    assert inbox.requests[-1].method == "DELETE"
    assert inbox.requests[-1].url.path == "/api/v1/budget_items/4"


@pytest.mark.asyncio
async def test_member_replace_warns_and_delete_sends_json(inbox: Inbox) -> None:
    preview = await _call("projects_set_project_members", {"project_id": 3, "relationships": [[8, "editor"]]})
    assert preview["status"] == "confirmation_required"
    assert "Unlisted members are removed." in preview["message"]
    assert inbox.requests == []
    await _call(
        "projects_set_project_members",
        {"project_id": 3, "relationships": [[8, "editor"]], "confirm": True},
    )
    assert _body(inbox.requests[0]) == {"relationships": [[8, "editor"]]}
    assert inbox.requests[0].method == "POST"
    await _call("projects_update_project_members", {"project_id": 3, "relationships": [[8, "viewer"]]})
    assert inbox.requests[1].method == "PUT"
    preview = await _call("projects_delete_project_members", {"project_id": 3, "user_ids": [8]})
    assert preview["status"] == "confirmation_required"
    await _call("projects_delete_project_members", {"project_id": 3, "user_ids": [8], "confirm": True})
    deleted = inbox.requests[-1]
    assert deleted.method == "DELETE"
    assert deleted.url.path == "/api/v1/projects/3/project_members"
    assert _body(deleted) == {"user_ids": [8]}
    from smartsheet_rm_mcp.tools.projects import _relationship_pairs

    with pytest.raises(ToolError, match="user_id must be an integer"):
        _relationship_pairs([[True, "editor"]])
    with pytest.raises(ToolError, match="must be a string"):
        _relationship_pairs([[8, ""]])
    with pytest.raises(ToolError, match="relationships must be a list"):
        _relationship_pairs("nope")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_user_memberships_replace_and_delete(inbox: Inbox) -> None:
    preview = await _call(
        "projects_set_user_project_memberships",
        {"user_id": 8, "relationships": [[3, "viewer"]]},
    )
    assert "Unlisted members are removed." in preview["message"]
    assert inbox.requests == []
    await _call(
        "projects_set_user_project_memberships",
        {"user_id": 8, "relationships": [[3, "viewer"]], "confirm": True},
    )
    await _call("projects_update_user_project_memberships", {"user_id": 8, "relationships": [[3, "editor"]]})
    await _call(
        "projects_delete_user_project_memberships",
        {"user_id": 8, "project_ids": [3], "confirm": True},
    )
    assert inbox.requests[-1].method == "DELETE"
    assert _body(inbox.requests[-1]) == {"project_ids": [3]}
    from smartsheet_rm_mcp.tools.projects import _int_id_list

    with pytest.raises(ToolError, match="project_ids must be a list of integers"):
        _int_id_list(["3"], "project_ids")  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_bill_rate_placeholder_and_subtask_updates(inbox: Inbox) -> None:
    await _call(
        "projects_update_project_bill_rate",
        {"project_id": 3, "bill_rate_id": 9, "rate": 100, "starts_at": "2026-01-01", "user_id": 8},
    )
    assert _body(inbox.requests[0])["rate"] == 100
    err = await _fail("projects_update_project_bill_rate", {"project_id": 3, "bill_rate_id": 9})
    assert err["error"]["type"] == "invalid_request"
    await _call(
        "projects_update_placeholder_resource",
        {"placeholder_resource_id": 6, "title": "Dev", "role": "QA"},
    )
    form = inbox.requests[-1]
    assert form.method == "PUT"
    assert "application/x-www-form-urlencoded" in form.headers["content-type"]
    assert _form(form)["title"] == ["Dev"]
    assert _form(form)["role"] == ["QA"]
    err = await _fail("projects_update_placeholder_resource", {"placeholder_resource_id": 6, "title": ""})
    assert err["error"]["type"] == "invalid_request"
    await _call(
        "projects_update_assignment_subtask",
        {"project_id": 3, "assignment_id": 4, "subtask_id": 5, "description": "Done", "completed": True},
    )
    assert _body(inbox.requests[-1]) == {"description": "Done", "completed": True}
    err = await _fail(
        "projects_update_assignment_subtask",
        {"project_id": 3, "assignment_id": 4, "subtask_id": 5},
    )
    assert err["error"]["type"] == "invalid_request"


@pytest.mark.asyncio
async def test_assignment_writes_match_the_shared_update_body(inbox: Inbox) -> None:
    args = {"start_date": "2026-01-01", "percent": 0.5, "note": "move"}
    await _call("projects_update_project_assignment", {"project_id": 3, "assignment_id": 4, **args})
    await _call("projects_update_user_assignment", {"user_id": 8, "assignment_id": 4, **args})
    assert _body(inbox.requests[0]) == {"starts_at": "2026-01-01", "percent": 0.5, "note": "move"}
    assert inbox.requests[0].url.path == "/api/v1/projects/3/assignments/4"
    assert inbox.requests[1].url.path == "/api/v1/users/8/assignments/4"
    preview = await _call("projects_delete_project_assignment", {"project_id": 3, "assignment_id": 4})
    assert preview["status"] == "confirmation_required"
    await _call("projects_delete_user_assignment", {"user_id": 8, "assignment_id": 4, "confirm": True})
    assert inbox.requests[-1].method == "DELETE"
    err = await _fail("projects_update_user_assignment", {"user_id": 8, "assignment_id": 4})
    assert err["error"]["type"] == "invalid_request"


@pytest.mark.asyncio
async def test_existing_creates_send_form_bodies(inbox: Inbox) -> None:
    await _call("projects_create_placeholder_resource", {"title": "Dev", "discipline": "Design"})
    created = inbox.requests[0]
    assert created.method == "POST"
    assert created.url.path == "/api/v1/placeholder_resources"
    assert "application/x-www-form-urlencoded" in created.headers["content-type"]
    assert _form(created) == {"title": ["Dev"], "discipline": ["Design"]}
    await _call("admin_set_user_status", {"user_id": 8, "status": "wfh", "notes": "home"})
    status = inbox.requests[1]
    assert status.url.path == "/api/v1/users/8/statuses"
    assert "application/x-www-form-urlencoded" in status.headers["content-type"]
    assert _form(status)["status"] == ["WFH"]
    assert _form(status)["notes"] == ["home"]


@pytest.mark.asyncio
async def test_availability_days_are_required_and_described(inbox: Inbox) -> None:
    days = {"day0": 0, "day1": 8, "day2": 8, "day3": 8, "day4": 8, "day5": 8, "day6": 0}
    await _call("admin_create_availability_block", {"user_id": 8, **days, "starts_at": "2026-01-01"})
    assert _body(inbox.requests[0])["day1"] == 8
    assert _body(inbox.requests[0])["starts_at"] == "2026-01-01"
    await _call(
        "admin_update_availability_block", {"user_id": 8, "availability_id": 2, **days, "ends_at": "2026-02-01"}
    )
    assert inbox.requests[1].method == "PUT"
    preview = await _call("admin_delete_availability_block", {"user_id": 8, "availability_id": 2})
    assert preview["status"] == "confirmation_required"
    from smartsheet_rm_mcp.tools.admin import _availability_body

    with pytest.raises(ToolError, match="day3 must be a number"):
        _availability_body(0, 8, 8, True, 8, 8, 0, None, None)  # type: ignore[arg-type]
    assert len(inbox.requests) == 2


@pytest.mark.asyncio
async def test_report_csv_passthrough_cap_and_no_post_retry(inbox: Inbox) -> None:
    inbox.responder = lambda _request: httpx.Response(200, text="a,b\n", headers={"content-type": "text/csv"})
    result = await _call(
        "admin_export_report_csv",
        {"report_parameters": {"view": "expenses", "time_frame": "this_week", "group_by": ["project_id"]}},
    )
    assert result == {"csv": "a,b\n", "bytes": len(b"a,b\n")}
    (req,) = inbox.requests
    assert req.method == "POST"
    assert req.url.path == "/api/v1/reports/underlying_data.csv"
    assert _body(req)["view"] == "expenses"
    inbox.requests.clear()
    inbox.responder = lambda _request: httpx.Response(200, content=b"x" * (10 * 1024 * 1024 + 1))
    err = await _fail(
        "admin_export_report_csv",
        {
            "report_parameters": {
                "view": "utilization",
                "time_frame": {"from": "2026-01-01", "to": "2026-01-31"},
                "group_by": ["user_id", "week"],
                "filters": {"project_id": 3},
                "today": "2026-01-15",
                "calc_incurred_using": "confirmed",
            }
        },
    )
    assert err["error"]["type"] == "response_too_large"
    inbox.requests.clear()
    calls = {"n": 0}

    def retry(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"error": "down"})

    inbox.responder = retry
    err = await _fail(
        "admin_export_report_csv",
        {"report_parameters": {"view": "expenses", "time_frame": "last_30", "group_by": ["date"]}},
    )
    assert err["error"]["type"] == "smartsheet_rm_api_error"
    assert calls["n"] == 1
    inbox.requests.clear()
    err = await _fail("admin_export_report_csv", {"report_parameters": {"view": "nope"}})
    assert err["error"]["type"] == "invalid_request"
    assert inbox.requests == []
    err = await _fail(
        "admin_export_report_csv",
        {"report_parameters": {"view": "expenses", "time_frame": "this_week", "group_by": []}},
    )
    assert err["error"]["type"] == "invalid_request"


def _pages(page2_token: str | None, *, fail_page: int | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def responder(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            if "renew" in request.url.path:
                return httpx.Response(200, json={"id": 10, "token": "tok-test-0003"})
            return httpx.Response(200, json={"status": "deleted", "id": 9})
        page = _query(request).get("page", ["1"])[0]
        if fail_page is not None and page == str(fail_page):
            return httpx.Response(500, json={"error": "down"})
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 1, "title": "other", "token": OTHER}],
                    "paging": {
                        "next": "/api/api_tokens?page=2",
                        "previous": None,
                        "self": "/api/api_tokens",
                        "page": 1,
                    },
                },
            )
        token: Any = page2_token
        return httpx.Response(
            200,
            json={
                "data": [{"id": 9, "title": "target", "token": token}],
                "paging": {"next": None, "page": 2},
            },
        )

    return responder


@pytest.mark.asyncio
async def test_api_tokens_mask_list_and_show_a_new_token_once(inbox: Inbox, caplog: pytest.LogCaptureFixture) -> None:
    inbox.responder = lambda _request: httpx.Response(
        200, json={"data": [{"id": 9, "title": "build", "token": TOKEN}], "paging": {"next": None}}
    )
    with caplog.at_level(logging.DEBUG):
        listed = await _call("admin_list_api_tokens")
    assert listed["data"][0]["title"] == "build"
    assert listed["data"][0]["token"] == "***REDACTED***"
    assert TOKEN not in caplog.text
    inbox.requests.clear()
    preview = await _call("admin_create_api_token", {"title": "build"})
    assert preview["status"] == "confirmation_required"
    assert "Returns the new token once" in preview["message"]
    assert inbox.requests == []
    inbox.responder = lambda _request: httpx.Response(201, json={"id": 9, "title": "build", "token": TOKEN})
    with caplog.at_level(logging.DEBUG):
        created = await _call("admin_create_api_token", {"title": "build", "confirm": True})
    assert created["token"] == TOKEN
    assert TOKEN not in caplog.text
    (req,) = inbox.requests
    assert (req.method, req.url.path) == ("POST", "/api/api_tokens")
    assert _query(req)["title"] == ["build"]
    inbox.requests.clear()
    err = await _fail("admin_create_api_token", {"title": "primary", "confirm": True})
    assert err["error"]["type"] == "invalid_request"
    assert inbox.requests == []
    inbox.responder = lambda _request: httpx.Response(200, json={"id": 9, "title": "renamed", "token": TOKEN})
    updated = await _call("admin_update_api_token", {"token_id": 9, "title": "renamed"})
    assert updated["token"] == "***REDACTED***"
    assert updated["title"] == "renamed"
    assert inbox.requests[-1].method == "PUT"
    assert TOKEN not in json.dumps(updated)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("page2", "fail_page", "fragment"),
    [
        (TOKEN, None, "would revoke the token this server uses"),
        (None, None, "missing or masked"),
        ("***REDACTED***", None, "missing or masked"),
        ("", None, "missing or masked"),
        (OTHER, 2, "could not be verified"),
        (OTHER, 1, "could not be verified"),
    ],
)
async def test_token_guard_refuses_before_delete(
    inbox: Inbox,
    monkeypatch: pytest.MonkeyPatch,
    page2: str | None,
    fail_page: int | None,
    fragment: str,
) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", TOKEN)
    inbox.responder = _pages(page2, fail_page=fail_page)
    if page2 == TOKEN:
        err = await _fail("admin_delete_api_token", {"token_id": 9, "confirm": True})
    elif fail_page == 1:
        err = await _fail("admin_renew_api_token", {"token_id": 9, "confirm": True})
    else:
        err = await _fail("admin_delete_api_token", {"token_id": 9, "confirm": True})
    assert fragment in json.dumps(err)
    assert not any(req.method in {"DELETE", "POST"} for req in inbox.requests)


@pytest.mark.asyncio
async def test_token_guard_not_found_and_proceeds_when_the_value_differs(
    inbox: Inbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", TOKEN)
    inbox.responder = _pages(OTHER)
    renewed = await _call("admin_renew_api_token", {"token_id": 9, "confirm": True})
    assert renewed["token"] == "tok-test-0003"
    assert any(req.method == "POST" and req.url.path == "/api/api_tokens/renew/9" for req in inbox.requests)
    inbox.requests.clear()

    def missing(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            return httpx.Response(200, json={"status": "deleted"})
        return httpx.Response(200, json={"data": [{"id": 1, "token": OTHER}], "paging": {"next": None}})

    inbox.responder = missing
    err = await _fail("admin_delete_api_token", {"token_id": 9, "confirm": True})
    text = json.dumps(err)
    assert "not found" in text
    assert not any(req.method == "DELETE" for req in inbox.requests)
    preview = await _call("admin_delete_api_token", {"token_id": 9})
    assert preview["status"] == "confirmation_required"


@pytest.mark.asyncio
async def test_delete_error_does_not_echo_a_remembered_token(
    inbox: Inbox, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", TOKEN)

    def responder(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": 9, "token": OTHER}], "paging": {"next": None}})
        return httpx.Response(400, json={"message": f"refused {OTHER}"})

    inbox.responder = responder
    with caplog.at_level(logging.DEBUG):
        err = await _fail("admin_delete_api_token", {"token_id": 9, "confirm": True})
    assert OTHER not in json.dumps(err)
    assert OTHER not in caplog.text
    assert any(req.method == "DELETE" for req in inbox.requests)


@pytest.mark.asyncio
async def test_remaining_validation_branches(inbox: Inbox, monkeypatch: pytest.MonkeyPatch) -> None:
    from smartsheet_rm_mcp.tools.admin import _report_csv_body
    from smartsheet_rm_mcp.tools.projects import _budget_item_body, _relationship_pairs

    for bad in (
        "nope",
        {"view": "expenses", "time_frame": "nope", "group_by": ["date"]},
        {"view": "expenses", "time_frame": {"from": "2026-01-01"}, "group_by": ["date"]},
        {"view": "expenses", "time_frame": 1, "group_by": ["date"]},
        {"view": "expenses", "time_frame": "this_week", "group_by": ["date"], "filters": []},
        {"view": "expenses", "time_frame": "this_week", "group_by": ["date"], "today": 1},
        {"view": "expenses", "time_frame": "this_week", "group_by": ["date"], "calc_incurred_using": "nope"},
    ):
        with pytest.raises(ToolError):
            _report_csv_body(bad)  # type: ignore[arg-type]
    with pytest.raises(ToolError):
        _relationship_pairs([[8]])
    with pytest.raises(ToolError):
        _budget_item_body("Nope")
    err = await _fail("projects_update_budget_item", {"budget_item_id": 4})
    assert err["error"]["type"] == "invalid_request"
    err = await _fail("projects_create_project_budget_item", {"project_id": 3, "item_type": "Nope", "amount": 1})
    assert err["error"]["type"] == "invalid_request"
    await _call(
        "projects_update_project_budget_item",
        {"project_id": 3, "budget_item_id": 4, "assignable_parent_id": 1},
    )
    await _call(
        "projects_update_project_bill_rate",
        {
            "project_id": 3,
            "bill_rate_id": 9,
            "ends_at": "2026-02-01",
            "role_id": 2,
            "discipline_id": 3,
            "assignable_id": 4,
        },
    )
    await _call(
        "projects_update_placeholder_resource",
        {"placeholder_resource_id": 6, "title": "Dev", "discipline": "Design", "location": "NY"},
    )
    await _call("projects_delete_project_assignment", {"project_id": 3, "assignment_id": 4, "confirm": True})
    await _call("admin_delete_availability_block", {"user_id": 8, "availability_id": 2, "confirm": True})
    preview = await _call("projects_delete_user_assignment", {"user_id": 8, "assignment_id": 4})
    assert preview["status"] == "confirmation_required"
    preview = await _call("projects_delete_user_project_memberships", {"user_id": 8, "project_ids": [3]})
    assert preview["status"] == "confirmation_required"
    preview = await _call("admin_renew_api_token", {"token_id": 9})
    assert preview["status"] == "confirmation_required"
    err = await _fail("admin_update_api_token", {"token_id": 9, "title": "  "})
    assert err["error"]["type"] == "invalid_request"
    err = await _fail("admin_update_api_token", {"token_id": 9, "title": "primary"})
    assert err["error"]["type"] == "invalid_request"
    inbox.responder = lambda _request: httpx.Response(201, json={"id": 9})
    err = await _fail("admin_create_api_token", {"confirm": True})
    assert err["error"]["type"] == "invalid_response"
    inbox.responder = lambda _request: httpx.Response(201, json=[1])
    err = await _fail("admin_create_api_token", {"title": "build", "confirm": True})
    assert err["error"]["type"] == "invalid_response"
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", TOKEN)
    inbox.responder = _pages(OTHER)

    def empty_delete(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _pages(OTHER)(request)
        return httpx.Response(200, content=b"")

    inbox.responder = empty_delete
    deleted = await _call("admin_delete_api_token", {"token_id": 9, "confirm": True})
    assert deleted["status"] == "deleted"


@pytest.mark.asyncio
async def test_token_tools_run_on_the_gov_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RM Authentication page requires an RM token on Gov and does not drop these operations."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": [], "paging": {"next": None}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        server,
        "_client",
        SmartsheetRMClient("rm-test", GOV_BASE_URL, smartsheet_api_token="ss-test", http_client=http),
    )
    listed = await _call("admin_list_api_tokens")
    assert listed["data"] == []
    (req,) = sent
    assert req.url.host == GOV_HOST
    assert req.url.path == "/2.0/rm/api/api_tokens"


@pytest.mark.asyncio
async def test_collect_api_tokens_follows_a_same_host_link_and_rejects_bad_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", "api.rm.smartsheet.com")
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.query:
            return httpx.Response(200, json={"data": [{"id": 2, "token": OTHER}], "paging": {"next": None}})
        return httpx.Response(
            200,
            json={
                "data": [{"id": 1, "token": TOKEN}],
                "paging": {"next": "https://api.rm.smartsheet.com/api/api_tokens?page=2"},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = SmartsheetRMClient("rm-test", "https://api.rm.smartsheet.com/api/v1", http_client=http)
    found = await client.collect_api_tokens()
    assert [item["id"] for item in found] == [1, 2]
    assert sent[-1].url.host == "api.rm.smartsheet.com"
    root = SmartsheetRMClient("rm-test", "https://api.rm.smartsheet.com/api", http_client=http)
    assert root._request_base("root") == "https://api.rm.smartsheet.com/api"
    assert root._request_base("v1") == "https://api.rm.smartsheet.com/api"

    async def collect(*bodies: httpx.Response) -> None:
        queue = list(bodies)

        def respond(_request: httpx.Request) -> httpx.Response:
            return queue.pop(0)

        local = SmartsheetRMClient(
            "rm-test",
            "https://api.rm.smartsheet.com/api/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        with pytest.raises(ValueError):
            await local.collect_api_tokens()

    await collect(httpx.Response(200, text="nope"))
    await collect(httpx.Response(200, json={"paging": {"next": None}}))
    await collect(httpx.Response(200, json={"data": ["x"], "paging": {"next": None}}))
    await collect(httpx.Response(200, json={"data": [], "paging": {}}))
    await collect(httpx.Response(200, json={"data": [], "paging": {"next": 1}}))
    await collect(httpx.Response(200, json={"data": [], "paging": {"next": "  "}}))
    await collect(httpx.Response(200, json={"data": [], "paging": {"next": "https://evil.example/api/api_tokens"}}))
    loop = httpx.Response(200, json={"data": [], "paging": {"next": "/api/api_tokens?page=loop"}})
    await collect(loop, httpx.Response(200, json={"data": [], "paging": {"next": "/api/api_tokens?page=loop"}}))
    relative = [
        httpx.Response(200, json={"data": [], "paging": {"next": "api_tokens?page=2"}}),
        httpx.Response(200, json={"data": [{"id": 3, "token": TOKEN}], "paging": {"next": None}}),
    ]

    def respond_relative(_request: httpx.Request) -> httpx.Response:
        return relative.pop(0)

    followed = SmartsheetRMClient(
        "rm-test",
        "https://api.rm.smartsheet.com/api/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond_relative)),
    )
    assert [item["id"] for item in await followed.collect_api_tokens()] == [3]
    await collect(httpx.Response(200, json={"data": [], "paging": {"next": "/api/users?page=2"}}))
