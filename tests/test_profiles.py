"""Tests for job allowlist profiles, domain-mount profiles, and the annotation-driven readonly gate."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import NotFoundError, ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.server.transforms.search import RegexSearchTransform
from mcp.types import ToolAnnotations
from scripts.check_tool_contract import EXPECTED_FULL_ONLY, EXPECTED_PROFILE_COUNTS

from smartsheet_rm_mcp import middleware, profiles, server
from smartsheet_rm_mcp.client import SmartsheetRMClient
from smartsheet_rm_mcp.config import settings
from smartsheet_rm_mcp.errors import SafetyViolationError
from smartsheet_rm_mcp.middleware import ReadOnlyGateMiddleware
from smartsheet_rm_mcp.profiles import (
    BULK_DESTRUCTIVE_TOOLS,
    FULL_ONLY_TOOLS,
    PROFILES,
    Profile,
    ReadOnlyToolFilter,
    is_read_only_tool,
)
from smartsheet_rm_mcp.server import create_server

JOB_PROFILES = [p for p in PROFILES.values() if p.is_allowlist]
TIMESHEETS = PROFILES["timesheets"]

# Signed-off counts (total tools, read-only tools); Nexus gates on these.
SIGNED_OFF = {
    "full": (100, 39),
    "readonly": (39, 39),
    "timesheets": (22, 12),
    "staffing": (25, 18),
    "org_setup": (35, 13),
    "portfolio": (33, 18),
}


@pytest.fixture(autouse=True)
def _offline_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Gate switches off and an offline API client that answers every GET with a fixture."""
    for var in (
        "SMARTSHEET_RM_PROFILE",
        "SMARTSHEET_RM_READONLY",
        "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE",
        "SMARTSHEET_RM_ENABLE_TOOL_SEARCH",
        "SMARTSHEET_RM_ENABLE_CODE_MODE",
        "SMARTSHEET_RM_TOOL_SEARCH_BACKEND",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(settings, "READONLY", False)
    monkeypatch.setattr(settings, "ALLOW_BULK_DESTRUCTIVE", False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": 7, "name": "Apollo"}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.rm.smartsheet.com/api/v1")
    monkeypatch.setattr(server, "_client", SmartsheetRMClient(api_token="test-token", http_client=http))
    yield


async def _tool_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools()}


async def _read_only_names(app: FastMCP) -> set[str]:
    return {t.name for t in await app.list_tools() if is_read_only_tool(t)}


# ---------------------------------------------------------------------------
# Profile counts (signed off) and catalog placement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("profile", "expected"), sorted(SIGNED_OFF.items()))
async def test_profile_counts_match_signed_off(profile: str, expected: tuple[int, int]) -> None:
    """Every signed-off profile builds with exactly its total and read-only tool counts."""
    app = create_server(profile=profile)
    assert (len(await _tool_names(app)), len(await _read_only_names(app))) == expected
    assert EXPECTED_PROFILE_COUNTS[profile] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", sorted(EXPECTED_PROFILE_COUNTS))
