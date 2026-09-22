"""FastMCP Server for Smartsheet Resource Management (10,000ft API).

Full REST API surface covering:
1. Time Tracking & Timesheets
2. Projects & Phases
3. Assignments & Allocations
4. Users, Roles & Disciplines
5. Clients & Contacts
6. Leaves & Holidays
7. Expense Tracking
8. Tags & Custom Fields
9. Composite Workflow Recipes

Environment variables controlling tool registration:
  SMARTSHEET_RM_PROFILE                  - Tool subset: time, projects, admin, full (default: full).
  SMARTSHEET_RM_READONLY=1               - When set, only tools annotated read_only_hint=True are registered.
  SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 - Required to register rm_bulk_delete_time_entries and rm_bulk_delete_assignments.
  SMARTSHEET_RM_ENABLE_TOOL_SEARCH=1     - Opt-in dynamic tool search transform.

Filter application order: annotations -> profile -> readonly -> bulk-destructive gating.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import FunctionTool

from smartsheet_rm_mcp import __version__
from smartsheet_rm_mcp.client import DEFAULT_BASE_URL, SmartsheetRMClient
from smartsheet_rm_mcp.common import (
    _HEADER_CLIENT_CACHE,
    StructuredJSONFormatter,
    _client,
    _destructive_gate,
    _invalid_request,
    _redact_secrets,
    configure_logging,
    get_client,
    rm_tool,
)
from smartsheet_rm_mcp.config import settings
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError, redact_secrets
from smartsheet_rm_mcp.middleware import ParentAuditMiddleware, ReadOnlyGateMiddleware
from smartsheet_rm_mcp.tools import (
    admin_server,
    create_admin_server,
    create_projects_server,
    create_time_server,
    project_staffing_plan,
    projects_server,
    rm_bulk_delete_assignments,
    rm_bulk_delete_time_entries,
    rm_capabilities_resource,
    rm_clone_project_schedule,
    rm_confirm_suggested_hours,
    rm_create_approval,
    rm_create_assignment,
    rm_create_assignment_subtask,
    rm_create_client,
    rm_create_client_contact,
    rm_create_custom_field,
    rm_create_discipline,
    rm_create_expense,
    rm_create_expense_category,
    rm_create_holiday,
    rm_create_leave_type,
    rm_create_placeholder_resource,
    rm_create_project,
    rm_create_project_phase,
    rm_create_role,
    rm_create_tag,
    rm_create_time_entry,
    rm_create_user,
    rm_create_user_bill_rate,
    rm_create_webhook,
    rm_delete_approval,
    rm_delete_assignment,
    rm_delete_assignment_subtask,
    rm_delete_client,
    rm_delete_client_contact,
    rm_delete_custom_field,
    rm_delete_discipline,
    rm_delete_expense,
    rm_delete_expense_category,
    rm_delete_holiday,
    rm_delete_leave_type,
    rm_delete_placeholder_resource,
    rm_delete_project,
    rm_delete_project_phase,
    rm_delete_role,
    rm_delete_tag,
    rm_delete_time_entry,
    rm_delete_user,
    rm_delete_webhook,
    rm_fill_weekly_timesheet,
    rm_get_assignment,
    rm_get_client,
    rm_get_custom_field,
    rm_get_expense,
    rm_get_holiday,
    rm_get_leave_type,
    rm_get_project,
    rm_get_project_phase,
    rm_get_report_rows,
    rm_get_report_totals,
    rm_get_time_entry,
    rm_get_user,
    rm_get_user_availability,
    rm_get_user_statuses,
    rm_get_user_utilization,
    rm_list_approvals,
    rm_list_assignment_subtasks,
    rm_list_assignments,
    rm_list_client_contacts,
    rm_list_clients,
    rm_list_custom_field_values,
    rm_list_custom_fields,
    rm_list_disciplines,
    rm_list_expense_categories,
    rm_list_expenses,
    rm_list_holidays,
    rm_list_leave_types,
    rm_list_placeholder_resources,
    rm_list_project_phases,
    rm_list_project_users,
    rm_list_projects,
    rm_list_roles,
    rm_list_status_options,
    rm_list_tags,
    rm_list_time_entries,
    rm_list_user_bill_rates,
    rm_list_user_suggestions,
    rm_list_users,
    rm_list_webhooks,
    rm_lock_timesheet,
    rm_quickstart_resource,
    rm_reconcile_and_submit_week,
    rm_set_custom_field_values,
    rm_set_user_status,
    rm_update_assignment,
    rm_update_client,
    rm_update_custom_field,
    rm_update_discipline,
    rm_update_expense,
    rm_update_holiday,
    rm_update_leave_type,
    rm_update_project,
    rm_update_project_phase,
    rm_update_role,
    rm_update_time_approval_status,
    rm_update_time_entry,
    rm_update_user,
    time_server,
    timesheet_reconciliation,
)

logger = logging.getLogger("smartsheet_rm_mcp")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server lifecycle and persistent client resources."""
    logger.info("Starting up Smartsheet RM MCP server")
    try:
        yield {"client": _client}
    finally:
        logger.info("Shutting down Smartsheet RM MCP resources")
        if _client is not None and hasattr(_client, "close"):
            res = _client.close()
            if inspect.isawaitable(res):
                await res
        for c in list(_HEADER_CLIENT_CACHE.values()):
            if hasattr(c, "close"):
                res = c.close()
                if inspect.isawaitable(res):
                    await res
            elif hasattr(c, "aclose"):
                res = c.aclose()
                if inspect.isawaitable(res):
                    await res
        _HEADER_CLIENT_CACHE.clear()


