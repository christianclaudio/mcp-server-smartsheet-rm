#!/usr/bin/env python3
"""Check that client and server tool implementations cover all Smartsheet RM API endpoints and audit parameter drift."""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

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


@dataclass
class ClientCall:
    method: str
    raw_path: str
    normalized_path: str
    query_params: set[str] = field(default_factory=set)
    body_keys: set[str] = field(default_factory=set)
    source_file: str = ""
    line_number: int = 0


@dataclass
class SpecEndpoint:
    method: str
    path: str
    normalized_path: str
    query_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    path_params: dict[str, dict[str, Any]] = field(default_factory=dict)
    deprecated_params: set[str] = field(default_factory=set)
    required_params: set[str] = field(default_factory=set)
    is_deprecated_route: bool = False


def normalize_path(path: str) -> str:
    """Normalize path by stripping query string, leading/trailing slashes, and standardizing parameters."""
    path = path.split("?")[0].strip()
    if not path.startswith("/"):
        path = "/" + path
    path = path.rstrip("/")
    return re.sub(r"\{[^}]*\}", "{}", path)


def is_parameter_deprecated(param_def: dict[str, Any]) -> bool:
    """Detect if a parameter is deprecated via boolean flag or description warning."""
    if param_def.get("deprecated") is True:
        return True
    desc = str(param_def.get("description", "")).lower()
    title = str(param_def.get("title", "")).lower()
    if "[deprecated]" in desc or "deprecated" in title or "end of support" in desc:
        return True
    return False


def parse_spec(raw_spec: dict[str, Any]) -> dict[tuple[str, str], SpecEndpoint]:
    """Index OpenAPI specification endpoints, parameters, and deprecation markers."""
    endpoints: dict[tuple[str, str], SpecEndpoint] = {}
    paths = raw_spec.get("paths", {})

    for path_str, methods in paths.items():
        norm_path = normalize_path(path_str)
        path_level_params = methods.get("parameters", []) if isinstance(methods, dict) else []

        for method_name, op in methods.items():
            if method_name.lower() not in ("get", "post", "put", "patch", "delete"):
                continue

            method = method_name.upper()
            op_params = op.get("parameters", []) if isinstance(op, dict) else []
            all_params = list(path_level_params) + op_params

            endpoint = SpecEndpoint(
                method=method,
                path=path_str,
                normalized_path=norm_path,
                is_deprecated_route=bool(op.get("deprecated", False)),
            )

            for param in all_params:
                if not isinstance(param, dict):
                    continue
                p_name = param.get("name")
                p_in = param.get("in", "query")
                if not p_name:
                    continue

                if p_in == "query":
                    endpoint.query_params[p_name] = param
                elif p_in == "path":
                    endpoint.path_params[p_name] = param

                if is_parameter_deprecated(param):
                    endpoint.deprecated_params.add(p_name)

                if param.get("required") is True:
                    endpoint.required_params.add(p_name)

            endpoints[(method, norm_path)] = endpoint

    return endpoints


class ClientAstVisitor(ast.NodeVisitor):
    """AST visitor to find HTTP client calls and extract method, path, and passed query params."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.calls: list[ClientCall] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func
        method_name = ""
        if isinstance(func, ast.Attribute):
            method_name = func.attr

        http_methods = {"get", "post", "put", "patch", "delete", "request"}
        if method_name.lower() in http_methods:
            method = ""
            raw_path = ""
            query_params: set[str] = set()
            body_keys: set[str] = set()

            if method_name.lower() == "request":
                if (
                    len(node.args) >= 1
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    method = node.args[0].value.upper()
                if len(node.args) >= 2:
                    raw_path = self._extract_path(node.args[1])
            else:
                method = method_name.upper()
                if len(node.args) >= 1:
                    raw_path = self._extract_path(node.args[0])

            for kw in node.keywords:
                if kw.arg in ("params", "query_params"):
                    query_params.update(self._extract_dict_keys(kw.value))
                elif kw.arg in ("json", "json_data", "body"):
                    body_keys.update(self._extract_dict_keys(kw.value))

            if method and raw_path:
                norm_path = normalize_path(raw_path)
                self.calls.append(
                    ClientCall(
                        method=method,
                        raw_path=raw_path,
                        normalized_path=norm_path,
                        query_params=query_params,
                        body_keys=body_keys,
                        source_file=self.filename,
                        line_number=node.lineno,
                    )
                )

        self.generic_visit(node)

    def _extract_path(self, node: ast.AST) -> str:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    parts.append(str(part.value))
                else:
                    parts.append("{}")
            return "".join(parts)
        return "{}"

    def _extract_dict_keys(self, node: ast.AST) -> set[str]:
        keys = set()
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        return keys


def extract_client_calls(source_dir: Path) -> list[ClientCall]:
    """Scan Python files in src/ for API client calls."""
    calls: list[ClientCall] = []
    for py_file in source_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            visitor = ClientAstVisitor(str(py_file.relative_to(source_dir.parent)))
            visitor.visit(tree)
            calls.extend(visitor.calls)
        except Exception:
            continue
    return calls


def run_drift_check(
    spec: dict[str, Any],
    source_dir: Path,
    strict: bool = False,
) -> tuple[int, list[str]]:
    """Execute complete drift and deprecation validation."""
    spec_endpoints = parse_spec(spec)
    client_calls = extract_client_calls(source_dir)

    issues: list[str] = []
    warnings: list[str] = []

    covered_keys: set[tuple[str, str]] = set()

    for call in client_calls:
        key = (call.method, call.normalized_path)
        spec_op = spec_endpoints.get(key)
        if not spec_op:
            continue

        covered_keys.add(key)
        if spec_op.is_deprecated_route:
            warnings.append(f"⚠️ DEPRECATED ROUTE: '{call.method} {spec_op.path}' in use.")

        for qp in call.query_params:
            if qp in spec_op.deprecated_params:
                msg = f"⚠️ DEPRECATED PARAMETER IN USE: '{qp}' on '{call.method} {spec_op.path}'"
                if strict:
                    issues.append("❌ " + msg[3:])
                else:
                    warnings.append(msg)

    output_lines: list[str] = []
    if warnings:
        output_lines.append("\n⚠️ WARNINGS:")
        for w in warnings:
            output_lines.append(f"  {w}")

    if issues:
        output_lines.append("\n❌ BREAKING DRIFT:")
        for err in issues:
            output_lines.append(f"  {err}")
        return 1, output_lines

    return 0, output_lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Smartsheet RM OpenAPI & Parameter Drift Checker")
    parser.add_argument("--spec-file", help="Path to local OpenAPI specification")
    parser.add_argument("--spec-url", help="URL to remote OpenAPI specification")
    parser.add_argument("--strict", action="store_true", help="Fail on deprecations")
    args, _ = parser.parse_known_args()

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

    # Parameter & schema drift check if spec provided
    raw_spec: dict[str, Any] | None = None
    if args.spec_file:
        try:
            raw_spec = json.loads(Path(args.spec_file).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ERROR reading spec-file: {e}", file=sys.stderr)
            return 2
    elif args.spec_url:
        try:
            resp = httpx.get(args.spec_url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            raw_spec = resp.json()
        except Exception as e:
            print(f"ERROR fetching spec-url: {e}", file=sys.stderr)
            return 2

    if raw_spec:
        code, report = run_drift_check(raw_spec, REPO / "src", strict=args.strict)
        for line in report:
            print(line)
        if code != 0:
            return code

    print(f"OpenAPI surface check passed successfully (100% coverage for {len(ENDPOINT_TO_METHOD)} operations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