async def test_profile_counts_compose_with_readonly_env(monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """SMARTSHEET_RM_READONLY=1 on any profile keeps exactly that profile's read-only tools."""
    plain = await _read_only_names(create_server(profile=profile))
    monkeypatch.setenv("SMARTSHEET_RM_READONLY", "1")
    names = await _tool_names(create_server(profile=profile))
    assert names == plain
    assert len(names) == EXPECTED_PROFILE_COUNTS[profile][1]


@pytest.mark.asyncio
async def test_full_is_exhaustive_and_every_tool_is_placed() -> None:
    """full lists every tool; each tool is in a job profile or marked full-only."""
    full_names = await _tool_names(create_server(profile="full"))
    in_jobs = set().union(*(p.tools or frozenset() for p in JOB_PROFILES))
    assert in_jobs | FULL_ONLY_TOOLS == full_names
    assert not in_jobs & FULL_ONLY_TOOLS


def test_full_only_tools() -> None:
    """The seven full-only tools: both bulk deletes plus five admin client/expense/tag tools."""
    assert FULL_ONLY_TOOLS == EXPECTED_FULL_ONLY
    assert len(FULL_ONLY_TOOLS) == 7
    assert BULK_DESTRUCTIVE_TOOLS <= FULL_ONLY_TOOLS
    assert server.FULL_ONLY_TOOLS is FULL_ONLY_TOOLS


def test_every_profile_has_a_one_line_job() -> None:
    """Each profile documents the job it serves in a single line."""
    for profile in PROFILES.values():
        assert profile.job.strip()
        assert "\n" not in profile.job


@pytest.mark.asyncio
async def test_every_tool_has_explicit_read_only_hint() -> None:
    """Reads carry readOnlyHint=True and writes readOnlyHint=False; none leave it unset."""
    tools = await create_server(profile="full").list_tools()
    missing = sorted(t.name for t in tools if t.annotations is None or t.annotations.read_only_hint is None)
    assert missing == []
    reads = {t.name for t in tools if t.annotations and t.annotations.read_only_hint is True}
    writes = {t.name for t in tools if t.annotations and t.annotations.read_only_hint is False}
    assert len(reads) == 39
    assert len(writes) == 61
    assert reads == set(PROFILES_READONLY_NAMES)


PROFILES_READONLY_NAMES = (
    "time_list_time_entries",
    "time_get_time_entry",
    "time_list_user_suggestions",
    "time_list_approvals",
    "projects_list_projects",
    "projects_get_project",
    "projects_list_project_users",
    "projects_list_project_phases",
    "projects_get_project_phase",
    "projects_list_assignments",
    "projects_get_assignment",
    "projects_list_status_options",
    "projects_list_placeholder_resources",
    "projects_list_assignment_subtasks",
    "admin_list_users",
    "admin_get_user",
    "admin_list_user_bill_rates",
    "admin_get_user_availability",
    "admin_get_user_utilization",
    "admin_list_roles",
    "admin_list_disciplines",
    "admin_list_clients",
    "admin_get_client",
    "admin_list_client_contacts",
    "admin_list_leave_types",
    "admin_get_leave_type",
    "admin_list_holidays",
    "admin_get_holiday",
    "admin_list_expenses",
    "admin_get_expense",
    "admin_list_expense_categories",
    "admin_list_tags",
    "admin_list_custom_fields",
    "admin_get_custom_field",
    "admin_list_custom_field_values",
    "admin_get_user_statuses",
    "admin_get_report_rows",
    "admin_get_report_totals",
    "admin_list_webhooks",
)


# ---------------------------------------------------------------------------
# Profile kinds: domain mounts and allowlists coexist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_exposes_exactly_its_tools() -> None:
    """timesheets exposes exactly its allowlist, spanning the time, projects and admin domains."""
    assert TIMESHEETS.tools is not None
    names = await _tool_names(create_server(profile="timesheets"))
    assert names == set(TIMESHEETS.tools)
    assert {n.split("_", 1)[0] for n in names} == {"time", "projects", "admin"}


@pytest.mark.asyncio
async def test_domain_mount_and_allowlist_profiles_coexist() -> None:
    """A domain-mount profile (time) and an allowlist profile (timesheets) both build."""
    assert not PROFILES["time"].is_allowlist
    assert TIMESHEETS.is_allowlist
    time_names = await _tool_names(create_server(profile="time"))
    sheet_names = await _tool_names(create_server(profile="timesheets"))
    assert "time_bulk_delete_time_entries" in time_names
    assert "time_bulk_delete_time_entries" not in sheet_names
    assert "admin_list_users" in sheet_names


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [p.name for p in JOB_PROFILES])
async def test_allowlist_keeps_prompts_and_resources(profile: str) -> None:
    """Allowlist filtering is tools-only: prompts and resources match the full profile."""
    full = create_server(profile="full")
    job = create_server(profile=profile)
    assert {p.name for p in await job.list_prompts()} == {p.name for p in await full.list_prompts()}
    assert {str(r.uri) for r in await job.list_resources()} == {str(r.uri) for r in await full.list_resources()}
    assert {p.name for p in await job.list_prompts()} == {
        "time_timesheet_reconciliation",
        "projects_project_staffing_plan",
    }
    assert {str(r.uri) for r in await job.list_resources()} == {"rm://admin/capabilities", "rm://admin/quickstart"}
    rendered = await job.render_prompt("projects_project_staffing_plan", {"project_id": "42"})
    assert "42" in str(rendered)


