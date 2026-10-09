"""Batch tools: every item is attempted; all-failed is isError true; shapes are uniform.

MCP spec 2026-07-28, server/tools, Error Handling: a call that fails reports it in the tool
result with ``isError: true``. The four batch tools raise a redacted ``ToolError`` when nothing
succeeded. A partial success did real work, so it stays a normal result with per-item errors.
Each failed item's ``error`` carries at least a ``message`` key. ``asyncio.CancelledError`` is
not caught (it is a ``BaseException``), so cancellation stops the batch instead of becoming a
failed item.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.common import _tool_failure
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError
from smartsheet_rm_mcp.server import create_server

_SECRET = "rm-api-token-value-for-redaction-test-0123456789"
_RAW_TOKEN = "sk-live-abc123"


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


def _setup(api: AsyncMock, tool: str, *, mode: str) -> None:
    """mode: all_fail | partial | all_ok"""
    if tool == "time_fill_weekly_timesheet":
        ok: Any = {"id": 1}
        if mode == "all_fail":
            api.create_time_entry.side_effect = _api_error("/time_entries", "POST")
        elif mode == "all_ok":
            api.create_time_entry.side_effect = None
            api.create_time_entry.return_value = ok
        else:
            api.create_time_entry.side_effect = [ok, *[_api_error("/time_entries", "POST")] * 4]
    elif tool == "time_confirm_suggested_hours":
        api.list_user_time_entries.return_value = {
            "data": [
                {"id": 10, "is_suggestion": True, "hours": 8.0, "date": "2026-08-10"},
                {"id": 11, "is_suggestion": True, "hours": 4.0, "date": "2026-08-11"},
            ]
        }
        if mode == "all_fail":
            api.update_time_entry.side_effect = _api_error("/time_entries/10", "PUT")
        elif mode == "all_ok":
            api.update_time_entry.side_effect = [{"id": 10}, {"id": 11}]
        else:
            api.update_time_entry.side_effect = [
                {"id": 10},
                _api_error("/time_entries/11", "PUT"),
            ]
    elif tool == "time_bulk_delete_time_entries":
        if mode == "all_fail":
            api.delete_time_entry.side_effect = _api_error("/time_entries/1", "DELETE")
        elif mode == "all_ok":
            api.delete_time_entry.side_effect = None
            api.delete_time_entry.return_value = {}
        else:
            api.delete_time_entry.side_effect = [{}, _api_error("/time_entries/2", "DELETE")]
    else:
        if mode == "all_fail":
            api.delete_assignment.side_effect = _api_error("/assignments/1", "DELETE")
        elif mode == "all_ok":
            api.delete_assignment.side_effect = None
            api.delete_assignment.return_value = {}
        else:
            api.delete_assignment.side_effect = [{}, _api_error("/assignments/2", "DELETE")]


_CASES = [
    (
        "time_fill_weekly_timesheet",
        {"user_id": 1, "start_date": "2026-08-10", "project_id": 100},
        "All 5 time entries failed to create; nothing was logged.",
        5,
        "filled_count",
        "date",
    ),
    (
        "time_confirm_suggested_hours",
        {"user_id": 1, "from_date": "2026-08-10", "to_date": "2026-08-16"},
        "All 2 suggested entries failed to confirm; nothing was confirmed.",
        2,
        "confirmed_count",
        "id",
    ),
    (
        "time_bulk_delete_time_entries",
        {"entry_ids": [1, 2], "confirm": True},
        "All 2 time entry deletions failed; nothing was deleted.",
        2,
        "deleted_count",
        "id",
    ),
    (
        "projects_bulk_delete_assignments",
        {"assignment_ids": [1, 2], "confirm": True},
        "All 2 assignment deletions failed; nothing was deleted.",
        2,
        "deleted_count",
        "id",
    ),
]


def _assert_failed_item(item: dict[str, Any], *, key: str) -> None:
    assert item["status"] == "failed"
    assert key in item
    item_error = item["error"]
    assert isinstance(item_error.get("message"), str) and item_error["message"]
    assert _SECRET not in item_error["message"]
    assert _SECRET not in json.dumps(item_error)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count", "verb", "key"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_with_every_item_failed_is_error_true(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int, verb: str, key: str
) -> None:
    _setup(api, tool, mode="all_fail")
    res = await _call(tool, args)
    assert res.is_error is True
    err = json.loads(res.content[0].text)["error"]  # type: ignore[union-attr]
    assert err["type"] == "batch_failed"
    assert err["message"] == message
    assert err["failed_count"] == count
    assert len(err["errors"]) == count
    for item in err["errors"]:
        _assert_failed_item(item, key=key)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count", "verb", "key"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_partial_success_stays_a_normal_result(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int, verb: str, key: str
) -> None:
    _setup(api, tool, mode="partial")
    res = await _call(tool, args)
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "partial_success"
    assert data["failed_count"] >= 1
    assert data[verb] >= 1
    assert isinstance(data["results"], list) and data["results"]
    assert isinstance(data["errors"], list) and data["errors"]
    for item in data["results"]:
        assert item["status"] in {"created", "confirmed", "deleted"}
        assert key in item
        assert "result" in item
    for item in data["errors"]:
        _assert_failed_item(item, key=key)
        assert item[key] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count", "verb", "key"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_all_succeed_is_a_normal_result(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int, verb: str, key: str
) -> None:
    _setup(api, tool, mode="all_ok")
    res = await _call(tool, args)
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "success"
    assert data["failed_count"] == 0
    assert data["errors"] == []
    assert data[verb] >= 1
    assert len(data["results"]) == data[verb]
    for item in data["results"]:
        assert item["status"] in {"created", "confirmed", "deleted"}
        assert key in item
        assert "result" in item


def test_tool_failure_is_redacted_without_exception_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", _SECRET)
    with pytest.raises(ToolError) as exc_info:
        _tool_failure("batch_failed", f"failed with {_SECRET}", errors=[{"detail": f"Bearer {_SECRET}"}])
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    text = str(exc_info.value)
    assert _SECRET not in text
    payload = json.loads(text)
    assert payload["error"]["type"] == "batch_failed"
    assert payload["error"]["message"] == "failed with ***REDACTED***"
    assert payload["error"]["errors"] == [{"detail": "Bearer ***REDACTED***"}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "mock_attr"),
    [
        ("time_bulk_delete_time_entries", {"entry_ids": [1, 2, 3], "confirm": True}, "delete_time_entry"),
        ("projects_bulk_delete_assignments", {"assignment_ids": [1, 2, 3], "confirm": True}, "delete_assignment"),
    ],
    ids=["time_bulk_delete", "projects_bulk_delete"],
)
async def test_bulk_delete_non_api_error_continues_and_redacts(
    api: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tool: str,
    args: dict[str, Any],
    mock_attr: str,
) -> None:
    """A plain Exception on one item is recorded; remaining items still run; secrets are redacted."""
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", _SECRET)
    calls: list[Any] = []

    async def side_effect(item_id: Any, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(item_id)
        if item_id == 2:
            raise RuntimeError(f"upstream rejected Authorization: Bearer {_RAW_TOKEN}")
        return {"deleted": True}

    getattr(api, mock_attr).side_effect = side_effect
    caplog.set_level(logging.DEBUG)

    res = await _call(tool, args)
    assert res.is_error is False
    text = res.content[0].text  # type: ignore[union-attr]
    assert _RAW_TOKEN not in text
    assert _SECRET not in text
    data = json.loads(text)
    assert data["status"] == "partial_success"
    assert data["deleted_count"] == 2
    assert data["failed_count"] == 1
    assert [r["id"] for r in data["results"]] == [1, 3]
    assert calls == [1, 2, 3]
    err = data["errors"][0]["error"]
    assert err["type"] == "internal"
    assert isinstance(err.get("message"), str) and err["message"]
    assert "***REDACTED***" in err["message"]
    assert "Authorization:" in err["message"]
    assert _RAW_TOKEN not in err["message"]
    assert _RAW_TOKEN not in caplog.text
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "mock_attr"),
    [
        ("time_bulk_delete_time_entries", {"entry_ids": [1, 2, 3], "confirm": True}, "delete_time_entry"),
        ("projects_bulk_delete_assignments", {"assignment_ids": [1, 2, 3], "confirm": True}, "delete_assignment"),
    ],
    ids=["time_bulk_delete", "projects_bulk_delete"],
)
async def test_bulk_delete_network_timeout_continues(
    api: AsyncMock, tool: str, args: dict[str, Any], mock_attr: str
) -> None:
    """A transport timeout on one item is a failed item; later items still run."""
    calls: list[Any] = []

    async def side_effect(item_id: Any, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(item_id)
        if item_id == 2:
            raise httpx.ReadTimeout("timed out")
        return {"ok": item_id}

    getattr(api, mock_attr).side_effect = side_effect
    res = await _call(tool, args)
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "partial_success"
    assert data["deleted_count"] == 2
    assert data["failed_count"] == 1
    assert [r["id"] for r in data["results"]] == [1, 3]
    assert data["results"][0]["result"] == {"ok": 1}
    assert data["errors"][0]["id"] == 2
    assert data["errors"][0]["error"]["type"] == "internal"
    assert "timed out" in data["errors"][0]["error"]["message"]
    assert calls == [1, 2, 3]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "args", "mock_attr"),
    [
        ("time_bulk_delete_time_entries", {"entry_ids": [1, 2], "confirm": True}, "delete_time_entry"),
        ("projects_bulk_delete_assignments", {"assignment_ids": [1, 2], "confirm": True}, "delete_assignment"),
    ],
    ids=["time_bulk_delete", "projects_bulk_delete"],
)
async def test_bulk_delete_api_error_item_carries_message_key(
    api: AsyncMock, tool: str, args: dict[str, Any], mock_attr: str
) -> None:
    """API failures in a partial batch expose a non-empty message on each failed item."""
    getattr(api, mock_attr).side_effect = [
        {},
        SmartsheetRMAPIError(404, "/x/2", "DELETE", "Not found"),
    ]
    res = await _call(tool, args)
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "partial_success"
    item_error = data["errors"][0]["error"]
    assert item_error["type"] == "smartsheet_rm_api_error"
    assert isinstance(item_error.get("message"), str) and item_error["message"]
    assert "returned 404" in item_error["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_fn", "mock_attr", "ids"),
    [
        (server.rm_bulk_delete_time_entries, "delete_time_entry", [1, 2, 3]),
        (server.rm_bulk_delete_assignments, "delete_assignment", [1, 2, 3]),
    ],
    ids=["time_bulk_delete", "projects_bulk_delete"],
)
async def test_bulk_delete_cancelled_error_propagates(
    api: AsyncMock,
    tool_fn: Any,
    mock_attr: str,
    ids: list[int],
) -> None:
    """asyncio.CancelledError must escape the batch loop, not become a failed item."""
    calls: list[Any] = []

    async def side_effect(item_id: Any, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(item_id)
        if item_id == 2:
            raise asyncio.CancelledError()
        return {"deleted": True}

    getattr(api, mock_attr).side_effect = side_effect

    with pytest.raises(asyncio.CancelledError):
        await tool_fn(ids, confirm=True)

    assert calls == [1, 2]


@pytest.mark.asyncio
async def test_fill_weekly_network_error_continues(api: AsyncMock) -> None:
    calls: list[int] = []

    async def side_effect(payload: dict[str, Any], *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 2:
            raise httpx.ReadTimeout("timed out")
        return {"id": len(calls)}

    api.create_time_entry.side_effect = side_effect
    res = await _call(
        "time_fill_weekly_timesheet",
        {"user_id": 1, "start_date": "2026-08-10", "project_id": 100},
    )
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "partial_success"
    assert data["filled_count"] == 4
    assert data["failed_count"] == 1
    assert len(calls) == 5
    assert data["errors"][0]["status"] == "failed"
    assert data["errors"][0]["error"]["type"] == "internal"
    assert "timed out" in data["errors"][0]["error"]["message"]
    assert "date" in data["errors"][0]


@pytest.mark.asyncio
async def test_confirm_suggested_network_error_continues(api: AsyncMock) -> None:
    api.list_user_time_entries.return_value = {
        "data": [
            {"id": 10, "is_suggestion": True, "hours": 8.0, "date": "2026-08-10"},
            {"id": 11, "is_suggestion": True, "hours": 4.0, "date": "2026-08-11"},
            {"id": 12, "is_suggestion": True, "hours": 4.0, "date": "2026-08-12"},
        ]
    }
    calls: list[Any] = []

    async def side_effect(entry_id: Any, *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(entry_id)
        if entry_id == 11:
            raise httpx.ReadTimeout("timed out")
        return {"id": entry_id}

    api.update_time_entry.side_effect = side_effect
    res = await _call(
        "time_confirm_suggested_hours",
        {"user_id": 1, "from_date": "2026-08-10", "to_date": "2026-08-16"},
    )
    assert res.is_error is False
    data = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert data["status"] == "partial_success"
    assert data["confirmed_count"] == 2
    assert data["failed_count"] == 1
    assert calls == [10, 11, 12]
    assert [r["id"] for r in data["results"]] == [10, 12]
    assert data["errors"][0]["id"] == 11
    assert data["errors"][0]["error"]["type"] == "internal"
