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
    # Field names from main stay alongside the fleet keys.
    assert data["days_filled"] == 5
    assert data["created_count"] == 4
    assert data["entries"] == [r["result"] for r in data["results"]]
    assert len(data["entries"]) == 4
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
    # Field name from main stays alongside the fleet keys.
    assert data["confirmed_entries"] == [{"id": 10}, {"id": 12}]


# --- Redaction, cancellation and logging on every batch tool and the clone recipe ---

_BEARER = "sk-live-abc123"
_API_TOKEN = "apitok-raw-9876543210"
_ACCESS_TOKEN = "acctok-raw-5555"
_QUERY_TOKEN = "qtok-raw-4444"
_RAW_VALUES = (_BEARER, _API_TOKEN, _ACCESS_TOKEN, _QUERY_TOKEN)


def _leaky_api_error(path: str, method: str) -> SmartsheetRMAPIError:
    """An API error whose path, detail and message all carry raw credentials."""
    return SmartsheetRMAPIError(
        401,
        f"{path}?token={_QUERY_TOKEN}&api_token={_API_TOKEN}&access_token={_ACCESS_TOKEN}",
        method,
        {
            "errors": [
                f"invalid Bearer {_BEARER}",
                f"retry with api_token={_API_TOKEN}",
                f"refresh with access_token={_ACCESS_TOKEN}",
                f"callback /cb?token={_QUERY_TOKEN}",
            ]
        },
    )


def _assert_no_raw_tokens(text: str) -> None:
    for raw in _RAW_VALUES:
        assert raw not in text


def _setup_leaky_partial(api: AsyncMock, tool: str) -> None:
    """One item succeeds and one fails with a credential-bearing API error."""
    if tool == "time_fill_weekly_timesheet":
        api.create_time_entry.side_effect = [
            {"id": 1},
            _leaky_api_error("/time_entries", "POST"),
            {"id": 3},
            {"id": 4},
            {"id": 5},
        ]
    elif tool == "time_confirm_suggested_hours":
        api.list_user_time_entries.return_value = {
            "data": [
                {"id": 10, "is_suggestion": True, "hours": 8.0, "date": "2026-08-10"},
                {"id": 11, "is_suggestion": True, "hours": 4.0, "date": "2026-08-11"},
            ]
        }
        api.update_time_entry.side_effect = [{"id": 10}, _leaky_api_error("/time_entries/11", "PUT")]
    elif tool == "time_bulk_delete_time_entries":
        api.delete_time_entry.side_effect = [{}, _leaky_api_error("/time_entries/2", "DELETE")]
    else:
        api.delete_assignment.side_effect = [{}, _leaky_api_error("/assignments/2", "DELETE")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count", "verb", "key"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_partial_api_error_item_is_redacted(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int, verb: str, key: str
) -> None:
    """A partial result is not redacted as a whole, so each API item error must be."""
    _setup_leaky_partial(api, tool)
    res = await _call(tool, args)
    assert res.is_error is False
    text = res.content[0].text  # type: ignore[union-attr]
    _assert_no_raw_tokens(text)
    data = json.loads(text)
    assert data["status"] == "partial_success"
    assert data["failed_count"] == 1
    assert data[verb] >= 1
    item_error = data["errors"][0]["error"]
    assert item_error["type"] == "smartsheet_rm_api_error"
    assert item_error["status_code"] == 401
    for field in ("message", "path", "detail"):
        rendered = json.dumps(item_error[field])
        _assert_no_raw_tokens(rendered)
        assert "***REDACTED***" in rendered
    assert "returned 401" in item_error["message"]
    assert item_error["detail"]["errors"] == [
        "invalid ***REDACTED***",
        "retry with api_token=***REDACTED***",
        "refresh with access_token=***REDACTED***",
        "callback /cb?token=***REDACTED***",
    ]
    assert item_error["path"].endswith("?token=***REDACTED***&api_token=***REDACTED***&access_token=***REDACTED***")


