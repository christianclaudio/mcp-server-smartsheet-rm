#!/usr/bin/env python3
"""OpenAPI route and parameter drift check for the Smartsheet RM client.

Ported from the MCP Forge template drift check and adapted to this repo: the client's
``self._request(...)`` calls are scanned, and the spec is fetched with this repo's
``fetch_pinned_https`` (one DNS lookup, TCP pinned to the checked IP, Host/SNI/certificate
on the original name, redirects refused).

Before the spec is read, two repo checks run (exit 2 on failure, since neither is drift): every
method named in ``ENDPOINT_TO_METHOD`` exists on ``SmartsheetRMClient``, and the default server
registers at least ``MIN_TOOLS`` tools.

Drift (exit 1):
1. PHANTOM ROUTE: a client call whose method and path are not in the spec.
2. UNRESOLVED CALL: a call on a recognized HTTP client whose method or path cannot be read
   from the source, so it cannot be checked. Mark a deliberate one with
   ``# drift-ignore: <reason>`` on any line of the call; ignored calls are listed in the report.
   The 46 undocumented routes the client keeps (see README "Undocumented routes") carry it.

Broken scan (exit 2, not drift; the spec is not compared, since the scan is incomplete):
- UNSCANNABLE SOURCE: a Python file under the source directory that does not parse.
- NO CLIENT CALLS: nothing under the source directory was recognized as a client call.

Warnings (exit 1 only with ``--strict``):
- DEPRECATED ROUTE or DEPRECATED PARAMETER: the spec marks the route or a query parameter the
  client passes as deprecated (``deprecated: true`` or a deprecation note in its description).
- UNDOCUMENTED PARAMETER: the client passes a query parameter the spec does not declare.

Spec endpoints no client call covers are listed for information.

A vendor spec is required (``--spec-file`` or ``--spec-url``). The check exits 2 without one,
or when the document cannot be read or has no ``paths``. Exit 1 means drift and nothing else;
every other failure, including an unexpected exception, exits 2, so the weekly monitor opens a
drift issue only on exit 1. Smartsheet publishes it at
``DEFAULT_SPEC_URL``; its paths are relative to ``https://api.rm.smartsheet.com/api/v1``,
the same as the client's, so no ``--path-prefix`` is needed.

Usage:
    python scripts/check_openapi_drift.py (--spec-file PATH | --spec-url URL)
        [--path-prefix PREFIX] [--source-dir DIR] [--strict]
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from smartsheet_rm_mcp.client import SmartsheetRMClient, fetch_pinned_https  # noqa: E402
from smartsheet_rm_mcp.server import mcp  # noqa: E402

# Smartsheet's published Resource Management OpenAPI document (linked from
# https://developers.smartsheet.com/api/resource_management/openapi).
DEFAULT_SPEC_URL = "https://developers.smartsheet.com/_spec/api/resource_management/openapi.json"
MIN_TOOLS = 90

# Endpoints the client must keep a method for: (path, verb) -> SmartsheetRMClient method.
# Removing a method fails the check; this list is the repo's own and is separate from the spec.
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
    ("reports/rows", "POST"): "get_report_rows",
    ("reports/totals", "POST"): "get_report_totals",
    ("webhooks", "GET"): "list_webhooks",
    ("webhooks", "POST"): "create_webhook",
    ("webhooks/{id}", "DELETE"): "delete_webhook",
}


HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")

# Receiver names treated as HTTP clients. ``self.request(...)`` and ``self._request(...)``
# (the SmartsheetRMClient wrapper) are also inspected.
CLIENT_NAMES = frozenset(
    {
        "client",
        "_client",
        "http_client",
        "_custom_client",
        "httpx",
        "custom_client",
        "session",
    }
)

SELF_REQUEST_METHODS = frozenset({"request", "_request"})

IGNORE_PRAGMA = re.compile(r"#\s*drift-ignore\b(?P<colon>:?)(?P<reason>.*)$")


class SpecError(ValueError):
    """The vendor spec is missing, unreadable, or not an OpenAPI document."""


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
class UnresolvedCall:
    source_file: str
    line_number: int
    detail: str


@dataclass
class IgnoredCall:
    source_file: str
    line_number: int
    reason: str


@dataclass
class ScanResult:
    calls: list[ClientCall] = field(default_factory=list)
    unresolved: list[UnresolvedCall] = field(default_factory=list)
    ignored: list[IgnoredCall] = field(default_factory=list)
    unscannable: list[str] = field(default_factory=list)


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
    """Normalize path by stripping query, leading/trailing slashes, and standardizing params."""
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


def validate_spec(raw_spec: Any) -> dict[str, Any]:
    """Fail closed on anything that is not an OpenAPI or Swagger document with paths."""
    if not isinstance(raw_spec, dict):
        raise SpecError("spec is not a JSON object")
    if "openapi" not in raw_spec and "swagger" not in raw_spec:
        raise SpecError("spec has no 'openapi' or 'swagger' version field")
    paths = raw_spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise SpecError("spec has no 'paths', so no client call could be checked against it")
    return raw_spec


def parse_spec(raw_spec: dict[str, Any]) -> dict[tuple[str, str], SpecEndpoint]:
    """Index OpenAPI specification endpoints, parameters, and deprecation markers."""
    endpoints: dict[tuple[str, str], SpecEndpoint] = {}
    paths = raw_spec.get("paths", {})
    if not isinstance(paths, dict):
        return endpoints

    for path_str, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        norm_path = normalize_path(path_str)
        path_level_params = methods.get("parameters", [])
        if not isinstance(path_level_params, list):
            path_level_params = []

        for method_name, op in methods.items():
            if method_name.lower() not in HTTP_METHODS or not isinstance(op, dict):
                continue

            method = method_name.upper()
            op_params = op.get("parameters", [])
            if not isinstance(op_params, list):
                op_params = []

            endpoint = SpecEndpoint(
                method=method,
                path=path_str,
                normalized_path=norm_path,
                is_deprecated_route=op.get("deprecated") is True,
            )

            for param in list(path_level_params) + op_params:
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


def _ignore_reason(lines: list[str], node: ast.Call) -> str | None:
    """Return the ``# drift-ignore`` reason on any line of the call, or None."""
    end = getattr(node, "end_lineno", None) or node.lineno
    for line in lines[node.lineno - 1 : end]:
        match = IGNORE_PRAGMA.search(line)
        if match:
            # Without the colon the marker is malformed and counts as having no reason.
            return match.group("reason").strip() if match.group("colon") else ""
    return None


