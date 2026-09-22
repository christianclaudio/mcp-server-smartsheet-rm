"""Common utilities, decorators, client resolution, and logging for Smartsheet RM MCP."""

from __future__ import annotations

import functools
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from typing import Any

from smartsheet_rm_mcp.client import DEFAULT_BASE_URL, SmartsheetRMClient
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError, redact_secrets

logger = logging.getLogger("smartsheet_rm_mcp")

_client: SmartsheetRMClient | None = None
_HEADER_CLIENT_CACHE: dict[tuple[str, str], SmartsheetRMClient] = {}


class StructuredJSONFormatter(logging.Formatter):
    """JSON formatter for enterprise log aggregators (Datadog/CloudWatch/Splunk)."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "name": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "tool_name"):
            log_obj["mcp_tool"] = record.tool_name
        if hasattr(record, "duration_ms"):
            log_obj["duration_ms"] = record.duration_ms
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj)


def configure_logging() -> None:
    """Configure logging format based on SMARTSHEET_RM_LOG_FORMAT."""
    log_format = os.environ.get("SMARTSHEET_RM_LOG_FORMAT", "").lower()
    if log_format == "json":
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJSONFormatter())
        logging.root.handlers = [handler]
        logging.root.setLevel(logging.INFO)


_redact_secrets = redact_secrets


def _invalid_request(message: str) -> str:
    """Return uniform structured error response for invalid requests."""
    return json.dumps({"error": {"type": "invalid_request", "message": message}})


def _destructive_gate(confirm: bool, action_name: str) -> str | None:
    """Enforce explicit user confirmation for destructive actions."""
    if not confirm:
        return _invalid_request(
            f"Action '{action_name}' is destructive and requires explicit confirmation. Pass confirm=True to execute."
        )
    return None


async def get_client(ctx: Any | None = None) -> SmartsheetRMClient:
    """Retrieve or construct the SmartsheetRMClient instance.

    Checks request context for per-request credentials (headers:
    auth or x-smartsheet-rm-token, x-smartsheet-rm-base-url), falling back to
    SMARTSHEET_RM_API_TOKEN and SMARTSHEET_RM_BASE_URL.
    Dynamically inspects server module for mock client injection during tests.
    """
    global _client
    srv = sys.modules.get("smartsheet_rm_mcp.server")
    if srv is not None and hasattr(srv, "_client"):
        _client = srv._client

    if ctx is not None:
        raw_headers: dict[str, Any] = {}
        if hasattr(ctx, "request_context") and ctx.request_context:
            raw_headers = getattr(ctx.request_context, "headers", {}) or {}
        elif isinstance(ctx, dict):
            raw_headers = ctx.get("headers", {})

        headers = {k.lower(): str(v) for k, v in raw_headers.items() if v is not None}
        req_token = headers.get("x-smartsheet-rm-token") or headers.get("auth")
        req_base_url = headers.get("x-smartsheet-rm-base-url") or os.environ.get(
            "SMARTSHEET_RM_BASE_URL", DEFAULT_BASE_URL
        )

        if req_token:
            cache_key = (req_token, req_base_url)
            if cache_key not in _HEADER_CLIENT_CACHE:
                if len(_HEADER_CLIENT_CACHE) >= 100:
                    oldest_key = next(iter(_HEADER_CLIENT_CACHE))
                    old_c = _HEADER_CLIENT_CACHE.pop(oldest_key)
                    await old_c.aclose()
                _HEADER_CLIENT_CACHE[cache_key] = SmartsheetRMClient(req_token, req_base_url)
            return _HEADER_CLIENT_CACHE[cache_key]

    if _client is None:
        token = os.environ.get("SMARTSHEET_RM_API_TOKEN", "").strip()
        base_url = os.environ.get("SMARTSHEET_RM_BASE_URL", DEFAULT_BASE_URL).strip()
        if not token:
            raise ValueError("SMARTSHEET_RM_API_TOKEN environment variable or request 'auth' header must be set")
        _client = SmartsheetRMClient(token, base_url)
    return _client


def rm_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that wraps MCP tools with structured error handling, secret redaction, and timing."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        start_t = time.perf_counter()
        try:
            result: str = await fn(*args, **kwargs)
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.info("Tool executed successfully", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            return result
        except SmartsheetRMAPIError as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error("Tool failed with API error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms})
            err = e.to_dict()
            if isinstance(err.get("detail"), str):
                err["detail"] = _redact_secrets(err["detail"])
            return json.dumps({"error": err})
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_t) * 1000, 2)
            logger.error(
                "Tool failed with internal error", extra={"tool_name": fn.__name__, "duration_ms": duration_ms}
            )
            msg = _redact_secrets(str(e))
            return json.dumps({"error": {"type": "internal", "message": msg}})

    return wrapper
