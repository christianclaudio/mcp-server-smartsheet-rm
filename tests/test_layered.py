"""Tests for FastMCP 4 Server Composition, profiles, middleware, and domain guards."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext

from smartsheet_rm_mcp.config import Settings, SmartsheetRMSettings, settings
from smartsheet_rm_mcp.middleware import (
    AdminDomainGuardMiddleware,
    ParentAuditMiddleware,
    ProjectsDomainGuardMiddleware,
    ReadOnlyGateMiddleware,
    TimeDomainGuardMiddleware,
)
from smartsheet_rm_mcp.server import _ToolManagerCompat, create_server, main, server_lifespan


@pytest.mark.asyncio
async def test_create_server_profiles() -> None:
    """Verify create_server mounts correct domain sub-servers and applies profile filters."""
    # 1. Full profile
    full_server = create_server(profile="full")
    tools_full = await full_server.list_tools()
    assert len(tools_full) == 98
    names = {t.name for t in tools_full}
    assert "time_list_time_entries" in names
    assert "projects_list_projects" in names
    assert "admin_list_users" in names

    # 2. Time profile
    time_srv = create_server(profile="time")
    tools_time = await time_srv.list_tools()
    assert len(tools_time) == 14
    assert "time_list_time_entries" in {t.name for t in tools_time}

    # 3. Projects profile
    proj_srv = create_server(profile="projects")
    tools_proj = await proj_srv.list_tools()
    assert len(tools_proj) == 24
    assert "projects_list_projects" in {t.name for t in tools_proj}

    # 4. Admin profile
    admin_srv = create_server(profile="admin")
    tools_admin = await admin_srv.list_tools()
    assert len(tools_admin) == 60
    assert "admin_list_users" in {t.name for t in tools_admin}

    # 5. Readonly profile
    ro_srv = create_server(profile="readonly")
    tools_ro = await ro_srv.list_tools()
    assert len(tools_ro) == 39
    assert all(t.annotations and t.annotations.read_only_hint for t in tools_ro)

    # 6. Invalid profile raises ValueError
    with pytest.raises(ValueError, match="Unknown SMARTSHEET_RM_PROFILE"):
        create_server(profile="invalid_profile_name")


@pytest.mark.asyncio
async def test_create_server_tool_search() -> None:
    """Verify opt-in regex tool search adds transform."""
    srv = create_server(enable_tool_search=True)
    assert srv is not None


@pytest.mark.asyncio
async def test_parent_audit_middleware_success() -> None:
    """Verify ParentAuditMiddleware logs success and returns tool result."""
    mw = ParentAuditMiddleware()
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.message = MagicMock()
    ctx.message.name = "rm_test_tool"
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
    ctx.message.name = "rm_test_tool"
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


@pytest.mark.asyncio
async def test_read_only_gate_middleware(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify ReadOnlyGateMiddleware blocks mutating operations in read-only mode."""
    mw = ReadOnlyGateMiddleware()

    # When READONLY is False, all operations are allowed
    monkeypatch.setattr(settings, "READONLY", False)
    monkeypatch.delenv("SMARTSHEET_RM_READONLY", raising=False)
    ctx = MagicMock(spec=MiddlewareContext)
    ctx.method = "tools/call"
    ctx.message = MagicMock()
    ctx.message.name = "rm_delete_project"
    called = False

    async def fake_next(_ctx: MiddlewareContext) -> str:
        nonlocal called
        called = True
        return "success"

    res = await mw.on_message(ctx, fake_next)
    assert res == "success"
    assert called is True

    # When READONLY is True, mutating operations are blocked
    monkeypatch.setattr(settings, "READONLY", True)
    with pytest.raises(PermissionError) as exc_info:
        await mw.on_message(ctx, fake_next)
    assert "Server is in read-only mode" in str(exc_info.value)

    # When READONLY is True, mutating recipe operations are also blocked
    ctx.message.name = "time_fill_weekly_timesheet"
    with pytest.raises(PermissionError):
        await mw.on_message(ctx, fake_next)

    # When READONLY is True, non-mutating operations are allowed
    ctx.message.name = "rm_list_projects"
    res2 = await mw.on_message(ctx, fake_next)
    assert res2 == "success"


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


def test_tool_manager_compat_root_local_provider() -> None:
    """Verify _ToolManagerCompat finds tools registered directly on root local provider."""
    root = FastMCP("test-root")

    @root.tool(name="rm_direct_root_tool")
    def direct_tool() -> str:
        return "root"

    mgr = _ToolManagerCompat(root)
    assert "rm_direct_root_tool" in mgr._tools
    mgr.remove_tool("rm_direct_root_tool")
    assert "rm_direct_root_tool" not in mgr._tools


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
    assert s.CATALOG_CACHE_TTL_MS == 3600000
