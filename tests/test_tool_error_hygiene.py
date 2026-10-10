"""rm_tool error hygiene: no exception chain, and the decorator's own redaction.

The decorator builds a fresh ToolError inside each ``except`` block and raises it
*after* the block ends (``from None``), so the unredacted original exception never
rides along as ``__cause__`` or ``__context__`` (tracebacks, OpenTelemetry exception
events). Its own ``_redact_secrets`` call must scrub a secret from an unexpected
exception message before it reaches the client or the logs.
"""

from __future__ import annotations

import json
import logging

import pytest
from fastmcp.exceptions import ToolError

from smartsheet_rm_mcp.common import rm_tool
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError

_SECRET = "rm-api-token-for-hygiene-test-0123456789"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        SmartsheetRMAPIError(500, "/users", "GET", f"upstream echoed {_SECRET}"),
        RuntimeError(f"unexpected failure {_SECRET}"),
    ],
    ids=["api_error", "internal_error"],
)
async def test_decorator_suppresses_the_original_exception(exc: Exception) -> None:
    @rm_tool
    async def handler() -> str:
        raise exc

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_decorator_redacts_an_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-client exception carrying a secret is redacted in the ToolError and the logs."""
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", _SECRET)
    caplog.set_level(logging.DEBUG)

    @rm_tool
    async def handler() -> str:
        raise RuntimeError(f"connect failed for {_SECRET} with Bearer abc.def.ghi")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    text = str(exc_info.value)
    assert json.loads(text) == {
        "error": {"type": "internal", "message": "connect failed for ***REDACTED*** with Bearer ***REDACTED***"}
    }
    assert _SECRET not in caplog.text
    assert "abc.def.ghi" not in caplog.text
    assert "Tool failed with internal error" in caplog.text
    assert exc_info.value.__context__ is None
    assert _SECRET not in repr(exc_info.value.__context__)


@pytest.mark.asyncio
async def test_decorator_context_does_not_hold_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A RuntimeError bearing a token must not survive on ToolError.__context__."""
    token = "sk-live-probe-bearer-token-xyz"
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", token)

    @rm_tool
    async def handler() -> str:
        raise RuntimeError(f"boom Authorization: Bearer {token}")

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert exc_info.value.__context__ is None
    assert token not in str(exc_info.value)
    assert token not in repr(exc_info.value.__context__)


@pytest.mark.asyncio
async def test_decorator_rebuilds_a_tool_error_raised_inside_an_except() -> None:
    """A ToolError raised inside a tool's own ``except`` reaches the caller without that chain.

    rm_tool copies the payload onto a fresh ToolError, so a bare re-raise of the original
    (which carries the inner exception as ``__context__``) fails this test.
    """
    token = "sk-live-inner-context-token"
    payload = json.dumps({"error": {"type": "clone_phase_failed", "message": "redacted"}})

    @rm_tool
    async def handler() -> str:
        try:
            raise RuntimeError(f"inner Authorization: Bearer {token}")
        except RuntimeError:
            raise ToolError(payload)  # noqa: B904 - the chain is what this test checks

    with pytest.raises(ToolError) as exc_info:
        await handler()
    assert str(exc_info.value) == payload
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__context__ is None
    assert exc_info.value.__suppress_context__ is True
    assert token not in repr(exc_info.value.__context__)
