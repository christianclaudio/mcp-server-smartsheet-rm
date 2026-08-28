"""Smartsheet Resource Management (10,000ft) REST API Client (async).

Covers the full API v1 surface for Resource Management by Smartsheet:
- Time Tracking & Timesheet Reconciliation
- Projects, Phases & Milestones
- Resource Scheduling & Allocations
- Users, Roles, Disciplines & Capacity
- Clients & Contacts
- Leaves, Holidays & PTO
- Expenses & Expense Categories
- Tags & Custom Fields
"""

from __future__ import annotations

import asyncio
import random
from typing import Any
from urllib.parse import quote

import httpx
from httpx import Response

from .errors import SmartsheetRMAPIError

JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None

DEFAULT_BASE_URL = "https://api.rm.smartsheet.com/api/v1"


class SmartsheetRMClient:
    """Asynchronous HTTP Client for Smartsheet Resource Management API."""

    def __init__(
        self,
        api_token: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_retry_delay: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_token = api_token.strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_retry_delay = max_retry_delay
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx.AsyncClient(timeout=60.0)

    async def aclose(self) -> None:
        """Close the underlying HTTP transport if owned by this client."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> SmartsheetRMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    def _headers(self) -> dict[str, str]:
        """Headers required by Smartsheet RM (10,000ft) API."""
        return {
            "auth": self.api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "mcp-server-smartsheet-rm/1.0.0",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: JSONValue = None,
        allow_statuses: frozenset[int] | None = None,
    ) -> Response:
        """Execute an HTTP request with exponential backoff on 429 and transient errors."""
        cleaned_path = "/" + path.lstrip("/")
        safe_segments = [quote(seg, safe="") if seg else "" for seg in cleaned_path.split("/")]
        safe_path = "/".join(safe_segments)
        url = f"{self.base_url}{safe_path}"

        clean_params = None
        if params is not None:
            clean_params = {k: v for k, v in params.items() if v is not None}

        for attempt in range(self.max_retries + 1):
            r = await self._http.request(
                method,
                url,
                headers=self._headers(),
                params=clean_params,
                json=json_data,
                timeout=60.0,
            )

            if r.status_code != 429:
                if r.status_code >= 400 and not (allow_statuses and r.status_code in allow_statuses):
                    detail: Any = None
                    try:
                        detail = r.json()
                    except Exception:
                        detail = r.text[:500] if r.text else None
                    request_id = r.headers.get("x-request-id") or r.headers.get("request-id")
                    raise SmartsheetRMAPIError(
                        status_code=r.status_code,
                        path=path,
                        method=method,
                        detail=detail,
                        request_id=request_id,
                    )
                return r

            if attempt == self.max_retries:
                raise SmartsheetRMAPIError(
                    status_code=429,
                    path=path,
                    method=method,
                    detail="Rate limit exceeded after max retries",
                    request_id=r.headers.get("x-request-id") or r.headers.get("request-id"),
                )

            retry_after = r.headers.get("Retry-After")
            parsed_delay = self._parse_retry_after(retry_after)
            if parsed_delay is not None:
                delay = parsed_delay * random.uniform(1.0, 1.3)
            else:
                delay = self.base_delay * (2**attempt) * random.uniform(0.5, 1.5)
            delay = min(delay, self.max_retry_delay)
            await asyncio.sleep(delay)

        raise SmartsheetRMAPIError(
            status_code=429, path=path, method=method, detail="Rate limit exceeded"
        )  # pragma: no cover

    @staticmethod
    def _parse_retry_after(header_val: str | None) -> float | None:
        """Parse Retry-After header as integer or float seconds."""
        if not header_val:
            return None
        try:
            val = float(header_val)
            return max(0.0, val)
        except (ValueError, OverflowError):
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. TIME TRACKING & APPROVALS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_time_entries(self, params: dict[str, Any] | None = None) -> Any:
        """List time entries across organization."""
        r = await self._request("GET", "/time_entries", params=params)
        return r.json()

    async def get_time_entry(self, entry_id: int | str) -> Any:
        """Get details for a specific time entry."""
        r = await self._request("GET", f"/time_entries/{entry_id}")
        return r.json()

    async def create_time_entry(self, data: dict[str, Any]) -> Any:
        """Create a new time entry."""
        r = await self._request("POST", "/time_entries", json_data=data)
        return r.json()

    async def update_time_entry(self, entry_id: int | str, data: dict[str, Any]) -> Any:
        """Update an existing time entry."""
        r = await self._request("PUT", f"/time_entries/{entry_id}", json_data=data)
        return r.json()

    async def delete_time_entry(self, entry_id: int | str) -> Any:
        """Delete a time entry."""
        r = await self._request("DELETE", f"/time_entries/{entry_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": entry_id}

    async def list_project_time_entries(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List time entries associated with a project."""
        r = await self._request("GET", f"/projects/{project_id}/time_entries", params=params)
        return r.json()

    async def create_project_time_entry(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Create a time entry directly under a project."""
        r = await self._request("POST", f"/projects/{project_id}/time_entries", json_data=data)
        return r.json()

    async def list_user_time_entries(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List time entries logged by a specific user."""
        r = await self._request("GET", f"/users/{user_id}/time_entries", params=params)
        return r.json()

    async def create_user_time_entry(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Create a time entry directly under a user."""
        r = await self._request("POST", f"/users/{user_id}/time_entries", json_data=data)
        return r.json()

    async def update_user_approval_status(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Approve or reject time entries for a user."""
        r = await self._request("PUT", f"/users/{user_id}/time_entries/approval_status", json_data=data)
        return r.json() if r.content else {"status": "updated", "user_id": user_id}

    async def lock_user_timesheet(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Lock or unlock timesheet records for a user up to a given date."""
        r = await self._request("POST", f"/users/{user_id}/time_entries/lock", json_data=data)
        return r.json() if r.content else {"status": "locked", "user_id": user_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. PROJECTS & PHASES
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_projects(self, params: dict[str, Any] | None = None) -> Any:
        """List projects in the organization."""
        r = await self._request("GET", "/projects", params=params)
        return r.json()

    async def get_project(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get details of a single project."""
        r = await self._request("GET", f"/projects/{project_id}", params=params)
        return r.json()

    async def create_project(self, data: dict[str, Any]) -> Any:
        """Create a new project."""
        r = await self._request("POST", "/projects", json_data=data)
        return r.json()

    async def update_project(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Update project details, budget, dates, or state."""
        r = await self._request("PUT", f"/projects/{project_id}", json_data=data)
        return r.json()

    async def delete_project(self, project_id: int | str) -> Any:
        """Delete or archive a project."""
        r = await self._request("DELETE", f"/projects/{project_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": project_id}

    async def list_project_users(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List users associated with or assigned to a project."""
        r = await self._request("GET", f"/projects/{project_id}/users", params=params)
        return r.json()

    async def list_project_phases(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List phases for a given project."""
        r = await self._request("GET", f"/projects/{project_id}/phases", params=params)
        return r.json()

    async def get_project_phase(self, project_id: int | str, phase_id: int | str) -> Any:
        """Get details of a specific project phase."""
        r = await self._request("GET", f"/projects/{project_id}/phases/{phase_id}")
        return r.json()

    async def create_project_phase(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Create a new phase under a project."""
        r = await self._request("POST", f"/projects/{project_id}/phases", json_data=data)
        return r.json()

    async def update_project_phase(self, project_id: int | str, phase_id: int | str, data: dict[str, Any]) -> Any:
        """Update a project phase."""
        r = await self._request("PUT", f"/projects/{project_id}/phases/{phase_id}", json_data=data)
        return r.json()

    async def delete_project_phase(self, project_id: int | str, phase_id: int | str) -> Any:
        """Delete a project phase."""
        r = await self._request(
            "DELETE", f"/projects/{project_id}/phases/{phase_id}", allow_statuses=frozenset({200, 204})
        )
        return r.json() if r.content else {"status": "deleted", "phase_id": phase_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. ASSIGNMENTS & SCHEDULING
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_assignments(self, params: dict[str, Any] | None = None) -> Any:
        """List resource scheduling assignments."""
        r = await self._request("GET", "/assignments", params=params)
        return r.json()

    async def get_assignment(self, assignment_id: int | str) -> Any:
        """Get a specific assignment."""
        r = await self._request("GET", f"/assignments/{assignment_id}")
        return r.json()

    async def create_assignment(self, data: dict[str, Any]) -> Any:
        """Create a new resource assignment."""
        r = await self._request("POST", "/assignments", json_data=data)
        return r.json()

    async def update_assignment(self, assignment_id: int | str, data: dict[str, Any]) -> Any:
        """Update an existing resource assignment."""
        r = await self._request("PUT", f"/assignments/{assignment_id}", json_data=data)
        return r.json()

    async def delete_assignment(self, assignment_id: int | str) -> Any:
        """Delete a resource assignment."""
        r = await self._request("DELETE", f"/assignments/{assignment_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": assignment_id}

    async def list_project_assignments(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List assignments for a specific project."""
        r = await self._request("GET", f"/projects/{project_id}/assignments", params=params)
        return r.json()

    async def create_project_assignment(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Create an assignment directly under a project."""
        r = await self._request("POST", f"/projects/{project_id}/assignments", json_data=data)
        return r.json()

    async def list_user_assignments(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List assignments for a specific user."""
        r = await self._request("GET", f"/users/{user_id}/assignments", params=params)
        return r.json()

    async def create_user_assignment(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Create an assignment directly under a user."""
        r = await self._request("POST", f"/users/{user_id}/assignments", json_data=data)
        return r.json()

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. USERS, ROLES, DISCIPLINES & CAPACITY
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_users(self, params: dict[str, Any] | None = None) -> Any:
        """List users in the organization."""
        r = await self._request("GET", "/users", params=params)
        return r.json()

    async def get_user(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get details for a specific user."""
        r = await self._request("GET", f"/users/{user_id}", params=params)
        return r.json()

    async def create_user(self, data: dict[str, Any]) -> Any:
        """Create a new user profile."""
        r = await self._request("POST", "/users", json_data=data)
        return r.json()

    async def update_user(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Update a user profile."""
        r = await self._request("PUT", f"/users/{user_id}", json_data=data)
        return r.json()

    async def delete_user(self, user_id: int | str) -> Any:
        """Delete / archive a user."""
        r = await self._request("DELETE", f"/users/{user_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": user_id}

    async def list_user_bill_rates(self, user_id: int | str) -> Any:
        """List bill rate tiers for a user."""
        r = await self._request("GET", f"/users/{user_id}/bill_rates")
        return r.json()

    async def create_user_bill_rate(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Add a bill rate tier for a user."""
        r = await self._request("POST", f"/users/{user_id}/bill_rates", json_data=data)
        return r.json()

    async def get_user_availability(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get user availability and scheduled capacity."""
        r = await self._request("GET", f"/users/{user_id}/availability", params=params)
        return r.json()

    async def get_user_utilization(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get user utilization metrics."""
        r = await self._request("GET", f"/users/{user_id}/utilization", params=params)
        return r.json()

    async def list_roles(self, params: dict[str, Any] | None = None) -> Any:
        """List configured user roles."""
        r = await self._request("GET", "/roles", params=params)
        return r.json()

    async def create_role(self, data: dict[str, Any]) -> Any:
        """Create a new role."""
        r = await self._request("POST", "/roles", json_data=data)
        return r.json()

    async def update_role(self, role_id: int | str, data: dict[str, Any]) -> Any:
        """Update a role."""
        r = await self._request("PUT", f"/roles/{role_id}", json_data=data)
        return r.json()

    async def delete_role(self, role_id: int | str) -> Any:
        """Delete a role."""
        r = await self._request("DELETE", f"/roles/{role_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": role_id}

    async def list_disciplines(self, params: dict[str, Any] | None = None) -> Any:
        """List configured disciplines."""
        r = await self._request("GET", "/disciplines", params=params)
        return r.json()

    async def create_discipline(self, data: dict[str, Any]) -> Any:
        """Create a new discipline."""
        r = await self._request("POST", "/disciplines", json_data=data)
        return r.json()

    async def update_discipline(self, discipline_id: int | str, data: dict[str, Any]) -> Any:
        """Update a discipline."""
        r = await self._request("PUT", f"/disciplines/{discipline_id}", json_data=data)
        return r.json()

    async def delete_discipline(self, discipline_id: int | str) -> Any:
        """Delete a discipline."""
        r = await self._request("DELETE", f"/disciplines/{discipline_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": discipline_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. CLIENTS & CONTACTS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_clients(self, params: dict[str, Any] | None = None) -> Any:
        """List clients."""
        r = await self._request("GET", "/clients", params=params)
        return r.json()

    async def get_client(self, client_id: int | str) -> Any:
        """Get details for a client."""
        r = await self._request("GET", f"/clients/{client_id}")
        return r.json()

    async def create_client(self, data: dict[str, Any]) -> Any:
        """Create a new client."""
        r = await self._request("POST", "/clients", json_data=data)
        return r.json()

    async def update_client(self, client_id: int | str, data: dict[str, Any]) -> Any:
        """Update client details."""
        r = await self._request("PUT", f"/clients/{client_id}", json_data=data)
        return r.json()

    async def delete_client(self, client_id: int | str) -> Any:
        """Delete a client."""
        r = await self._request("DELETE", f"/clients/{client_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": client_id}

    async def list_client_contacts(self, client_id: int | str) -> Any:
        """List contacts for a client."""
        r = await self._request("GET", f"/clients/{client_id}/contacts")
        return r.json()

    async def create_client_contact(self, client_id: int | str, data: dict[str, Any]) -> Any:
        """Add a contact for a client."""
        r = await self._request("POST", f"/clients/{client_id}/contacts", json_data=data)
        return r.json()

    async def delete_client_contact(self, client_id: int | str, contact_id: int | str) -> Any:
        """Delete a client contact."""
        r = await self._request(
            "DELETE", f"/clients/{client_id}/contacts/{contact_id}", allow_statuses=frozenset({200, 204})
        )
        return r.json() if r.content else {"status": "deleted", "contact_id": contact_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. LEAVES & HOLIDAYS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_leave_types(self, params: dict[str, Any] | None = None) -> Any:
        """List leave types (Vacation, Sick, etc.)."""
        r = await self._request("GET", "/leave_types", params=params)
        return r.json()

    async def get_leave_type(self, leave_type_id: int | str) -> Any:
        """Get leave type details."""
        r = await self._request("GET", f"/leave_types/{leave_type_id}")
        return r.json()

    async def create_leave_type(self, data: dict[str, Any]) -> Any:
        """Create a new leave type."""
        r = await self._request("POST", "/leave_types", json_data=data)
        return r.json()

    async def update_leave_type(self, leave_type_id: int | str, data: dict[str, Any]) -> Any:
        """Update a leave type."""
        r = await self._request("PUT", f"/leave_types/{leave_type_id}", json_data=data)
        return r.json()

    async def delete_leave_type(self, leave_type_id: int | str) -> Any:
        """Delete a leave type."""
        r = await self._request("DELETE", f"/leave_types/{leave_type_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": leave_type_id}

    async def list_holidays(self, params: dict[str, Any] | None = None) -> Any:
        """List regional and company holidays."""
        r = await self._request("GET", "/holidays", params=params)
        return r.json()

    async def get_holiday(self, holiday_id: int | str) -> Any:
        """Get holiday details."""
        r = await self._request("GET", f"/holidays/{holiday_id}")
        return r.json()

    async def create_holiday(self, data: dict[str, Any]) -> Any:
        """Create a new holiday."""
        r = await self._request("POST", "/holidays", json_data=data)
        return r.json()

    async def update_holiday(self, holiday_id: int | str, data: dict[str, Any]) -> Any:
        """Update a holiday."""
        r = await self._request("PUT", f"/holidays/{holiday_id}", json_data=data)
        return r.json()

    async def delete_holiday(self, holiday_id: int | str) -> Any:
        """Delete a holiday."""
        r = await self._request("DELETE", f"/holidays/{holiday_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": holiday_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 7. EXPENSES & CATEGORIES
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_expenses(self, params: dict[str, Any] | None = None) -> Any:
        """List logged expenses."""
        r = await self._request("GET", "/expenses", params=params)
        return r.json()

    async def get_expense(self, expense_id: int | str) -> Any:
        """Get details for an expense."""
        r = await self._request("GET", f"/expenses/{expense_id}")
        return r.json()

    async def create_expense(self, data: dict[str, Any]) -> Any:
        """Log a new expense."""
        r = await self._request("POST", "/expenses", json_data=data)
        return r.json()

    async def update_expense(self, expense_id: int | str, data: dict[str, Any]) -> Any:
        """Update an expense."""
        r = await self._request("PUT", f"/expenses/{expense_id}", json_data=data)
        return r.json()

    async def delete_expense(self, expense_id: int | str) -> Any:
        """Delete an expense."""
        r = await self._request("DELETE", f"/expenses/{expense_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": expense_id}

    async def list_project_expenses(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List expenses for a project."""
        r = await self._request("GET", f"/projects/{project_id}/expenses", params=params)
        return r.json()

    async def list_user_expenses(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List expenses submitted by a user."""
        r = await self._request("GET", f"/users/{user_id}/expenses", params=params)
        return r.json()

    async def list_expense_categories(self, params: dict[str, Any] | None = None) -> Any:
        """List expense categories."""
        r = await self._request("GET", "/expense_categories", params=params)
        return r.json()

    async def create_expense_category(self, data: dict[str, Any]) -> Any:
        """Create an expense category."""
        r = await self._request("POST", "/expense_categories", json_data=data)
        return r.json()

    async def delete_expense_category(self, category_id: int | str) -> Any:
        """Delete an expense category."""
        r = await self._request("DELETE", f"/expense_categories/{category_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": category_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 8. TAGS & CUSTOM FIELDS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_tags(self, params: dict[str, Any] | None = None) -> Any:
        """List tags."""
        r = await self._request("GET", "/tags", params=params)
        return r.json()

    async def create_tag(self, data: dict[str, Any]) -> Any:
        """Create a tag."""
        r = await self._request("POST", "/tags", json_data=data)
        return r.json()

    async def delete_tag(self, tag_id: int | str) -> Any:
        """Delete a tag."""
        r = await self._request("DELETE", f"/tags/{tag_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": tag_id}

    async def list_custom_fields(self, params: dict[str, Any] | None = None) -> Any:
        """List custom field definitions."""
        r = await self._request("GET", "/custom_fields", params=params)
        return r.json()

    async def get_custom_field(self, custom_field_id: int | str) -> Any:
        """Get a custom field definition."""
        r = await self._request("GET", f"/custom_fields/{custom_field_id}")
        return r.json()

    async def create_custom_field(self, data: dict[str, Any]) -> Any:
        """Create a custom field definition."""
        r = await self._request("POST", "/custom_fields", json_data=data)
        return r.json()

    async def update_custom_field(self, custom_field_id: int | str, data: dict[str, Any]) -> Any:
        """Update a custom field definition."""
        r = await self._request("PUT", f"/custom_fields/{custom_field_id}", json_data=data)
        return r.json()

    async def delete_custom_field(self, custom_field_id: int | str) -> Any:
        """Delete a custom field definition."""
        r = await self._request("DELETE", f"/custom_fields/{custom_field_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": custom_field_id}

    async def list_custom_field_values(self, params: dict[str, Any] | None = None) -> Any:
        """List custom field values."""
        r = await self._request("GET", "/custom_field_values", params=params)
        return r.json()

    async def set_custom_field_values(self, data: dict[str, Any]) -> Any:
        """Set or update custom field values."""
        r = await self._request("PUT", "/custom_field_values", json_data=data)
        return r.json()

    # ═══════════════════════════════════════════════════════════════════════════
    # 9. APPROVALS, STATUS OPTIONS, USER STATUSES & PLACEHOLDERS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_approvals(self, params: dict[str, Any] | None = None) -> Any:
        """List submitted approvals across organization."""
        r = await self._request("GET", "/approvals", params=params)
        return r.json()

    async def create_approval(self, data: dict[str, Any]) -> Any:
        """Submit or approve approvable records."""
        r = await self._request("POST", "/approvals", json_data=data)
        return r.json()

    async def delete_approval(self, approval_id: int | str) -> Any:
        """Delete a pending approval record."""
        r = await self._request("DELETE", f"/approvals/{approval_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": approval_id}

    async def list_status_options(self, params: dict[str, Any] | None = None) -> Any:
        """List assignment work status options."""
        r = await self._request("GET", "/status_options", params=params)
        return r.json()

    async def get_user_statuses(self, user_id: int | str) -> Any:
        """Get work statuses for a user (ITO, WFH, SIC, OOO, VAC, OOF)."""
        r = await self._request("GET", f"/users/{user_id}/statuses")
        return r.json()

    async def set_user_status(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Set current status for a user."""
        r = await self._request("POST", f"/users/{user_id}/statuses", json_data=data)
        return r.json()

    async def list_placeholder_resources(self, params: dict[str, Any] | None = None) -> Any:
        """List placeholder resources."""
        r = await self._request("GET", "/placeholder_resources", params=params)
        return r.json()

    async def create_placeholder_resource(self, data: dict[str, Any]) -> Any:
        """Create a placeholder resource."""
        r = await self._request("POST", "/placeholder_resources", json_data=data)
        return r.json()

    async def delete_placeholder_resource(self, placeholder_id: int | str) -> Any:
        """Delete a placeholder resource."""
        r = await self._request(
            "DELETE", f"/placeholder_resources/{placeholder_id}", allow_statuses=frozenset({200, 204})
        )
        return r.json() if r.content else {"status": "deleted", "id": placeholder_id}

    async def list_subtasks(self, project_id: int | str, assignment_id: int | str) -> Any:
        """List subtasks for an assignment."""
        r = await self._request("GET", f"/projects/{project_id}/assignments/{assignment_id}/subtasks")
        return r.json()

    async def create_subtask(self, project_id: int | str, assignment_id: int | str, data: dict[str, Any]) -> Any:
        """Create a subtask on an assignment."""
        r = await self._request("POST", f"/projects/{project_id}/assignments/{assignment_id}/subtasks", json_data=data)
        return r.json()

    async def delete_subtask(self, project_id: int | str, assignment_id: int | str, subtask_id: int | str) -> Any:
        """Delete a subtask."""
        r = await self._request(
            "DELETE",
            f"/projects/{project_id}/assignments/{assignment_id}/subtasks/{subtask_id}",
            allow_statuses=frozenset({200, 204}),
        )
        return r.json() if r.content else {"status": "deleted", "id": subtask_id}

    async def get_report_rows(self, data: dict[str, Any]) -> Any:
        """Generate and retrieve report rows."""
        r = await self._request("POST", "/reports/rows", json_data=data)
        return r.json()

    async def get_report_totals(self, data: dict[str, Any]) -> Any:
        """Generate and retrieve report aggregated totals."""
        r = await self._request("POST", "/reports/totals", json_data=data)
        return r.json()

    async def list_webhooks(self, params: dict[str, Any] | None = None) -> Any:
        """List configured webhooks."""
        r = await self._request("GET", "/webhooks", params=params)
        return r.json()

    async def create_webhook(self, data: dict[str, Any]) -> Any:
        """Create a webhook."""
        r = await self._request("POST", "/webhooks", json_data=data)
        return r.json()

    async def delete_webhook(self, webhook_id: int | str) -> Any:
        """Delete a webhook."""
        r = await self._request("DELETE", f"/webhooks/{webhook_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": webhook_id}