def _streamable_http_app(
    self: FastMCP,
    path: str | None = None,
    stateless_http: bool | None = None,
    json_response: bool | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: Any,
) -> Any:
    """Compatibility bridge for streamable HTTP ASGI application."""
    allowed_hosts = kwargs.pop("allowed_hosts", None)
    if allowed_hosts is None:
        allowed_hosts = [host, "localhost", f"{host}:{port}", f"localhost:{port}"]
    return self.http_app(
        path=path,
        transport="streamable-http",
        stateless_http=stateless_http,
        json_response=json_response,
        host_origin_protection=True,
        allowed_hosts=allowed_hosts,
        **kwargs,
    )


if not hasattr(FunctionTool, "input_schema"):
    FunctionTool.input_schema = property(lambda self: self.parameters)  # type: ignore[attr-defined]


class _ToolManagerCompat:
    """Compatibility bridge for internal _tool_manager access."""

    def __init__(self, server: FastMCP) -> None:
        self._server = server
        self._custom_tools: dict[str, Any] | None = None

    @property
    def _tools(self) -> dict[str, Any]:
        if self._custom_tools is not None:
            return self._custom_tools
        tools: dict[str, Any] = {}
        for c in self._server._local_provider._components.values():
            if hasattr(c, "name") and (getattr(c, "type", None) == "tool" or hasattr(c, "parameters")):
                tools[c.name] = c
        for p in getattr(self._server, "providers", []):
            sub = getattr(p, "server", None) or getattr(getattr(p, "_inner", None), "server", None)
            transforms = getattr(p, "_transforms", [])
            ns = next((t for t in transforms if hasattr(t, "_transform_name")), None)
            if sub and hasattr(sub, "_local_provider"):
                for c in sub._local_provider._components.values():
                    if hasattr(c, "name") and (getattr(c, "type", None) == "tool" or hasattr(c, "parameters")):
                        tool_name = ns._transform_name(c.name) if ns else c.name
                        tools[tool_name] = c
        return tools

    @_tools.setter
    def _tools(self, val: dict[str, Any] | None) -> None:
        self._custom_tools = val

    @_tools.deleter
    def _tools(self) -> None:
        self._custom_tools = None

    def remove_tool(self, name: str) -> None:
        try:
            self._server._local_provider.remove_tool(name)
        except Exception:
            pass
        for p in getattr(self._server, "providers", []):
            sub = getattr(p, "server", None) or getattr(getattr(p, "_inner", None), "server", None)
            transforms = getattr(p, "_transforms", [])
            ns = next((t for t in transforms if hasattr(t, "_reverse_name")), None)
            sub_name = ns._reverse_name(name) if ns else name
            if sub and hasattr(sub, "_local_provider"):
                try:
                    sub._local_provider.remove_tool(sub_name)
                except Exception:
                    pass
                try:
                    sub._local_provider.remove_tool(name)
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════════════════════
# SERVER COMPOSITION GATEWAY FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_PROFILES = {"full", "time", "projects", "admin", "readonly"}
_BULK_DESTRUCTIVE_TOOLS = {"time_bulk_delete_time_entries", "projects_bulk_delete_assignments"}