class ClientAstVisitor(ast.NodeVisitor):
    """Find HTTP client calls and record method, path, and literal query and body keys."""

    def __init__(self, filename: str, source: str = "", path_prefix: str = "") -> None:
        self.filename = filename
        self.lines = source.splitlines()
        self.path_prefix = path_prefix.strip("/")
        self.calls: list[ClientCall] = []
        self.unresolved: list[UnresolvedCall] = []
        self.ignored: list[IgnoredCall] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self._inspect(node)
        self.generic_visit(node)

    def _inspect(self, node: ast.Call) -> None:
        func = node.func
        if not isinstance(func, ast.Attribute):
            return
        method_name = func.attr.lower()
        if method_name == "_request":
            method_name = "request"
        if isinstance(func.value, ast.Name):
            caller_name = func.value.id
        elif isinstance(func.value, ast.Attribute):
            caller_name = func.value.attr
        else:
            return

        if caller_name == "self":
            if func.attr.lower() not in SELF_REQUEST_METHODS:
                return
        elif func.attr.lower() == "_request":
            return
        elif caller_name not in CLIENT_NAMES:
            return
        if method_name != "request" and method_name not in HTTP_METHODS:
            return

        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        args = list(node.args)
        method_node: ast.AST | None
        if method_name == "request":
            method_node = args.pop(0) if args else keywords.get("method")
        else:
            method_node = ast.Constant(value=method_name)
        path_node = args.pop(0) if args else keywords.get("url", keywords.get("path"))

        reason = _ignore_reason(self.lines, node)
        if reason is not None:
            if reason:
                self.ignored.append(IgnoredCall(self.filename, node.lineno, reason))
            else:
                self.unresolved.append(
                    UnresolvedCall(self.filename, node.lineno, "'# drift-ignore' needs a reason after ':'")
                )
            return

        method = ""
        if isinstance(method_node, ast.Constant) and isinstance(method_node.value, str):
            method = method_node.value.upper()
        raw_path = self._extract_path(path_node)

        problems = []
        if method.lower() not in HTTP_METHODS:
            problems.append("HTTP method is not a string literal")
        if raw_path is None:
            problems.append("path is not a string literal or f-string with a literal segment")
        if problems:
            self.unresolved.append(UnresolvedCall(self.filename, node.lineno, "; ".join(problems) + "."))
            return
        assert raw_path is not None

        query_params: set[str] = set()
        body_keys: set[str] = set()
        for name in ("params", "query_params"):
            if name in keywords:
                query_params.update(self._extract_dict_keys(keywords[name]))
        for name in ("json", "json_data", "body"):
            if name in keywords:
                body_keys.update(self._extract_dict_keys(keywords[name]))
        if args and method_name in HTTP_METHODS:
            if method_name == "get":
                query_params.update(self._extract_dict_keys(args[0]))
            else:
                body_keys.update(self._extract_dict_keys(args[0]))

        full_path = f"{self.path_prefix}/{raw_path.lstrip('/')}" if self.path_prefix else raw_path
        self.calls.append(
            ClientCall(
                method=method,
                raw_path=raw_path,
                normalized_path=normalize_path(full_path),
                query_params=query_params,
                body_keys=body_keys,
                source_file=self.filename,
                line_number=node.lineno,
            )
        )

    @staticmethod
    def _extract_path(node: ast.AST | None) -> str | None:
        """Return the path template, or None when it cannot be read from the source."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            path = node.value
        elif isinstance(node, ast.JoinedStr):
            parts = []
            for part in node.values:
                if isinstance(part, ast.Constant):
                    parts.append(str(part.value))
                else:
                    parts.append("{}")
            path = "".join(parts)
        else:
            return None
        # A path made only of placeholders (for example f"{base}/{path}") names no endpoint.
        if not re.sub(r"\{[^}]*\}|/", "", path.split("?")[0]).strip():
            return None
        return path

    @staticmethod
    def _extract_dict_keys(node: ast.AST) -> set[str]:
        keys = set()
        if isinstance(node, ast.Dict):
            for k in node.keys:
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    keys.add(k.value)
        return keys


def scan_source(source_dir: Path, path_prefix: str = "") -> ScanResult:
    """Scan every Python file under ``source_dir`` for client calls."""
    result = ScanResult()
    for py_file in sorted(source_dir.rglob("*.py")):
        rel = str(py_file.relative_to(source_dir.parent))
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as exc:
            result.unscannable.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        visitor = ClientAstVisitor(rel, source, path_prefix)
        visitor.visit(tree)
        result.calls.extend(visitor.calls)
        result.unresolved.extend(visitor.unresolved)
        result.ignored.extend(visitor.ignored)
    return result


def extract_client_calls(source_dir: Path) -> list[ClientCall]:
    """Return only the resolved client calls under ``source_dir``."""
    return scan_source(source_dir).calls


def run_drift_check(
    spec: dict[str, Any],
    source_dir: Path,
    strict: bool = False,
    path_prefix: str = "",
) -> tuple[int, list[str]]:
    """Compare client calls with the spec. Returns (exit code, report lines)."""
    spec_endpoints = parse_spec(spec)
    scan = scan_source(source_dir, path_prefix)

    issues: list[str] = []
    warnings: list[str] = []

    print(f"[*] Spec endpoints indexed: {len(spec_endpoints)}")
    print(f"[*] Client calls detected:  {len(scan.calls)}")
    print(f"[*] Ignored client calls:   {len(scan.ignored)}")

    # A broken scan is not drift: exit 2 so the monitor fails without opening a drift issue.
    scan_errors = [f"❌ UNSCANNABLE SOURCE: {entry}" for entry in scan.unscannable]
    if not scan.calls:
        scan_errors.append(
            f"❌ NO CLIENT CALLS: nothing under {source_dir} was recognized as a client call, "
            "so nothing was checked against the spec."
        )
    if scan_errors:
        return 2, ["\n❌ DRIFT CHECK COULD NOT SCAN THE SOURCE (not drift):", *(f"  {e}" for e in scan_errors)]

    for unresolved in scan.unresolved:
        issues.append(
            f"❌ UNRESOLVED CALL: {unresolved.source_file}:{unresolved.line_number}: "
            f"{unresolved.detail} Make it checkable or mark it '# drift-ignore: <reason>'."
        )

    methods_by_path: dict[str, set[str]] = {}
    for method, norm in spec_endpoints:
        methods_by_path.setdefault(norm, set()).add(method)

    covered_keys: set[tuple[str, str]] = set()

    for call in scan.calls:
        where = f"{call.source_file}:{call.line_number}"
        key = (call.method, call.normalized_path)
        spec_op = spec_endpoints.get(key)

        if not spec_op:
            other = sorted(methods_by_path.get(call.normalized_path, set()))
            hint = f" The spec has {', '.join(other)} on that path." if other else ""
            issues.append(
                f"❌ PHANTOM ROUTE: Client calls '{call.method} {call.raw_path}' "
                f"({where}) which does not exist in OpenAPI spec.{hint}"
            )
            continue

        covered_keys.add(key)

        if spec_op.is_deprecated_route:
            warnings.append(
                f"⚠️ DEPRECATED ROUTE: Client calls deprecated route '{call.method} {spec_op.path}' ({where})."
            )

        for qp in sorted(call.query_params):
            if qp in spec_op.deprecated_params:
                warnings.append(
                    f"⚠️ DEPRECATED PARAMETER IN USE: '{qp}' on '{call.method} {spec_op.path}' "
                    f"is deprecated in OpenAPI spec! ({where})"
                )
            elif qp not in spec_op.query_params:
                warnings.append(
                    f"⚠️ UNDOCUMENTED PARAMETER: '{qp}' on '{call.method} {spec_op.path}' "
                    f"is not a query parameter in OpenAPI spec. ({where})"
                )

    uncovered = sorted(set(spec_endpoints) - covered_keys)
    print(f"[*] Covered endpoints:      {len(covered_keys)}")
    print(f"[*] Uncovered endpoints:    {len(uncovered)}")

    output_lines: list[str] = []

    if scan.ignored:
        output_lines.append("\nℹ️ IGNORED CLIENT CALLS (# drift-ignore):")
        for ignored in scan.ignored:
            output_lines.append(f"  {ignored.source_file}:{ignored.line_number}: {ignored.reason}")

    if uncovered:
        output_lines.append("\nℹ️ SPEC ENDPOINTS NO CLIENT CALL COVERS:")
        for method, _norm in uncovered:
            output_lines.append(f"  {method} {spec_endpoints[(method, _norm)].path}")

    if warnings:
        heading = "❌ WARNINGS (failing under --strict):" if strict else "⚠️ WARNINGS:"
        output_lines.append(f"\n{heading}")
        for w in warnings:
            output_lines.append(f"  {w}")

    if issues:
        output_lines.append("\n❌ BREAKING DRIFT DETECTED:")
        for err in issues:
            output_lines.append(f"  {err}")
        return 1, output_lines

    if warnings and strict:
        return 1, output_lines

    output_lines.append("\n[✓] No breaking drift or path mismatches detected.")
    return 0, output_lines


def load_spec(spec_file: str | None, spec_url: str | None) -> dict[str, Any]:
    """Load and validate the vendor spec from a file or an HTTPS URL."""
    if spec_file:
        file_path = Path(spec_file)
        if not file_path.is_file():
            raise SpecError(f"spec file not found at {file_path}")
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise SpecError(f"cannot read spec file as JSON: {exc}") from exc
        return validate_spec(raw)

    if not spec_url:
        raise SpecError(
            "no vendor spec given. Pass --spec-file or --spec-url; the check never runs "
            "without the vendor's OpenAPI document."
        )
    print(f"[*] Fetching OpenAPI spec from: {spec_url}")
    try:
        # fetch_pinned_https checks the URL, resolves the host once, connects to that checked
        # IP with Host/SNI/certificate on the name, and never follows a redirect.
        resp = fetch_pinned_https(spec_url, timeout=30.0)
    except ValueError as exc:
        raise SpecError(f"refused to fetch spec URL: {exc}") from exc
    except Exception as exc:
        raise SpecError(f"cannot fetch spec from URL: {exc}") from exc
    if resp.is_redirect:
        location = resp.headers.get("location", "")
        raise SpecError(
            f"refused to follow redirect for spec URL (HTTP {resp.status_code} -> {location or 'no Location'})"
        )
    try:
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        raise SpecError(f"cannot fetch spec from URL: {exc}") from exc
    return validate_spec(raw)


def check_client_surface() -> list[str]:
    """Return errors for missing ``ENDPOINT_TO_METHOD`` methods or too few registered tools."""
    client_methods = {
        name
        for name, _ in inspect.getmembers(SmartsheetRMClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    errors = [
        f"Missing SmartsheetRMClient.{method_name} for {verb} {endpoint}"
        for (endpoint, verb), method_name in ENDPOINT_TO_METHOD.items()
        if method_name not in client_methods
    ]
    tool_count = len(registered_tool_names())
    print(
        f"[*] Client methods for ENDPOINT_TO_METHOD: {len(ENDPOINT_TO_METHOD) - len(errors)}/{len(ENDPOINT_TO_METHOD)}"
    )
    print(f"[*] Registered MCP tools:   {tool_count}")
    if tool_count < MIN_TOOLS:
        errors.append(f"Expected at least {MIN_TOOLS} MCP tools, found {tool_count}")
    return errors


def registered_tool_names() -> list[str]:
    """Return the client-visible tool names of the default gateway via public ``list_tools()``."""
    return [tool.name for tool in asyncio.run(mcp.list_tools())]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI route and parameter drift check")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--spec-url", help=f"Vendor OpenAPI JSON URL (HTTPS, public host), e.g. {DEFAULT_SPEC_URL}")
    source.add_argument("--spec-file", help="Path to a vendor OpenAPI JSON file")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also fail on deprecated routes, deprecated parameters, and undocumented parameters",
    )
    parser.add_argument(
        "--path-prefix",
        default="",
        help="Prefix the spec puts on every path that client paths omit (for example /v2)",
    )
    parser.add_argument(
        "--source-dir",
        default=str(REPO / "src"),
        help="Root source directory to inspect",
    )
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except Exception as exc:
        # Only drift may exit 1; anything unexpected is a broken run, not drift.
        print(f"Error: drift check failed unexpectedly: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    """Run the surface checks, load the spec and report drift (1) or any other failure (2)."""
    surface_errors = check_client_surface()
    if surface_errors:
        for err in surface_errors:
            print(f"Error: {err}", file=sys.stderr)
        return 2

    try:
        raw_spec = load_spec(args.spec_file, args.spec_url)
    except SpecError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    source_path = Path(args.source_dir)
    if not source_path.is_dir():
        print(f"Error: source directory not found at {source_path}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("🔍 OPENAPI ROUTE & PARAMETER DRIFT CHECK")
    print("=" * 70)

    exit_code, report_lines = run_drift_check(raw_spec, source_path, strict=args.strict, path_prefix=args.path_prefix)
    for line in report_lines:
        print(line)

    print("=" * 70)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
