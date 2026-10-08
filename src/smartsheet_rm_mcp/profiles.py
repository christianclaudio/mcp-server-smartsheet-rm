"""Server profiles and annotation-driven tool visibility.

A profile is one of two kinds; both coexist and both are selected with
``--profile`` / ``SMARTSHEET_RM_PROFILE``:

* **Domain-mount profile** — ``domains`` names the domain sub-servers to mount.
  Use it when a job maps cleanly onto whole domains (``full``, ``time``, ``projects``, ``admin``).
* **Allowlist profile** — ``tools`` is an explicit set of client-visible tool names
  applied over the full mounted catalog. Use it for job-shaped profiles that cut
  across domains (``timesheets``, ``staffing``, ``org_setup``, ``portfolio``).
  Filtering is tools-only: prompts and resources stay.

``readonly=True`` keeps only tools whose MCP ``readOnlyHint`` annotation is ``True``.
The annotation is the single source of truth for read-only classification; a missing
annotation or ``readOnlyHint`` other than ``True`` counts as a write (fail closed).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from fastmcp.server.transforms import GetToolNext, Transform
from fastmcp.tools import Tool
from fastmcp.utilities.versions import VersionSpec
from mcp.types import ToolAnnotations

ALL_DOMAINS: tuple[str, ...] = ("time", "projects", "admin")

# Bulk destructive tools: listed in ``full`` (and their domain-mount profile) but refused at
# call time by the domain guard unless SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1.
BULK_DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {"time_bulk_delete_time_entries", "projects_bulk_delete_assignments"}
)

# Job (allowlist) profile tool lists, in catalog order.
TIMESHEETS_TOOLS: frozenset[str] = frozenset(
    {
        "time_list_time_entries",
        "time_get_time_entry",
        "time_create_time_entry",
        "time_update_time_entry",
        "time_delete_time_entry",
        "time_list_user_suggestions",
        "time_update_time_approval_status",
        "time_lock_timesheet",
        "time_fill_weekly_timesheet",
        "time_confirm_suggested_hours",
        "time_reconcile_and_submit_week",
        "time_list_approvals",
        "time_create_approval",
        "time_delete_approval",
        "projects_list_projects",
        "projects_get_project",
        "projects_list_assignments",
        "projects_list_assignment_subtasks",
        "admin_list_users",
        "admin_get_user",
        "admin_list_leave_types",
        "admin_list_holidays",
    }
)

STAFFING_TOOLS: frozenset[str] = frozenset(
    {
        "projects_list_projects",
        "projects_get_project",
        "projects_list_project_users",
        "projects_list_project_phases",
        "projects_get_project_phase",
        "projects_list_assignments",
        "projects_get_assignment",
        "projects_create_assignment",
        "projects_update_assignment",
        "projects_delete_assignment",
        "projects_list_status_options",
        "projects_list_placeholder_resources",
        "projects_create_placeholder_resource",
        "projects_delete_placeholder_resource",
        "projects_list_assignment_subtasks",
        "projects_create_assignment_subtask",
        "projects_delete_assignment_subtask",
        "admin_list_users",
        "admin_get_user",
        "admin_get_user_availability",
        "admin_get_user_utilization",
        "admin_list_roles",
        "admin_list_disciplines",
        "admin_list_holidays",
        "admin_get_user_statuses",
    }
)

ORG_SETUP_TOOLS: frozenset[str] = frozenset(
    {
        "admin_list_users",
        "admin_get_user",
        "admin_create_user",
        "admin_update_user",
        "admin_delete_user",
        "admin_list_user_bill_rates",
        "admin_create_user_bill_rate",
        "admin_list_roles",
        "admin_create_role",
        "admin_update_role",
        "admin_delete_role",
        "admin_list_disciplines",
        "admin_create_discipline",
        "admin_update_discipline",
        "admin_delete_discipline",
        "admin_list_leave_types",
        "admin_get_leave_type",
        "admin_create_leave_type",
        "admin_update_leave_type",
        "admin_delete_leave_type",
        "admin_list_holidays",
        "admin_get_holiday",
        "admin_create_holiday",
        "admin_update_holiday",
        "admin_delete_holiday",
        "admin_list_custom_fields",
        "admin_get_custom_field",
        "admin_create_custom_field",
        "admin_update_custom_field",
        "admin_delete_custom_field",
        "admin_get_user_statuses",
        "admin_set_user_status",
        "admin_list_webhooks",
        "admin_create_webhook",
        "admin_delete_webhook",
    }
)

PORTFOLIO_TOOLS: frozenset[str] = frozenset(
    {
        "projects_list_projects",
        "projects_get_project",
        "projects_create_project",
        "projects_update_project",
        "projects_delete_project",
        "projects_list_project_users",
        "projects_list_project_phases",
        "projects_get_project_phase",
        "projects_create_project_phase",
        "projects_update_project_phase",
        "projects_delete_project_phase",
        "projects_clone_project_schedule",
        "projects_list_status_options",
        "admin_list_clients",
        "admin_get_client",
        "admin_create_client",
        "admin_update_client",
        "admin_list_client_contacts",
        "admin_create_client_contact",
        "admin_list_expenses",
        "admin_get_expense",
        "admin_create_expense",
        "admin_update_expense",
        "admin_delete_expense",
        "admin_list_expense_categories",
        "admin_list_tags",
        "admin_create_tag",
        "admin_list_custom_fields",
        "admin_get_custom_field",
        "admin_list_custom_field_values",
        "admin_set_custom_field_values",
        "admin_get_report_rows",
        "admin_get_report_totals",
    }
)

# Tools in no job (allowlist) profile. They stay reachable in ``full`` (and in their
# domain-mount profile). Tests require every tool in ``full`` to be in a job profile or
# listed here, so a new tool is placed on purpose.
FULL_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "time_bulk_delete_time_entries",
        "projects_bulk_delete_assignments",
        "admin_delete_client",
        "admin_delete_client_contact",
        "admin_create_expense_category",
        "admin_delete_expense_category",
        "admin_delete_tag",
    }
)


@dataclass(frozen=True)
class Profile:
    """A named server profile: domain mounts or a tool-name allowlist."""

    name: str
    job: str
    domains: tuple[str, ...] = ALL_DOMAINS
    tools: frozenset[str] | None = None
    readonly: bool = False

    @property
    def is_allowlist(self) -> bool:
        """True when the profile is an explicit tool-name allowlist."""
        return self.tools is not None


PROFILES: dict[str, Profile] = {
    profile.name: profile
    for profile in (
        # Domain-mount profiles
        Profile(
            name="full",
            job="Complete catalog: every tool, including bulk/destructive tools (still gated).",
        ),
        Profile(name="time", job="Time tracking, approvals and timesheet recipes (time domain).", domains=("time",)),
        Profile(
            name="projects",
            job="Projects, phases, assignments, placeholders and subtasks (projects domain).",
            domains=("projects",),
        ),
        Profile(
            name="admin",
            job="Users, org reference data, clients, expenses, fields, reports, webhooks (admin domain).",
            domains=("admin",),
        ),
        Profile(
            name="readonly",
            job="Inspect without side effects: every tool annotated readOnlyHint=True.",
            readonly=True,
        ),
        # Allowlist (job-shaped) profiles: span domains, names are client-visible names
        Profile(
            name="timesheets",
            job=(
                "Team member or manager logs, corrects, submits, locks and approves weekly time against their "
                "assignments."
            ),
            tools=TIMESHEETS_TOOLS,
        ),
        Profile(
            name="staffing",
            job=(
                "Resource manager checks people's availability and utilization and creates or adjusts "
                "assignments, placeholders and subtasks on projects."
            ),
            tools=STAFFING_TOOLS,
        ),
        Profile(
            name="org_setup",
            job=(
                "Resource-management admin onboards people and maintains org reference data: users, bill rates, "
                "roles, disciplines, leave types, holidays, custom-field definitions, user statuses and "
                "webhooks."
            ),
            tools=ORG_SETUP_TOOLS,
        ),
        Profile(
            name="portfolio",
            job=(
                "PMO sets up and maintains projects, phases, clients, expenses and project metadata, and reads "
                "budget/report totals."
            ),
            tools=PORTFOLIO_TOOLS,
        ),
    )
}

READ_ONLY_ANNOTATIONS = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


def get_profile(name: str) -> Profile:
    """Return the named profile or raise ``ValueError`` listing valid profiles."""
    key = name.lower()
    if key not in PROFILES:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown profile {name!r}; valid profiles: {valid}.")
    return PROFILES[key]


def validate_allowlist(profile: Profile, catalog: Collection[str]) -> frozenset[str]:
    """Return the profile allowlist, raising ``ValueError`` on any name not in ``catalog``."""
    allowlist = profile.tools or frozenset()
    unknown = sorted(allowlist - set(catalog))
    if unknown:
        raise ValueError(f"Profile {profile.name!r} allowlists tools not in the full catalog: {', '.join(unknown)}.")
    return allowlist


def is_read_only_tool(tool: Tool | None) -> bool:
    """Return True only for a tool annotated ``readOnlyHint=True`` (fail closed)."""
    if tool is None or tool.annotations is None:
        return False
    return tool.annotations.read_only_hint is True


class ReadOnlyToolFilter(Transform):
    """Keep only tools annotated ``readOnlyHint=True``; other component types pass through."""

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [tool for tool in tools if is_read_only_tool(tool)]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return tool if is_read_only_tool(tool) else None


class ReadOnlyAnnotations(Transform):
    """Annotate named synthetic discovery tools as ``readOnlyHint=True``.

    FastMCP's synthetic discovery tools (``search_tools``; Code Mode ``search`` /
    ``get_schema``) ship without annotations. They only read the catalog, so the
    server marks them read-only to keep discovery usable under the readonly gate.
    ``call_tool`` / ``execute`` are deliberately not annotated: the gate classifies
    ``call_tool`` by the tool it proxies, and ``execute`` stays refused under readonly.
    """

    def __init__(self, names: Collection[str]) -> None:
        self.names = frozenset(names)

    def _annotate(self, tool: Tool) -> Tool:
        if tool.name not in self.names:
            return tool
        return tool.model_copy(update={"annotations": READ_ONLY_ANNOTATIONS})

    async def list_tools(self, tools: Sequence[Tool]) -> Sequence[Tool]:
        return [self._annotate(tool) for tool in tools]

    async def get_tool(self, name: str, call_next: GetToolNext, *, version: VersionSpec | None = None) -> Tool | None:
        tool = await call_next(name, version=version)
        return None if tool is None else self._annotate(tool)