def create_server(
    profile: str | None = None,
    readonly: bool | None = None,
    allow_bulk_destructive: bool | None = None,
    enable_tool_search: bool | None = None,
) -> FastMCP:
    """Factory creating the composed root FastMCP gateway.

    Mounts domain sub-servers (time, projects, admin) with namespaces and enforces:
    - Hierarchical middleware (ParentAuditMiddleware, ReadOnlyGateMiddleware)
    - Native tool annotations (readOnlyHint, destructiveHint, idempotentHint, openWorldHint)
    - Profile filtering via selective mounting (full, time, projects, admin, readonly)
    - Read-only filtering (SMARTSHEET_RM_READONLY=1)
    - Bulk-destructive gating (SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1)
    - Opt-in tool search (RegexSearchTransform)
    """
    raw_profile = profile or os.environ.get("SMARTSHEET_RM_PROFILE") or settings.PROFILE
    active_profile = raw_profile.lower()

    if active_profile not in _VALID_PROFILES:
        raise ValueError(
            f"Unknown SMARTSHEET_RM_PROFILE {raw_profile!r}. Valid: time, projects, admin, full, readonly."
        )

    active_readonly = (
        readonly
        if readonly is not None
        else (
            settings.READONLY
            or os.environ.get("SMARTSHEET_RM_READONLY", "").strip() == "1"
            or active_profile == "readonly"
        )
    )
    active_allow_bulk = (
        allow_bulk_destructive
        if allow_bulk_destructive is not None
        else (
            settings.ALLOW_BULK_DESTRUCTIVE or os.environ.get("SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE", "").strip() == "1"
        )
    )
    active_tool_search = (
        enable_tool_search
        if enable_tool_search is not None
        else (settings.ENABLE_TOOL_SEARCH or os.environ.get("SMARTSHEET_RM_ENABLE_TOOL_SEARCH", "").strip() == "1")
    )

    root = FastMCP(
        "mcp-server-smartsheet-rm",
        version=__version__,
        lifespan=server_lifespan,
        cache_ttl=settings.CATALOG_CACHE_TTL_MS // 1000,
        cache_scope="public",
    )

    # 1. Global Parent Middleware
    root.add_middleware(ParentAuditMiddleware())
    root.add_middleware(ReadOnlyGateMiddleware())

    # 2. Server Composition via selective mount(subserver, namespace=...)
    if active_profile in ("full", "time", "readonly"):
        root.mount(create_time_server(), namespace="time")
    if active_profile in ("full", "projects", "readonly"):
        root.mount(create_projects_server(), namespace="projects")
    if active_profile in ("full", "admin", "readonly"):
        root.mount(create_admin_server(), namespace="admin")

    # Compatibility bridge
    root.streamable_http_app = _streamable_http_app.__get__(root, FastMCP)  # type: ignore[attr-defined]
    tool_mgr = _ToolManagerCompat(root)
    root._tool_manager = tool_mgr  # type: ignore[attr-defined]

    # 3. Read-Only Filtering
    if active_readonly:
        ro_remove = [
            name
            for name, tool_obj in list(tool_mgr._tools.items())
            if not (tool_obj.annotations and tool_obj.annotations.read_only_hint)
        ]
        for name in ro_remove:
            tool_mgr.remove_tool(name)

    # 4. Bulk-Destructive Gating
    if not active_allow_bulk:
        for name in _BULK_DESTRUCTIVE_TOOLS:
            if name in tool_mgr._tools:
                tool_mgr.remove_tool(name)

    # 5. Opt-In Dynamic Tool Search Transform
    if active_tool_search:
        from fastmcp.server.transforms.search import RegexSearchTransform

        root.add_transform(RegexSearchTransform())

    return root


