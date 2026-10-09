"""FastMCP Server for Smartsheet Resource Management (10,000ft API).

Built on the public REST API, covering:
1. Time Tracking & Timesheets
2. Projects & Phases
3. Assignments & Allocations
4. Users, Roles & Disciplines
5. Clients & Contacts
6. Leaves & Holidays
7. Expense Tracking
8. Tags & Custom Fields
9. Composite Workflow Recipes

Environment variables controlling the gateway:
  SMARTSHEET_RM_PROFILE                  - Profile (default: full). Domain mounts: full, time, projects, admin,
                                           readonly. Job allowlists: timesheets, staffing, org_setup, portfolio.
  SMARTSHEET_RM_READONLY=1               - Keep only tools annotated readOnlyHint=True and refuse every other call.
  SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 - Required to execute time_bulk_delete_time_entries and
                                           projects_bulk_delete_assignments (listed in full, refused without it).
  SMARTSHEET_RM_ENABLE_TOOL_SEARCH=1     - Opt-in Tool Search (profile=full only).
  SMARTSHEET_RM_TOOL_SEARCH_BACKEND      - Tool Search backend: regex (default) or bm25.
  SMARTSHEET_RM_ENABLE_CODE_MODE=1       - Opt-in experimental Code Mode (profile=full only; not with Tool Search).

Build order: domain mounts -> job allowlist -> read-only filter -> discovery (full only).
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, Literal, cast

from fastmcp import FastMCP
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
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
from smartsheet_rm_mcp.config import readonly_enabled, settings
from smartsheet_rm_mcp.errors import SafetyViolationError, SmartsheetRMAPIError, redact_secrets
from smartsheet_rm_mcp.middleware import ParentAuditMiddleware, ReadOnlyGateMiddleware
from smartsheet_rm_mcp.profiles import (
    FULL_ONLY_TOOLS,
    PROFILES,
    ReadOnlyAnnotations,
    ReadOnlyToolFilter,
    get_profile,
    validate_allowlist,
)
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

ToolSearchBackend = Literal["regex", "bm25"]

# Domain sub-server factories by mount namespace (hybrid A+B: the namespace is the domain).
# A fresh sub-server is built per create_server call, so profile state never leaks between builds.
DOMAIN_SERVER_FACTORIES: dict[str, Callable[[], FastMCP]] = {
    "time": create_time_server,
    "projects": create_projects_server,
    "admin": create_admin_server,
}

# FastMCP synthetic discovery tools that only read the catalog (annotated readOnlyHint=True).
TOOL_SEARCH_READ_ONLY_TOOLS = ("search_tools",)
CODE_MODE_READ_ONLY_TOOLS = ("search", "get_schema")


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Manage server lifecycle and persistent client resources."""
    logger.info("Starting up Smartsheet RM MCP server")
    try:
        yield {"client": _client}
    finally:
        logger.info("Shutting down Smartsheet RM MCP resources")
        if _client is not None:
            closer = getattr(_client, "close", None) or getattr(_client, "aclose", None)
            if closer is not None:
                res = closer()
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


def _catalog_tool_names(root: FastMCP) -> set[str]:
    """Return the client-visible tool names of ``root`` via the public ``list_tools()``.

    Runs on a worker thread with its own event loop so ``create_server`` stays synchronous
    and safe to call from inside a running loop (tests, hosts).
    """

    async def _collect() -> set[str]:
        return {tool.name for tool in await root.list_tools()}

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _collect()).result()


def _apply_tool_allowlist(root: FastMCP, allowlist: frozenset[str]) -> None:
    """Expose only ``allowlist`` tools; prompts, resources, and templates are untouched.

    Public visibility API: disable every tool, then re-enable the named tools. The later
    ``enable`` wins. ``enable(only=True)`` is not used because it disables every component
    type first, which would also hide prompts and resources.
    """
    root.disable(components={"tool"})
    root.enable(names=set(allowlist), components={"tool"})


