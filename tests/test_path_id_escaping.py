"""Caller-supplied IDs stay inside one URL path segment for every ID-based client wrapper."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from smartsheet_rm_mcp.client import SmartsheetRMClient


def _client() -> tuple[SmartsheetRMClient, list[httpx.Request]]:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": []})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SmartsheetRMClient(api_token="test-token", http_client=http), sent


CASES: list[tuple[str, Callable[[SmartsheetRMClient], Awaitable[Any]], bytes]] = [
    ("static path unchanged", lambda c: c.list_assignables(), b"/api/v1/assignables"),
    ("int id unchanged", lambda c: c.get_assignable(42), b"/api/v1/assignables/42"),
    ("slash", lambda c: c.get_assignable("a/b"), b"/api/v1/assignables/a%2Fb"),
    ("query", lambda c: c.get_project("7?include=x"), b"/api/v1/projects/7%3Finclude%3Dx"),
    ("fragment", lambda c: c.delete_role("x#y"), b"/api/v1/roles/x%23y"),
    ("dot climb", lambda c: c.get_project_budget_item("1", "../2"), b"/api/v1/projects/1/budget_items/..%2F2"),
    ("pre-encoded", lambda c: c.get_user("%2e%2e"), b"/api/v1/users/%252e%252e"),
    (
        "nested climb",
        lambda c: c.list_user_project_memberships("5/../6"),
        b"/api/v1/users/5%2F..%2F6/project_memberships",
    ),
    (
        "three ids",
        lambda c: c.delete_subtask("1", "2", "3/4"),
        b"/api/v1/projects/1/assignments/2/subtasks/3%2F4",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "call", "raw_path"), CASES, ids=[c[0] for c in CASES])
async def test_id_is_one_encoded_segment(
    label: str, call: Callable[[SmartsheetRMClient], Awaitable[Any]], raw_path: bytes
) -> None:
    client, sent = _client()
    await call(client)
    (req,) = sent
    assert req.url.host == "api.rm.smartsheet.com"
    assert req.url.raw_path == raw_path
    assert req.url.query == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["", ".", ".."])
async def test_empty_and_dot_ids_are_refused(bad: str) -> None:
    client, sent = _client()
    with pytest.raises(ValueError, match="Invalid path ID"):
        await client.get_assignable(bad)
    with pytest.raises(ValueError, match="Invalid path ID"):
        await client.get_project_budget_item("1", bad)
    assert sent == []
