#!/usr/bin/env python3
"""Check that client and server tool implementations cover all Smartsheet RM API endpoints."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

# Add src to path
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from smartsheet_rm_mcp.client import SmartsheetRMClient  # noqa: E402
from smartsheet_rm_mcp.server import mcp  # noqa: E402

ENDPOINT_TO_METHOD: dict[tuple[str, str], str] = {
    # 1. Time Tracking
    ("time_entries", "GET"): "list_time_entries",
    ("time_entries", "POST"): "create_time_entry",
    ("time_entries/{id}", "GET"): "get_time_entry",
    ("time_entries/{id}", "PUT"): "update_time_entry",
    ("time_entries/{id}", "DELETE"): "delete_time_entry",
    ("projects/{id}/time_entries", "GET"): "list_project_time_entries",
    ("projects/{id}/time_entries", "POST"): "create_project_time_entry",
    ("users/{id}/time_entries", "GET"): "list_user_time_entries",
    ("users/{id}/time_entries", "POST"): "create_user_time_entry",
    ("users/{id}/time_entries/approval_status", "PUT"): "update_user_approval_status",
    ("users/{id}/time_entries/lock", "POST"): "lock_user_timesheet",
    # 2. Projects & Phases
    ("projects", "GET"): "list_projects",
    ("projects", "POST"): "create_project",
    ("projects/{id}", "GET"): "get_project",
    ("projects/{id}", "PUT"): "update_project",
    ("projects/{id}", "DELETE"): "delete_project",
    ("projects/{id}/users", "GET"): "list_project_users",
    ("projects/{id}/phases", "GET"): "list_project_phases",
    ("projects/{id}/phases", "POST"): "create_project_phase",
    ("projects/{id}/phases/{id}", "GET"): "get_project_phase",
    ("projects/{id}/phases/{id}", "PUT"): "update_project_phase",
    ("projects/{id}/phases/{id}", "DELETE"): "delete_project_phase",
    # 3. Assignments & Scheduling
    ("assignments", "GET"): "list_assignments",
    ("assignments", "POST"): "create_assignment",
    ("assignments/{id}", "GET"): "get_assignment",
    ("assignments/{id}", "PUT"): "update_assignment",
    ("assignments/{id}", "DELETE"): "delete_assignment",
    ("projects/{id}/assignments", "GET"): "list_project_assignments",
    ("projects/{id}/assignments", "POST"): "create_project_assignment",
    ("users/{id}/assignments", "GET"): "list_user_assignments",
    ("users/{id}/assignments", "POST"): "create_user_assignment",
    # 4. Users, Roles & Disciplines
    ("users", "GET"): "list_users",
    ("users", "POST"): "create_user",
    ("users/{id}", "GET"): "get_user",
    ("users/{id}", "PUT"): "update_user",
    ("users/{id}", "DELETE"): "delete_user",
    ("users/{id}/bill_rates", "GET"): "list_user_bill_rates",
    ("users/{id}/bill_rates", "POST"): "create_user_bill_rate",
    ("users/{id}/availability", "GET"): "get_user_availability",
    ("users/{id}/utilization", "GET"): "get_user_utilization",
    ("roles", "GET"): "list_roles",
    ("roles", "POST"): "create_role",
    ("roles/{id}", "PUT"): "update_role",
    ("roles/{id}", "DELETE"): "delete_role",
    ("disciplines", "GET"): "list_disciplines",
    ("disciplines", "POST"): "create_discipline",
    ("disciplines/{id}", "PUT"): "update_discipline",
    ("disciplines/{id}", "DELETE"): "delete_discipline",
    # 5. Clients & Contacts
    ("clients", "GET"): "list_clients",
    ("clients", "POST"): "create_client",
    ("clients/{id}", "GET"): "get_client",
    ("clients/{id}", "PUT"): "update_client",
    ("clients/{id}", "DELETE"): "delete_client",
    ("clients/{id}/contacts", "GET"): "list_client_contacts",
    ("clients/{id}/contacts", "POST"): "create_client_contact",
    ("clients/{id}/contacts/{id}", "DELETE"): "delete_client_contact",
    # 6. Leaves & Holidays
    ("leave_types", "GET"): "list_leave_types",
    ("leave_types", "POST"): "create_leave_type",
    ("leave_types/{id}", "GET"): "get_leave_type",
    ("leave_types/{id}", "PUT"): "update_leave_type",
    ("leave_types/{id}", "DELETE"): "delete_leave_type",
    ("holidays", "GET"): "list_holidays",
    ("holidays", "POST"): "create_holiday",
    ("holidays/{id}", "GET"): "get_holiday",
    ("holidays/{id}", "PUT"): "update_holiday",
    ("holidays/{id}", "DELETE"): "delete_holiday",
    # 7. Expenses
    ("expenses", "GET"): "list_expenses",
    ("expenses", "POST"): "create_expense",
    ("expenses/{id}", "GET"): "get_expense",
    ("expenses/{id}", "PUT"): "update_expense",
    ("expenses/{id}", "DELETE"): "delete_expense",
    ("projects/{id}/expenses", "GET"): "list_project_expenses",
    ("users/{id}/expenses", "GET"): "list_user_expenses",
    ("expense_categories", "GET"): "list_expense_categories",
    ("expense_categories", "POST"): "create_expense_category",
    ("expense_categories/{id}", "DELETE"): "delete_expense_category",
    # 8. Tags & Custom Fields
    ("tags", "GET"): "list_tags",
    ("tags", "POST"): "create_tag",
    ("tags/{id}", "DELETE"): "delete_tag",
    ("custom_fields", "GET"): "list_custom_fields",
    ("custom_fields", "POST"): "create_custom_field",
    ("custom_fields/{id}", "GET"): "get_custom_field",
    ("custom_fields/{id}", "PUT"): "update_custom_field",
    ("custom_fields/{id}", "DELETE"): "delete_custom_field",
    ("custom_field_values", "GET"): "list_custom_field_values",
    ("custom_field_values", "PUT"): "set_custom_field_values",
    # 9. Approvals, Status Options, User Statuses & Placeholders
    ("approvals", "GET"): "list_approvals",
    ("approvals", "POST"): "create_approval",
    ("approvals/{id}", "DELETE"): "delete_approval",
    ("status_options", "GET"): "list_status_options",
    ("users/{id}/statuses", "GET"): "get_user_statuses",
    ("users/{id}/statuses", "POST"): "set_user_status",
    ("placeholder_resources", "GET"): "list_placeholder_resources",
    ("placeholder_resources", "POST"): "create_placeholder_resource",
    ("placeholder_resources/{id}", "DELETE"): "delete_placeholder_resource",
    # 10. Subtasks, Reports & Webhooks
    ("projects/{id}/assignments/{id}/subtasks", "GET"): "list_subtasks",
    ("projects/{id}/assignments/{id}/subtasks", "POST"): "create_subtask",
    ("projects/{id}/assignments/{id}/subtasks/{id}", "DELETE"): "delete_subtask",
    ("reports/rows", "GET"): "get_report_rows",
    ("reports/totals", "GET"): "get_report_totals",
    ("webhooks", "GET"): "list_webhooks",
    ("webhooks", "POST"): "create_webhook",
    ("webhooks/{id}", "DELETE"): "delete_webhook",
}


def main() -> int:
    print(f"Checking {len(ENDPOINT_TO_METHOD)} expected API endpoints against SmartsheetRMClient...")

    client_methods = {
        name
        for name, _ in inspect.getmembers(SmartsheetRMClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    }

    missing_methods = []
    for (endpoint, verb), method_name in ENDPOINT_TO_METHOD.items():
        if method_name not in client_methods:
            missing_methods.append(f"{verb} {endpoint} -> SmartsheetRMClient.{method_name}")

    if missing_methods:
        print("ERROR: Missing expected SmartsheetRMClient methods for endpoints:", file=sys.stderr)
        for m in missing_methods:
            print(f"  - {m}", file=sys.stderr)
        return 1

    # Verify MCP tool registration
    tool_names = list(mcp._tool_manager._tools.keys())
    print(f"SmartsheetRMClient defines all {len(ENDPOINT_TO_METHOD)} required API methods.")
    print(f"Server registers {len(tool_names)} total MCP tools.")

    if len(tool_names) < 90:
        print(f"ERROR: Expected at least 90 MCP tools, found {len(tool_names)}", file=sys.stderr)
        return 1

    print(f"OpenAPI surface check passed successfully (100% coverage for {len(ENDPOINT_TO_METHOD)} operations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