@pytest.mark.asyncio
async def test_allowlist_hides_tools_outside_the_list() -> None:
    """A tool outside the allowlist cannot be called on that profile."""
    app = create_server(profile="timesheets")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("admin_delete_user", {"user_id": 1, "confirm": True})


def test_unknown_allowlist_name_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every allowlisted name must exist in the full catalog; no silent drop."""
    broken = Profile(
        name="broken",
        job="Test only.",
        tools=frozenset({"time_list_time_entries", "time_nonexistent", "rm_list_users"}),
    )
    monkeypatch.setitem(profiles.PROFILES, "broken", broken)
    with pytest.raises(ValueError, match="rm_list_users, time_nonexistent"):
        create_server(profile="broken")


def test_unknown_profile_fails_at_build() -> None:
    """An unknown profile name raises instead of building an empty server."""
    with pytest.raises(ValueError, match="Unknown profile 'nope'"):
        create_server(profile="nope")


def test_unknown_profile_from_env_fails_at_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """SMARTSHEET_RM_PROFILE is validated the same way as the argument."""
    monkeypatch.setenv("SMARTSHEET_RM_PROFILE", "rm")
    with pytest.raises(ValueError, match="Unknown profile 'rm'"):
        create_server()


def test_main_accepts_allowlist_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """--profile staffing is a valid CLI choice."""
    fake_run = MagicMock()
    monkeypatch.setattr(FastMCP, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["smartsheet-rm-mcp", "--profile", "staffing"])
    server.main()
    fake_run.assert_called_once()


# ---------------------------------------------------------------------------
# Bulk destructive tools: listed in full, refused at call time without the env
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("time_bulk_delete_time_entries", {"entry_ids": [1], "confirm": True}),
        ("projects_bulk_delete_assignments", {"assignment_ids": [1], "confirm": True}),
    ],
)
async def test_bulk_tool_listed_in_full_but_blocked_without_gate(tool: str, arguments: dict[str, Any]) -> None:
    """Bulk destructive tools are listed in full but refused (isError) while the bulk gate is off."""
    app = create_server(profile="full")
    assert tool in await _tool_names(app)
    with pytest.raises(SafetyViolationError, match="Bulk destructive operations disabled"):
        await app.call_tool(tool, arguments)
    async with Client(app) as client:
        res = await client.call_tool(tool, arguments, raise_on_error=False)
    assert res.is_error
    assert "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1" in str(res.content)


@pytest.mark.asyncio
async def test_bulk_tool_runs_with_gate_and_still_needs_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """With SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE=1 the call reaches the handler's confirm gate."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE", "1")
    app = create_server(profile="full")
    res = await app.call_tool("time_bulk_delete_time_entries", {"entry_ids": [1], "confirm": False})
    assert not res.is_error
    payload = json.loads(res.content[0].text)  # type: ignore[union-attr]
    assert payload["status"] == "confirmation_required"
    assert "time_bulk_delete_time_entries(count=1)" in payload["message"]
    assert "Re-call this tool with confirm=true" in payload["message"]


# ---------------------------------------------------------------------------
# Discovery stays full-only on allowlist profiles
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_allowlist_profile_with_search_stays_flat(caplog: pytest.LogCaptureFixture) -> None:
    """Requesting Tool Search or Code Mode on an allowlist profile logs and stays flat."""
    with caplog.at_level(logging.WARNING):
        searched = create_server(profile="timesheets", enable_tool_search=True)
        coded = create_server(profile="timesheets", enable_code_mode=True)
    assert TIMESHEETS.tools is not None
    assert await _tool_names(searched) == set(TIMESHEETS.tools)
    assert await _tool_names(coded) == set(TIMESHEETS.tools)
    messages = [r.message for r in caplog.records]
    assert any("Tool Search requested with profile='timesheets'" in m for m in messages)
    assert any("Code Mode requested with profile='timesheets'" in m for m in messages)


