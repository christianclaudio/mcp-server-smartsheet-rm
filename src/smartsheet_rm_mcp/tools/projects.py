"""Projects, phases, and assignments domain sub-server for Smartsheet RM MCP."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastmcp import FastMCP

from smartsheet_rm_mcp.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _destructive_gate,
    _invalid_request,
    get_client,
    rm_tool,
)
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError
from smartsheet_rm_mcp.middleware import ProjectsDomainGuardMiddleware


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


@rm_tool
async def rm_get_project(project_id: int | str, with_phases: bool = True) -> str:
    """Get project details including budget, phases, client, and dates."""
    params: dict[str, Any] = {}
    if with_phases:
        params["with_phases"] = "true"
    client = await get_client()
    data = await client.get_project(project_id, params=params)
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_delete_project(project_id: int | str, confirm: bool = False) -> str:
    """Delete a project (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_project({project_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_project(project_id)
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_list_project_phases(project_id: int | str) -> str:
    """List phases belonging to a project."""
    client = await get_client()
    data = await client.list_project_phases(project_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_project_phase(project_id: int | str, phase_id: int | str) -> str:
    """Get details for a specific project phase."""
    client = await get_client()
    data = await client.get_project_phase(project_id, phase_id)
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_get_assignment(assignment_id: int | str) -> str:
    """Get details for a specific assignment."""
    client = await get_client()
    data = await client.get_assignment(assignment_id)
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_delete_assignment(assignment_id: int | str, confirm: bool = False) -> str:
    """Delete a resource assignment (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_assignment({assignment_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_assignment(assignment_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_clone_project_schedule(
    source_project_id: int | str,
    target_project_name: str,
    new_start_date: str | None = None,
    client_id: int | str | None = None,
) -> str:
    """Duplicate project budget settings, timeline, and phase milestone structure to a new project."""
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

    date_offset: timedelta | None = None
    if new_start_date:
        source_start_date = source_project.get("starts_at")
        if not source_start_date:
            return _invalid_request("Source project has no starts_at date to calculate schedule offset")
        try:
            date_offset = date.fromisoformat(new_start_date) - date.fromisoformat(source_start_date)
        except ValueError as exc:
            return _invalid_request(f"Invalid date format: {exc}")

        new_proj_payload["starts_at"] = new_start_date
        if source_project.get("ends_at"):
            try:
                new_proj_payload["ends_at"] = (date.fromisoformat(source_project["ends_at"]) + date_offset).isoformat()
            except ValueError:
                pass

    new_project = await client.create_project(new_proj_payload)
    new_proj_id = new_project.get("id")

    # 3. Recreate phases under target project
    created_phases = []
    if new_proj_id:
        for phase in phase_list:
            if isinstance(phase, dict):
                phase_start = phase.get("starts_at")
                phase_end = phase.get("ends_at")
                if date_offset is not None:
                    if phase_start:
                        try:
                            phase_start = (date.fromisoformat(phase_start) + date_offset).isoformat()
                        except ValueError:
                            pass
                    if phase_end:
                        try:
                            phase_end = (date.fromisoformat(phase_end) + date_offset).isoformat()
                        except ValueError:
                            pass
                phase_payload = {
                    "name": phase.get("name", "Phase"),
                    "starts_at": phase_start,
                    "ends_at": phase_end,
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


@rm_tool
async def rm_list_status_options() -> str:
    """List account-level assignment work status options."""
    client = await get_client()
    data = await client.list_status_options()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_placeholder_resources(page: int = 1, per_page: int = 50) -> str:
    """List placeholder resources used for forecasting and capacity modeling."""
    client = await get_client()
    data = await client.list_placeholder_resources(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


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


@rm_tool
async def rm_delete_placeholder_resource(placeholder_id: int | str, confirm: bool = False) -> str:
    """Delete a placeholder resource (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_placeholder_resource({placeholder_id})")
    if gate:
        return gate
    client = await get_client()
    data = await client.delete_placeholder_resource(placeholder_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_assignment_subtasks(project_id: int | str, assignment_id: int | str) -> str:
    """List subtasks (checklist tasks) for a project assignment."""
    client = await get_client()
    data = await client.list_subtasks(project_id, assignment_id)
    return json.dumps(data, indent=2)


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


def project_staffing_plan(project_id: str) -> str:
    """Analyze resource assignments and phase timelines for a project."""
    return f"""Evaluate resource assignments and capacity for project ID {project_id}.
1. Fetch project details and phases with projects_get_project and projects_list_project_phases.
2. List scheduled assignments using projects_list_assignments(project_id={project_id}).
3. Identify potential over-allocations or scheduling bottlenecks across disciplines."""


list_projects = rm_list_projects
get_project = rm_get_project
create_project = rm_create_project
update_project = rm_update_project
delete_project = rm_delete_project
list_project_users = rm_list_project_users
list_project_phases = rm_list_project_phases
get_project_phase = rm_get_project_phase
create_project_phase = rm_create_project_phase
update_project_phase = rm_update_project_phase
delete_project_phase = rm_delete_project_phase
list_assignments = rm_list_assignments
get_assignment = rm_get_assignment
create_assignment = rm_create_assignment
update_assignment = rm_update_assignment
delete_assignment = rm_delete_assignment
clone_project_schedule = rm_clone_project_schedule
bulk_delete_assignments = rm_bulk_delete_assignments
list_status_options = rm_list_status_options
list_placeholder_resources = rm_list_placeholder_resources
create_placeholder_resource = rm_create_placeholder_resource
delete_placeholder_resource = rm_delete_placeholder_resource
list_assignment_subtasks = rm_list_assignment_subtasks
create_assignment_subtask = rm_create_assignment_subtask
delete_assignment_subtask = rm_delete_assignment_subtask

_PROJECTS_TOOLS_CONFIG = [
    ("list_projects", rm_list_projects, ANNOTATION_READ_ONLY),
    ("get_project", rm_get_project, ANNOTATION_READ_ONLY),
    ("create_project", rm_create_project, ANNOTATION_WRITE_SAFE),
    ("update_project", rm_update_project, ANNOTATION_WRITE_SAFE),
    ("delete_project", rm_delete_project, ANNOTATION_DESTRUCTIVE),
    ("list_project_users", rm_list_project_users, ANNOTATION_READ_ONLY),
    ("list_project_phases", rm_list_project_phases, ANNOTATION_READ_ONLY),
    ("get_project_phase", rm_get_project_phase, ANNOTATION_READ_ONLY),
    ("create_project_phase", rm_create_project_phase, ANNOTATION_WRITE_SAFE),
    ("update_project_phase", rm_update_project_phase, ANNOTATION_WRITE_SAFE),
    ("delete_project_phase", rm_delete_project_phase, ANNOTATION_DESTRUCTIVE),
    ("list_assignments", rm_list_assignments, ANNOTATION_READ_ONLY),
    ("get_assignment", rm_get_assignment, ANNOTATION_READ_ONLY),
    ("create_assignment", rm_create_assignment, ANNOTATION_WRITE_SAFE),
    ("update_assignment", rm_update_assignment, ANNOTATION_WRITE_SAFE),
    ("delete_assignment", rm_delete_assignment, ANNOTATION_DESTRUCTIVE),
    ("clone_project_schedule", rm_clone_project_schedule, ANNOTATION_WRITE_SAFE),
    ("bulk_delete_assignments", rm_bulk_delete_assignments, ANNOTATION_DESTRUCTIVE),
    ("list_status_options", rm_list_status_options, ANNOTATION_READ_ONLY),
    ("list_placeholder_resources", rm_list_placeholder_resources, ANNOTATION_READ_ONLY),
    ("create_placeholder_resource", rm_create_placeholder_resource, ANNOTATION_WRITE_SAFE),
    ("delete_placeholder_resource", rm_delete_placeholder_resource, ANNOTATION_DESTRUCTIVE),
    ("list_assignment_subtasks", rm_list_assignment_subtasks, ANNOTATION_READ_ONLY),
    ("create_assignment_subtask", rm_create_assignment_subtask, ANNOTATION_WRITE_SAFE),
    ("delete_assignment_subtask", rm_delete_assignment_subtask, ANNOTATION_DESTRUCTIVE),
]


def create_projects_server() -> FastMCP:
    """Construct a fresh smartsheet-rm-projects domain sub-server instance."""
    server = FastMCP("smartsheet-rm-projects")
    server.add_middleware(ProjectsDomainGuardMiddleware())
    for name, tool_fn, tool_annotations in _PROJECTS_TOOLS_CONFIG:
        server.tool(name=name, annotations=tool_annotations)(tool_fn)
    server.prompt()(project_staffing_plan)
    return server


projects_server = create_projects_server()
