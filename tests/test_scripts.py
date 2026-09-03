"""Unit tests for script utilities and checks."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from scripts import check_openapi_drift, check_tool_contract, smoke_test


def test_check_tool_contract_main(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_tool_contract.main()
    assert code == 0
    captured = capsys.readouterr()
    assert "README count validation:" in captured.out
    assert "All tool-contract assertions passed." in captured.out


def test_check_openapi_drift_main_success(capsys: pytest.CaptureFixture[str]) -> None:
    code = check_openapi_drift.main()
    assert code == 0
    captured = capsys.readouterr()
    assert "OpenAPI surface check passed successfully" in captured.out


def test_check_openapi_drift_main_missing_methods(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("inspect.getmembers", return_value=[("other_method", lambda: None)]):
        code = check_openapi_drift.main()
        assert code == 1
        captured = capsys.readouterr()
        assert "ERROR: Missing expected SmartsheetRMClient methods" in captured.err


def test_check_openapi_drift_main_insufficient_tools(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(check_openapi_drift.mcp._tool_manager, "_tools", {}):
        code = check_openapi_drift.main()
        assert code == 1
        captured = capsys.readouterr()
        assert "ERROR: Expected at least 90 MCP tools" in captured.err


def test_smoke_test_main_success(capsys: pytest.CaptureFixture[str]) -> None:
    mock_res = MagicMock(returncode=0, stdout=">>> ALL STDIO PROTOCOL SMOKE TESTS PASSED CLEANLY <<<", stderr="")
    with patch("subprocess.run", return_value=mock_res):
        code = smoke_test.main()
        assert code == 0


def test_smoke_test_main_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["test"], timeout=60)):
        code = smoke_test.main()
        assert code == 1
        captured = capsys.readouterr()
        assert "timed out after 60 seconds" in captured.err


def test_smoke_test_main_failure(capsys: pytest.CaptureFixture[str]) -> None:
    mock_res = MagicMock(returncode=2, stdout="Failure output", stderr="STDERR message")
    with patch("subprocess.run", return_value=mock_res):
        code = smoke_test.main()
        assert code == 2
        captured = capsys.readouterr()
        assert "STDERR message" in captured.err


def test_drift_helpers() -> None:
    assert check_openapi_drift.normalize_path("projects/{id}/phases") == "/projects/{}/phases"
    assert check_openapi_drift.is_parameter_deprecated({"name": "old_param", "deprecated": True}) is True
    assert (
        check_openapi_drift.is_parameter_deprecated({"name": "old_param", "description": "**[Deprecated]** use new"})
        is True
    )
    assert check_openapi_drift.is_parameter_deprecated({"name": "valid", "description": "active"}) is False


def test_drift_spec_validation(tmp_path: Path) -> None:
    spec = {
        "paths": {
            "/projects": {
                "get": {
                    "parameters": [
                        {"name": "archived", "in": "query", "deprecated": True},
                    ]
                }
            }
        }
    }
    endpoints = check_openapi_drift.parse_spec(spec)
    assert ("GET", "/projects") in endpoints

    # Test run_drift_check
    mock_src = tmp_path / "mock.py"
    mock_src.write_text('client.request("GET", "projects", params={"archived": True})\n')
    code, lines = check_openapi_drift.run_drift_check(spec, tmp_path, strict=False)
    assert code == 0
    assert any("DEPRECATED PARAMETER IN USE" in line for line in lines)

    code_strict, _ = check_openapi_drift.run_drift_check(spec, tmp_path, strict=True)
    assert code_strict == 1


def test_drift_main_with_spec_file(tmp_path: Path) -> None:
    spec_file = tmp_path / "spec.json"
    spec_file.write_text('{"paths": {}}')
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-file", str(spec_file)]):
        assert check_openapi_drift.main() == 0

    # Invalid file
    with patch("sys.argv", ["check_openapi_drift.py", "--spec-file", str(tmp_path / "missing.json")]):
        assert check_openapi_drift.main() == 2


def test_drift_main_with_spec_url() -> None:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"paths": {}}
    mock_resp.raise_for_status = MagicMock()
    with patch("httpx.get", return_value=mock_resp):
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://api.example.com/spec.json"]):
            assert check_openapi_drift.main() == 0
