"""Session guard: CI must run the Code Mode tests, never skip them (template v1.6.0)."""

import importlib.util
import os

import pytest


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