def _attach_tool_search(root: FastMCP, backend: ToolSearchBackend) -> None:
    """Attach Regex (default) or BM25 Tool Search transform to the root gateway."""
    if backend == "bm25":
        root.add_transform(BM25SearchTransform())
    else:
        root.add_transform(RegexSearchTransform())
    root.add_transform(ReadOnlyAnnotations(TOOL_SEARCH_READ_ONLY_TOOLS))


def _attach_code_mode(root: FastMCP) -> bool:
    """Attach experimental Code Mode when the FastMCP build exports it.

    Returns True when the transform was attached; False when ImportError skipped it.
    """
    try:
        from fastmcp.experimental.transforms.code_mode import CodeMode
    except ImportError:
        logger.warning(
            "Code Mode requested but fastmcp.experimental.transforms.code_mode is unavailable; "
            "skipping attach. Upgrade FastMCP or omit --enable-code-mode."
        )
        return False
    root.add_transform(CodeMode())
    root.add_transform(ReadOnlyAnnotations(CODE_MODE_READ_ONLY_TOOLS))
    return True


def _env_bool(name: str, configured: bool) -> bool:
    """Return ``configured`` or the live ``name=1`` environment flag."""
    return configured or os.environ.get(name, "").strip() == "1"


def _configured_search_backend() -> ToolSearchBackend:
    """Return the Tool Search backend from the live env or settings; reject unknown values."""
    raw = (os.environ.get("SMARTSHEET_RM_TOOL_SEARCH_BACKEND") or settings.TOOL_SEARCH_BACKEND).strip().lower()
    if raw not in ("regex", "bm25"):
        raise ValueError(f"Unknown SMARTSHEET_RM_TOOL_SEARCH_BACKEND {raw!r}; valid: bm25, regex.")
    return cast(ToolSearchBackend, raw)