def _setup_leaky_all_failed(api: AsyncMock, tool: str) -> None:
    """Every item fails with a credential-bearing API error."""
    if tool == "time_fill_weekly_timesheet":
        api.create_time_entry.side_effect = _leaky_api_error("/time_entries", "POST")
    elif tool == "time_confirm_suggested_hours":
        api.list_user_time_entries.return_value = {
            "data": [
                {"id": 10, "is_suggestion": True, "hours": 8.0, "date": "2026-08-10"},
                {"id": 11, "is_suggestion": True, "hours": 4.0, "date": "2026-08-11"},
            ]
        }
        api.update_time_entry.side_effect = _leaky_api_error("/time_entries/10", "PUT")
    elif tool == "time_bulk_delete_time_entries":
        api.delete_time_entry.side_effect = _leaky_api_error("/time_entries/1", "DELETE")
    else:
        api.delete_assignment.side_effect = _leaky_api_error("/assignments/1", "DELETE")


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args", "message", "count", "verb", "key"), _CASES, ids=[c[0] for c in _CASES])
async def test_batch_all_failed_api_errors_are_redacted(
    api: AsyncMock, tool: str, args: dict[str, Any], message: str, count: int, verb: str, key: str
) -> None:
    """The all-failed ToolError carries no api_token, access_token, ?token= or Bearer value."""
    _setup_leaky_all_failed(api, tool)
    res = await _call(tool, args)
    assert res.is_error is True
    text = res.content[0].text  # type: ignore[union-attr]
    _assert_no_raw_tokens(text)
    err = json.loads(text)["error"]
    assert err["type"] == "batch_failed"
    assert err["failed_count"] == count
    for item in err["errors"]:
        rendered = json.dumps(item["error"])
        _assert_no_raw_tokens(rendered)
        assert "***REDACTED***" in item["error"]["path"]
        assert "***REDACTED***" in json.dumps(item["error"]["detail"])


def test_api_error_to_dict_redacts_string_detail() -> None:
    err = SmartsheetRMAPIError(401, "/x", "GET", f"invalid Bearer {_BEARER}").to_dict()
    assert err["detail"] == "invalid ***REDACTED***"
    assert err["path"] == "/x"
    assert err["message"] == "Smartsheet RM API GET /x returned 401"


