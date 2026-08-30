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
  SMARTSHEET_RM_PROFILE                - Tool subset: time, projects, admin, full (default: full).
  SMARTSHEET_RM_READONLY=1             - When set, only tools annotated read_only_hint=True are registered.
  SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 - Required to register rm_bulk_delete_time_entries and rm_bulk_delete_assignments.

Filter application order: annotations -> profile -> readonly -> bulk-destructive gating.
"""

from __future__ import annotations

import argparse
import datetime
import functools
import json
import logging
import os
import re
import signal
import time
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from .client import DEFAULT_BASE_URL, SmartsheetRMClient
from .errors import SmartsheetRMAPIError

logger = logging.getLogger("smartsheet_rm_mcp")


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


mcp = MCPServer(
    "mcp-server-smartsheet-rm",
    description="Enterprise MCP server for Smartsheet Resource Management (10,000ft API). Orchestrate timesheets, resource scheduling, project staffing, capacity planning, and approvals.",
)

_client: SmartsheetRMClient | None = None
_HEADER_CLIENT_CACHE: dict[tuple[str, str], SmartsheetRMClient] = {}

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)auth:\s*[a-zA-Z0-9\-\._~\+\/]+"),
    re.compile(r"(?i)(api_token=|\"api_token\":\s*\")[a-zA-Z0-9\-\._~\+\/]+=*\"?"),
    re.compile(r"(?i)(SMARTSHEET_RM_API_TOKEN=)[a-zA-Z0-9\-\._~\+\/]+"),
]


def _redact_secrets(text: str, extra_secret: str | None = None) -> str:
    """Redact tokens, credentials, and API secrets from output and logs."""
    if not text:
        return text
    secret = os.environ.get("SMARTSHEET_RM_API_TOKEN", "")
    if secret and secret in text:
        text = text.replace(secret, "***REDACTED***")
    if extra_secret and extra_secret in text:
        text = text.replace(extra_secret, "***REDACTED***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***REDACTED***", text)
    return text


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
    """
    global _client
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