def create_server(
    profile: str | None = None,
    enable_tool_search: bool | None = None,
    enable_code_mode: bool | None = None,
    tool_search_backend: ToolSearchBackend | None = None,
) -> FastMCP:
    """Build root gateway FastMCP instance using Server Composition and Hierarchical Middleware.

    Architecture:
    Gateway (FastMCP)
    ├── Parent Middleware (ParentAuditMiddleware, ReadOnlyGateMiddleware)
    ├── mount(time_server, namespace="time")          # domain mount, not a product stamp
    ├── mount(projects_server, namespace="projects")
    ├── mount(admin_server, namespace="admin")
    ├── (allowlist profiles) tools-only visibility allowlist over the full catalog
    ├── (readonly) ReadOnlyToolFilter: keep tools annotated readOnlyHint=True
    └── (Optional, profile==full only) Tool Search XOR experimental Code Mode

    Profile rules:
    * Domain-mount profiles mount the listed domains; allowlist profiles mount every
      domain and expose only the allowlisted tool names (prompts/resources stay).
    * An unknown profile, or an allowlisted name missing from the full catalog, raises
      ``ValueError`` at build time.
    * Bulk destructive tools stay listed; the time/projects domain guards refuse them at
      call time unless ``SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1``.

    Discovery rules:
    * Default: flat ``tools/list`` of mounted domain tools (curated or full).
    * Tool Search / Code Mode attach only when explicitly enabled **and** ``profile == "full"``.
    * Requesting either discovery mode on a curated profile logs a warning and skips attach.
    * Enabling both Tool Search and Code Mode raises ``ValueError`` (mutual exclusion).
    """
    active = get_profile(profile or os.environ.get("SMARTSHEET_RM_PROFILE") or settings.PROFILE)
    active_profile = active.name
    use_tool_search = (
        enable_tool_search
        if enable_tool_search is not None
        else _env_bool("SMARTSHEET_RM_ENABLE_TOOL_SEARCH", settings.ENABLE_TOOL_SEARCH)
    )
    use_code_mode = (
        enable_code_mode
        if enable_code_mode is not None
        else _env_bool("SMARTSHEET_RM_ENABLE_CODE_MODE", settings.ENABLE_CODE_MODE)
    )
    search_backend: ToolSearchBackend = (
        tool_search_backend if tool_search_backend is not None else _configured_search_backend()
    )

    if use_tool_search and use_code_mode:
        raise ValueError("Tool Search and Code Mode are mutually exclusive; enable only one discovery mode.")

    root = FastMCP(
        "mcp-server-smartsheet-rm",
        version=__version__,
        lifespan=server_lifespan,
        cache_ttl=settings.CATALOG_CACHE_TTL_MS // 1000,
        cache_scope="public",
    )

    # Attach ASGI streamable HTTP compatibility bridge
    root.streamable_http_app = _streamable_http_app.__get__(root, FastMCP)  # type: ignore[attr-defined]

    # 1. Global Parent Middleware (Audit logging, request timing, and read-only gate)
    readonly_gate = ReadOnlyGateMiddleware(enforce=active.readonly)
    root.add_middleware(ParentAuditMiddleware())
    root.add_middleware(readonly_gate)

    # 2. Server Composition via mount(subserver, namespace=...) — Hybrid A+B domain mounts
    for domain in active.domains:
        root.mount(DOMAIN_SERVER_FACTORIES[domain](), namespace=domain)

    # 3. Allowlist profiles: validate against the full mounted catalog, then filter tools
    if active.is_allowlist:
        allowlist = validate_allowlist(active, _catalog_tool_names(root))
        _apply_tool_allowlist(root, allowlist)

    # 4. Read-only: keep only tools annotated readOnlyHint=True (annotation is the truth)
    if active.readonly or readonly_enabled():
        # Capture the profile catalog before the filter hides writes, so the gate refuses a
        # hidden real tool but lets an unknown name reach FastMCP's "Unknown tool" error.
        readonly_gate.catalog = frozenset(_catalog_tool_names(root))
        root.add_transform(ReadOnlyToolFilter())

    # 5. Opt-in discovery transforms — full profile only (never dump full catalog by default)
    if use_tool_search:
        if active_profile != "full":
            logger.warning(
                "Tool Search requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping flat curated tools/list.",
                active_profile,
            )
        else:
            _attach_tool_search(root, search_backend)

    if use_code_mode:
        if active_profile != "full":
            logger.warning(
                "Code Mode requested with profile=%r; attach is allowed only on profile='full'. "
                "Keeping flat curated tools/list.",
                active_profile,
            )
        else:
            _attach_code_mode(root)

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
        type=str.lower,
        choices=sorted(PROFILES),
        default=(os.environ.get("SMARTSHEET_RM_PROFILE") or settings.PROFILE).lower(),
        help=(
            "Server profile (default 'full'): domain-mount profiles full/time/projects/admin/readonly "
            "or job-shaped allowlist profiles timesheets/staffing/org_setup/portfolio."
        ),
    )
    parser.add_argument(
        "--enable-tool-search",
        action="store_true",
        default=_env_bool("SMARTSHEET_RM_ENABLE_TOOL_SEARCH", settings.ENABLE_TOOL_SEARCH),
        help="Enable Tool Search on profile=full only (replaces tools/list with search_tools + call_tool).",
    )
    parser.add_argument(
        "--tool-search-backend",
        choices=["regex", "bm25"],
        default=None,
        help="Tool Search backend: 'regex' (default) or 'bm25'.",
    )
    parser.add_argument(
        "--enable-code-mode",
        action="store_true",
        default=_env_bool("SMARTSHEET_RM_ENABLE_CODE_MODE", settings.ENABLE_CODE_MODE),
        help=(
            "Enable experimental Code Mode on profile=full only (search + execute). "
            "Mutually exclusive with --enable-tool-search."
        ),
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
        enable_code_mode=args.enable_code_mode,
        tool_search_backend=args.tool_search_backend,
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
    "DOMAIN_SERVER_FACTORIES",
    "FULL_ONLY_TOOLS",
    "PROFILES",
    "SafetyViolationError",
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
