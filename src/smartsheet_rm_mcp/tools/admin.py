"""Administrative, users, clients, tags, expenses, and reporting domain sub-server."""

from __future__ import annotations

import json
from typing import Any

from fastmcp import FastMCP

from smartsheet_rm_mcp import __version__
from smartsheet_rm_mcp.client import DEFAULT_BASE_URL
from smartsheet_rm_mcp.common import (
    ANNOTATION_DESTRUCTIVE,
    ANNOTATION_IDEMPOTENT,
    ANNOTATION_READ_ONLY,
    ANNOTATION_WRITE_SAFE,
    _destructive_gate,
    _invalid_request,
    get_rm_client,
    rm_tool,
)
from smartsheet_rm_mcp.middleware import AdminDomainGuardMiddleware


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
        return _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_user(user_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_user(user_id: int | str, confirm: bool = False) -> str:
    """Delete or archive a user (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_user({user_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_user(user_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_user_bill_rates(user_id: int | str) -> str:
    """List bill rate tiers for a user."""
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
    """Add a bill rate tier with an effective date range for a user."""
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
    """Query scheduled hours vs available capacity for a user."""
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
    """Get billable utilization metrics for a user."""
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
    """Create a new role."""
    client = await get_rm_client()
    data = await client.create_role({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_role(role_id: int | str, name: str) -> str:
    """Update an existing role."""
    client = await get_rm_client()
    data = await client.update_role(role_id, {"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_role(role_id: int | str, confirm: bool = False) -> str:
    """Delete a role (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_role({role_id})")
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
    """Create a new discipline."""
    client = await get_rm_client()
    data = await client.create_discipline({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_discipline(discipline_id: int | str, name: str) -> str:
    """Update an existing discipline."""
    client = await get_rm_client()
    data = await client.update_discipline(discipline_id, {"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_discipline(discipline_id: int | str, confirm: bool = False) -> str:
    """Delete a discipline (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_discipline({discipline_id})")
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
    """List clients."""
    params: dict[str, Any] = {"page": page, "per_page": per_page}
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    client = await get_rm_client()
    data = await client.list_clients(params=params)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_get_client(client_id: int | str) -> str:
    """Get details for a specific client."""
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

    client = await get_rm_client()
    data = await client.update_client(client_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_client(client_id: int | str, confirm: bool = False) -> str:
    """Delete a client record (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_client({client_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_client(client_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_client_contacts(client_id: int | str) -> str:
    """List contacts associated with a client."""
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
    """Add a contact for a client."""
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
    """Delete a client contact (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_client_contact({client_id}, {contact_id})")
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
    """Create a new leave type."""
    client = await get_rm_client()
    data = await client.create_leave_type({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_update_leave_type(leave_type_id: int | str, name: str) -> str:
    """Update a leave type name."""
    client = await get_rm_client()
    data = await client.update_leave_type(leave_type_id, {"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_leave_type(leave_type_id: int | str, confirm: bool = False) -> str:
    """Delete a leave type (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_leave_type({leave_type_id})")
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
    """Get details for a specific holiday."""
    client = await get_rm_client()
    data = await client.get_holiday(holiday_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_holiday(name: str, date: str, end_date: str | None = None) -> str:
    """Create a new company holiday or non-working day."""
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

    client = await get_rm_client()
    data = await client.update_holiday(holiday_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_holiday(holiday_id: int | str, confirm: bool = False) -> str:
    """Delete a holiday (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_holiday({holiday_id})")
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
    """List logged expenses across projects or filtered by project/user."""
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
    """Get details for a specific logged expense."""
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

    client = await get_rm_client()
    data = await client.update_expense(expense_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_expense(expense_id: int | str, confirm: bool = False) -> str:
    """Delete an expense item (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_expense({expense_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_expense(expense_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_expense_categories() -> str:
    """List configured expense categories."""
    client = await get_rm_client()
    data = await client.list_expense_categories()
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_expense_category(name: str) -> str:
    """Create an expense category."""
    client = await get_rm_client()
    data = await client.create_expense_category({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_expense_category(category_id: int | str, confirm: bool = False) -> str:
    """Delete an expense category (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_expense_category({category_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_expense_category(category_id)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_list_tags(page: int = 1, per_page: int = 50) -> str:
    """List tags."""
    client = await get_rm_client()
    data = await client.list_tags(params={"page": page, "per_page": per_page})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_create_tag(name: str) -> str:
    """Create a tag."""
    client = await get_rm_client()
    data = await client.create_tag({"name": name})
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_tag(tag_id: int | str, confirm: bool = False) -> str:
    """Delete a tag (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_tag({tag_id})")
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
        return _invalid_request("No update fields provided")

    client = await get_rm_client()
    data = await client.update_custom_field(custom_field_id, payload)
    return json.dumps(data, indent=2)


@rm_tool
async def rm_delete_custom_field(custom_field_id: int | str, confirm: bool = False) -> str:
    """Delete a custom field definition (Destructive: requires confirm=True)."""
    gate = _destructive_gate(confirm, f"rm_delete_custom_field({custom_field_id})")
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
    """List custom field values for a specific entity."""
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
    """Set custom field values for an entity."""
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
        return _invalid_request(f"Status must be one of: {', '.join(valid_statuses)}")
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
    gate = _destructive_gate(confirm, f"rm_delete_webhook({webhook_id})")
    if gate:
        return gate
    client = await get_rm_client()
    data = await client.delete_webhook(webhook_id)
    return json.dumps(data, indent=2)


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
1. **Timesheet Reconciliation**: Run `rm_reconcile_and_submit_week(user_id=123, start_date='2026-08-10')`.
2. **Weekly Time Filling**: Batch-fill working days via `rm_fill_weekly_timesheet(user_id=123, start_date='2026-08-10', daily_hours=8.0)`.
3. **Project Schedule Cloning**: Duplicate templates with `rm_clone_project_schedule(source_project_id=456, target_project_name='Client X Rollout')`.
4. **Capacity Planning**: Inspect capacity with `rm_get_user_availability(user_id=123, from_date='2026-08-01', to_date='2026-08-31')`.
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
