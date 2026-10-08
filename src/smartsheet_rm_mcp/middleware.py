"""Hierarchical middleware for FastMCP 4 Server Composition.

Provides:
- ParentAuditMiddleware: Gateway-level execution timing, audit logging, and secret scrubbing.
- ReadOnlyGateMiddleware: Gateway-level read-only enforcement driven by the ``readOnlyHint`` annotation.
- TimeDomainGuardMiddleware: Child domain guardrail validating time tracking parameters and the bulk gate.
- ProjectsDomainGuardMiddleware: Child domain guardrail validating project parameters and the bulk gate.
- AdminDomainGuardMiddleware: Child domain guardrail validating administrative and report queries.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Collection
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools import Tool

from smartsheet_rm_mcp.config import bulk_destructive_allowed, readonly_enabled
from smartsheet_rm_mcp.errors import SafetyViolationError, redact_secrets
from smartsheet_rm_mcp.profiles import is_read_only_tool

logger = logging.getLogger(__name__)

# Synthetic discovery proxies that may wrap a real tool name in arguments.
_SEARCH_PROXY_TOOLS = {"call_tool"}


def _resolve_effective_tool_name(context: MiddlewareContext) -> str:
    """Return the tool name under enforcement, unwrapping search ``call_tool`` proxies."""
    message = context.message
    tool_name = getattr(message, "name", "") if message else ""
    if tool_name not in _SEARCH_PROXY_TOOLS or message is None:
        return str(tool_name)

    arguments = getattr(message, "arguments", None)
    if not isinstance(arguments, dict):
        return str(tool_name)

    nested = arguments.get("name")
    if isinstance(nested, str) and nested:
        return nested
    return str(tool_name)


class ParentAuditMiddleware(Middleware):
    """Global gateway middleware logging operation timing, method, and sanitizing errors."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Intercept request, track timing, log lifecycle, and redact sensitive output."""
        start_time = time.perf_counter()
        method = getattr(context, "method", "unknown")
        tool_name = getattr(context.message, "name", None) if context.message else None
        target = f"{method}:{tool_name}" if tool_name else str(method)

        logger.debug("→ MCP Request received: %s", target)
        try:
            result = await call_next(context)
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            logger.debug("← MCP Request completed: %s in %.2fms", target, duration_ms)
            return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            sanitized_msg = redact_secrets(str(exc))
            logger.error("✗ MCP Request failed: %s in %.2fms: %s", target, duration_ms, sanitized_msg)
            if str(exc) != sanitized_msg:
                try:
                    sanitized_exc: Exception = type(exc)(sanitized_msg)
                except Exception:
                    sanitized_exc = RuntimeError(sanitized_msg)
                sanitized_exc.__traceback__ = exc.__traceback__
                raise sanitized_exc from None
            raise


def _read_only_refusal(name: str) -> SafetyViolationError:
    """Build the read-only refusal for ``name`` (a FastMCP ``ToolError``: ``isError: true``)."""
    return SafetyViolationError(
        f"Server running in read-only mode; tool '{name}' blocked (not annotated readOnlyHint=True)."
    )


