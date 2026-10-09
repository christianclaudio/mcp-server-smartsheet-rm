"""Input validation failures are tool execution errors; the confirm two-step is not.

MCP spec 2026-07-28, server/tools, Error Handling: input validation errors are tool
execution errors, "reported in tool results with `isError: true`", so the model can
correct the call. A destructive call without ``confirm=True`` is the designed two-step:
it changes nothing and returns a normal result telling the caller to re-call.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from smartsheet_rm_mcp import server
from smartsheet_rm_mcp.common import _destructive_gate, _invalid_request, rm_tool
from smartsheet_rm_mcp.server import create_server

_SECRET = "rm-api-token-value-for-redaction-test-0123456789"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """A Resource Management client the validation and confirm paths must never reach."""
    client = AsyncMock()
    monkeypatch.setattr(server, "_client", client)
    return client


@pytest.mark.asyncio
async def test_bad_argument_is_error_true_with_redacted_text(monkeypatch: pytest.MonkeyPatch, api: AsyncMock) -> None:
    """Over the wire, a bad argument comes back as isError: true with secrets redacted."""
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", _SECRET)
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool(
            "time_fill_weekly_timesheet",
            {"user_id": 1, "start_date": f"{_SECRET}"},
            raise_on_error=False,
        )
    assert res.is_error is True
    text = res.content[0].text  # type: ignore[union-attr]
    payload = json.loads(text)
    assert payload["error"]["type"] == "invalid_request"
    assert payload["error"]["message"].startswith("Invalid start_date")
    assert _SECRET not in text
    assert "***REDACTED***" in text
    api.list_user_assignments.assert_not_called()


@pytest.mark.asyncio
async def test_missing_update_fields_is_error_true(api: AsyncMock) -> None:
    """An update with nothing to change is a failed call, not a successful result."""
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool("time_update_time_entry", {"entry_id": 1}, raise_on_error=False)
    assert res.is_error is True
    payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert payload == {"error": {"type": "invalid_request", "message": "No update fields provided"}}
    api.update_time_entry.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_prompt_is_a_normal_result(api: AsyncMock) -> None:
    """The confirm two-step stays isError: false with a clear re-call message, and changes nothing."""
    async with Client(create_server(profile="full")) as client:
        res = await client.call_tool("time_delete_time_entry", {"entry_id": 7}, raise_on_error=False)
    assert res.is_error is False
    payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert payload == {
        "status": "confirmation_required",
        "executed": False,
        "message": (
            "Action 'time_delete_time_entry(7)' is destructive and was not executed. "
            "Re-call this tool with confirm=true to proceed."
        ),
    }
    api.delete_time_entry.assert_not_called()


def test_invalid_request_raises_redacted_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reverting the helper to return a string, or dropping its redaction, fails here."""
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", _SECRET)
    try:
        raise ValueError(_SECRET)
    except ValueError:
        with pytest.raises(ToolError) as exc_info:
            _invalid_request(f"bad value {_SECRET}")
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    assert json.loads(str(exc_info.value)) == {
        "error": {"type": "invalid_request", "message": "bad value ***REDACTED***"}
    }


@pytest.mark.asyncio
async def test_rm_tool_passes_tool_error_through_unchanged() -> None:
    """rm_tool must not rewrap a validation ToolError as an internal error."""

    @rm_tool
    async def handler() -> str:
        _invalid_request("x is required")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert json.loads(str(exc_info.value))["error"] == {"type": "invalid_request", "message": "x is required"}


def test_destructive_gate_is_not_an_error_payload() -> None:
    payload = json.loads(_destructive_gate(False, "thing(1)") or "")
    assert "error" not in payload
    assert payload["executed"] is False
    assert _destructive_gate(True, "thing(1)") is None
