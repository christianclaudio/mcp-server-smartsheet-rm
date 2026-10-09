"""Tests for FastMCP 4 Server Composition, profiles, middleware, discovery, and domain guards."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext

from smartsheet_rm_mcp.config import Settings, SmartsheetRMSettings, settings
from smartsheet_rm_mcp.errors import SafetyViolationError
from smartsheet_rm_mcp.middleware import (
    AdminDomainGuardMiddleware,
    ParentAuditMiddleware,
    ProjectsDomainGuardMiddleware,
    ReadOnlyGateMiddleware,
    TimeDomainGuardMiddleware,
)
from smartsheet_rm_mcp.server import create_server, main, server_lifespan


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with read-only, bulk, and discovery switches off."""
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
    yield


@pytest.mark.asyncio
async def test_create_server_profiles() -> None:
    """Verify create_server mounts the right domain sub-servers per domain-mount profile."""
    # 1. Full profile: exhaustive, bulk tools listed (refused at call time without the env)
    full_server = create_server(profile="full")
    tools_full = await full_server.list_tools()
    assert len(tools_full) == 100
    names = {t.name for t in tools_full}
    assert "time_list_time_entries" in names
    assert "projects_list_projects" in names
    assert "admin_list_users" in names
    assert {"time_bulk_delete_time_entries", "projects_bulk_delete_assignments"} <= names

    # 2. Time profile
    tools_time = await create_server(profile="time").list_tools()
    assert len(tools_time) == 15
    assert {t.name.split("_", 1)[0] for t in tools_time} == {"time"}

    # 3. Projects profile
    tools_proj = await create_server(profile="projects").list_tools()
    assert len(tools_proj) == 25
    assert {t.name.split("_", 1)[0] for t in tools_proj} == {"projects"}

    # 4. Admin profile
    tools_admin = await create_server(profile="admin").list_tools()
    assert len(tools_admin) == 60
    assert {t.name.split("_", 1)[0] for t in tools_admin} == {"admin"}

    # 5. Readonly profile
    tools_ro = await create_server(profile="readonly").list_tools()
    assert len(tools_ro) == 39
    assert all(t.annotations and t.annotations.read_only_hint is True for t in tools_ro)

    # 6. Invalid profile raises ValueError
    with pytest.raises(ValueError, match="Unknown profile 'invalid_profile_name'"):
        create_server(profile="invalid_profile_name")


@pytest.mark.asyncio
async def test_create_server_tool_search() -> None:
    """full + Tool Search replaces tools/list with the discovery meta-tools."""
    srv = create_server(enable_tool_search=True)
    assert [t.name for t in await srv.list_tools()] == ["search_tools", "call_tool"]
    res = await srv.call_tool("search_tools", {"pattern": "time_list"})
    assert not res.is_error
    assert "time_list_time_entries" in str(res.content)


@pytest.mark.asyncio
async def test_bm25_tool_search_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_search_backend='bm25' (argument or env) still collapses tools/list to meta-tools."""
    app = create_server(profile="full", enable_tool_search=True, tool_search_backend="bm25")
    assert [t.name for t in await app.list_tools()] == ["search_tools", "call_tool"]
    res = await app.call_tool("search_tools", {"query": "timesheet"})
    assert not res.is_error
    assert "time_" in str(res.content)

    monkeypatch.setenv("SMARTSHEET_RM_TOOL_SEARCH_BACKEND", "BM25")
    env_app = create_server(profile="full", enable_tool_search=True)
    assert [t.name for t in await env_app.list_tools()] == ["search_tools", "call_tool"]

    monkeypatch.setenv("SMARTSHEET_RM_TOOL_SEARCH_BACKEND", "fuzzy")
    with pytest.raises(ValueError, match="Unknown SMARTSHEET_RM_TOOL_SEARCH_BACKEND 'fuzzy'"):
        create_server(profile="full", enable_tool_search=True)


@pytest.mark.asyncio
async def test_discovery_flags_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SMARTSHEET_RM_ENABLE_TOOL_SEARCH=1 / SMARTSHEET_RM_ENABLE_CODE_MODE=1 drive discovery."""
    monkeypatch.setenv("SMARTSHEET_RM_ENABLE_TOOL_SEARCH", "1")
    assert [t.name for t in await create_server(profile="full").list_tools()] == ["search_tools", "call_tool"]
    monkeypatch.setenv("SMARTSHEET_RM_ENABLE_CODE_MODE", "1")
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full")