def test_discovery_modes_mutually_exclusive_on_allowlist_profile() -> None:
    """Mutual exclusion is checked before profile handling."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="timesheets", enable_tool_search=True, enable_code_mode=True)


# ---------------------------------------------------------------------------
# Read-only: readOnlyHint is the single source of truth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_profile_equals_read_only_annotated_set() -> None:
    """The readonly profile exposes exactly the full tools annotated readOnlyHint=True."""
    annotated = await _read_only_names(create_server(profile="full"))
    assert await _tool_names(create_server(profile="readonly")) == annotated


@pytest.mark.asyncio
async def test_readonly_profile_gate_enforced_without_env() -> None:
    """profile=readonly enforces the gate itself, even with SMARTSHEET_RM_READONLY unset."""
    app = create_server(profile="readonly")
    with pytest.raises(SafetyViolationError, match="projects_create_project"):
        await app.call_tool("projects_create_project", {"name": "x"})
    res = await app.call_tool("projects_list_projects", {})
    assert not res.is_error


@pytest.mark.asyncio
async def test_readonly_enforced_under_allowlist_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """SMARTSHEET_RM_READONLY=1 on an allowlist profile hides and refuses its write tools."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="timesheets")
    assert len(await _tool_names(app)) == 12
    with pytest.raises(SafetyViolationError, match="time_create_time_entry"):
        await app.call_tool("time_create_time_entry", {})
    res = await app.call_tool("projects_get_project", {"project_id": 7})
    assert not res.is_error


def _annotation_probe_server() -> FastMCP:
    """Minimal server with one tool per annotation state, gate on, and Tool Search."""
    app = FastMCP("annotation-probe")
    app.add_middleware(ReadOnlyGateMiddleware())

    @app.tool(annotations=ToolAnnotations(read_only_hint=True))
    def annotated_read() -> str:
        return "read"

    @app.tool(annotations=ToolAnnotations(read_only_hint=False))
    def annotated_write() -> str:
        return "write"

    @app.tool
    def unannotated() -> str:
        return "unannotated"

    @app.tool(annotations=ToolAnnotations(title="no hint"))
    def hint_missing() -> str:
        return "hint missing"

    app.add_transform(RegexSearchTransform())
    return app


@pytest.mark.asyncio
async def test_gate_allows_read_only_annotation_directly_and_via_call_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """readOnlyHint=True is allowed under readonly, directly and through call_tool."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = _annotation_probe_server()
    direct = await app.call_tool("annotated_read", {})
    assert "read" in str(direct.content)
    proxied = await app.call_tool("call_tool", {"name": "annotated_read", "arguments": {}})
    assert "read" in str(proxied.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["annotated_write", "unannotated", "hint_missing"])
async def test_gate_refuses_false_or_missing_annotation(monkeypatch: pytest.MonkeyPatch, tool_name: str) -> None:
    """readOnlyHint=False or no hint is a write: refused directly and via call_tool."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = _annotation_probe_server()
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool(tool_name, {})
    with pytest.raises(SafetyViolationError, match=tool_name):
        await app.call_tool("call_tool", {"name": tool_name, "arguments": {}})


@pytest.mark.asyncio
async def test_readonly_tool_filter_get_tool() -> None:
    """ReadOnlyToolFilter.get_tool returns read-only tools and drops the rest."""
    app = _annotation_probe_server()
    app.add_transform(ReadOnlyToolFilter())
    assert await app.get_tool("annotated_read") is not None
    assert await app.get_tool("annotated_write") is None
    assert await app.get_tool("unannotated") is None


def test_safety_violation_is_a_tool_error() -> None:
    """Refusals are FastMCP ToolErrors, so clients get a tools/call result with isError: true."""
    assert issubclass(SafetyViolationError, ToolError)