# Default server instance
mcp = create_server()


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT & SHUTDOWN HANDLING
# ═══════════════════════════════════════════════════════════════════════════════


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Gracefully handle SIGTERM/SIGINT from host supervisor and unwind cleanly."""
    logger.info("Received signal %s; shutting down.", signum)
    sys.exit(0)


def main() -> None:
    """Parse CLI arguments and start MCP server."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    parser = argparse.ArgumentParser(description="Smartsheet RM MCP Server (2026-07-28 Spec)")
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "streamable-http", "sse"],
        help="Transport protocol: 'stdio' (default), 'streamable-http' (modern), or 'sse'.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host address for HTTP transports (default: 127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for HTTP transports (default: 8000).")
    parser.add_argument(
        "--profile",
        choices=["full", "time", "projects", "admin", "readonly"],
        default=os.environ.get("SMARTSHEET_RM_PROFILE") or settings.PROFILE,
        help="Domain profile: 'full', 'time', 'projects', 'admin', or 'readonly'.",
    )
    parser.add_argument(
        "--enable-tool-search",
        action="store_true",
        default=settings.ENABLE_TOOL_SEARCH or os.environ.get("SMARTSHEET_RM_ENABLE_TOOL_SEARCH", "").strip() == "1",
        help="Enable dynamic tool search transform instead of flat tools/list.",
    )
    parser.add_argument(
        "--stateless",
        action=getattr(argparse, "BooleanOptionalAction", "store_true"),
        default=os.environ.get("SMARTSHEET_RM_STATELESS_HTTP", "").lower() in ("1", "true", "yes"),
        help="Run Streamable HTTP in stateless mode (fresh connection per request, no Mcp-Session-Id).",
    )
    parser.add_argument(
        "--json-response",
        action=getattr(argparse, "BooleanOptionalAction", "store_true"),
        default=os.environ.get("SMARTSHEET_RM_JSON_RESPONSE", "").lower() in ("1", "true", "yes"),
        help="Return direct JSON responses instead of SSE text/event-stream over Streamable HTTP.",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Additional allowed Host header value for DNS rebinding protection (can be repeated).",
    )
    parser.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        help="Additional allowed browser Origin header value for DNS rebinding protection (can be repeated).",
    )
    args = parser.parse_args()

    configure_logging()

    if args.transport != "streamable-http":
        if args.stateless:
            logger.warning("--stateless flag is only applicable to 'streamable-http' transport.")
        if args.json_response:
            logger.warning("--json-response flag is only applicable to 'streamable-http' transport.")

    if args.transport == "streamable-http" and args.host in ("0.0.0.0", "::") and not args.allowed_host:
        parser.error("--allowed-host is required when binding to a wildcard host")
    if any(h.strip() == "*" for h in args.allowed_host):
        parser.error("Wildcard '*' is not permitted in --allowed-host; specify explicit hostnames.")

    hosts = [
        args.host,
        "localhost",
        f"{args.host}:{args.port}",
        f"localhost:{args.port}",
    ] + args.allowed_host
    origins = args.allowed_origin

    server_instance = create_server(
        profile=args.profile,
        enable_tool_search=args.enable_tool_search,
    )
    target_server = server_instance if getattr(mcp.run, "__func__", None) is getattr(FastMCP, "run", None) else mcp

    if args.transport == "sse":  # pragma: no cover
        logger.warning(
            "Deprecation Warning: HTTP+SSE transport is deprecated per MCP 2026-07-28 spec "
            "(SEP-2577). Please migrate to Streamable HTTP (--transport streamable-http)."
        )
        target_server.run(
            transport="sse",
            host=args.host,
            port=args.port,
            host_origin_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        )
    elif args.transport == "streamable-http":  # pragma: no cover
        target_server.run(
            transport="streamable-http",
            host=args.host,
            port=args.port,
            stateless_http=args.stateless,
            json_response=args.json_response,
            host_origin_protection=True,
            allowed_hosts=hosts,
            allowed_origins=origins,
        )
    else:
        target_server.run(transport="stdio")  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    main()

