"""Hierarchical middleware for FastMCP 4 Server Composition.

Provides:
- ParentAuditMiddleware: Gateway-level execution timing, audit logging, and secret scrubbing.
- ReadOnlyGateMiddleware: Gateway-level read-only enforcement when SMARTSHEET_RM_READONLY=1.
- TimeDomainGuardMiddleware: Child domain guardrail validating time tracking parameters.
- ProjectsDomainGuardMiddleware: Child domain guardrail validating project & assignment parameters.
- AdminDomainGuardMiddleware: Child domain guardrail validating administrative and report queries.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from smartsheet_rm_mcp.config import settings
from smartsheet_rm_mcp.errors import redact_secrets

logger = logging.getLogger(__name__)

# Mutating operation prefixes across the Smartsheet RM tools
_MUTATING_PREFIXES = (
    "rm_create_",
    "rm_update_",
    "rm_delete_",
    "rm_bulk_delete_",
    "rm_lock_",
    "rm_set_",
    "rm_fill_",
    "rm_confirm_",
    "rm_reconcile_",
    "rm_clone_",
    "time_create_",
    "time_update_",
    "time_delete_",
    "time_bulk_delete_",
    "time_lock_",
    "time_fill_",
    "time_confirm_",
    "time_reconcile_",
    "projects_create_",
    "projects_update_",
    "projects_delete_",
    "projects_bulk_delete_",
    "projects_clone_",
    "admin_create_",
    "admin_update_",
    "admin_delete_",
    "admin_set_",
)


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
                sanitized_exc = type(exc)(sanitized_msg)
                sanitized_exc.__traceback__ = exc.__traceback__
                raise sanitized_exc from None
            raise


class ReadOnlyGateMiddleware(Middleware):
    """Gateway-level middleware enforcing read-only operation when SMARTSHEET_RM_READONLY=1."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Reject mutating operations when server runs in read-only mode."""
        is_readonly = settings.READONLY or os.environ.get("SMARTSHEET_RM_READONLY", "").strip() == "1"
        if is_readonly and getattr(context, "method", None) == "tools/call":
            tool_name = getattr(context.message, "name", "") if context.message else ""
            if any(tool_name.startswith(p) for p in _MUTATING_PREFIXES):
                logger.warning("Blocked mutating tool call in read-only mode: %s", tool_name)
                raise PermissionError(
                    f"Server is in read-only mode (SMARTSHEET_RM_READONLY=1). Tool '{tool_name}' is blocked."
                )
        return await call_next(context)


class TimeDomainGuardMiddleware(Middleware):
    """Child domain guard middleware for the time tracking sub-server."""

    async def on_message(
        self,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Any],
    ) -> Any:
        """Validate time domain arguments and enforce query bounds."""
        if getattr(context, "method", None) == "tools/call":
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
        """Validate projects domain arguments and enforce constraints."""
        if getattr(context, "method", None) == "tools/call":
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
