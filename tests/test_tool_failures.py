"""A batch tool whose every item failed is a tool execution error (``isError: true``).

MCP spec 2026-07-28, server/tools, Error Handling: a call that fails reports it in the tool
result with ``isError: true``. The four batch tools used to return ``"status": "failed"`` as a
normal result when nothing succeeded; that path now raises a redacted ``ToolError`` ``from None``.
A partial success did real work, so it stays a normal result with per-item errors.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.common import _tool_failure
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError
from smartsheet_rm_mcp.server import create_server

_SECRET = "rm-api-token-value-for-redaction-test-0123456789"


def _api_error(path: str, method: str) -> SmartsheetRMAPIError:
    return SmartsheetRMAPIError(500, path, method, "Server error")


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    client = AsyncMock()
    monkeypatch.setattr(server, "_client", client)
    monkeypatch.setenv("SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE", "1")
    return client


async def _call(name: str, args: dict[str, Any]) -> Any:
    async with Client(create_server(profile="full")) as client:
        return await client.call_tool(name, args, raise_on_error=False)


def _setup(api: AsyncMock, tool: str, *, all_fail: bool) -> None:
    if tool == "time_fill_weekly_timesheet":
        ok: Any = {"id": 1}
        api.create_time_entry.side_effect = (
            _api_error("/time_entries", "POST") if all_fail else [ok, *[_api_error("/time_entries", "POST")] * 4]
        )
    elif tool == "time_confirm_suggested_hours":
        api.list_user_time_entries.return_value = {
            "data": [{"id": 10, "is_suggestion": True, "hours": 8.0}, {"id": 11, "is_suggestion": True, "hours": 4.0}]
        }
        api.update_time_entry.side_effect = (
            _api_error("/time_entries/10", "PUT") if all_fail else [{"id": 10}, _api_error("/time_entries/11", "PUT")]
        )
    elif tool == "time_bulk_delete_time_entries":
        api.delete_time_entry.side_effect = (
            _api_error("/time_entries/1", "DELETE") if all_fail else [{}, _api_error("/time_entries/2", "DELETE")]
        )
    else:
        api.delete_assignment.side_effect = (
            _api_error("/assignments/1", "DELETE") if all_fail else [{}, _api_error("/assignments/2", "DELETE")]
        )


_CASES = [
    (
        "time_fill_weekly_timesheet",
        {"user_id": 1, "start_date": "2026-08-10", "project_id": 100},
        "All 5 time entries failed to create; nothing was logged.",
        5,
    ),
    (
        "time_confirm_suggested_hours",
        {"user_id": 1, "from_date": "2026-08-10", "to_date": "2026-08-16"},
        "All 2 suggested entries failed to confirm; nothing was confirmed.",
        2,
    ),
    (
        "time_bulk_delete_time_entries",
        {"entry_ids": [1, 2], "confirm": True},
        "All 2 time entry deletions failed; nothing was deleted.",
        2,
    ),
    (
        "projects_bulk_delete_assignments",
        {"assignment_ids": [1, 2], "confirm": True},
        "All 2 assignment deletions failed; nothing was deleted.",
        2,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_with_every_item_failed_is_error_true(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int
) -> None:
    _setup(api, tool, all_fail=True)
    res = await _call(tool, args)
    assert res.is_error is True
    err = json.loads(res.content[0].text)["error"]  # type: ignore[union-attr]
    assert err["type"] == "batch_failed"
    assert err["message"] == message
    assert err["failed_count"] == count
    assert len(err["errors"]) == count


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_partial_success_stays_a_normal_result(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int
) -> None:
    _setup(api, tool, all_fail=False)
    res = await _call(tool, args)
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "partial_success"
    assert data["failed_count"] >= 1


def test_tool_failure_is_redacted_without_exception_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", _SECRET)
    try:
        raise ValueError(f"upstream {_SECRET}")
    except ValueError:
        with pytest.raises(ToolError) as exc_info:
            _tool_failure("batch_failed", f"failed with {_SECRET}", errors=[{"detail": f"Bearer {_SECRET}"}])
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    text = str(exc_info.value)
    assert _SECRET not in text
    payload = json.loads(text)
    assert payload["error"]["type"] == "batch_failed"
    assert payload["error"]["message"] == "failed with ***REDACTED***"
    assert payload["error"]["errors"] == [{"detail": "Bearer ***REDACTED***"}]