# ---------------------------------------------------------------------------
# Read-only through the Tool Search proxy on full
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_search_readonly_read_succeeds_write_refused_on_wire(monkeypatch: pytest.MonkeyPatch) -> None:
    """On full + Tool Search + readonly: a READ via call_tool works; a WRITE is isError."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    async with Client(app) as client:
        read = await client.call_tool(
            "call_tool",
            {"name": "projects_list_projects", "arguments": {}},
            raise_on_error=False,
        )
        assert not read.is_error
        assert "Apollo" in str(read.content)

        write = await client.call_tool(
            "call_tool",
            {"name": "projects_create_project", "arguments": {"name": "blocked"}},
            raise_on_error=False,
        )
        assert write.is_error
        assert "read-only mode" in str(write.content)
        assert "projects_create_project" in str(write.content)


@pytest.mark.asyncio
async def test_gate_refuses_on_outer_call_tool_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal comes from the gate unwrapping the outer call_tool, not an inner re-run."""
    monkeypatch.setattr(settings, "READONLY", True)
    seen: list[tuple[str, str]] = []
    real_resolve = middleware._resolve_effective_tool_name

    def spy(context: MiddlewareContext) -> str:
        effective = real_resolve(context)
        seen.append((context.message.name, effective))
        return effective

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", spy)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="projects_create_project"):
        await app.call_tool("call_tool", {"name": "projects_create_project", "arguments": {}})
    # Exactly one gate decision: on the outer proxy call. The inner call never ran.
    assert seen == [("call_tool", "projects_create_project")]


@pytest.mark.asyncio
async def test_without_unwrap_the_proxy_gate_behaviour_breaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: with the unwrap removed, reads via call_tool are refused and the
    write refusal names the proxy instead of the proxied tool."""
    monkeypatch.setattr(settings, "READONLY", True)

    def no_unwrap(context: MiddlewareContext) -> str:
        name: Any = getattr(context.message, "name", "")
        return str(name)

    monkeypatch.setattr(middleware, "_resolve_effective_tool_name", no_unwrap)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
        await app.call_tool("call_tool", {"name": "projects_list_projects", "arguments": {}})
    with pytest.raises(SafetyViolationError) as exc_info:
        await app.call_tool("call_tool", {"name": "projects_create_project", "arguments": {}})
    assert "projects_create_project" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_search_tools_annotated_read_only_and_usable_under_readonly(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_tools carries readOnlyHint=True, works under readonly, and finds only reads."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search_tools"])
    assert not is_read_only_tool(listed["call_tool"])
    res = await app.call_tool("search_tools", {"pattern": "projects_"})
    assert not res.is_error
    assert "projects_list_projects" in str(res.content)
    assert "projects_create_project" not in str(res.content)
    assert await app.get_tool("not_a_tool") is None


@pytest.mark.asyncio
async def test_code_mode_discovery_read_only_and_execute_refused_under_readonly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Code Mode search/get_schema are annotated read-only; execute is refused under readonly."""
    try:
        from fastmcp.experimental.transforms.code_mode import CodeMode  # noqa: F401
    except ImportError:  # pragma: no cover - depends on FastMCP build
        pytest.skip("CodeMode not available in this FastMCP build")

    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full", enable_code_mode=True)
    listed = {t.name: t for t in await app.list_tools()}
    assert is_read_only_tool(listed["search"])
    assert is_read_only_tool(listed["get_schema"])
    assert not is_read_only_tool(listed["execute"])
    with pytest.raises(SafetyViolationError, match="'execute' blocked"):
        await app.call_tool("execute", {"code": "return 1"})


