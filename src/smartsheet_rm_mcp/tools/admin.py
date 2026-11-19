"""Administrative, users, clients, tags, expenses, and reporting domain sub-server."""

from __future__ import annotations

import hmac
import json
from typing import Any

from fastmcp import FastMCP

from smartsheet_rm_mcp import __version__
from smartsheet_rm_mcp.client import DEFAULT_BASE_URL, SmartsheetRMClient
from smartsheet_rm_mcp.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _confirm_gate,
    _destructive_gate,
    _invalid_request,
    _tool_failure,
    get_rm_client,
    rm_tool,
)
from smartsheet_rm_mcp.errors import MASK, SafetyViolationError, redact_payload, remember_secret, remember_token_values
from smartsheet_rm_mcp.middleware import AdminDomainGuardMiddleware

# The published POST /reports/underlying_data.csv operation and the Resource Management docs
# (https://developers.smartsheet.com/api/resource_management/openapi) give no byte limit.
# The export is capped at 10 MB.
_REPORT_CSV_MAX_BYTES = 10 * 1024 * 1024
_REPORT_VIEWS = frozenset(
    {
        "time_fees_hours",
        "time_fees_days",
        "time_fees_amounts",
        "time_fees_hours_and_amounts",
        "time_fees_days_and_amounts",
        "budgets_hours",
        "budgets_days",
        "budgets_amounts",
        "utilization",
        "expenses",
    }
)
_REPORT_TIME_FRAMES = frozenset(
    {
        "this_week",
        "this_month",
        "this_quarter",
        "this_year",
        "last_week",
        "last_month",
        "last_quarter",
        "last_year",
        "next_30",
        "next_60",
        "next_90",
        "last_30",
        "last_60",
        "last_90",
        "last_and_next_90",
    }
)
_REPORT_GROUP_BY = frozenset(
    {
        "user_id",
        "role",
        "discipline",
        "location",
        "assignment_status",
        "date",
        "week",
        "month",
        "project_id",
        "client",
        "leave_type",
        "project_type",
        "project_state",
        "phase_name",
        "record_type",
        "entry_type",
        "category",
        "approval_status",
        "approved_by",
    }
)
_REPORT_CALC = frozenset({"confirmed-unconfirmed", "confirmed", "approved"})
_NEW_TOKEN_NOTE = "Returns the new token once; store it now."


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

    client = await get_rm_client()
    data = await client.list_users(params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_user(user_id: int | str) -> str:
    """Get details for a specific user."""
    client = await get_rm_client()
    data = await client.get_user(user_id)
    return json.dumps(data, indent=2)


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

    client = await get_rm_client()
    data = await client.create_user(payload)
    return json.dumps(data, indent=2)


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
        _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_user(user_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_user(user_id: int | str, confirm: bool = False) -> str:
    """Delete or archive a user (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"admin_delete_user({user_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_user(user_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_user_bill_rates(user_id: int | str) -> str:
    """List bill rate tiers for a user.

    Undocumented route: `GET /users/{user_id}/bill_rates` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `GET /bill_rates` and `GET /projects/{project_id}/bill_rates`.
    """
    client = await get_rm_client()
    data = await client.list_user_bill_rates(user_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_user_bill_rate(
    user_id: int | str,
    rate: float,
    start_date: str,
    end_date: str | None = None,
) -> str:
    """Add a bill rate tier with an effective date range for a user.

    Undocumented route: `POST /users/{user_id}/bill_rates` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents bill rates only under `/bill_rates` and `/projects/{project_id}/bill_rates`, with no create.
    """
    payload: dict[str, Any] = {"rate": rate, "starts_at": start_date}
    if end_date:
        payload["ends_at"] = end_date
    client = await get_rm_client()
    data = await client.create_user_bill_rate(user_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_user_availability(
    user_id: int | str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """Query scheduled hours vs available capacity for a user.

    Undocumented route: `GET /users/{user_id}/availability` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented route: `GET /users/{user_id}/availabilities`.
    """
    params: dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    client = await get_rm_client()
    data = await client.get_user_availability(user_id, params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_user_utilization(
    user_id: int | str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> str:
    """Get billable utilization metrics for a user.

    Undocumented route: `GET /users/{user_id}/utilization` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    params: dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    client = await get_rm_client()
    data = await client.get_user_utilization(user_id, params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_roles() -> str:
    """List all configured user roles."""
    client = await get_rm_client()
    data = await client.list_roles()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_role(name: str) -> str:
    """Create a new role.

    Undocumented route: `POST /roles` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for roles.
    """
    client = await get_rm_client()
    data = await client.create_role({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_role(role_id: int | str, name: str) -> str:
    """Update an existing role.

    Undocumented route: `PUT /roles/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for roles.
    """
    client = await get_rm_client()
    data = await client.update_role(role_id, {"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_role(role_id: int | str, confirm: bool = False) -> str:
    """Delete a role (Destructive: requires confirm=True).

    Undocumented route: `DELETE /roles/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for roles.
    """
    gate = _destructive_gate(confirm, f"admin_delete_role({role_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_role(role_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_disciplines() -> str:
    """List all configured disciplines."""
    client = await get_rm_client()
    data = await client.list_disciplines()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_discipline(name: str) -> str:
    """Create a new discipline.

    Undocumented route: `POST /disciplines` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for disciplines.
    """
    client = await get_rm_client()
    data = await client.create_discipline({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_discipline(discipline_id: int | str, name: str) -> str:
    """Update an existing discipline.

    Undocumented route: `PUT /disciplines/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for disciplines.
    """
    client = await get_rm_client()
    data = await client.update_discipline(discipline_id, {"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_discipline(discipline_id: int | str, confirm: bool = False) -> str:
    """Delete a discipline (Destructive: requires confirm=True).

    Undocumented route: `DELETE /disciplines/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for disciplines.
    """
    gate = _destructive_gate(confirm, f"admin_delete_discipline({discipline_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_discipline(discipline_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_clients(
    page: int = 1,
    per_page: int = 50,
    archived: bool | None = None,
) -> str:
    """List clients.

    Undocumented route: `GET /clients` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    client = await get_rm_client()
    data = await client.list_clients(params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_client(client_id: int | str) -> str:
    """Get details for a specific client.

    Undocumented route: `GET /clients/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    client = await get_rm_client()
    data = await client.get_client(client_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_client(
    name: str,
    address: str | None = None,
    city: str | None = None,
    state: str | None = None,
    zipcode: str | None = None,
    country: str | None = None,
) -> str:
    """Create a new client record.

    Undocumented route: `POST /clients` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
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

    client = await get_rm_client()
    data = await client.create_client(payload)
    return json.dumps(data, indent=2)


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
    """Update a client record.

    Undocumented route: `PUT /clients/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
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
        _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_client(client_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_client(client_id: int | str, confirm: bool = False) -> str:
    """Delete a client record (Destructive: requires confirm=True).

    Undocumented route: `DELETE /clients/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    gate = _destructive_gate(confirm, f"admin_delete_client({client_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_client(client_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_client_contacts(client_id: int | str) -> str:
    """List contacts associated with a client.

    Undocumented route: `GET /clients/{id}/contacts` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    client = await get_rm_client()
    data = await client.list_client_contacts(client_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_client_contact(
    client_id: int | str,
    first_name: str,
    last_name: str,
    email: str | None = None,
    phone: str | None = None,
    title: str | None = None,
) -> str:
    """Add a contact for a client.

    Undocumented route: `POST /clients/{id}/contacts` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    payload: dict[str, Any] = {"first_name": first_name, "last_name": last_name}
    if email:
        payload["email"] = email
    if phone:
        payload["phone"] = phone
    if title:
        payload["title"] = title

    client = await get_rm_client()
    data = await client.create_client_contact(client_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_client_contact(
    client_id: int | str,
    contact_id: int | str,
    confirm: bool = False,
) -> str:
    """Delete a client contact (Destructive: requires confirm=True).

    Undocumented route: `DELETE /clients/{id}/contacts/{contact_id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. No documented equivalent was found.
    """
    gate = _destructive_gate(confirm, f"admin_delete_client_contact({client_id}, {contact_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_client_contact(client_id, contact_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_leave_types() -> str:
    """List leave types (e.g. Vacation, Sick, Parental, PTO)."""
    client = await get_rm_client()
    data = await client.list_leave_types()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_leave_type(leave_type_id: int | str) -> str:
    """Get details for a specific leave type."""
    client = await get_rm_client()
    data = await client.get_leave_type(leave_type_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_leave_type(name: str) -> str:
    """Create a new leave type.

    Undocumented route: `POST /leave_types` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for leave types.
    """
    client = await get_rm_client()
    data = await client.create_leave_type({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_leave_type(leave_type_id: int | str, name: str) -> str:
    """Update a leave type name.

    Undocumented route: `PUT /leave_types/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for leave types.
    """
    client = await get_rm_client()
    data = await client.update_leave_type(leave_type_id, {"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_leave_type(leave_type_id: int | str, confirm: bool = False) -> str:
    """Delete a leave type (Destructive: requires confirm=True).

    Undocumented route: `DELETE /leave_types/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only GET for leave types.
    """
    gate = _destructive_gate(confirm, f"admin_delete_leave_type({leave_type_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_leave_type(leave_type_id)
    return json.dumps(data, indent=2)


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
    client = await get_rm_client()
    data = await client.list_holidays(params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_holiday(holiday_id: int | str) -> str:
    """Get details for a specific holiday.

    Undocumented route: `GET /holidays/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only `GET /holidays`.
    """
    client = await get_rm_client()
    data = await client.get_holiday(holiday_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_holiday(name: str, date: str, end_date: str | None = None) -> str:
    """Create a new company holiday or non-working day.

    Undocumented route: `POST /holidays` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only `GET /holidays`.
    """
    payload: dict[str, Any] = {"name": name, "date": date}
    if end_date:
        payload["ends_at"] = end_date
    client = await get_rm_client()
    data = await client.create_holiday(payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_holiday(
    holiday_id: int | str,
    name: str | None = None,
    date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Update a holiday.

    Undocumented route: `PUT /holidays/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only `GET /holidays`.
    """
    payload: dict[str, Any] = {}
    if name is not None:
        payload["name"] = name
    if date is not None:
        payload["date"] = date
    if end_date is not None:
        payload["ends_at"] = end_date

    if not payload:
        _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_holiday(holiday_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_holiday(holiday_id: int | str, confirm: bool = False) -> str:
    """Delete a holiday (Destructive: requires confirm=True).

    Undocumented route: `DELETE /holidays/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only `GET /holidays`.
    """
    gate = _destructive_gate(confirm, f"admin_delete_holiday({holiday_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_holiday(holiday_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_expenses(
    project_id: int | str | None = None,
    user_id: int | str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = 1,
    per_page: int = 50,
) -> str:
    """List logged expenses across projects or filtered by project/user.

    Undocumented route: `GET /expenses`, `GET /projects/{project_id}/expenses`, `GET /users/{user_id}/expenses` are not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove them without notice. Documented routes: `GET /users/{user_id}/expense_items` and `GET /projects/{project_id}/expense_items`.
    """
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date

    client = await get_rm_client()
    if project_id is not None:
        data = await client.list_project_expenses(project_id, params=params)
    elif user_id is not None:
        data = await client.list_user_expenses(user_id, params=params)
    else:
        data = await client.list_expenses(params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_expense(expense_id: int | str) -> str:
    """Get details for a specific logged expense.

    Undocumented route: `GET /expenses/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `GET /users/{user_id}/expense_items/{expense_item_id}` and `GET /projects/{project_id}/expense_items/{expense_item_id}`.
    """
    client = await get_rm_client()
    data = await client.get_expense(expense_id)
    return json.dumps(data, indent=2)


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
    """Log a project expense item.

    Undocumented route: `POST /expenses` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented route: `POST /users/{user_id}/expense_items`.
    """
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
    client = await get_rm_client()
    data = await client.create_expense(payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_expense(
    expense_id: int | str,
    amount: float | None = None,
    notes: str | None = None,
    is_billable: bool | None = None,
    date: str | None = None,
) -> str:
    """Update a logged expense item.

    Undocumented route: `PUT /expenses/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented route: `PUT /users/{user_id}/expense_items/{expense_item_id}`.
    """
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
        _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_expense(expense_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_expense(expense_id: int | str, confirm: bool = False) -> str:
    """Delete an expense item (Destructive: requires confirm=True).

    Undocumented route: `DELETE /expenses/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented route: `DELETE /users/{user_id}/expense_items/{expense_item_id}`.
    """
    gate = _destructive_gate(confirm, f"admin_delete_expense({expense_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_expense(expense_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_expense_categories() -> str:
    """List configured expense categories.

    Undocumented route: `GET /expense_categories` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented route: `GET /expense_item_categories`.
    """
    client = await get_rm_client()
    data = await client.list_expense_categories()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_expense_category(name: str) -> str:
    """Create an expense category.

    Undocumented route: `POST /expense_categories` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only `GET /expense_item_categories`.
    """
    client = await get_rm_client()
    data = await client.create_expense_category({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_expense_category(category_id: int | str, confirm: bool = False) -> str:
    """Delete an expense category (Destructive: requires confirm=True).

    Undocumented route: `DELETE /expense_categories/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. The reference documents only `GET /expense_item_categories`.
    """
    gate = _destructive_gate(confirm, f"admin_delete_expense_category({category_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_expense_category(category_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_tags(page: int = 1, per_page: int = 50) -> str:
    """List tags.

    Undocumented route: `GET /tags` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `GET /projects/{project_id}/tags` and `GET /users/{user_id}/tags`.
    """
    client = await get_rm_client()
    data = await client.list_tags(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_tag(name: str) -> str:
    """Create a tag.

    Undocumented route: `POST /tags` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `POST /projects/{project_id}/tags` and `POST /users/{user_id}/tags`.
    """
    client = await get_rm_client()
    data = await client.create_tag({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_tag(tag_id: int | str, confirm: bool = False) -> str:
    """Delete a tag (Destructive: requires confirm=True).

    Undocumented route: `DELETE /tags/{id}` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `DELETE /projects/{project_id}/tags/{tag_id}` and `DELETE /users/{user_id}/tags/{tag_id}`.
    """
    gate = _destructive_gate(confirm, f"admin_delete_tag({tag_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_tag(tag_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_custom_fields() -> str:
    """List custom field definitions."""
    client = await get_rm_client()
    data = await client.list_custom_fields()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_custom_field(custom_field_id: int | str) -> str:
    """Get details of a custom field definition."""
    client = await get_rm_client()
    data = await client.get_custom_field(custom_field_id)
    return json.dumps(data, indent=2)


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
    client = await get_rm_client()
    data = await client.create_custom_field(payload)
    return json.dumps(data, indent=2)


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
        _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_custom_field(custom_field_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_custom_field(custom_field_id: int | str, confirm: bool = False) -> str:
    """Delete a custom field definition (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"admin_delete_custom_field({custom_field_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_custom_field(custom_field_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_custom_field_values(
    target_id: int | str | None = None,
    target_type: str | None = None,
) -> str:
    """List custom field values, optionally filtered to one entity by target_id and target_type.

    Undocumented route: `GET /custom_field_values` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `GET /projects/{project_id}/custom_field_values` and `GET /users/{user_id}/custom_field_values`.

    Args:
        target_id: Optional ID of the entity whose values to list; use with target_type.
        target_type: Optional entity type for target_id, such as `Project`, `User` or `Phase`.

    Returns:
        The API's JSON response with the matching custom field values, as a string.
    """
    params: dict[str, Any] = {}
    if target_id is not None:
        params["target_id"] = target_id
    if target_type:
        params["target_type"] = target_type
    client = await get_rm_client()
    data = await client.list_custom_field_values(params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_set_custom_field_values(
    target_id: int | str,
    target_type: str,
    values: dict[str, Any],
) -> str:
    """Set custom field values for an entity.

    Undocumented route: `PUT /custom_field_values` is not in Smartsheet's Resource Management API reference or the archived 10,000ft API docs, so Smartsheet may change or remove it without notice. Documented routes: `PUT /projects/{project_id}/custom_field_values/{custom_field_value_id}` and `PUT /users/{user_id}/custom_field_values/{custom_field_value_id}`.
    """
    payload = {
        "target_id": target_id,
        "target_type": target_type,
        "custom_field_values": values,
    }
    client = await get_rm_client()
    data = await client.set_custom_field_values(payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_user_statuses(user_id: int | str) -> str:
    """Retrieve work status history for a specific user (ITO, WFH, SIC, OOO, VAC, OOF)."""
    client = await get_rm_client()
    data = await client.get_user_statuses(user_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_set_user_status(
    user_id: int | str,
    status: str,
    notes: str | None = None,
) -> str:
    """Set current working status for a user (status: 'ITO', 'WFH', 'SIC', 'OOO', 'VAC', 'OOF')."""
    valid_statuses = ("ITO", "WFH", "SIC", "OOO", "VAC", "OOF")
    if status.upper() not in valid_statuses:
        _invalid_request(f"Status must be one of: {', '.join(valid_statuses)}")
    payload: dict[str, Any] = {"status": status.upper()}
    if notes:
        payload["notes"] = notes
    client = await get_rm_client()
    data = await client.set_user_status(user_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_report_rows(report_parameters: dict[str, Any]) -> str:
    """Generate detailed custom report rows."""
    client = await get_rm_client()
    data = await client.get_report_rows(report_parameters)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_report_totals(report_parameters: dict[str, Any]) -> str:
    """Generate aggregated custom report totals."""
    client = await get_rm_client()
    data = await client.get_report_totals(report_parameters)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_webhooks() -> str:
    """List configured organization webhooks."""
    client = await get_rm_client()
    data = await client.list_webhooks()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_webhook(event_type: str, callback_url: str) -> str:
    """Register a webhook subscription (e.g. 'time.entry.created', 'project.updated', 'assignment.created')."""
    payload: dict[str, Any] = {"event_type": event_type, "url": callback_url}
    client = await get_rm_client()
    data = await client.create_webhook(payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_webhook(webhook_id: int | str, confirm: bool = False) -> str:
    """Delete a webhook subscription (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"admin_delete_webhook({webhook_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_webhook(webhook_id)
    return json.dumps(data, indent=2)


def _availability_body(
    day0: float,
    day1: float,
    day2: float,
    day3: float,
    day4: float,
    day5: float,
    day6: float,
    starts_at: str | None,
    ends_at: str | None,
) -> dict[str, Any]:
    """AvailabilityCreateRequest: day0 through day6 are required hours."""
    days = (day0, day1, day2, day3, day4, day5, day6)
    payload: dict[str, Any] = {}
    for index, hours in enumerate(days):
        if isinstance(hours, bool) or not isinstance(hours, (int, float)):
            _invalid_request(f"day{index} must be a number of hours")
        payload[f"day{index}"] = hours
    if starts_at is not None:
        payload["starts_at"] = starts_at
    if ends_at is not None:
        payload["ends_at"] = ends_at
    return payload


def _report_csv_body(report_parameters: dict[str, Any]) -> dict[str, Any]:
    """ReportRequestParams for POST /reports/underlying_data.csv."""
    if not isinstance(report_parameters, dict):
        _invalid_request("report_parameters must be an object")
    view = report_parameters.get("view")
    if view not in _REPORT_VIEWS:
        _invalid_request("view is required and must be one of the 10 report views")
    time_frame = report_parameters.get("time_frame")
    frame: str | dict[str, str]
    if isinstance(time_frame, str):
        if time_frame not in _REPORT_TIME_FRAMES:
            _invalid_request("time_frame must be a documented shortcut or an object with from and to")
        frame = time_frame
    elif isinstance(time_frame, dict):
        start = time_frame.get("from")
        end = time_frame.get("to")
        if not isinstance(start, str) or not isinstance(end, str):
            _invalid_request("time_frame object requires from and to dates")
        frame = {"from": start, "to": end}
    else:
        _invalid_request("time_frame is required")
    group_by = report_parameters.get("group_by")
    if (
        not isinstance(group_by, list)
        or not 1 <= len(group_by) <= 5
        or any(not isinstance(item, str) or item not in _REPORT_GROUP_BY for item in group_by)
    ):
        _invalid_request("group_by must be 1 to 5 documented group attributes")
    body: dict[str, Any] = {"view": view, "time_frame": frame, "group_by": list(group_by)}
    filters = report_parameters.get("filters")
    if "filters" in report_parameters:
        if not isinstance(filters, dict):
            _invalid_request("filters must be an object")
        body["filters"] = filters
    if "today" in report_parameters:
        today = report_parameters.get("today")
        if not isinstance(today, str):
            _invalid_request("today must be a date string")
        body["today"] = today
    if "calc_incurred_using" in report_parameters:
        calc = report_parameters.get("calc_incurred_using")
        if calc not in _REPORT_CALC:
            _invalid_request("calc_incurred_using must be confirmed-unconfirmed, confirmed, or approved")
        body["calc_incurred_using"] = calc
    return body


def _reject_primary_title(title: str | None) -> None:
    if title is not None and title.strip() == "primary":
        _invalid_request('title cannot be "primary"')


def _token_unusable(value: object) -> bool:
    if not isinstance(value, str):
        return True
    text = value.strip()
    return not text or text == MASK or set(text) <= {"*"}


async def _refuse_if_own_token(token_id: int | str) -> SmartsheetRMClient:
    """Fail closed before delete or renew, and return the client the mutation must use.

    The comparison uses the credential this request resolved to (environment, request header,
    or cached header client), not the environment variable. The token list stays in memory
    and is not returned.
    """
    client = await get_rm_client()
    try:
        tokens = await client.collect_api_tokens()
    except Exception:
        raise SafetyViolationError(
            "Refusing to delete or renew this API token because the token list could not be verified."
        ) from None
    target: dict[str, Any] | None = None
    for item in tokens:
        if str(item.get("id")) == str(token_id):
            target = item
            break
    if target is None:
        raise SafetyViolationError("Refusing to delete or renew this API token because the target was not found.")
    raw = target.get("token")
    if _token_unusable(raw):
        raise SafetyViolationError(
            "Refusing to delete or renew this API token because the target value is missing or masked."
        )
    own = (getattr(client, "api_token", "") or "").strip()
    if not own:
        raise SafetyViolationError(
            "Refusing to delete or renew this API token because the token this request uses could not be determined."
        )
    if hmac.compare_digest(str(raw).strip(), own):
        raise SafetyViolationError(
            "Refusing to delete or renew this API token because it would revoke the token this server uses."
        )
    return client


def _redacted_token_payload(payload: Any) -> str:
    remember_token_values(payload)
    return json.dumps(redact_payload(payload), indent=2)


def _new_token_result(payload: Any) -> str:
    if not isinstance(payload, dict):
        _tool_failure("invalid_response", "The API response did not include a new token.")
    token = payload.get("token")
    if not isinstance(token, str) or not token or token == MASK:
        _tool_failure("invalid_response", "The API response did not include a new token.")
    remember_secret(token)
    return json.dumps(payload, indent=2)


@rm_tool
async def rm_list_bill_rates(page: int = 1, per_page: int = 20) -> str:
    """List account bill rates (GET /bill_rates)."""
    client = await get_rm_client()
    data = await client.list_bill_rates(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_bill_rate(bill_rate_id: int | str) -> str:
    """Fetch one bill rate (GET /bill_rates/{bill_rate_id})."""
    client = await get_rm_client()
    data = await client.get_bill_rate(bill_rate_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_availability_blocks(user_id: int | str, page: int = 1, per_page: int = 20) -> str:
    """List a user's availability time blocks (GET /users/{user_id}/availabilities).

    These are the documented availability time blocks, not the undocumented /availability route
    behind admin_get_user_availability. Changing part-time availability does not update existing
    assignments or their suggested time entries.
    """
    client = await get_rm_client()
    data = await client.list_availability_blocks(user_id, params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_availability_block(user_id: int | str, availability_id: int | str) -> str:
    """Fetch one availability time block (GET /users/{user_id}/availabilities/{id}).

    Changing part-time availability does not update existing assignments or their suggested time entries.
    """
    client = await get_rm_client()
    data = await client.get_availability_block(user_id, availability_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_availability_block(
    user_id: int | str,
    day0: float,
    day1: float,
    day2: float,
    day3: float,
    day4: float,
    day5: float,
    day6: float,
    starts_at: str | None = None,
    ends_at: str | None = None,
) -> str:
    """Create an availability time block (POST /users/{user_id}/availabilities).

    day0 (Sunday) through day6 (Saturday) are required hours. starts_at and ends_at are optional
    dates. Changing part-time availability does not update existing assignments or their
    suggested time entries.
    """
    payload = _availability_body(day0, day1, day2, day3, day4, day5, day6, starts_at, ends_at)
    client = await get_rm_client()
    data = await client.create_availability_block(user_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_availability_block(
    user_id: int | str,
    availability_id: int | str,
    day0: float,
    day1: float,
    day2: float,
    day3: float,
    day4: float,
    day5: float,
    day6: float,
    starts_at: str | None = None,
    ends_at: str | None = None,
) -> str:
    """Update an availability time block (PUT /users/{user_id}/availabilities/{id}).

    day0 through day6 are required. Changing part-time availability does not update existing
    assignments or their suggested time entries.
    """
    payload = _availability_body(day0, day1, day2, day3, day4, day5, day6, starts_at, ends_at)
    client = await get_rm_client()
    data = await client.update_availability_block(user_id, availability_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_availability_block(user_id: int | str, availability_id: int | str, confirm: bool = False) -> str:
    """Delete an availability time block (DELETE /users/{user_id}/availabilities/{id}).

    Changing part-time availability does not update existing assignments or their suggested time entries.
    """
    gate = _destructive_gate(confirm, f"admin_delete_availability_block({user_id}, {availability_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_availability_block(user_id, availability_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_discipline(discipline_id: int | str) -> str:
    """Fetch one discipline (GET /disciplines/{discipline_id})."""
    client = await get_rm_client()
    data = await client.get_discipline(discipline_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_role(role_id: int | str) -> str:
    """Fetch one role (GET /roles/{role_id})."""
    client = await get_rm_client()
    data = await client.get_role(role_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_webhook(webhook_id: int | str) -> str:
    """Fetch one webhook (GET /webhooks/{webhook_id})."""
    client = await get_rm_client()
    data = await client.get_webhook(webhook_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_project_expense_item(project_id: int | str, expense_item_id: int | str) -> str:
    """Fetch one project expense item (GET /projects/{project_id}/expense_items/{expense_item_id})."""
    client = await get_rm_client()
    data = await client.get_project_expense_item(project_id, expense_item_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_user_expense_item(user_id: int | str, expense_item_id: int | str) -> str:
    """Fetch one user expense item (GET /users/{user_id}/expense_items/{expense_item_id})."""
    client = await get_rm_client()
    data = await client.get_user_expense_item(user_id, expense_item_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_project_custom_field_value(project_id: int | str, custom_field_value_id: int | str) -> str:
    """Fetch one project custom field value (GET /projects/{project_id}/custom_field_values/{id})."""
    client = await get_rm_client()
    data = await client.get_project_custom_field_value(project_id, custom_field_value_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_user_custom_field_value(user_id: int | str, custom_field_value_id: int | str) -> str:
    """Fetch one user custom field value (GET /users/{user_id}/custom_field_values/{id})."""
    client = await get_rm_client()
    data = await client.get_user_custom_field_value(user_id, custom_field_value_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_export_report_csv(report_parameters: dict[str, Any]) -> str:
    """Export report rows as CSV (POST /reports/underlying_data.csv).

    This POST has no side effects, like admin_get_report_rows. view, time_frame, and group_by
    are required. The body is capped at 10 MB because the published operation gives no smaller
    limit. Returns {"csv": text, "bytes": n}.
    """
    payload = _report_csv_body(report_parameters)
    client = await get_rm_client()
    text, size = await client.export_report_csv(payload)
    if size > _REPORT_CSV_MAX_BYTES:
        _tool_failure(
            "response_too_large",
            f"Report CSV is {size} bytes, above the 10 MB cap.",
        )
    return json.dumps({"csv": text, "bytes": size}, indent=2)


@rm_tool
async def rm_list_api_tokens() -> str:
    """List organization API tokens (GET /api_tokens, on /api rather than /api/v1).

    Admin and sensitive: manages your organization's API tokens. Token values are masked.
    """
    client = await get_rm_client()
    data = await client.list_api_tokens()
    return _redacted_token_payload(data)


@rm_tool
async def rm_create_api_token(title: str | None = None, confirm: bool = False) -> str:
    """Create an API token (POST /api_tokens). title is an optional query parameter and cannot be "primary".

    Admin and sensitive: manages your organization's API tokens. Requires confirm=true.
    Returns the new token once; store it now.
    """
    gate = _confirm_gate(confirm, "admin_create_api_token", _NEW_TOKEN_NOTE)
    if gate:
        return gate
    _reject_primary_title(title)
    client = await get_rm_client()
    data = await client.create_api_token(title)
    return _new_token_result(data)


@rm_tool
async def rm_update_api_token(token_id: int | str, title: str) -> str:
    """Update an API token title (PUT /api_tokens/{id}). title is a required query parameter.

    Admin and sensitive: manages your organization's API tokens. title cannot be "primary".
    The token value in the response is masked.
    """
    if not title or not title.strip():
        _invalid_request("title is required")
    _reject_primary_title(title)
    client = await get_rm_client()
    data = await client.update_api_token(token_id, title)
    return _redacted_token_payload(data)


@rm_tool
async def rm_delete_api_token(token_id: int | str, confirm: bool = False) -> str:
    """Delete an API token (DELETE /api_tokens/{id}). Requires confirm=true.

    Admin and sensitive: manages your organization's API tokens. Refuses when the target is
    the token this server uses, when the token list cannot be verified, or when the target
    value is missing or masked.
    """
    gate = _destructive_gate(confirm, f"admin_delete_api_token({token_id})")
    if gate:
        return gate
    client = await _refuse_if_own_token(token_id)
    data = await client.delete_api_token(token_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_renew_api_token(token_id: int | str, confirm: bool = False) -> str:
    """Renew an API token (POST /api_tokens/renew/{id}). The API deletes the token and creates a new one.

    Admin and sensitive: manages your organization's API tokens. Requires confirm=true.
    Returns the new token once; store it now. Refuses when the target is the token this
    server uses.
    """
    gate = _destructive_gate(confirm, f"admin_renew_api_token({token_id})")
    if gate:
        return gate
    client = await _refuse_if_own_token(token_id)
    data = await client.renew_api_token(token_id)
    return _new_token_result(data)


def rm_capabilities_resource() -> str:
    """Resource documentation detailing Smartsheet Resource Management capabilities."""
    return json.dumps(
        {
            "server": "mcp-server-smartsheet-rm",
            "version": __version__,
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


def rm_quickstart_resource() -> str:
    """Quickstart guide for Smartsheet Resource Management MCP operations."""
    return """# Smartsheet Resource Management (10,000ft) MCP Quickstart

## Essential Workflows:
1. **Timesheet Reconciliation**: Run `time_reconcile_and_submit_week(user_id=123, start_date='2026-08-10')`.
2. **Weekly Time Filling**: Batch-fill working days via `time_fill_weekly_timesheet(user_id=123, start_date='2026-08-10', daily_hours=8.0)`.
3. **Project Schedule Cloning**: Duplicate templates with `projects_clone_project_schedule(source_project_id=456, target_project_name='Client X Rollout')`.
4. **Capacity Planning**: Inspect capacity with `admin_get_user_availability(user_id=123, from_date='2026-08-01', to_date='2026-08-31')`.
"""


list_users = rm_list_users
get_user = rm_get_user
create_user = rm_create_user
update_user = rm_update_user
delete_user = rm_delete_user
list_user_bill_rates = rm_list_user_bill_rates
create_user_bill_rate = rm_create_user_bill_rate
get_user_availability = rm_get_user_availability
get_user_utilization = rm_get_user_utilization
list_roles = rm_list_roles
create_role = rm_create_role
update_role = rm_update_role
delete_role = rm_delete_role
list_disciplines = rm_list_disciplines
create_discipline = rm_create_discipline
update_discipline = rm_update_discipline
delete_discipline = rm_delete_discipline
list_clients = rm_list_clients
get_client = rm_get_client
create_client = rm_create_client
update_client = rm_update_client
delete_client = rm_delete_client
list_client_contacts = rm_list_client_contacts
create_client_contact = rm_create_client_contact
delete_client_contact = rm_delete_client_contact
list_leave_types = rm_list_leave_types
get_leave_type = rm_get_leave_type
create_leave_type = rm_create_leave_type
update_leave_type = rm_update_leave_type
delete_leave_type = rm_delete_leave_type
list_holidays = rm_list_holidays
get_holiday = rm_get_holiday
create_holiday = rm_create_holiday
update_holiday = rm_update_holiday
delete_holiday = rm_delete_holiday
list_expenses = rm_list_expenses
get_expense = rm_get_expense
create_expense = rm_create_expense
update_expense = rm_update_expense
delete_expense = rm_delete_expense
list_expense_categories = rm_list_expense_categories
create_expense_category = rm_create_expense_category
delete_expense_category = rm_delete_expense_category
list_tags = rm_list_tags
create_tag = rm_create_tag
delete_tag = rm_delete_tag
list_custom_fields = rm_list_custom_fields
get_custom_field = rm_get_custom_field
create_custom_field = rm_create_custom_field
update_custom_field = rm_update_custom_field
delete_custom_field = rm_delete_custom_field
list_custom_field_values = rm_list_custom_field_values
set_custom_field_values = rm_set_custom_field_values
get_user_statuses = rm_get_user_statuses
set_user_status = rm_set_user_status
get_report_rows = rm_get_report_rows
get_report_totals = rm_get_report_totals
list_webhooks = rm_list_webhooks
create_webhook = rm_create_webhook
delete_webhook = rm_delete_webhook
list_bill_rates = rm_list_bill_rates
get_bill_rate = rm_get_bill_rate
list_availability_blocks = rm_list_availability_blocks
get_availability_block = rm_get_availability_block
create_availability_block = rm_create_availability_block
update_availability_block = rm_update_availability_block
delete_availability_block = rm_delete_availability_block
get_discipline = rm_get_discipline
get_role = rm_get_role
get_webhook = rm_get_webhook
get_project_expense_item = rm_get_project_expense_item
get_user_expense_item = rm_get_user_expense_item
get_project_custom_field_value = rm_get_project_custom_field_value
get_user_custom_field_value = rm_get_user_custom_field_value
export_report_csv = rm_export_report_csv
list_api_tokens = rm_list_api_tokens
create_api_token = rm_create_api_token
update_api_token = rm_update_api_token
delete_api_token = rm_delete_api_token
renew_api_token = rm_renew_api_token

_ADMIN_TOOLS_CONFIG = [
    ("list_users", rm_list_users, ANNOTATION_READ_ONLY),
    ("get_user", rm_get_user, ANNOTATION_READ_ONLY),
    ("create_user", rm_create_user, ANNOTATION_WRITE_SAFE),
    ("update_user", rm_update_user, ANNOTATION_WRITE_SAFE),
    ("delete_user", rm_delete_user, ANNOTATION_DESTRUCTIVE),
    ("list_user_bill_rates", rm_list_user_bill_rates, ANNOTATION_READ_ONLY),
    ("create_user_bill_rate", rm_create_user_bill_rate, ANNOTATION_WRITE_SAFE),
    ("get_user_availability", rm_get_user_availability, ANNOTATION_READ_ONLY),
    ("get_user_utilization", rm_get_user_utilization, ANNOTATION_READ_ONLY),
    ("list_roles", rm_list_roles, ANNOTATION_READ_ONLY),
    ("create_role", rm_create_role, ANNOTATION_WRITE_SAFE),
    ("update_role", rm_update_role, ANNOTATION_WRITE_SAFE),
    ("delete_role", rm_delete_role, ANNOTATION_DESTRUCTIVE),
    ("list_disciplines", rm_list_disciplines, ANNOTATION_READ_ONLY),
    ("create_discipline", rm_create_discipline, ANNOTATION_WRITE_SAFE),
    ("update_discipline", rm_update_discipline, ANNOTATION_WRITE_SAFE),
    ("delete_discipline", rm_delete_discipline, ANNOTATION_DESTRUCTIVE),
    ("list_clients", rm_list_clients, ANNOTATION_READ_ONLY),
    ("get_client", rm_get_client, ANNOTATION_READ_ONLY),
    ("create_client", rm_create_client, ANNOTATION_WRITE_SAFE),
    ("update_client", rm_update_client, ANNOTATION_WRITE_SAFE),
    ("delete_client", rm_delete_client, ANNOTATION_DESTRUCTIVE),
    ("list_client_contacts", rm_list_client_contacts, ANNOTATION_READ_ONLY),
    ("create_client_contact", rm_create_client_contact, ANNOTATION_WRITE_SAFE),
    ("delete_client_contact", rm_delete_client_contact, ANNOTATION_DESTRUCTIVE),
    ("list_leave_types", rm_list_leave_types, ANNOTATION_READ_ONLY),
    ("get_leave_type", rm_get_leave_type, ANNOTATION_READ_ONLY),
    ("create_leave_type", rm_create_leave_type, ANNOTATION_WRITE_SAFE),
    ("update_leave_type", rm_update_leave_type, ANNOTATION_WRITE_SAFE),
    ("delete_leave_type", rm_delete_leave_type, ANNOTATION_DESTRUCTIVE),
    ("list_holidays", rm_list_holidays, ANNOTATION_READ_ONLY),
    ("get_holiday", rm_get_holiday, ANNOTATION_READ_ONLY),
    ("create_holiday", rm_create_holiday, ANNOTATION_WRITE_SAFE),
    ("update_holiday", rm_update_holiday, ANNOTATION_WRITE_SAFE),
    ("delete_holiday", rm_delete_holiday, ANNOTATION_DESTRUCTIVE),
    ("list_expenses", rm_list_expenses, ANNOTATION_READ_ONLY),
    ("get_expense", rm_get_expense, ANNOTATION_READ_ONLY),
    ("create_expense", rm_create_expense, ANNOTATION_WRITE_SAFE),
    ("update_expense", rm_update_expense, ANNOTATION_WRITE_SAFE),
    ("delete_expense", rm_delete_expense, ANNOTATION_DESTRUCTIVE),
    ("list_expense_categories", rm_list_expense_categories, ANNOTATION_READ_ONLY),
    ("create_expense_category", rm_create_expense_category, ANNOTATION_WRITE_SAFE),
    ("delete_expense_category", rm_delete_expense_category, ANNOTATION_DESTRUCTIVE),
    ("list_tags", rm_list_tags, ANNOTATION_READ_ONLY),
    ("create_tag", rm_create_tag, ANNOTATION_WRITE_SAFE),
    ("delete_tag", rm_delete_tag, ANNOTATION_DESTRUCTIVE),
    ("list_custom_fields", rm_list_custom_fields, ANNOTATION_READ_ONLY),
    ("get_custom_field", rm_get_custom_field, ANNOTATION_READ_ONLY),
    ("create_custom_field", rm_create_custom_field, ANNOTATION_WRITE_SAFE),
    ("update_custom_field", rm_update_custom_field, ANNOTATION_WRITE_SAFE),
    ("delete_custom_field", rm_delete_custom_field, ANNOTATION_DESTRUCTIVE),
    ("list_custom_field_values", rm_list_custom_field_values, ANNOTATION_READ_ONLY),
    ("set_custom_field_values", rm_set_custom_field_values, ANNOTATION_IDEMPOTENT),
    ("get_user_statuses", rm_get_user_statuses, ANNOTATION_READ_ONLY),
    ("set_user_status", rm_set_user_status, ANNOTATION_IDEMPOTENT),
    ("get_report_rows", rm_get_report_rows, ANNOTATION_READ_ONLY),
    ("get_report_totals", rm_get_report_totals, ANNOTATION_READ_ONLY),
    ("list_webhooks", rm_list_webhooks, ANNOTATION_READ_ONLY),
    ("create_webhook", rm_create_webhook, ANNOTATION_WRITE_SAFE),
    ("delete_webhook", rm_delete_webhook, ANNOTATION_DESTRUCTIVE),
    ("list_bill_rates", rm_list_bill_rates, ANNOTATION_READ_ONLY),
    ("get_bill_rate", rm_get_bill_rate, ANNOTATION_READ_ONLY),
    ("list_availability_blocks", rm_list_availability_blocks, ANNOTATION_READ_ONLY),
    ("get_availability_block", rm_get_availability_block, ANNOTATION_READ_ONLY),
    ("create_availability_block", rm_create_availability_block, ANNOTATION_WRITE_SAFE),
    ("update_availability_block", rm_update_availability_block, ANNOTATION_WRITE_SAFE),
    ("delete_availability_block", rm_delete_availability_block, ANNOTATION_DESTRUCTIVE),
    ("get_discipline", rm_get_discipline, ANNOTATION_READ_ONLY),
    ("get_role", rm_get_role, ANNOTATION_READ_ONLY),
    ("get_webhook", rm_get_webhook, ANNOTATION_READ_ONLY),
    ("get_project_expense_item", rm_get_project_expense_item, ANNOTATION_READ_ONLY),
    ("get_user_expense_item", rm_get_user_expense_item, ANNOTATION_READ_ONLY),
    ("get_project_custom_field_value", rm_get_project_custom_field_value, ANNOTATION_READ_ONLY),
    ("get_user_custom_field_value", rm_get_user_custom_field_value, ANNOTATION_READ_ONLY),
    ("export_report_csv", rm_export_report_csv, ANNOTATION_READ_ONLY),
    ("list_api_tokens", rm_list_api_tokens, ANNOTATION_READ_ONLY),
    ("create_api_token", rm_create_api_token, ANNOTATION_WRITE_SAFE),
    ("update_api_token", rm_update_api_token, ANNOTATION_WRITE_SAFE),
    ("delete_api_token", rm_delete_api_token, ANNOTATION_DESTRUCTIVE),
    ("renew_api_token", rm_renew_api_token, ANNOTATION_DESTRUCTIVE),
]


def create_admin_server() -> FastMCP:
    """Construct a fresh smartsheet-rm-admin domain sub-server instance."""
    server = FastMCP("smartsheet-rm-admin")
    server.add_middleware(AdminDomainGuardMiddleware())
    for name, tool_fn, tool_annotations in _ADMIN_TOOLS_CONFIG:
        server.tool(name=name, annotations=tool_annotations)(tool_fn)
    server.resource("rm://capabilities")(rm_capabilities_resource)
    server.resource("rm://quickstart")(rm_quickstart_resource)
    return server


admin_server = create_admin_server()
