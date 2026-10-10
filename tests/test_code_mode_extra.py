"""The public code-mode extra and the CI guard that keeps Code Mode tests from skipping."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest
from tests.conftest import code_mode_ci_guard_error

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _project() -> dict[str, Any]:
    data: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    return data


def _fastmcp_floor(specs: list[str], extra: str) -> str:
    pattern = re.compile(rf"^fastmcp{re.escape(extra)}>=(\S+)$")
    floors = [m.group(1) for s in specs if (m := pattern.match(s))]
    assert len(floors) == 1, (extra, specs)
    return floors[0]


def test_code_mode_extra_is_public_and_pinned_to_the_fastmcp_floor() -> None:
    """``mcp-server-<name>[code-mode]`` pulls fastmcp[code-mode] at the core fastmcp floor."""
    project = _project()
    extras = project["optional-dependencies"]
    assert extras["code-mode"] == [f"fastmcp[code-mode]>={_fastmcp_floor(project['dependencies'], '')}"]
    # Code Mode stays optional: the base install never pulls the sandbox.
    assert not any("code-mode" in dep for dep in project["dependencies"])


def test_dev_extra_carries_the_same_code_mode_pin() -> None:
    """dev repeats the code-mode pin exactly so tests run execute and the two never drift."""
    extras = _project()["optional-dependencies"]
    code_mode = extras["code-mode"][0]
    assert code_mode in extras["dev"]


def test_ci_guard_ignores_local_runs() -> None:
    assert code_mode_ci_guard_error({}) is None
    assert code_mode_ci_guard_error({"CI": ""}) is None


def test_ci_guard_fails_when_ci_lacks_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    error = code_mode_ci_guard_error({"CI": "true"})
    assert error is not None
    assert "--extra code-mode" in error


def test_ci_guard_passes_when_ci_has_the_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
    assert code_mode_ci_guard_error({"CI": "true"}) is None
