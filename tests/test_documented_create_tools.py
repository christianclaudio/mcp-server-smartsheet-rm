"""Tests for the time-entry and assignment create tools on documented nested routes.

Routes and bodies follow Smartsheet's Resource Management API reference:
https://developers.smartsheet.com/_spec/api/resource_management/openapi.json
(CreateUserTimeEntry, CreateProjectTimeEntry, and the project and user assignment creates).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.client import SmartsheetRMClient
from smartsheet_rm_mcp.config import settings
from smartsheet_rm_mcp.errors import SafetyViolationError
from smartsheet_rm_mcp.profiles import PROFILES
from smartsheet_rm_mcp.server import create_server

NEW_TOOLS = {
    "time_create_user_time_entry": "timesheets",
    "time_create_project_time_entry": "timesheets",
    "projects_create_project_assignment": "staffing",
    "projects_create_user_assignment": "staffing",
}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """Offline client that records every request and echoes the JSON body back with an id."""
    for var in (
        "SMARTSHEET_RM_PROFILE",
        "SMARTSHEET_RM_READONLY",
        "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE",
        "SMARTSHEET_RM_ENABLE_TOOL_SEARCH",
        "SMARTSHEET_RM_ENABLE_CODE_MODE",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "READONLY", False)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": 1, **json.loads(request.content or b"{}")})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=http))
    yield requests


async def _call(name: str, args: dict[str, Any]) -> Any:
    res = await create_server(profile="full").call_tool(name, args)
    assert not res.is_error
    return json.loads(res.content[0].text)  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_user_time_entry_posts_documented_route(sent: list[httpx.Request]) -> None:
    """Minimal call: required fields only, user_id repeated in the body."""
    out = await _call(
        "time_create_user_time_entry", {"user_id": 5, "assignable_id": 9, "date": "2026-10-19", "hours": 7.5}
    )
    (req,) = sent
    assert (req.method, req.url.path) == ("POST", "/api/v1/users/5/time_entries")
    assert json.loads(req.content) == {"user_id": 5, "assignable_id": 9, "date": "2026-10-19", "hours": 7.5}
    assert out["id"] == 1


@pytest.mark.asyncio
async def test_project_time_entry_uses_project_as_assignable(sent: list[httpx.Request]) -> None:
    """assignable_id is the URL project; optional fields are sent only when given."""
    await _call(
        "time_create_project_time_entry",
        {
            "project_id": 42,
            "user_id": 5,
            "date": "2026-10-19",
            "hours": 2,
            "task": "Design",
            "notes": "Kickoff",
            "bill_rate_id": 3,
        },
    )
    (req,) = sent
    assert (req.method, req.url.path) == ("POST", "/api/v1/projects/42/time_entries")
    assert json.loads(req.content) == {
        "user_id": 5,
        "assignable_id": 42,
        "date": "2026-10-19",
        "hours": 2,
        "task": "Design",
        "notes": "Kickoff",
        "bill_rate_id": 3,
    }


@pytest.mark.asyncio
async def test_user_time_entry_optional_fields(sent: list[httpx.Request]) -> None:
    """task, notes and bill_rate_id pass through on the user route too."""
    await _call(
        "time_create_user_time_entry",
        {
            "user_id": 5,
            "assignable_id": 9,
            "date": "2026-10-19",
            "hours": 1,
            "task": "t",
            "notes": "n",
            "bill_rate_id": 4,
        },
    )
    body = json.loads(sent[0].content)
    assert (body["task"], body["notes"], body["bill_rate_id"]) == ("t", "n", 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["time_create_user_time_entry", "time_create_project_time_entry"])
async def test_time_entry_hours_guard(sent: list[httpx.Request], name: str) -> None:
    """The time domain guard rejects more than 24 hours before any request is sent."""
    args: dict[str, Any] = {"user_id": 5, "date": "2026-10-19", "hours": 25}
    args |= {"assignable_id": 9} if name == "time_create_user_time_entry" else {"project_id": 9}
    with pytest.raises(ToolError, match="out of realistic range"):
        await create_server(profile="full").call_tool(name, args)
    assert sent == []


@pytest.mark.asyncio
async def test_project_assignment_posts_documented_route(sent: list[httpx.Request]) -> None:
    """Defaults to percent mode and omits unset optional fields."""
    await _call(
        "projects_create_project_assignment",
        {"project_id": 42, "user_id": 5, "start_date": "2026-11-02", "end_date": "2026-11-27", "percent": 0.5},
    )
    (req,) = sent
    assert (req.method, req.url.path) == ("POST", "/api/v1/projects/42/assignments")
    assert json.loads(req.content) == {
        "user_id": 5,
        "starts_at": "2026-11-02",
        "ends_at": "2026-11-27",
        "allocation_mode": "percent",
        "percent": 0.5,
    }


@pytest.mark.asyncio
async def test_user_assignment_posts_documented_route(sent: list[httpx.Request]) -> None:
    """Every optional field passes through and allocation_mode is sent as given."""
    await _call(
        "projects_create_user_assignment",
        {
            "user_id": 5,
            "assignable_id": 77,
            "start_date": "2026-11-02",
            "end_date": "2026-11-27",
            "allocation_mode": "fixed_hours",
            "hours_per_day": 4,
            "fixed_hours": 40,
            "description": "Discovery",
            "status_option_id": 2,
            "bill_rate_id": 3,
        },
    )
    (req,) = sent
    assert (req.method, req.url.path) == ("POST", "/api/v1/users/5/assignments")
    assert json.loads(req.content) == {
        "assignable_id": 77,
        "starts_at": "2026-11-02",
        "ends_at": "2026-11-27",
        "allocation_mode": "fixed_hours",
        "hours_per_day": 4,
        "fixed_hours": 40,
        "description": "Discovery",
        "status_option_id": 2,
        "bill_rate_id": 3,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["projects_create_project_assignment", "projects_create_user_assignment"])
async def test_assignment_allocation_mode_names_both_fixed_spellings(sent: list[httpx.Request], name: str) -> None:
    """The param description lists `fixed` and `fixed_hours`, and `fixed` is sent unchanged."""
    tools = {t.name: t for t in await create_server(profile="full").list_tools()}
    description = tools[name].parameters["properties"]["allocation_mode"]["description"]
    assert "`fixed`" in description
    assert "`fixed_hours`" in description
    args: dict[str, Any] = {"user_id": 5, "start_date": "2026-11-02", "end_date": "2026-11-27"}
    args |= {"project_id": 42} if name == "projects_create_project_assignment" else {"assignable_id": 77}
    await _call(name, args | {"allocation_mode": "fixed", "fixed_hours": 40})
    (req,) = sent
    body = json.loads(req.content)
    assert (body["allocation_mode"], body["fixed_hours"]) == ("fixed", 40)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["projects_create_project_assignment", "projects_create_user_assignment"])
@pytest.mark.parametrize("mode", [{}, {"allocation_mode": "percent"}])
async def test_assignment_percent_mode_requires_percent(
    sent: list[httpx.Request], name: str, mode: dict[str, Any]
) -> None:
    """Percent mode, given or by default, without percent is an isError result and sends nothing."""
    args: dict[str, Any] = {"user_id": 5, "start_date": "2026-11-02", "end_date": "2026-11-27"}
    args |= {"project_id": 42} if name == "projects_create_project_assignment" else {"assignable_id": 77}
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(name, args | mode, raise_on_error=False)
    assert res.is_error is True
    assert json.loads(res.content[0].text) == {  # type: ignore[union-attr]
        "error": {
            "type": "invalid_request",
            "message": "percent is required when allocation_mode is percent (the default)",
        }
    }
    assert sent == []
    tools = {t.name: t for t in await create_server(profile="full").list_tools()}
    props = tools[name].parameters["properties"]
    assert "`percent` is then required" in props["allocation_mode"]["description"]
    assert "Required when allocation_mode is `percent`" in props["percent"]["description"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["projects_create_project_assignment", "projects_create_user_assignment"])
@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("hours_per_day", "hours_per_day is required when allocation_mode is hours_per_day"),
        ("fixed", "fixed_hours is required when allocation_mode is fixed"),
        ("fixed_hours", "fixed_hours is required when allocation_mode is fixed_hours"),
    ],
)
async def test_assignment_mode_requires_its_value(
    sent: list[httpx.Request], name: str, mode: str, message: str
) -> None:
    """hours_per_day and the fixed mode without their value are an isError result and send nothing.

    A value for another mode does not count: percent alone does not satisfy hours_per_day or fixed.
    """
    args: dict[str, Any] = {"user_id": 5, "start_date": "2026-11-02", "end_date": "2026-11-27", "percent": 0.5}
    args |= {"project_id": 42} if name == "projects_create_project_assignment" else {"assignable_id": 77}
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(name, args | {"allocation_mode": mode}, raise_on_error=False)
    assert res.is_error is True
    assert json.loads(res.content[0].text) == {  # type: ignore[union-attr]
        "error": {"type": "invalid_request", "message": message}
    }
    assert sent == []
    tools = {t.name: t for t in await create_server(profile="full").list_tools()}
    description = tools[name].parameters["properties"]["allocation_mode"]["description"]
    assert "`hours_per_day` is then required" in description
    assert "`fixed_hours` is then required" in description


@pytest.mark.asyncio
async def test_new_tools_are_safe_writes_in_job_profiles(sent: list[httpx.Request]) -> None:
    """Each new tool is a non-destructive, non-idempotent write listed in its job profile."""
    tools = {t.name: t for t in await create_server(profile="full").list_tools()}
    for name, profile in NEW_TOOLS.items():
        ann = tools[name].annotations
        assert ann is not None
        assert (ann.readOnlyHint, ann.destructiveHint, ann.idempotentHint) == (False, False, False)
        assert name in (PROFILES[profile].tools or frozenset())
        assert name in {t.name for t in await create_server(profile=profile).list_tools()}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(NEW_TOOLS))
async def test_new_tools_refused_in_readonly(
    monkeypatch: pytest.MonkeyPatch, sent: list[httpx.Request], name: str
) -> None:
    """SMARTSHEET_RM_READONLY=1 hides and refuses every new write tool."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full")
    assert name not in {t.name for t in await app.list_tools()}
    with pytest.raises(SafetyViolationError, match=name):
        await app.call_tool(name, {})
    assert sent == []