# ═══════════════════════════════════════════════════════════════════════════════
# 1. TIME TRACKING & APPROVALS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_time_entries(
    project_id: int | str | None = None,
    user_id: int | str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    with_suggestions: bool = False,
    page: int = 1,
    per_page: int = 50,
) -> str:
    """List time entries across organization or filtered by project, user, or date range (YYYY-MM-DD)."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    if with_suggestions:
        params["with_suggestions"] = "true"

    client = await get_client()
    if project_id is not None:
        data = await client.list_project_time_entries(project_id, params=params)
    elif user_id is not None:
        data = await client.list_user_time_entries(user_id, params=params)
    else:
        data = await client.list_time_entries(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_time_entry(entry_id: int | str) -> str:
    """Get details for a specific time entry by its ID."""
    client = await get_client()
    data = await client.get_time_entry(entry_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_time_entry(
    user_id: int | str,
    project_id: int | str,
    date: str,
    hours: float,
    notes: str | None = None,
    phase_id: int | str | None = None,
    is_billable: bool = True,
    custom_field_values: dict[str, Any] | None = None,
) -> str:
    """Create a new time entry for a user on a project (date format: YYYY-MM-DD)."""
    payload: dict[str, Any] = {
        "user_id": user_id,
        "assignable_id": phase_id or project_id,
        "date": date,
        "hours": hours,
        "billable": is_billable,
    }
    if notes:
        payload["notes"] = notes
    if custom_field_values:
        payload["custom_field_values"] = custom_field_values

    client = await get_client()
    data = await client.create_time_entry(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_time_entry(
    entry_id: int | str,
    hours: float | None = None,
    notes: str | None = None,
    date: str | None = None,
    is_billable: bool | None = None,
) -> str:
    """Update an existing time entry."""
    payload: dict[str, Any] = {}
    if hours is not None:
        payload["hours"] = hours
    if notes is not None:
        payload["notes"] = notes
    if date is not None:
        payload["date"] = date
    if is_billable is not None:
        payload["billable"] = is_billable

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_time_entry(entry_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_time_entry(entry_id: int | str, confirm: bool = False) -> str:
    """Delete a time entry (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_time_entry({entry_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_time_entry(entry_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_user_suggestions(
    user_id: int | str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """Read unconfirmed scheduled time suggestions for a user."""
    params: dict[str, Any] = {"with_suggestions": "true"}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    client = await get_client()
    data = await client.list_user_time_entries(user_id, params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_time_approval_status(
    user_id: int | str,
    entry_ids: list[int | str],
    status: str,
    approver_notes: str | None = None,
) -> str:
    """Approve or reject time entries for a user (status: 'approved', 'rejected', 'pending')."""
    if status not in ("approved", "rejected", "pending"):
        return _invalid_request("Status must be one of: 'approved', 'rejected', 'pending'")
    payload: dict[str, Any] = {"time_entry_ids": entry_ids, "status": status}
    if approver_notes:
        payload["notes"] = approver_notes

    client = await get_client()
    data = await client.update_user_approval_status(user_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_lock_timesheet(
    user_id: int | str,
    lock_date: str,
    unlock: bool = False,
) -> str:
    """Lock or unlock timesheet records for a user up to a specified lock date (YYYY-MM-DD)."""
    payload = {"date": lock_date, "locked": not unlock}
    client = await get_client()
    data = await client.lock_user_timesheet(user_id, payload)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PROJECTS & PHASES
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_projects(
    page: int = 1,
    per_page: int = 50,
    with_phases: bool = False,
    archived: bool | None = None,
    filter_field: str | None = None,
    filter_value: str | None = None,
    sort_field: str | None = None,
    sort_order: str = "asc",
) -> str:
    """List projects in Smartsheet RM with filtering, phase inclusion, and pagination."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if with_phases:
        params["with_phases"] = "true"
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    if filter_field and filter_value:
        params["filter_field"] = filter_field
        params["filter_value"] = filter_value
    if sort_field:
        params["sort_field"] = sort_field
        params["sort_order"] = sort_order

    client = await get_client()
    data = await client.list_projects(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_project(project_id: int | str, with_phases: bool = True) -> str:
    """Get project details including budget, phases, client, and dates."""
    params: dict[str, Any] = {}
    if with_phases:
        params["with_phases"] = "true"
    client = await get_client()
    data = await client.get_project(project_id, params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_project(
    name: str,
    client_id: int | str | None = None,
    project_state: str = "Tentative",
    project_type: str = "Billable",
    secure: bool = False,
    budget: float | None = None,
    budget_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    description: str | None = None,
) -> str:
    """Create a new project in Smartsheet RM."""
    payload: dict[str, Any] = {
        "name": name,
        "project_state": project_state,
        "project_type": project_type,
        "secure": secure,
    }
    if client_id is not None:
        payload["client_id"] = client_id
    if budget is not None:
        payload["budget"] = budget
    if budget_type is not None:
        payload["budget_type"] = budget_type
    if start_date:
        payload["starts_at"] = start_date
    if end_date:
        payload["ends_at"] = end_date
    if description:
        payload["description"] = description

    client = await get_client()
    data = await client.create_project(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_project(
    project_id: int | str,
    name: str | None = None,
    project_state: str | None = None,
    budget: float | None = None,
    budget_type: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    description: str | None = None,
    archived: bool | None = None,
) -> str:
    """Update project metadata, state, dates, budget or archive status."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if project_state is not None:
        payload["project_state"] = project_state
    if budget is not None:
        payload["budget"] = budget
    if budget_type is not None:
        payload["budget_type"] = budget_type
    if start_date is not None:
        payload["starts_at"] = start_date
    if end_date is not None:
        payload["ends_at"] = end_date
    if description is not None:
        payload["description"] = description
    if archived is not None:
        payload["archived"] = archived

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_project(project_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_project(project_id: int | str, confirm: bool = False) -> str:
    """Delete a project (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_project({project_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_project(project_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_project_users(
    project_id: int | str,
    page: int | None = None,
    per_page: int | None = None,
) -> str:
    """List users associated with or assigned to a specific project."""
    client = await get_client()
    params: dict[str, Any] = {}
    if page is not None:
        params["page"] = page
    if per_page is not None:
        params["per_page"] = per_page
    data = await client.list_project_users(project_id, params=params if params else None)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_project_phases(project_id: int | str) -> str:
    """List phases belonging to a project."""
    client = await get_client()
    data = await client.list_project_phases(project_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_project_phase(project_id: int | str, phase_id: int | str) -> str:
    """Get details for a specific project phase."""
    client = await get_client()
    data = await client.get_project_phase(project_id, phase_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_project_phase(
    project_id: int | str,
    name: str,
    start_date: str,
    end_date: str,
    budget: float | None = None,
    description: str | None = None,
) -> str:
    """Create a new phase under a project."""
    payload: dict[str, Any] = {
        "name": name,
        "starts_at": start_date,
        "ends_at": end_date,
    }
    if budget is not None:
        payload["budget"] = budget
    if description is not None:
        payload["description"] = description

    client = await get_client()
    data = await client.create_project_phase(project_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_project_phase(
    project_id: int | str,
    phase_id: int | str,
    name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    budget: float | None = None,
    description: str | None = None,
) -> str:
    """Update an existing project phase."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if start_date is not None:
        payload["starts_at"] = start_date
    if end_date is not None:
        payload["ends_at"] = end_date
    if budget is not None:
        payload["budget"] = budget
    if description is not None:
        payload["description"] = description

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_project_phase(project_id, phase_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_project_phase(
    project_id: int | str,
    phase_id: int | str,
    confirm: bool = False,
) -> str:
    """Delete a project phase (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_project_phase({project_id}, {phase_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_project_phase(project_id, phase_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. ASSIGNMENTS & SCHEDULING
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_assignments(
    project_id: int | str | None = None,
    user_id: int | str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> str:
    """List resource scheduling assignments across projects or filtered by user/project."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    client = await get_client()
    if project_id is not None:
        data = await client.list_project_assignments(project_id, params=params)
    elif user_id is not None:
        data = await client.list_user_assignments(user_id, params=params)
    else:
        data = await client.list_assignments(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_assignment(assignment_id: int | str) -> str:
    """Get details for a specific assignment."""
    client = await get_client()
    data = await client.get_assignment(assignment_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_assignment(
    project_id: int | str,
    user_id: int | str,
    start_date: str,
    end_date: str,
    percent: float | None = None,
    hours_per_day: float | None = None,
    fixed_hours: float | None = None,
    allocation_mode: str = "percent",
    phase_id: int | str | None = None,
    note: str | None = None,
) -> str:
    """Create a resource assignment on a project or phase (dates: YYYY-MM-DD)."""
    payload: dict[str, Any] = {
        "user_id": user_id,
        "assignable_id": phase_id or project_id,
        "starts_at": start_date,
        "ends_at": end_date,
        "allocation_mode": allocation_mode,
    }
    if percent is not None:
        payload["percent"] = percent
    if hours_per_day is not None:
        payload["hours_per_day"] = hours_per_day
    if fixed_hours is not None:
        payload["fixed_hours"] = fixed_hours
    if note:
        payload["note"] = note

    client = await get_client()
    data = await client.create_assignment(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_assignment(
    assignment_id: int | str,
    start_date: str | None = None,
    end_date: str | None = None,
    percent: float | None = None,
    hours_per_day: float | None = None,
    fixed_hours: float | None = None,
    note: str | None = None,
) -> str:
    """Update dates, allocation percentage, or hours on an existing assignment."""
    payload: dict[str, Any] = {}
    if start_date is not None:
        payload["starts_at"] = start_date
    if end_date is not None:
        payload["ends_at"] = end_date
    if percent is not None:
        payload["percent"] = percent
    if hours_per_day is not None:
        payload["hours_per_day"] = hours_per_day
    if fixed_hours is not None:
        payload["fixed_hours"] = fixed_hours
    if note is not None:
        payload["note"] = note

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_assignment(assignment_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_assignment(assignment_id: int | str, confirm: bool = False) -> str:
    """Delete a resource assignment (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_assignment({assignment_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_assignment(assignment_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. USERS, ROLES, DISCIPLINES & CAPACITY
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_users(
    page: int = 1,
    per_page: int = 50,
    role: str | None = None,
    discipline: str | None = None,
    archived: bool | None = None,
    include_billability: bool = True,
) -> str:
    """List users in the organization with role/discipline filtering."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if role:
        params["role"] = role
    if discipline:
        params["discipline"] = discipline
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    if include_billability:
        params["include_billability"] = "true"

    client = await get_client()
    data = await client.list_users(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_user(user_id: int | str) -> str:
    """Get details for a specific user."""
    client = await get_client()
    data = await client.get_user(user_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_user(
    first_name: str,
    last_name: str,
    email: str,
    role: str | None = None,
    discipline: str | None = None,
    billability_target: float | None = None,
    bill_rate: float | None = None,
    cost_rate: float | None = None,
    user_type_id: int | None = None,
    location: str | None = None,
) -> str:
    """Create a new user profile in Smartsheet RM."""
    payload: dict[str, Any] = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
    }
    if role:
        payload["role"] = role
    if discipline:
        payload["discipline"] = discipline
    if billability_target is not None:
        payload["billability_target"] = billability_target
    if bill_rate is not None:
        payload["bill_rate"] = bill_rate
    if cost_rate is not None:
        payload["cost_rate"] = cost_rate
    if user_type_id is not None:
        payload["user_type_id"] = user_type_id
    if location:
        payload["location"] = location

    client = await get_client()
    data = await client.create_user(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_user(
    user_id: int | str,
    first_name: str | None = None,
    last_name: str | None = None,
    email: str | None = None,
    role: str | None = None,
    discipline: str | None = None,
    billability_target: float | None = None,
    bill_rate: float | None = None,
    cost_rate: float | None = None,
    archived: bool | None = None,
) -> str:
    """Update a user's profile, role, discipline, bill rate, or archive state."""
    payload: dict[str, Any] = {}
    if first_name is not None:
        payload["first_name"] = first_name
    if last_name is not None:
        payload["last_name"] = last_name
    if email is not None:
        payload["email"] = email
    if role is not None:
        payload["role"] = role
    if discipline is not None:
        payload["discipline"] = discipline
    if billability_target is not None:
        payload["billability_target"] = billability_target
    if bill_rate is not None:
        payload["bill_rate"] = bill_rate
    if cost_rate is not None:
        payload["cost_rate"] = cost_rate
    if archived is not None:
        payload["archived"] = archived

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_user(user_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_user(user_id: int | str, confirm: bool = False) -> str:
    """Delete or archive a user (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_user({user_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_user(user_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_user_bill_rates(user_id: int | str) -> str:
    """List bill rate tiers for a user."""
    client = await get_client()
    data = await client.list_user_bill_rates(user_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_user_bill_rate(
    user_id: int | str,
    rate: float,
    start_date: str,
    end_date: str | None = None,
) -> str:
    """Add a bill rate tier with an effective date range for a user."""
    payload: dict[str, Any] = {"rate": rate, "starts_at": start_date}
    if end_date:
        payload["ends_at"] = end_date
    client = await get_client()
    data = await client.create_user_bill_rate(user_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_user_availability(
    user_id: int | str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """Query scheduled hours vs available capacity for a user."""
    params: dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    client = await get_client()
    data = await client.get_user_availability(user_id, params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_user_utilization(
    user_id: int | str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """Get billable utilization metrics for a user."""
    params: dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    client = await get_client()
    data = await client.get_user_utilization(user_id, params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_roles() -> str:
    """List all configured user roles."""
    client = await get_client()
    data = await client.list_roles()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_role(name: str) -> str:
    """Create a new role."""
    client = await get_client()
    data = await client.create_role({"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_role(role_id: int | str, name: str) -> str:
    """Update an existing role."""
    client = await get_client()
    data = await client.update_role(role_id, {"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_role(role_id: int | str, confirm: bool = False) -> str:
    """Delete a role (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_role({role_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_role(role_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_disciplines() -> str:
    """List all configured disciplines."""
    client = await get_client()
    data = await client.list_disciplines()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_discipline(name: str) -> str:
    """Create a new discipline."""
    client = await get_client()
    data = await client.create_discipline({"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_discipline(discipline_id: int | str, name: str) -> str:
    """Update an existing discipline."""
    client = await get_client()
    data = await client.update_discipline(discipline_id, {"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_discipline(discipline_id: int | str, confirm: bool = False) -> str:
    """Delete a discipline (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_discipline({discipline_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_discipline(discipline_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. CLIENTS & CONTACTS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_clients(
    page: int = 1,
    per_page: int = 50,
    archived: bool | None = None,
) -> str:
    """List clients."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    client = await get_client()
    data = await client.list_clients(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_client(client_id: int | str) -> str:
    """Get details for a specific client."""
    client = await get_client()
    data = await client.get_client(client_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_client(
    name: str,
    address: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zipcode: str | None = None,
    country: str | None = None,
) -> str:
    """Create a new client record."""
    payload: dict[str, Any] = {"name": name}
    if address:
        payload["address"] = address
    if city:
        payload["city"] = city
    if state:
        payload["state"] = state
    if zipcode:
        payload["zipcode"] = zipcode
    if country:
        payload["country"] = country

    client = await get_client()
    data = await client.create_client(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_client(
    client_id: int | str,
    name: str | None = None,
    address: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zipcode: str | None = None,
    country: str | None = None,
    archived: bool | None = None,
) -> str:
    """Update a client record."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if address is not None:
        payload["address"] = address
    if city is not None:
        payload["city"] = city
    if state is not None:
        payload["state"] = state
    if zipcode is not None:
        payload["zipcode"] = zipcode
    if country is not None:
        payload["country"] = country
    if archived is not None:
        payload["archived"] = archived

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_client(client_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_client(client_id: int | str, confirm: bool = False) -> str:
    """Delete a client record (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_client({client_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_client(client_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_client_contacts(client_id: int | str) -> str:
    """List contacts associated with a client."""
    client = await get_client()
    data = await client.list_client_contacts(client_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_client_contact(
    client_id: int | str,
    first_name: str,
    last_name: str,
    email: str | None = None,
    phone: str | None = None,
    title: str | None = None,
) -> str:
    """Add a contact for a client."""
    payload: dict[str, Any] = {"first_name": first_name, "last_name": last_name}
    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone
    if title:
        payload["title"] = title

    client = await get_client()
    data = await client.create_client_contact(client_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_client_contact(
    client_id: int | str,
    contact_id: int | str,
    confirm: bool = False,
) -> str:
    """Delete a client contact (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_client_contact({client_id}, {contact_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_client_contact(client_id, contact_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. LEAVES & HOLIDAYS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_leave_types() -> str:
    """List leave types (e.g. Vacation, Sick, Parental, PTO)."""
    client = await get_client()
    data = await client.list_leave_types()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_leave_type(leave_type_id: int | str) -> str:
    """Get details for a specific leave type."""
    client = await get_client()
    data = await client.get_leave_type(leave_type_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_leave_type(name: str) -> str:
    """Create a new leave type."""
    client = await get_client()
    data = await client.create_leave_type({"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_leave_type(leave_type_id: int | str, name: str) -> str:
    """Update a leave type name."""
    client = await get_client()
    data = await client.update_leave_type(leave_type_id, {"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_leave_type(leave_type_id: int | str, confirm: bool = False) -> str:
    """Delete a leave type (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_leave_type({leave_type_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_leave_type(leave_type_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_holidays(
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """List company and regional holidays."""
    params: dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    client = await get_client()
    data = await client.list_holidays(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_holiday(holiday_id: int | str) -> str:
    """Get details for a specific holiday."""
    client = await get_client()
    data = await client.get_holiday(holiday_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_holiday(name: str, date: str, end_date: str | None = None) -> str:
    """Create a new company holiday or non-working day."""
    payload: dict[str, Any] = {"name": name, "date": date}
    if end_date:
        payload["ends_at"] = end_date
    client = await get_client()
    data = await client.create_holiday(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_holiday(
    holiday_id: int | str,
    name: str | None = None,
    date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Update a holiday."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if date is not None:
        payload["date"] = date
    if end_date is not None:
        payload["ends_at"] = end_date

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_holiday(holiday_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_holiday(holiday_id: int | str, confirm: bool = False) -> str:
    """Delete a holiday (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_holiday({holiday_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_holiday(holiday_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EXPENSE TRACKING
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_expenses(
    project_id: int | str | None = None,
    user_id: int | str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> str:
    """List logged expenses across projects or filtered by project/user."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    client = await get_client()
    if project_id is not None:
        data = await client.list_project_expenses(project_id, params=params)
    elif user_id is not None:
        data = await client.list_user_expenses(user_id, params=params)
    else:
        data = await client.list_expenses(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_expense(expense_id: int | str) -> str:
    """Get details for a specific logged expense."""
    client = await get_client()
    data = await client.get_expense(expense_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_expense(
    project_id: int | str,
    user_id: int | str,
    expense_category_id: int | str,
    amount: float,
    date: str,
    notes: str | None = None,
    is_billable: bool = True,
) -> str:
    """Log a project expense item."""
    payload: dict[str, Any] = {
        "project_id": project_id,
        "user_id": user_id,
        "expense_category_id": expense_category_id,
        "amount": amount,
        "date": date,
        "billable": is_billable,
    }
    if notes:
        payload["notes"] = notes
    client = await get_client()
    data = await client.create_expense(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_expense(
    expense_id: int | str,
    amount: float | None = None,
    notes: str | None = None,
    is_billable: bool | None = None,
    date: str | None = None,
) -> str:
    """Update a logged expense item."""
    payload: dict[str, Any] = {}
    if amount is not None:
        payload["amount"] = amount
    if notes is not None:
        payload["notes"] = notes
    if is_billable is not None:
        payload["billable"] = is_billable
    if date is not None:
        payload["date"] = date

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_expense(expense_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_expense(expense_id: int | str, confirm: bool = False) -> str:
    """Delete an expense item (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_expense({expense_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_expense(expense_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_expense_categories() -> str:
    """List configured expense categories."""
    client = await get_client()
    data = await client.list_expense_categories()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_expense_category(name: str) -> str:
    """Create an expense category."""
    client = await get_client()
    data = await client.create_expense_category({"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_expense_category(category_id: int | str, confirm: bool = False) -> str:
    """Delete an expense category (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_expense_category({category_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_expense_category(category_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. TAGS & CUSTOM FIELDS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_tags(page: int = 1, per_page: int = 50) -> str:
    """List tags."""
    client = await get_client()
    data = await client.list_tags(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_tag(name: str) -> str:
    """Create a tag."""
    client = await get_client()
    data = await client.create_tag({"name": name})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_tag(tag_id: int | str, confirm: bool = False) -> str:
    """Delete a tag (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_tag({tag_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_tag(tag_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_custom_fields() -> str:
    """List custom field definitions."""
    client = await get_client()
    data = await client.list_custom_fields()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_custom_field(custom_field_id: int | str) -> str:
    """Get details of a custom field definition."""
    client = await get_client()
    data = await client.get_custom_field(custom_field_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_custom_field(
    name: str,
    field_type: str,
    target_type: str,
    options: list[str] | None = None,
) -> str:
    """Create a custom field definition (field_type: text, number, select, date; target_type: Project, User, Phase)."""
    payload: dict[str, Any] = {
        "name": name,
        "type": field_type,
        "target_type": target_type,
    }
    if options:
        payload["options"] = options
    client = await get_client()
    data = await client.create_custom_field(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_update_custom_field(
    custom_field_id: int | str,
    name: str | None = None,
    options: list[str] | None = None,
) -> str:
    """Update a custom field definition."""
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if options is not None:
        payload["options"] = options

    if not payload:
        return _invalid_request("No update fields provided")

    client = await get_client()
    data = await client.update_custom_field(custom_field_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_custom_field(custom_field_id: int | str, confirm: bool = False) -> str:
    """Delete a custom field definition (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_custom_field({custom_field_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_custom_field(custom_field_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_custom_field_values(
    target_id: int | str | None = None,
    target_type: str | None = None,
) -> str:
    """List custom field values for a specific entity."""
    params: dict[str, Any] = {}
    if target_id is not None:
        params["target_id"] = target_id
    if target_type:
        params["target_type"] = target_type
    client = await get_client()
    data = await client.list_custom_field_values(params=params)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_set_custom_field_values(
    target_id: int | str,
    target_type: str,
    values: dict[str, Any],
) -> str:
    """Set custom field values for an entity."""
    payload = {
        "target_id": target_id,
        "target_type": target_type,
        "custom_field_values": values,
    }
    client = await get_client()
    data = await client.set_custom_field_values(payload)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. COMPOSITE WORKFLOW RECIPES
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_fill_weekly_timesheet(
    user_id: int | str,
    start_date: str,
    daily_hours: float = 8.0,
    project_id: int | str | None = None,
    notes: str = "Standard logged hours",
    include_weekends: bool = False,
    weekend_hours: float | None = None,
) -> str:
    """Batch-fill weekly timesheet entries (Mon-Fri 8h default, or 7-day with include_weekends=True) for a user.

    start_date: Monday date of the week in YYYY-MM-DD.
    include_weekends: When True, logs Saturday and Sunday entries as well.
    weekend_hours: Specific hours for Saturday/Sunday (defaults to daily_hours if omitted).
    """
    try:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return _invalid_request(f"Invalid start_date '{start_date}'. Must be in YYYY-MM-DD format.")

    client = await get_client()

    target_project_id = project_id
    if target_project_id is None:
        # Determine first active project assignment for this user
        end_date_str = (start_dt + datetime.timedelta(days=6)).strftime("%Y-%m-%d")
        assignments = await client.list_user_assignments(user_id, params={"from": start_date, "to": end_date_str})
        entries = assignments.get("data", []) if isinstance(assignments, dict) else assignments
        if not entries:
            return _invalid_request(
                f"No active assignments found for user {user_id} in week {start_date}. Specify project_id explicitly."
            )
        target_project_id = entries[0].get("project_id") or entries[0].get("assignable_id")
        if not target_project_id:
            return _invalid_request("Unable to resolve project_id from assignment.")

    days_to_fill = 7 if include_weekends else 5
    created_entries = []
    errors = []
    total_hours = 0.0
    for day_offset in range(days_to_fill):
        entry_date = (start_dt + datetime.timedelta(days=day_offset)).strftime("%Y-%m-%d")
        is_weekend = day_offset >= 5
        hours_for_day = weekend_hours if (is_weekend and weekend_hours is not None) else daily_hours
        entry_data = {
            "user_id": user_id,
            "assignable_id": target_project_id,
            "date": entry_date,
            "hours": hours_for_day,
            "notes": notes,
            "billable": True,
        }
        try:
            res = await client.create_time_entry(entry_data)
            created_entries.append(res)
            total_hours += hours_for_day
        except SmartsheetRMAPIError as err:
            errors.append({"date": entry_date, "error": err.to_dict()})

    return json.dumps(
        {
            "status": "success" if not errors else ("partial_success" if created_entries else "failed"),
            "user_id": user_id,
            "project_id": target_project_id,
            "week_start": start_date,
            "days_filled": days_to_fill,
            "total_hours": total_hours,
            "created_count": len(created_entries),
            "failed_count": len(errors),
            "entries": created_entries,
            "errors": errors,
        },
        indent=2,
    )


@mcp.tool()
@rm_tool
async def rm_confirm_suggested_hours(
    user_id: int | str,
    from_date: str,
    to_date: str,
) -> str:
    """Auto-confirm all unconfirmed scheduled suggestions for a user within a date range."""
    client = await get_client()
    data = await client.list_user_time_entries(
        user_id, params={"from": from_date, "to": to_date, "with_suggestions": "true"}
    )
    items = data.get("data", []) if isinstance(data, dict) else data
    confirmed = []
    errors = []

    for item in items:
        # If suggestion is unconfirmed (e.g. is_suggestion is True or hours unconfirmed)
        if isinstance(item, dict) and item.get("is_suggestion"):
            entry_id = item.get("id")
            if entry_id:
                try:
                    # Update/Confirm suggestion
                    res = await client.update_time_entry(
                        entry_id,
                        {
                            "hours": item.get("hours", 0.0),
                            "date": item.get("date"),
                            "notes": item.get("notes") or "Auto-confirmed suggestion",
                        },
                    )
                    confirmed.append(res)
                except SmartsheetRMAPIError as err:
                    errors.append({"id": entry_id, "error": err.to_dict()})

    return json.dumps(
        {
            "status": "success" if not errors else ("partial_success" if confirmed else "failed"),
            "user_id": user_id,
            "date_range": f"{from_date} to {to_date}",
            "confirmed_count": len(confirmed),
            "failed_count": len(errors),
            "confirmed_entries": confirmed,
            "errors": errors,
        },
        indent=2,
    )


@mcp.tool()
@rm_tool
async def rm_reconcile_and_submit_week(
    user_id: int | str,
    start_date: str,
    target_hours: float = 40.0,
    auto_submit: bool = False,
) -> str:
    """Audit weekly logged hours against a target (40h) and optionally submit/approve timesheet."""
    try:
        start_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return _invalid_request(f"Invalid start_date '{start_date}'. Must be in YYYY-MM-DD format.")

    end_date_str = (start_dt + datetime.timedelta(days=6)).strftime("%Y-%m-%d")

    client = await get_client()
    data = await client.list_user_time_entries(user_id, params={"from": start_date, "to": end_date_str})
    entries = data.get("data", []) if isinstance(data, dict) else data
    total_hours = sum(float(e.get("hours", 0.0)) for e in entries if isinstance(e, dict))
    variance = total_hours - target_hours
    is_balanced = abs(variance) < 0.01

    entry_ids = [e.get("id") for e in entries if isinstance(e, dict) and e.get("id")]
    submission_res = None
    if auto_submit and is_balanced and entry_ids:
        submission_res = await client.update_user_approval_status(
            user_id, {"time_entry_ids": entry_ids, "status": "approved"}
        )

    return json.dumps(
        {
            "status": "balanced" if is_balanced else "variance_detected",
            "user_id": user_id,
            "week_start": start_date,
            "week_end": end_date_str,
            "target_hours": target_hours,
            "logged_hours": total_hours,
            "variance": variance,
            "entry_count": len(entry_ids),
            "submitted": bool(submission_res),
            "submission_details": submission_res,
        },
        indent=2,
    )


@mcp.tool()
@rm_tool
async def rm_clone_project_schedule(
    source_project_id: int | str,
    target_project_name: str,
    new_start_date: str | None = None,
    client_id: int | str | None = None,
) -> str:
    """Duplicate project phases, milestone structure, and assignments to a new project."""
    client = await get_client()

    # 1. Fetch source project & phases
    source_project = await client.get_project(source_project_id, params={"with_phases": "true"})
    phases = await client.list_project_phases(source_project_id)
    phase_list = phases.get("data", []) if isinstance(phases, dict) else phases

    # 2. Create target project
    new_proj_payload: dict[str, Any] = {
        "name": target_project_name,
        "project_state": source_project.get("project_state", "Tentative"),
        "project_type": source_project.get("project_type", "Billable"),
        "secure": source_project.get("secure", False),
        "budget": source_project.get("budget"),
        "budget_type": source_project.get("budget_type"),
    }
    if client_id is not None:
        new_proj_payload["client_id"] = client_id
    elif source_project.get("client_id"):
        new_proj_payload["client_id"] = source_project.get("client_id")

    if new_start_date:
        new_proj_payload["starts_at"] = new_start_date

    new_project = await client.create_project(new_proj_payload)
    new_proj_id = new_project.get("id")

    # 3. Recreate phases under target project
    created_phases = []
    if new_proj_id:
        for phase in phase_list:
            if isinstance(phase, dict):
                phase_payload = {
                    "name": phase.get("name", "Phase"),
                    "starts_at": phase.get("starts_at"),
                    "ends_at": phase.get("ends_at"),
                    "budget": phase.get("budget"),
                    "description": phase.get("description"),
                }
                created_p = await client.create_project_phase(new_proj_id, phase_payload)
                created_phases.append(created_p)

    return json.dumps(
        {
            "status": "success",
            "source_project_id": source_project_id,
            "target_project": new_project,
            "cloned_phases_count": len(created_phases),
            "cloned_phases": created_phases,
        },
        indent=2,
    )


@mcp.tool()
@rm_tool
async def rm_bulk_delete_time_entries(
    entry_ids: list[int | str],
    confirm: bool = False,
) -> str:
    """Bulk delete multiple time entries (Destructive: gated by SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 and confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_bulk_delete_time_entries(count={len(entry_ids)})")
    if gate:
        return gate
    client = await get_client()
    deleted = []
    errors = []
    for entry_id in entry_ids:
        try:
            res = await client.delete_time_entry(entry_id)
            deleted.append({"id": entry_id, "status": "deleted", "result": res})
        except SmartsheetRMAPIError as err:
            errors.append({"id": entry_id, "status": "failed", "error": err.to_dict()})

    return json.dumps(
        {
            "status": "success" if not errors else ("partial_success" if deleted else "failed"),
            "deleted_count": len(deleted),
            "failed_count": len(errors),
            "results": deleted,
            "errors": errors,
        },
        indent=2,
    )


@mcp.tool()
@rm_tool
async def rm_bulk_delete_assignments(
    assignment_ids: list[int | str],
    confirm: bool = False,
) -> str:
    """Bulk delete multiple resource assignments (Destructive: gated by SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 and confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_bulk_delete_assignments(count={len(assignment_ids)})")
    if gate:
        return gate
    client = await get_client()
    deleted = []
    errors = []
    for aid in assignment_ids:
        try:
            res = await client.delete_assignment(aid)
            deleted.append({"id": aid, "status": "deleted", "result": res})
        except SmartsheetRMAPIError as err:
            errors.append({"id": aid, "status": "failed", "error": err.to_dict()})

    return json.dumps(
        {
            "status": "success" if not errors else ("partial_success" if deleted else "failed"),
            "deleted_count": len(deleted),
            "failed_count": len(errors),
            "results": deleted,
            "errors": errors,
        },
        indent=2,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 10. APPROVALS, STATUS OPTIONS, USER STATUSES & PLACEHOLDERS (OPENAPI EXTENSIONS)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
@rm_tool
async def rm_list_approvals(page: int = 1, per_page: int = 50) -> str:
    """List submitted time entry and expense approvals across the organization."""
    client = await get_client()
    data = await client.list_approvals(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_approval(
    approvable_type: str,
    approvable_ids: list[int | str],
    status: str = "approved",
    notes: str | None = None,
) -> str:
    """Submit or approve approvable records (approvable_type: 'time_entries' or 'expense_items')."""
    payload: dict[str, Any] = {
        "approvable_type": approvable_type,
        "approvable_ids": approvable_ids,
        "status": status,
    }
    if notes:
        payload["notes"] = notes
    client = await get_client()
    data = await client.create_approval(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_approval(approval_id: int | str, confirm: bool = False) -> str:
    """Delete a pending approval record (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_approval({approval_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_approval(approval_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_status_options() -> str:
    """List account-level assignment work status options."""
    client = await get_client()
    data = await client.list_status_options()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_user_statuses(user_id: int | str) -> str:
    """Retrieve work status history for a specific user (ITO, WFH, SIC, OOO, VAC, OOF)."""
    client = await get_client()
    data = await client.get_user_statuses(user_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_set_user_status(
    user_id: int | str,
    status: str,
    notes: str | None = None,
) -> str:
    """Set current working status for a user (status: 'ITO', 'WFH', 'SIC', 'OOO', 'VAC', 'OOF')."""
    valid_statuses = ("ITO", "WFH", "SIC", "OOO", "VAC", "OOF")
    if status.upper() not in valid_statuses:
        return _invalid_request(f"Status must be one of: {', '.join(valid_statuses)}")
    payload: dict[str, Any] = {"status": status.upper()}
    if notes:
        payload["notes"] = notes
    client = await get_client()
    data = await client.set_user_status(user_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_placeholder_resources(page: int = 1, per_page: int = 50) -> str:
    """List placeholder resources used for forecasting and capacity modeling."""
    client = await get_client()
    data = await client.list_placeholder_resources(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_placeholder_resource(
    title: str,
    role: str | None = None,
    discipline: str | None = None,
    location: str | None = None,
) -> str:
    """Create a placeholder resource."""
    payload: dict[str, Any] = {"title": title}
    if role:
        payload["role"] = role
    if discipline:
        payload["discipline"] = discipline
    if location:
        payload["location"] = location
    client = await get_client()
    data = await client.create_placeholder_resource(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_placeholder_resource(placeholder_id: int | str, confirm: bool = False) -> str:
    """Delete a placeholder resource (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_placeholder_resource({placeholder_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_placeholder_resource(placeholder_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_assignment_subtasks(project_id: int | str, assignment_id: int | str) -> str:
    """List subtasks (checklist tasks) for a project assignment."""
    client = await get_client()
    data = await client.list_subtasks(project_id, assignment_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_assignment_subtask(
    project_id: int | str,
    assignment_id: int | str,
    description: str,
    completed: bool = False,
) -> str:
    """Create a subtask under a project assignment."""
    payload: dict[str, Any] = {"description": description, "completed": completed}
    client = await get_client()
    data = await client.create_subtask(project_id, assignment_id, payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_assignment_subtask(
    project_id: int | str,
    assignment_id: int | str,
    subtask_id: int | str,
    confirm: bool = False,
) -> str:
    """Delete an assignment subtask (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_assignment_subtask({project_id}, {assignment_id}, {subtask_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_subtask(project_id, assignment_id, subtask_id)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_report_rows(report_parameters: dict[str, Any]) -> str:
    """Generate detailed custom report rows."""
    client = await get_client()
    data = await client.get_report_rows(report_parameters)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_get_report_totals(report_parameters: dict[str, Any]) -> str:
    """Generate aggregated custom report totals."""
    client = await get_client()
    data = await client.get_report_totals(report_parameters)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_list_webhooks() -> str:
    """List configured organization webhooks."""
    client = await get_client()
    data = await client.list_webhooks()
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_create_webhook(event_type: str, callback_url: str) -> str:
    """Register a webhook subscription (e.g. 'time.entry.created', 'project.updated', 'assignment.created')."""
    payload: dict[str, Any] = {"event_type": event_type, "url": callback_url}
    client = await get_client()
    data = await client.create_webhook(payload)
    return json.dumps(data, indent=2)


@mcp.tool()
@rm_tool
async def rm_delete_webhook(webhook_id: int | str, confirm: bool = False) -> str:
    """Delete a webhook subscription (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_webhook({webhook_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_webhook(webhook_id)
    return json.dumps(data, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# RESOURCES & PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.resource("rm://capabilities")
def rm_capabilities_resource() -> str:
    """Resource documentation detailing Smartsheet Resource Management capabilities."""
    return json.dumps(
        {
            "server": "mcp-server-smartsheet-rm",
            "version": "1.0.0",
            "domains": [
                "Time Tracking & Approvals",
                "Projects & Phases",
                "Assignments & Scheduling",
                "Users, Roles, Disciplines & Capacity",
                "Clients & Contacts",
                "Leaves & Holidays",
                "Expense Tracking",
                "Tags & Custom Fields",
                "Composite Workflow Recipes",
            ],
            "auth": "Header 'auth: <SMARTSHEET_RM_API_TOKEN>' or env SMARTSHEET_RM_API_TOKEN",
            "base_url": DEFAULT_BASE_URL,
        },
        indent=2,
    )


@mcp.resource("rm://quickstart")
def rm_quickstart_resource() -> str:
    """Quickstart guide for Smartsheet Resource Management MCP operations."""
    return """# Smartsheet Resource Management (10,000ft) MCP Quickstart

## Essential Workflows:
1. **Timesheet Reconciliation**: Run `rm_reconcile_and_submit_week(user_id=123, start_date='2026-08-10')`.
2. **Weekly Time Filling**: Batch-fill working days via `rm_fill_weekly_timesheet(user_id=123, start_date='2026-08-10', daily_hours=8.0)`.
3. **Project Schedule Cloning**: Duplicate templates with `rm_clone_project_schedule(source_project_id=456, target_project_name='Client X Rollout')`.
4. **Capacity Planning**: Inspect capacity with `rm_get_user_availability(user_id=123, from_date='2026-08-01', to_date='2026-08-31')`.
"""


@mcp.prompt()
def timesheet_reconciliation(user_id: str, week_start_date: str) -> str:
    """Audit and reconcile weekly timesheets against 40-hour capacity target."""
    return f"""Audit timesheets for user ID {user_id} for the week starting {week_start_date}.
1. Call rm_list_time_entries for user {user_id} with from_date={week_start_date}.
2. Check for suggestions via rm_list_user_suggestions and unconfirmed entries.
3. Compute total logged hours vs 40-hour target.
4. If balanced, approve using rm_update_time_approval_status or prompt for confirmation."""


@mcp.prompt()
def project_staffing_plan(project_id: str) -> str:
    """Analyze resource assignments and phase timelines for a project."""
    return f"""Evaluate resource assignments and capacity for project ID {project_id}.
1. Fetch project details and phases with rm_get_project and rm_list_project_phases.
2. List scheduled assignments using rm_list_assignments(project_id={project_id}).
3. Identify potential over-allocations or scheduling bottlenecks across disciplines."""


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════


def _handle_shutdown(signum: int, frame: Any) -> None:
    """Gracefully handle SIGTERM/SIGINT from host supervisor to exit with status 0 immediately."""
    os._exit(0)


def main() -> None:
    """Parse CLI arguments and start MCP server."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)
    parser = argparse.ArgumentParser(description="Smartsheet RM MCP Server")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"], help="MCP transport mode")
    parser.add_argument("--host", default="0.0.0.0", help="SSE host")
    parser.add_argument("--port", type=int, default=8080, help="SSE port")
    args = parser.parse_args()

    configure_logging()
    if args.transport == "sse":  # pragma: no cover
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")  # pragma: no cover


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D1: TOOL ANNOTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=True)
_WRITE_SAFE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)
_DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=True)
_IDEMPOTENT = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

_RO_PREFIXES = ("rm_list_", "rm_get_")
_RO_NAMES = {
    "rm_list_time_entries",
    "rm_get_time_entry",
    "rm_list_user_suggestions",
    "rm_list_projects",
    "rm_get_project",
    "rm_list_project_phases",
    "rm_get_project_phase",
    "rm_list_assignments",
    "rm_get_assignment",
    "rm_list_users",
    "rm_get_user",
    "rm_list_user_bill_rates",
    "rm_get_user_availability",
    "rm_get_user_utilization",
    "rm_list_roles",
    "rm_list_disciplines",
    "rm_list_clients",
    "rm_get_client",
    "rm_list_client_contacts",
    "rm_list_leave_types",
    "rm_get_leave_type",
    "rm_list_holidays",
    "rm_get_holiday",
    "rm_list_expenses",
    "rm_get_expense",
    "rm_list_expense_categories",
    "rm_list_tags",
    "rm_list_custom_fields",
    "rm_get_custom_field",
    "rm_list_custom_field_values",
    "rm_list_approvals",
    "rm_list_status_options",
    "rm_get_user_statuses",
    "rm_list_placeholder_resources",
    "rm_list_assignment_subtasks",
    "rm_get_report_rows",
    "rm_get_report_totals",
    "rm_list_webhooks",
}

_DESTRUCTIVE_NAMES = {
    "rm_delete_time_entry",
    "rm_delete_project",
    "rm_delete_project_phase",
    "rm_delete_assignment",
    "rm_delete_user",
    "rm_delete_role",
    "rm_delete_discipline",
    "rm_delete_client",
    "rm_delete_client_contact",
    "rm_delete_leave_type",
    "rm_delete_holiday",
    "rm_delete_expense",
    "rm_delete_expense_category",
    "rm_delete_tag",
    "rm_delete_custom_field",
    "rm_delete_approval",
    "rm_delete_placeholder_resource",
    "rm_delete_assignment_subtask",
    "rm_delete_webhook",
    "rm_bulk_delete_time_entries",
    "rm_bulk_delete_assignments",
}

_IDEMPOTENT_NAMES = {
    "rm_set_custom_field_values",
    "rm_lock_timesheet",
    "rm_update_time_approval_status",
    "rm_set_user_status",
}

for tool_name, tool_obj in mcp._tool_manager._tools.items():
    if tool_name in _DESTRUCTIVE_NAMES:
        tool_obj.annotations = _DESTRUCTIVE
    elif tool_name in _IDEMPOTENT_NAMES:
        tool_obj.annotations = _IDEMPOTENT
    elif tool_name in _RO_NAMES or any(tool_name.startswith(p) for p in _RO_PREFIXES):
        tool_obj.annotations = _READ_ONLY
    else:
        tool_obj.annotations = _WRITE_SAFE


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D2: TOOL PROFILES
# ═══════════════════════════════════════════════════════════════════════════════

_TIME_TOOLS = {
    "rm_list_time_entries",
    "rm_get_time_entry",
    "rm_create_time_entry",
    "rm_update_time_entry",
    "rm_delete_time_entry",
    "rm_list_user_suggestions",
    "rm_update_time_approval_status",
    "rm_lock_timesheet",
    "rm_list_leave_types",
    "rm_get_leave_type",
    "rm_list_holidays",
    "rm_get_holiday",
    "rm_list_approvals",
    "rm_create_approval",
    "rm_delete_approval",
    "rm_get_user_statuses",
    "rm_set_user_status",
    "rm_fill_weekly_timesheet",
    "rm_confirm_suggested_hours",
    "rm_reconcile_and_submit_week",
    "rm_bulk_delete_time_entries",
}

_PROJECTS_TOOLS = {
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
    "rm_list_status_options",
    "rm_list_placeholder_resources",
    "rm_create_placeholder_resource",
    "rm_delete_placeholder_resource",
    "rm_list_assignment_subtasks",
    "rm_create_assignment_subtask",
    "rm_delete_assignment_subtask",
    "rm_clone_project_schedule",
    "rm_bulk_delete_assignments",
}

_ADMIN_TOOLS = {
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
    "rm_create_leave_type",
    "rm_update_leave_type",
    "rm_delete_leave_type",
    "rm_list_holidays",
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
    "rm_list_approvals",
    "rm_create_approval",
    "rm_delete_approval",
    "rm_list_status_options",
    "rm_get_user_statuses",
    "rm_set_user_status",
    "rm_list_placeholder_resources",
    "rm_create_placeholder_resource",
    "rm_delete_placeholder_resource",
    "rm_get_report_rows",
    "rm_get_report_totals",
    "rm_list_webhooks",
    "rm_create_webhook",
    "rm_delete_webhook",
}

_PROFILES = {
    "time": _TIME_TOOLS,
    "projects": _PROJECTS_TOOLS,
    "admin": _ADMIN_TOOLS,
}

_profile = os.environ.get("SMARTSHEET_RM_PROFILE", "full").lower()
if _profile != "full":
    if _profile not in _PROFILES:
        raise ValueError(f"Unknown SMARTSHEET_RM_PROFILE {_profile!r}. Valid: time, projects, admin, full.")
    _allowed = _PROFILES[_profile]
    _to_remove = [name for name in mcp._tool_manager._tools if name not in _allowed]
    for name in _to_remove:
        mcp._tool_manager.remove_tool(name)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D3: READONLY FILTER (SMARTSHEET_RM_READONLY=1)
# ═══════════════════════════════════════════════════════════════════════════════

if os.environ.get("SMARTSHEET_RM_READONLY", "").strip() == "1":
    _ro_remove = [
        name
        for name, tool_obj in mcp._tool_manager._tools.items()
        if not (tool_obj.annotations and tool_obj.annotations.read_only_hint)
    ]
    for name in _ro_remove:
        mcp._tool_manager.remove_tool(name)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE D4: BULK-DESTRUCTIVE GATING (SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1)
# ═══════════════════════════════════════════════════════════════════════════════

_BULK_DESTRUCTIVE_TOOLS = {"rm_bulk_delete_time_entries", "rm_bulk_delete_assignments"}

if os.environ.get("SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE", "").strip() != "1":
    for name in _BULK_DESTRUCTIVE_TOOLS:
        if name in mcp._tool_manager._tools:
            mcp._tool_manager.remove_tool(name)


if __name__ == "__main__":  # pragma: no cover
    main()
