"""Common utilities, decorators, client resolution, and logging for Smartsheet RM MCP."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import Any, NoReturn

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from smartsheet_rm_mcp.client import (
    DEFAULT_BASE_URL,
    SmartsheetRMClient,
    _validate_base_url,
)
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError, redact_secrets

ANNOTATION_READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
ANNOTATION_DESTRUCTIVE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=False,
    open_world_hint=True,
)
ANNOTATION_IDEMPOTENT = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)
ANNOTATION_WRITE_SAFE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=False,
    open_world_hint=True,
)

logger = logging.getLogger("smartsheet_rm_mcp")

_client: SmartsheetRMClient | None = None
_HEADER_CLIENT_CACHE: dict[tuple[str, str], SmartsheetRMClient] = {}
_CACHE_LOCK: asyncio.Lock = asyncio.Lock()


class StructuredJSONFormatter(logging.Formatter):
    """JSON formatter for enterprise log aggregators (Datadog/CloudWatch/Splunk)."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, str | float | None] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": _redact_secrets(record.getMessage()),
        }
        if hasattr(record, "tool_name"):
            log_obj["mcp_tool"] = str(getattr(record, "tool_name"))
        if hasattr(record, "duration_ms"):
            log_obj["duration_ms"] = float(getattr(record, "duration_ms"))
        if record.exc_info:
            log_obj["exception"] = _redact_secrets(self.formatException(record.exc_info))
        return json.dumps(log_obj)


def configure_logging() -> None:
    """Configure logging format based on SMARTSHEET_RM_LOG_FORMAT."""
    log_format = os.environ.get("SMARTSHEET_RM_LOG_FORMAT", "").lower()
    if log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJSONFormatter())
        logging.root.handlers = [handler]


_redact_secrets = redact_secrets


def _invalid_request(message: str) -> NoReturn:
    """Raise an input validation failure as a tool execution error.

    The MCP spec lists input validation errors among tool execution errors, reported in the
    tool result with ``isError: true`` so the model can correct the call. This raises FastMCP
    ``ToolError`` with the redacted ``{"error": {"type": "invalid_request", ...}}`` JSON, the same
    path ``rm_tool`` uses for API and internal failures, which re-raises it unchanged.
    """
    raise ToolError(
        json.dumps({"error": {"type": "invalid_request", "message": _redact_secrets(message)}}, indent=2)
    ) from None


def _tool_failure(error_type: str, message: str, **details: Any) -> NoReturn:
    """Raise a failed tool call as a tool execution error (``isError: true``).

    For failures the tool detects itself after its API calls returned, such as a batch in which
    every item failed. Raises FastMCP ``ToolError`` with the ``{"error": {"type": ..., "message":
    ...}}`` JSON, redacted as a whole document like ``rm_tool``'s API errors, ``from None``.
    ``rm_tool`` re-raises it unchanged.
    """
    payload: dict[str, Any] = {"type": error_type, "message": message, **details}
    raise ToolError(_redact_secrets(json.dumps({"error": payload}, indent=2))) from None


def _destructive_gate(confirm: bool, action_name: str) -> str | None:
    """Enforce explicit user confirmation for destructive tools.

    Without confirmation, returns the confirm two-step prompt as a normal tool result
    (``isError: false``): the tool made no change and tells the caller to re-call with
    ``confirm=true``. That is the designed behavior, not a failed call. Returns None if confirmed.
    """
    if not confirm:
        return json.dumps(
            {
                "status": "confirmation_required",
                "executed": False,
                "message": (
                    f"Action '{action_name}' is destructive and was not executed. "
                    "Re-call this tool with confirm=true to proceed."
                ),
            },
            indent=2,
        )
    return None


async def get_client(ctx: Any | None = None) -> SmartsheetRMClient:
    """Retrieve or construct the SmartsheetRMClient instance.

    Checks request context for per-request credentials (headers:
    auth or x-smartsheet-rm-token, x-smartsheet-rm-base-url), falling back to
    SMARTSHEET_RM_API_TOKEN and SMARTSHEET_RM_BASE_URL. Base URL overrides must
    name a host in SMARTSHEET_RM_ALLOWED_HOSTS (default api.rm.smartsheet.com).
    Dynamically inspects server module for mock client injection during tests.
    """
    global _client
    srv = sys.modules.get("smartsheet_rm_mcp.server")
    if srv is not None and hasattr(srv, "_client"):
        injected = getattr(srv, "_client")
        if injected is not _client:
            _client = injected

    raw_headers: dict[str, Any] = {}
    if ctx is not None:
        if hasattr(ctx, "request_context") and ctx.request_context:
            raw_headers = getattr(ctx.request_context, "headers", {}) or {}
        elif isinstance(ctx, dict):
            raw_headers = ctx.get("headers", {})
    else:
        try:
            from fastmcp.server.dependencies import get_http_headers

            raw_headers = get_http_headers(include_all=True) or {}
        except Exception:  # pragma: no cover
            raw_headers = {}

    if raw_headers:
        headers = {k.lower(): str(v) for k, v in raw_headers.items() if v is not None}
        req_token = headers.get("x-smartsheet-rm-token") or headers.get("auth")
        raw_base_url = headers.get("x-smartsheet-rm-base-url") or os.environ.get(
            "SMARTSHEET_RM_BASE_URL", DEFAULT_BASE_URL
        )
        req_base_url = _validate_base_url(raw_base_url, check_dns=False)

        if req_token:
            cache_key = (req_token, req_base_url)
            async with _CACHE_LOCK:
                if cache_key not in _HEADER_CLIENT_CACHE:
                    if len(_HEADER_CLIENT_CACHE) >= 100:
                        oldest_key = next(iter(_HEADER_CLIENT_CACHE))
                        old_c = _HEADER_CLIENT_CACHE.pop(oldest_key)
                        await old_c.aclose()
                    _HEADER_CLIENT_CACHE[cache_key] = SmartsheetRMClient(req_token, req_base_url)
                return _HEADER_CLIENT_CACHE[cache_key]

    if _client is None:
        token = os.environ.get("SMARTSHEET_RM_API_TOKEN", "").strip()
        base_url = _validate_base_url(
            os.environ.get("SMARTSHEET_RM_BASE_URL", DEFAULT_BASE_URL).strip(),
            check_dns=False,
        )

        if not token:
            raise ValueError("SMARTSHEET_RM_API_TOKEN environment variable or request 'auth' header must be set")
        _client = SmartsheetRMClient(token, base_url)
        if srv is not None and hasattr(srv, "_client"):
            setattr(srv, "_client", _client)
    return _client


get_rm_client = get_client


def rm_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that wraps MCP tools with structured error handling, secret redaction, and timing.

    A failed call raises FastMCP ``ToolError`` carrying the redacted ``{"error": ...}`` JSON,
    so the client receives a ``tools/call`` result with ``isError: true``. It is raised
    ``from None`` so the unredacted original exception does not ride along as the cause.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        start_t = time.perf_counter()
        try:
            result: str = await fn(*args, **kwargs)
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info("Tool executed successfully", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            return result
        except ToolError:
            # Already a redacted tool execution error (``_invalid_request``); keep its payload.
            raise
        except SmartsheetRMAPIError as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error("Tool failed with API error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            err_doc = json.dumps({"error": e.to_dict()})
            raise ToolError(_redact_secrets(err_doc)) from None
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(
                "Tool failed with internal error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms}
            )
            msg = _redact_secrets(str(e))
            raise ToolError(json.dumps({"error": {"type": "internal", "message": msg}})) from None

    return wrapper