# ---------------------------------------------------------------------------
# Read-only: unknown names keep FastMCP's "Unknown tool"; real writes are refused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_readonly_unknown_name_direct_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct call to a name outside the catalog gets Unknown tool, not the read-only refusal."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full")
    with pytest.raises(NotFoundError, match="Unknown tool: 'rm_list_users'"):
        await app.call_tool("rm_list_users", {})
    async with Client(app) as client:
        res = await client.call_tool("rm_list_users", {}, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_unknown_name_via_call_tool_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """call_tool with a name outside the catalog gets Unknown tool on full + search."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(ToolError, match="Unknown tool: 'projects_nope'") as exc_info:
        await app.call_tool("call_tool", {"name": "projects_nope", "arguments": {}})
    assert not isinstance(exc_info.value, SafetyViolationError)
    async with Client(app) as client:
        res = await client.call_tool("call_tool", {"name": "projects_nope", "arguments": {}}, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("via_proxy", [False, True])
async def test_readonly_hidden_write_refused_and_read_succeeds(
    monkeypatch: pytest.MonkeyPatch, via_proxy: bool
) -> None:
    """A real write hidden by the read-only filter is refused (isError); a real read still works."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full", enable_tool_search=via_proxy)
    assert "projects_create_project" not in await _tool_names(app)

    def call(name: str, arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        if via_proxy:
            return "call_tool", {"name": name, "arguments": arguments}
        return name, arguments

    async with Client(app) as client:
        write = await client.call_tool(*call("projects_create_project", {"name": "x"}), raise_on_error=False)
        read = await client.call_tool(*call("projects_get_project", {"project_id": 7}), raise_on_error=False)
    assert write.is_error
    assert "read-only mode" in str(write.content)
    assert "projects_create_project" in str(write.content)
    assert not read.is_error
    assert "Apollo" in str(read.content)
    with pytest.raises(SafetyViolationError, match="projects_create_project"):
        await app.call_tool(*call("projects_create_project", {"name": "x"}))


@pytest.mark.asyncio
async def test_readonly_outside_allowlist_is_unknown_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under read-only, a tool outside the active allowlist profile is unknown, like without it."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="timesheets")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("admin_delete_user", {"user_id": 1})
    with pytest.raises(SafetyViolationError, match="time_create_time_entry"):
        await app.call_tool("time_create_time_entry", {})


@pytest.mark.asyncio
async def test_readonly_switched_on_after_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """With read-only switched on at runtime (no filter, empty gate catalog), unknown names
    still reach Unknown tool and real writes are still refused."""
    app = create_server(profile="full")
    monkeypatch.setenv("SMARTSHEET_RM_READONLY", "1")
    with pytest.raises(NotFoundError, match="Unknown tool"):
        await app.call_tool("projects_nope", {})
    with pytest.raises(SafetyViolationError, match="projects_create_project"):
        await app.call_tool("projects_create_project", {"name": "x"})


# ---------------------------------------------------------------------------
# Read-only without Tool Search: call_tool is an unknown name, not a proxy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["full", "timesheets", "readonly"])
@pytest.mark.parametrize("inner", ["projects_create_project", "projects_list_projects"])
async def test_readonly_call_tool_without_search_is_unknown_tool(
    monkeypatch: pytest.MonkeyPatch, profile: str, inner: str
) -> None:
    """Without Tool Search, call_tool is not on the server: Unknown tool 'call_tool' for both
    a write and a read inner name, never the read-only refusal."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile=profile, enable_tool_search=False)
    arguments = {"name": inner, "arguments": {}}
    with pytest.raises(NotFoundError, match="Unknown tool: 'call_tool'") as exc_info:
        await app.call_tool("call_tool", arguments)
    assert not isinstance(exc_info.value, SafetyViolationError)
    async with Client(app) as client:
        res = await client.call_tool("call_tool", arguments, raise_on_error=False)
    assert res.is_error
    assert "Unknown tool: 'call_tool'" in str(res.content)
    assert "read-only" not in str(res.content)


@pytest.mark.asyncio
async def test_readonly_call_tool_with_search_still_unwraps(monkeypatch: pytest.MonkeyPatch) -> None:
    """With Tool Search on full, call_tool is real: unwrap and judge the proxied tool."""
    monkeypatch.setattr(settings, "READONLY", True)
    app = create_server(profile="full", enable_tool_search=True)
    with pytest.raises(SafetyViolationError, match="projects_create_project"):
        await app.call_tool("call_tool", {"name": "projects_create_project", "arguments": {}})
    read = await app.call_tool("call_tool", {"name": "projects_list_projects", "arguments": {}})
    assert not read.is_error