__all__ = [
    "__version__",
    "mcp",
    "create_server",
    "main",
    "server_lifespan",
    "DEFAULT_BASE_URL",
    "SmartsheetRMClient",
    "SmartsheetRMAPIError",
    "redact_secrets",
    "_redact_secrets",
    "settings",
    "ParentAuditMiddleware",
    "ReadOnlyGateMiddleware",
    "get_client",
    "_client",
    "_HEADER_CLIENT_CACHE",
    "rm_tool",
    "_destructive_gate",
    "_invalid_request",
    "configure_logging",
    "StructuredJSONFormatter",
    "_handle_shutdown",
    "create_time_server",
    "time_server",
    "create_projects_server",
    "projects_server",
    "create_admin_server",
    "admin_server",
    "timesheet_reconciliation",
    "project_staffing_plan",
    "rm_capabilities_resource",
    "rm_quickstart_resource",
    "rm_list_time_entries",
    "rm_get_time_entry",
    "rm_create_time_entry",
    "rm_update_time_entry",
    "rm_delete_time_entry",
    "rm_list_user_suggestions",
    "rm_update_time_approval_status",
    "rm_lock_timesheet",
    "rm_fill_weekly_timesheet",
    "rm_confirm_suggested_hours",
    "rm_reconcile_and_submit_week",
    "rm_bulk_delete_time_entries",
    "rm_list_approvals",
    "rm_create_approval",
    "rm_delete_approval",
    "rm_list_projects",
    "rm_get_project",
    "rm_create_project",
    "rm_update_project",
    "rm_delete_project",
    "rm_list_project_users",
    "rm_list_project_phases",
    "rm_get_project_phase",
    "rm_create_project_phase",
    "rm_update_project_phase",
    "rm_delete_project_phase",
    "rm_list_assignments",
    "rm_get_assignment",
    "rm_create_assignment",
    "rm_update_assignment",
    "rm_delete_assignment",
    "rm_clone_project_schedule",
    "rm_bulk_delete_assignments",
    "rm_list_status_options",
    "rm_list_placeholder_resources",
    "rm_create_placeholder_resource",
    "rm_delete_placeholder_resource",
    "rm_list_assignment_subtasks",
    "rm_create_assignment_subtask",
    "rm_delete_assignment_subtask",
    "rm_list_users",
    "rm_get_user",
    "rm_create_user",
    "rm_update_user",
    "rm_delete_user",
    "rm_list_user_bill_rates",
    "rm_create_user_bill_rate",
    "rm_get_user_availability",
    "rm_get_user_utilization",
    "rm_list_roles",
    "rm_create_role",
    "rm_update_role",
    "rm_delete_role",
    "rm_list_disciplines",
    "rm_create_discipline",
    "rm_update_discipline",
    "rm_delete_discipline",
    "rm_list_clients",
    "rm_get_client",
    "rm_create_client",
    "rm_update_client",
    "rm_delete_client",
    "rm_list_client_contacts",
    "rm_create_client_contact",
    "rm_delete_client_contact",
    "rm_list_leave_types",
    "rm_get_leave_type",
    "rm_create_leave_type",
    "rm_update_leave_type",
    "rm_delete_leave_type",
    "rm_list_holidays",
    "rm_get_holiday",
    "rm_create_holiday",
    "rm_update_holiday",
    "rm_delete_holiday",
    "rm_list_expenses",
    "rm_get_expense",
    "rm_create_expense",
    "rm_update_expense",
    "rm_delete_expense",
    "rm_list_expense_categories",
    "rm_create_expense_category",
    "rm_delete_expense_category",
    "rm_list_tags",
    "rm_create_tag",
    "rm_delete_tag",
    "rm_list_custom_fields",
    "rm_get_custom_field",
    "rm_create_custom_field",
    "rm_update_custom_field",
    "rm_delete_custom_field",
    "rm_list_custom_field_values",
    "rm_set_custom_field_values",
    "rm_get_user_statuses",
    "rm_set_user_status",
    "rm_get_report_rows",
    "rm_get_report_totals",
    "rm_list_webhooks",
    "rm_create_webhook",
    "rm_delete_webhook",
]