class ReadOnlyGateMiddleware(Middleware):
    """Refuse calls to real tools not annotated ``readOnlyHint=True`` while read-only is on.

    Read-only is on when ``SMARTSHEET_RM_READONLY=1`` (or ``settings.READONLY``) or the gate is
    built with ``enforce=True`` (the ``readonly`` profile). Classification uses only the MCP
    ``readOnlyHint`` annotation of the resolved tool: a missing annotation or
    ``readOnlyHint`` other than ``True`` is a write and is refused (fail closed).

    A name that is not a tool on this server is not refused here: it passes through so
    FastMCP returns its normal ``Unknown tool`` error, and a typo is never reported as an
    existing tool. ``catalog`` holds the profile's tool names captured before the
    read-only filter hid the writes, so a hidden real tool is still refused, not unknown.
    The ``call_tool`` proxy gets the same check: it is unwrapped only when it is a real
    tool on this server (Tool Search attached).
    """

    def __init__(self, enforce: bool = False, catalog: Collection[str] = ()) -> None:
        self.enforce = enforce
        self.catalog = frozenset(catalog)

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Block non-read-only tool calls before they reach tool handlers.

        1. No serving FastMCP context: the annotation cannot be read, so refuse.
        2. Outer name is ``call_tool`` but not a tool on this server (no Tool Search):
           pass through, so FastMCP reports ``Unknown tool: 'call_tool'``.
        3. Otherwise resolve the effective name (``call_tool`` unwrapped to the proxied
           tool) and refuse it only if it is a real tool not annotated read-only.
        """
        readonly = self.enforce or readonly_enabled()
        if not readonly or getattr(context, "method", None) != "tools/call":
            return await call_next(context)

        fastmcp_context = context.fastmcp_context
        if fastmcp_context is None:
            raise _read_only_refusal(_resolve_effective_tool_name(context))
        server = fastmcp_context.fastmcp

        outer_name = getattr(context.message, "name", "")
        if outer_name in _SEARCH_PROXY_TOOLS:
            _, proxy_known = await self._lookup(server, outer_name)
            if not proxy_known:
                return await call_next(context)

        effective_name = _resolve_effective_tool_name(context)
        tool, known = await self._lookup(server, effective_name)
        if known and not is_read_only_tool(tool):
            logger.warning("Blocked non-read-only tool call in read-only mode: %s", effective_name)
            raise _read_only_refusal(effective_name)
        return await call_next(context)

    async def _lookup(self, server: FastMCP, name: str) -> tuple[Tool | None, bool]:
        """Resolve ``name`` with the public ``get_tool``; return ``(tool, is_real_tool)``.

        ``get_tool`` returns ``None`` for an unknown name and for a tool hidden by the
        read-only filter; the recorded ``catalog`` tells the two apart.
        """
        tool = await server.get_tool(name)
        return tool, tool is not None or name in self.catalog


def _bulk_gate(context: MiddlewareContext, local_name: str, domain: str) -> None:
    """Refuse a bulk destructive call unless ``SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1``.

    Inside a mounted child the tool name is the local name (``bulk_delete_time_entries``);
    the namespaced name is checked too so the guard also holds if mounted differently.
    """
    tool_name = getattr(context.message, "name", "") if context.message else ""
    if tool_name in (local_name, f"{domain}_{local_name}") and not bulk_destructive_allowed():
        raise SafetyViolationError(
            f"Bulk destructive operations disabled; tool '{domain}_{local_name}' blocked. "
            "Set SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1."
        )


class TimeDomainGuardMiddleware(Middleware):
    """Child domain guard middleware for the time tracking sub-server."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Validate time domain arguments, enforce query bounds, and apply the bulk gate."""
        if getattr(context, "method", None) == "tools/call":
            _bulk_gate(context, "bulk_delete_time_entries", "time")
            arguments = getattr(context.message, "arguments", {}) or {}
            hours = arguments.get("hours")
            if hours is not None and isinstance(hours, (int, float)) and (hours < 0 or hours > 24):
                raise ValueError(f"Logged hours {hours} out of realistic range (0.0 to 24.0 hours).")
            daily_hours = arguments.get("daily_hours")
            if (
                daily_hours is not None
                and isinstance(daily_hours, (int, float))
                and (daily_hours < 0 or daily_hours > 24)
            ):
                raise ValueError(f"Daily hours {daily_hours} out of realistic range (0.0 to 24.0 hours).")
        return await call_next(context)


class ProjectsDomainGuardMiddleware(Middleware):
    """Child domain guard middleware for the projects and scheduling sub-server."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Validate projects domain arguments, enforce constraints, and apply the bulk gate."""
        if getattr(context, "method", None) == "tools/call":
            _bulk_gate(context, "bulk_delete_assignments", "projects")
            arguments = getattr(context.message, "arguments", {}) or {}
            name = arguments.get("name")
            if name is not None and isinstance(name, str) and not name.strip():
                raise ValueError("Project name must not be empty.")
            target_project_name = arguments.get("target_project_name")
            if (
                target_project_name is not None
                and isinstance(target_project_name, str)
                and not target_project_name.strip()
            ):
                raise ValueError("Target project name must not be empty.")
        return await call_next(context)


class AdminDomainGuardMiddleware(Middleware):
    """Child domain guard middleware for administrative and capacity operations."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Validate admin domain arguments and enforce query limits."""
        if getattr(context, "method", None) == "tools/call":
            arguments = getattr(context.message, "arguments", {}) or {}
            per_page = arguments.get("per_page")
            if per_page is not None and isinstance(per_page, int) and per_page > 1000:
                raise ValueError(f"per_page {per_page} exceeds maximum allowable batch size of 1000.")
        return await call_next(context)
