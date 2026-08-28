"""Unit tests for script utilities and checks."""

from __future__ import annotations

import subprocess
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
