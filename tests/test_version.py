"""The git tag is the version: ``__version__`` comes from the installed distribution."""

from __future__ import annotations

import importlib
import importlib.metadata

import pytest

import smartsheet_rm_mcp


def test_version_matches_distribution_metadata() -> None:
    """PyPA single-source check: no hard-coded version in the package."""
    assert smartsheet_rm_mcp.__version__ == importlib.metadata.version("mcp-server-smartsheet-rm")


def test_version_falls_back_without_an_installed_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source tree without an install reports the uv-dynamic-versioning fallback."""

    def _missing(_name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(_name)

    monkeypatch.setattr(importlib.metadata, "version", _missing)
    try:
        assert importlib.reload(smartsheet_rm_mcp).__version__ == "0.0.0"
    finally:
        monkeypatch.undo()
        importlib.reload(smartsheet_rm_mcp)
    assert smartsheet_rm_mcp.__version__ == importlib.metadata.version("mcp-server-smartsheet-rm")
