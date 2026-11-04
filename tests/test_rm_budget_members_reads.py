"""Mock-level tests for RM assignables, budget_items, members, memberships, and time_entry_categories.

Paths and query params follow Smartsheet Resource Management OpenAPI:
https://developers.smartsheet.com/_spec/api/resource_management/openapi.json
UI: https://developers.smartsheet.com/api/resource_management/openapi
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.client import SmartsheetRMClient
from smartsheet_rm_mcp.config import settings
from smartsheet_rm_mcp.server import create_server


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
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
        return httpx.Response(200, json={"data": [], "paging": {"page": 1}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=http))
    yield requests


async def _call(name: str, args: dict[str, Any] | None = None) -> Any:
    res = await create_server(profile="full").call_tool(name, args or {})
    assert not res.is_error
    return json.loads(res.content[0].text)  # type: ignore[union-attr]


def _q(req: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(req.url.query.decode() if isinstance(req.url.query, bytes) else (req.url.query or ""))


@pytest.mark.asyncio
async def test_list_assignables_get(sent: list[httpx.Request]) -> None:
    await _call("projects_list_assignables", {"page": 2, "per_page": 50})
    (req,) = sent
    assert (req.method, req.url.path) == ("GET", "/api/v1/assignables")
    assert _q(req)["page"] == ["2"]
    assert _q(req)["per_page"] == ["50"]


@pytest.mark.asyncio
async def test_get_assignable_get(sent: list[httpx.Request]) -> None:
    await _call("projects_get_assignable", {"assignable_id": 42})
    (req,) = sent
    assert (req.method, req.url.path) == ("GET", "/api/v1/assignables/42")


@pytest.mark.asyncio
async def test_list_project_budget_items_requires_item_type(sent: list[httpx.Request]) -> None:
    await _call("projects_list_project_budget_items", {"project_id": 9, "item_type": "TimeFeesDays"})
    (req,) = sent
    assert (req.method, req.url.path) == ("GET", "/api/v1/projects/9/budget_items")
    assert _q(req)["item_type"] == ["TimeFeesDays"]


@pytest.mark.asyncio
async def test_get_budget_item_and_project_budget_item(sent: list[httpx.Request]) -> None:
    await _call("projects_get_budget_item", {"budget_item_id": 7})
    await _call("projects_get_project_budget_item", {"project_id": 9, "budget_item_id": 7})
    assert [(r.method, r.url.path) for r in sent] == [
        ("GET", "/api/v1/budget_items/7"),
        ("GET", "/api/v1/projects/9/budget_items/7"),
    ]


@pytest.mark.asyncio
async def test_list_project_members_membership_filter(sent: list[httpx.Request]) -> None:
    await _call(
        "projects_list_project_members",
        {"project_id": 9, "membership": "editor", "page": 1, "per_page": 20},
    )
    (req,) = sent
    assert (req.method, req.url.path) == ("GET", "/api/v1/projects/9/project_members")
    assert _q(req)["membership"] == ["editor"]


@pytest.mark.asyncio
async def test_relationships_members_and_memberships(sent: list[httpx.Request]) -> None:
    await _call("projects_list_relationships_project_members", {"membership": "viewer"})
    await _call(
        "projects_list_relationships_project_memberships",
        {"user_ids": "1,2", "membership": "member"},
    )
    assert [(r.method, r.url.path) for r in sent] == [
        ("GET", "/api/v1/relationships/project_members"),
        ("GET", "/api/v1/relationships/project_memberships"),
    ]
    assert _q(sent[0])["membership"] == ["viewer"]
    assert _q(sent[1])["user_ids"] == ["1,2"]


@pytest.mark.asyncio
async def test_list_user_project_memberships_date_filters(sent: list[httpx.Request]) -> None:
    await _call(
        "projects_list_user_project_memberships",
        {"user_id": 5, "from_date": "2026-01-01", "to_date": "2026-01-31", "membership": "member"},
    )
    (req,) = sent
    assert (req.method, req.url.path) == ("GET", "/api/v1/users/5/project_memberships")
    q = _q(req)
    assert q["from"] == ["2026-01-01"]
    assert q["to"] == ["2026-01-31"]
    assert q["membership"] == ["member"]


@pytest.mark.asyncio
async def test_time_entry_categories(sent: list[httpx.Request]) -> None:
    await _call("time_list_time_entry_categories", {"page": 1, "per_page": 20})
    await _call(
        "time_list_project_time_entry_categories",
        {"project_id": 9, "item_type": "TimeFees"},
    )
    assert [(r.method, r.url.path) for r in sent] == [
        ("GET", "/api/v1/time_entry_categories"),
        ("GET", "/api/v1/projects/9/time_entry_categories"),
    ]
    assert _q(sent[1])["item_type"] == ["TimeFees"]
