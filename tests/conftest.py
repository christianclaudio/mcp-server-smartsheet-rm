"""Session guard: CI must run the Code Mode tests, never skip them (template v1.6.0)."""

import importlib.util
import os

import pytest

# The module-level ``smartsheet_rm_mcp.server.mcp`` reads the token when it is
# imported, which happens at collection, before any fixture runs. Clear the
# shell's values first.
for _name in (
    "SMARTSHEET_RM_MCP_AUTH_TOKEN",
    "SMARTSHEET_RM_MCP_ALLOW_UNAUTHENTICATED_BIND",
):
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def _clear_smartsheet_rm_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a token or opt-in already set in the shell out of every test."""
    monkeypatch.delenv("SMARTSHEET_RM_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("SMARTSHEET_RM_MCP_ALLOW_UNAUTHENTICATED_BIND", raising=False)
    # The module-level ``mcp`` was built at import, so a shell token may already
    # be attached to it even after the env vars are cleared.
    import smartsheet_rm_mcp.server as server_mod

    if getattr(server_mod, "mcp", None) is not None:
        monkeypatch.setattr(server_mod.mcp, "auth", None)


def code_mode_ci_guard_error(environ: dict[str, str] | None = None) -> str | None:
    """Return an error when CI runs without the Code Mode sandbox, else None.

    Code Mode tests ``pytest.importorskip("pydantic_monty")`` so a local run without the
    ``code-mode`` extra still passes. In CI (``CI`` set, as on GitHub Actions) a missing
    sandbox would turn those tests into silent skips, so the session aborts instead.
    """
    env = os.environ if environ is None else environ
    if not env.get("CI"):
        return None
    if importlib.util.find_spec("pydantic_monty") is not None:
        return None
    return (
        "CI is set but pydantic_monty is not installed: install the code-mode extra "
        "(uv sync --locked --extra dev --extra code-mode) so Code Mode tests run, not skip."
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Abort a CI session that would silently skip the Code Mode tests."""
    error = code_mode_ci_guard_error()
    if error is not None:
        pytest.exit(error, returncode=3)