@pytest.mark.asyncio
async def test_fill_weekly_cancelled_error_propagates(api: AsyncMock) -> None:
    """asyncio.CancelledError must escape fill_weekly's loop, not become a failed item."""
    calls: list[int] = []

    async def side_effect(payload: dict[str, Any], *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        return {"id": len(calls)}

    api.create_time_entry.side_effect = side_effect
    with pytest.raises(asyncio.CancelledError):
        await server.rm_fill_weekly_timesheet(1, "2026-08-10", project_id=100)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_confirm_suggested_cancelled_error_propagates(api: AsyncMock) -> None:
    """asyncio.CancelledError must escape confirm's loop, not become a failed item."""
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
            raise asyncio.CancelledError()
        return {"id": entry_id}

    api.update_time_entry.side_effect = side_effect
    with pytest.raises(asyncio.CancelledError):
        await server.rm_confirm_suggested_hours(1, "2026-08-10", "2026-08-16")
    assert calls == [10, 11]


def _assert_item_log_is_clean(caplog: pytest.LogCaptureFixture) -> None:
    """The item failure is logged once, redacted, with no exc_info and no traceback."""
    _assert_no_raw_tokens(caplog.text)
    assert "Traceback" not in caplog.text
    item_records = [r for r in caplog.records if "***REDACTED***" in r.getMessage()]
    assert item_records
    for record in item_records:
        assert record.exc_info is None
        assert record.exc_text is None


@pytest.mark.asyncio
async def test_fill_weekly_non_api_error_is_redacted_without_traceback(
    api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[int] = []

    async def side_effect(payload: dict[str, Any], *a: Any, **k: Any) -> dict[str, Any]:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError(f"upstream rejected Authorization: Bearer {_BEARER}")
        return {"id": len(calls)}

    api.create_time_entry.side_effect = side_effect
    caplog.set_level(logging.DEBUG)
    res = await _call(
        "time_fill_weekly_timesheet",
        {"user_id": 1, "start_date": "2026-08-10", "project_id": 100},
    )
    assert res.is_error is False
    text = res.content[0].text  # type: ignore[union-attr]
    _assert_no_raw_tokens(text)
    data = json.loads(text)
    assert data["filled_count"] == 4
    err = data["errors"][0]["error"]
    assert err["type"] == "internal"
    assert err["message"] == "upstream rejected Authorization: ***REDACTED***"
    _assert_item_log_is_clean(caplog)


@pytest.mark.asyncio
async def test_confirm_suggested_non_api_error_is_redacted_without_traceback(
    api: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    api.list_user_time_entries.return_value = {
        "data": [
            {"id": 10, "is_suggestion": True, "hours": 8.0, "date": "2026-08-10"},
            {"id": 11, "is_suggestion": True, "hours": 4.0, "date": "2026-08-11"},
        ]
    }

    async def side_effect(entry_id: Any, *a: Any, **k: Any) -> dict[str, Any]:
        if entry_id == 11:
            raise RuntimeError(f"upstream rejected Authorization: Bearer {_BEARER}")
        return {"id": entry_id}

    api.update_time_entry.side_effect = side_effect
    caplog.set_level(logging.DEBUG)
    res = await _call(
        "time_confirm_suggested_hours",
        {"user_id": 1, "from_date": "2026-08-10", "to_date": "2026-08-16"},
    )
    assert res.is_error is False
    text = res.content[0].text  # type: ignore[union-attr]
    _assert_no_raw_tokens(text)
    data = json.loads(text)
    assert data["confirmed_count"] == 1
    err = data["errors"][0]["error"]
    assert err["type"] == "internal"
    assert err["message"] == "upstream rejected Authorization: ***REDACTED***"
    _assert_item_log_is_clean(caplog)


def _setup_clone(api: AsyncMock, phase_error: Exception) -> None:
    api.get_project.return_value = {"id": 100, "name": "Template", "starts_at": "2026-08-01"}
    api.list_project_phases.return_value = {
        "data": [{"name": "Phase 1", "starts_at": "2026-08-01", "ends_at": "2026-08-10"}]
    }
    api.create_project.return_value = {"id": 777, "name": "Partial Clone"}
    api.create_project_phase.side_effect = phase_error


_CLONE_ERRORS = [
    _leaky_api_error("/projects/777/phases", "POST"),
    RuntimeError(f"transport broken Authorization: Bearer {_BEARER}"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase_error", _CLONE_ERRORS, ids=["api_error", "internal_error"])
async def test_clone_phase_failure_raises_after_except_and_names_project(
    api: AsyncMock, caplog: pytest.LogCaptureFixture, phase_error: Exception
) -> None:
    """The tool's own ToolError (before rm_tool) has no chain, names the project, and is redacted."""
    _setup_clone(api, phase_error)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ToolError) as exc_info:
        await server.rm_clone_project_schedule.__wrapped__(100, "Clone", new_start_date="2026-09-01")
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    text = str(exc_info.value)
    _assert_no_raw_tokens(text)
    err = json.loads(text)["error"]
    assert err["type"] == "clone_phase_failed"
    assert err["project_id"] == 777
    assert str(777) in err["message"]
    assert "left in place" in err["message"]
    assert "***REDACTED***" in json.dumps(err["error"])
    api.delete_project.assert_not_called()
    _assert_no_raw_tokens(caplog.text)
    assert "Traceback" not in caplog.text
    if isinstance(phase_error, RuntimeError):
        assert err["error"] == {"type": "internal", "message": "transport broken Authorization: ***REDACTED***"}
        _assert_item_log_is_clean(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase_error", _CLONE_ERRORS, ids=["api_error", "internal_error"])
async def test_clone_phase_failure_through_rm_tool_has_no_chain(api: AsyncMock, phase_error: Exception) -> None:
    _setup_clone(api, phase_error)
    with pytest.raises(ToolError) as exc_info:
        await server.rm_clone_project_schedule(100, "Clone", new_start_date="2026-09-01")
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    _assert_no_raw_tokens(str(exc_info.value))
    assert json.loads(str(exc_info.value))["error"]["project_id"] == 777
