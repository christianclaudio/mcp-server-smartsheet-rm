"""Time tracking and timesheet domain sub-server for Smartsheet RM MCP."""

from __future__ import annotations

import datetime
import json
from typing import Any

from fastmcp import FastMCP

from smartsheet_rm_mcp.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _destructive_gate,
    _invalid_request,
    get_client,
    rm_tool,
)
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError
from smartsheet_rm_mcp.middleware import TimeDomainGuardMiddleware


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


@rm_tool
async def rm_get_time_entry(entry_id: int | str) -> str:
    """Get details for a specific time entry by its ID."""
    client = await get_client()
    data = await client.get_time_entry(entry_id)
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_delete_time_entry(entry_id: int | str, confirm: bool = False) -> str:
    """Delete a time entry (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_time_entry({entry_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_time_entry(entry_id)
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_list_approvals(page: int = 1, per_page: int = 50) -> str:
    """List submitted time entry and expense approvals across the organization."""
    client = await get_client()
    data = await client.list_approvals(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_delete_approval(approval_id: int | str, confirm: bool = False) -> str:
    """Delete a pending approval record (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_approval({approval_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_approval(approval_id)
    return json.dumps(data, indent=2)


def timesheet_reconciliation(user_id: str, week_start_date: str) -> str:
    """Audit and reconcile weekly timesheets against 40-hour capacity target."""
    return f"""Audit timesheets for user ID {user_id} for the week starting {week_start_date}.
1. Call rm_list_time_entries for user {user_id} with from_date={week_start_date}.
2. Check for suggestions via rm_list_user_suggestions and unconfirmed entries.
3. Compute total logged hours vs 40-hour target.
4. If balanced, approve using rm_update_time_approval_status or prompt for confirmation."""


list_time_entries = rm_list_time_entries
get_time_entry = rm_get_time_entry
create_time_entry = rm_create_time_entry
update_time_entry = rm_update_time_entry
delete_time_entry = rm_delete_time_entry
list_user_suggestions = rm_list_user_suggestions
update_time_approval_status = rm_update_time_approval_status
lock_timesheet = rm_lock_timesheet
fill_weekly_timesheet = rm_fill_weekly_timesheet
confirm_suggested_hours = rm_confirm_suggested_hours
reconcile_and_submit_week = rm_reconcile_and_submit_week
bulk_delete_time_entries = rm_bulk_delete_time_entries
list_approvals = rm_list_approvals
create_approval = rm_create_approval
delete_approval = rm_delete_approval

_TIME_TOOLS_CONFIG = [
    ("list_time_entries", rm_list_time_entries, ANNOTATION_READ_ONLY),
    ("get_time_entry", rm_get_time_entry, ANNOTATION_READ_ONLY),
    ("create_time_entry", rm_create_time_entry, ANNOTATION_WRITE_SAFE),
    ("update_time_entry", rm_update_time_entry, ANNOTATION_WRITE_SAFE),
    ("delete_time_entry", rm_delete_time_entry, ANNOTATION_DESTRUCTIVE),
    ("list_user_suggestions", rm_list_user_suggestions, ANNOTATION_READ_ONLY),
    ("update_time_approval_status", rm_update_time_approval_status, ANNOTATION_IDEMPOTENT),
    ("lock_timesheet", rm_lock_timesheet, ANNOTATION_IDEMPOTENT),
    ("fill_weekly_timesheet", rm_fill_weekly_timesheet, ANNOTATION_WRITE_SAFE),
    ("confirm_suggested_hours", rm_confirm_suggested_hours, ANNOTATION_WRITE_SAFE),
    ("reconcile_and_submit_week", rm_reconcile_and_submit_week, ANNOTATION_WRITE_SAFE),
    ("bulk_delete_time_entries", rm_bulk_delete_time_entries, ANNOTATION_DESTRUCTIVE),
    ("list_approvals", rm_list_approvals, ANNOTATION_READ_ONLY),
    ("create_approval", rm_create_approval, ANNOTATION_WRITE_SAFE),
    ("delete_approval", rm_delete_approval, ANNOTATION_DESTRUCTIVE),
]


def create_time_server() -> FastMCP:
    """Construct a fresh smartsheet-rm-time domain sub-server instance."""
    server = FastMCP("smartsheet-rm-time")
    server.add_middleware(TimeDomainGuardMiddleware())
    for name, tool_fn, tool_annotations in _TIME_TOOLS_CONFIG:
        server.tool(name=name, annotations=tool_annotations)(tool_fn)
    server.prompt()(timesheet_reconciliation)
    return server


time_server = create_time_server()