@pytest.mark.asyncio
async def test_curated_profile_stays_flat_without_discovery_meta(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Curated profiles expose a flat list; never search/Code Mode meta-tools."""
    with caplog.at_level(logging.WARNING):
        time_app = create_server(profile="time", enable_tool_search=True, enable_code_mode=False)
    time_names = {t.name for t in await time_app.list_tools()}
    assert len(time_names) == 15
    assert not time_names & {"search_tools", "call_tool", "search", "execute"}
    assert any("Tool Search requested with profile='time'" in r.message for r in caplog.records)


def test_tool_search_and_code_mode_are_mutually_exclusive() -> None:
    """Enabling both discovery modes raises ValueError."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        create_server(profile="full", enable_tool_search=True, enable_code_mode=True)


@pytest.mark.asyncio
async def test_full_code_mode_attaches_when_available(caplog: pytest.LogCaptureFixture) -> None:
    """full + enable_code_mode attaches experimental meta-tools; curated profiles refuse it."""
    try:
        from fastmcp.experimental.transforms.code_mode import CodeMode  # noqa: F401
    except ImportError:  # pragma: no cover - depends on FastMCP build
        pytest.skip("CodeMode not available in this FastMCP build")

    app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    names = {t.name for t in await app.list_tools()}
    assert "execute" in names
    assert "search_tools" not in names
    assert "time_list_time_entries" not in names

    with caplog.at_level(logging.WARNING):
        curated = create_server(profile="projects", enable_code_mode=True)
    curated_names = {t.name for t in await curated.list_tools()}
    assert "execute" not in curated_names
    assert "projects_list_projects" in curated_names
    assert any("Code Mode requested with profile='projects'" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_code_mode_skips_attach_without_sandbox(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Without pydantic-monty (no fastmcp[code-mode]), Code Mode is skipped with a warning.

    The CodeMode import itself succeeds on a plain install; only the sandbox is missing.
    """
    import importlib.util

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pydantic_monty":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    with caplog.at_level(logging.WARNING):
        app = create_server(profile="full", enable_code_mode=True, enable_tool_search=False)
    names = {t.name for t in await app.list_tools()}
    assert "execute" not in names
    assert "search" not in names
    assert "time_list_time_entries" in names
    assert any("pydantic-monty" in r.message and "fastmcp[code-mode]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_parent_audit_middleware_success() -> None:
    """Verify ParentAuditMiddleware logs success and returns tool result."""
    mw = ParentAuditMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.message = MagicMock()
    ctx.message.name = "time_test_tool"
    ctx.method = "tools/call"

    expected_res = MagicMock()

    async def fake_call_next(_ctx: MiddlewareContext) -> MagicMock:
        return expected_res

    res = await mw.on_message(ctx, fake_call_next)
    assert res is expected_res


@pytest.mark.asyncio
async def test_parent_audit_middleware_error() -> None:
    """Verify ParentAuditMiddleware logs error with secret redaction and re-raises."""
    mw = ParentAuditMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.message = MagicMock()
    ctx.message.name = "time_test_tool"
    ctx.method = "tools/call"

    async def fake_failing_next(_ctx: MiddlewareContext) -> None:
        raise ValueError("Failed with Bearer secret-auth-token-xyz")

    with pytest.raises(ValueError) as exc_info:
        await mw.on_message(ctx, fake_failing_next)

    assert "secret-auth-token-xyz" not in str(exc_info.value)
    assert "***REDACTED***" in str(exc_info.value)

    # Clean error without secrets
    async def fake_clean_next(_ctx: MiddlewareContext) -> None:
        raise ValueError("Plain clean failure")

    with pytest.raises(ValueError) as clean_exc:
        await mw.on_message(ctx, fake_clean_next)
    assert str(clean_exc.value) == "Plain clean failure"

    # Exception with non-standard constructor falling back to RuntimeError
    class ComplexCustomError(Exception):
        def __init__(self, code: int, details: dict[str, str]) -> None:
            super().__init__(f"code={code}: {details}")

    async def fake_complex_next(_ctx: MiddlewareContext) -> None:
        raise ComplexCustomError(500, {"token": "Bearer secret-token-nested"})

    with pytest.raises(RuntimeError) as fallback_exc:
        await mw.on_message(ctx, fake_complex_next)
    assert "***REDACTED***" in str(fallback_exc.value)
    assert "secret-token-nested" not in str(fallback_exc.value)


@pytest.mark.asyncio
async def test_read_only_gate_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """ReadOnlyGateMiddleware passes everything when off; when on, judges by readOnlyHint."""
    serving = SimpleNamespace(fastmcp=create_server(profile="full"))
    gate = ReadOnlyGateMiddleware()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "success"

    def ctx(name: str) -> MiddlewareContext:
        return MiddlewareContext(
            method="tools/call",
            message=SimpleNamespace(name=name, arguments={}),
            fastmcp_context=serving,  # type: ignore[arg-type]
        )

    # Read-only off: writes pass
    assert await gate.on_message(ctx("projects_delete_project"), fake_next) == "success"

    # Read-only on via the live env flag: writes and recipe writes are refused
    monkeypatch.setenv("SMARTSHEET_RM_READONLY", "1")
    with pytest.raises(SafetyViolationError, match="'projects_delete_project' blocked"):
        await gate.on_message(ctx("projects_delete_project"), fake_next)
    with pytest.raises(SafetyViolationError, match="time_fill_weekly_timesheet"):
        await gate.on_message(ctx("time_fill_weekly_timesheet"), fake_next)

    # Reads and non-tools/call methods pass
    assert await gate.on_message(ctx("projects_list_projects"), fake_next) == "success"
    list_ctx = MiddlewareContext(method="tools/list", message=SimpleNamespace())
    assert await gate.on_message(list_ctx, fake_next) == "success"


@pytest.mark.asyncio
async def test_readonly_gate_unwraps_call_tool_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit: ReadOnlyGateMiddleware resolves the nested name inside call_tool arguments."""
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))
    monkeypatch.setattr(settings, "READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    proxy_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(
            name="call_tool",
            arguments={"name": "projects_delete_project", "arguments": {"project_id": 1}},
        ),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        await gate.on_message(proxy_ctx, allowed_call_next)
    assert "projects_delete_project" in str(exc_info.value)

    read_ctx = MiddlewareContext(
        method="tools/call",
        message=SimpleNamespace(name="call_tool", arguments={"name": "projects_list_projects", "arguments": {}}),
        fastmcp_context=serving,  # type: ignore[arg-type]
    )
    assert await gate.on_message(read_ctx, allowed_call_next) == "allowed"


@pytest.mark.asyncio
async def test_readonly_gate_call_tool_without_nested_name_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_tool without a usable nested name is classified as itself: unannotated → refused."""
    monkeypatch.setattr(settings, "READONLY", True)
    gate = ReadOnlyGateMiddleware()
    serving = SimpleNamespace(fastmcp=create_server(profile="full", enable_tool_search=True))

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    for message in (
        SimpleNamespace(name="call_tool", arguments=None),
        SimpleNamespace(name="call_tool", arguments={"name": 123}),
        SimpleNamespace(name="call_tool", arguments={"name": ""}),
    ):
        ctx = MiddlewareContext(
            method="tools/call",
            message=message,
            fastmcp_context=serving,  # type: ignore[arg-type]
        )
        with pytest.raises(SafetyViolationError, match="'call_tool' blocked"):
            await gate.on_message(ctx, allowed_call_next)


@pytest.mark.asyncio
async def test_readonly_gate_without_server_context_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no serving FastMCP context the annotation cannot be read, so the call is refused."""
    monkeypatch.setattr(settings, "READONLY", True)
    gate = ReadOnlyGateMiddleware()

    async def allowed_call_next(ctx: MiddlewareContext) -> str:
        return "allowed"

    for message in (
        SimpleNamespace(name="projects_list_projects", arguments={}),
        None,
    ):
        ctx = MiddlewareContext(method="tools/call", message=message)
        with pytest.raises(SafetyViolationError, match="read-only mode"):
            await gate.on_message(ctx, allowed_call_next)


@pytest.mark.asyncio
async def test_time_domain_guard_middleware() -> None:
    """Verify TimeDomainGuardMiddleware validates hours and daily_hours."""
    mw = TimeDomainGuardMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "ok"

    # Valid hours
    ctx.message.arguments = {"hours": 8.0, "daily_hours": 7.5}
    assert await mw.on_message(ctx, fake_next) == "ok"

    # Negative hours
    ctx.message.arguments = {"hours": -1.0}
    with pytest.raises(ValueError, match="Logged hours -1.0 out of realistic range"):
        await mw.on_message(ctx, fake_next)

    # Excessive hours
    ctx.message.arguments = {"hours": 25.0}
    with pytest.raises(ValueError, match="Logged hours 25.0 out of realistic range"):
        await mw.on_message(ctx, fake_next)

    # Negative daily_hours
    ctx.message.arguments = {"daily_hours": -0.5}
    with pytest.raises(ValueError, match="Daily hours -0.5 out of realistic range"):
        await mw.on_message(ctx, fake_next)

    # Excessive daily_hours
    ctx.message.arguments = {"daily_hours": 24.5}
    with pytest.raises(ValueError, match="Daily hours 24.5 out of realistic range"):
        await mw.on_message(ctx, fake_next)


@pytest.mark.asyncio
async def test_projects_domain_guard_middleware() -> None:
    """Verify ProjectsDomainGuardMiddleware validates project names."""
    mw = ProjectsDomainGuardMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "ok"

    # Valid arguments
    ctx.message.arguments = {"name": "Project Alpha", "target_project_name": "Project Beta"}
    assert await mw.on_message(ctx, fake_next) == "ok"

    # Blank project name
    ctx.message.arguments = {"name": "   "}
    with pytest.raises(ValueError, match="Project name must not be empty"):
        await mw.on_message(ctx, fake_next)

    # Blank target project name
    ctx.message.arguments = {"target_project_name": "   "}
    with pytest.raises(ValueError, match="Target project name must not be empty"):
        await mw.on_message(ctx, fake_next)


@pytest.mark.asyncio
async def test_admin_domain_guard_middleware() -> None:
    """Verify AdminDomainGuardMiddleware validates pagination limits."""
    mw = AdminDomainGuardMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()

    async def fake_next(_ctx: MiddlewareContext) -> str:
        return "ok"

    # Valid per_page
    ctx.message.arguments = {"per_page": 100}
    assert await mw.on_message(ctx, fake_next) == "ok"

    # Excessive per_page
    ctx.message.arguments = {"per_page": 1001}
    with pytest.raises(ValueError, match="per_page 1001 exceeds maximum allowable batch size"):
        await mw.on_message(ctx, fake_next)


@pytest.mark.asyncio
async def test_server_lifespan_aclose_branch() -> None:
    """Verify server_lifespan closes client with aclose when close is absent."""
    import smartsheet_rm_mcp.server as srv

    dummy_closed = False

    class DummyClientWithAcloseOnly:
        async def aclose(self) -> None:
            nonlocal dummy_closed
            dummy_closed = True

    srv._HEADER_CLIENT_CACHE[("tok", "url")] = DummyClientWithAcloseOnly()  # type: ignore[assignment]
    srv._client = DummyClientWithAcloseOnly()  # type: ignore[assignment]
    try:
        async with server_lifespan(srv.mcp):
            pass
        assert dummy_closed is True
    finally:
        srv._client = None
        srv._HEADER_CLIENT_CACHE.clear()


def test_main_cli_profile_and_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify CLI accepts --profile and --enable-tool-search flags."""
    run_called = False

    def mock_run(self: FastMCP, *args: object, **kwargs: object) -> None:
        nonlocal run_called
        run_called = True

    monkeypatch.setattr(FastMCP, "run", mock_run)
    monkeypatch.setattr(
        "sys.argv",
        ["smartsheet-rm-mcp", "--profile", "time", "--enable-tool-search"],
    )
    main()
    assert run_called is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--profile", "full", "--enable-code-mode"],
        ["--profile", "full", "--enable-tool-search", "--tool-search-backend", "bm25"],
        ["--profile", "timesheets"],
    ],
)
def test_main_cli_discovery_and_job_profiles(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """CLI accepts --enable-code-mode, --tool-search-backend, and job allowlist profiles."""
    fake_run = MagicMock()
    monkeypatch.setattr(FastMCP, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["smartsheet-rm-mcp", *argv])
    main()
    fake_run.assert_called_once()


def test_smartsheet_rm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify SmartsheetRMSettings defaults, env prefix, and backward-compatible Settings alias."""
    assert Settings is SmartsheetRMSettings
    for var in (
        "SMARTSHEET_RM_API_TOKEN",
        "SMARTSHEET_RM_BASE_URL",
        "SMARTSHEET_RM_PROFILE",
        "SMARTSHEET_RM_READONLY",
        "SMARTSHEET_RM_ALLOW_BULK_DESTRUCTIVE",
        "SMARTSHEET_RM_ENABLE_TOOL_SEARCH",
        "SMARTSHEET_RM_TOOL_SEARCH_BACKEND",
        "SMARTSHEET_RM_ENABLE_CODE_MODE",
        "SMARTSHEET_RM_CATALOG_CACHE_TTL_MS",
    ):
        monkeypatch.delenv(var, raising=False)

    s = SmartsheetRMSettings(_env_file=None, API_TOKEN="test-token")  # type: ignore[call-arg]
    assert s.API_TOKEN == "test-token"
    assert s.BASE_URL == "https://api.rm.smartsheet.com/api/v1"
    assert s.PROFILE == "full"
    assert s.READONLY is False
    assert s.ALLOW_BULK_DESTRUCTIVE is False
    assert s.ENABLE_TOOL_SEARCH is False
    assert s.TOOL_SEARCH_BACKEND == "regex"
    assert s.ENABLE_CODE_MODE is False
    assert s.CATALOG_CACHE_TTL_MS == 3600000
