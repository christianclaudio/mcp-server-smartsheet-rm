"""Unit tests for script utilities and checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from scripts import check_openapi_drift, check_tool_contract


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
    request = httpx.Request("GET", "https://93.184.216.34/spec.json")
    mock_resp = httpx.Response(200, json={"paths": {}}, request=request)
    with patch("scripts.check_openapi_drift.fetch_pinned_https", return_value=mock_resp) as fetch:
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://api.example.com/spec.json"]):
            assert check_openapi_drift.main() == 0
        fetch.assert_called_once_with("https://api.example.com/spec.json", timeout=30.0)


def test_drift_spec_url_blocks_ssrf_and_redirects(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("httpx.Client") as client:
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://169.254.169.254/latest/meta-data"]):
            assert check_openapi_drift.main() == 2
        client.assert_not_called()
    err = capsys.readouterr().err
    assert "ERROR fetching spec-url" in err
    assert "Blocked private/reserved" in err

    with patch("httpx.Client") as client:
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "http://api.example.com/spec.json"]):
            assert check_openapi_drift.main() == 2
        client.assert_not_called()

    redirect = httpx.Response(
        302,
        headers={"location": "http://127.0.0.1/secret"},
        request=httpx.Request("GET", "https://93.184.216.34/spec.json"),
    )
    with patch("scripts.check_openapi_drift.fetch_pinned_https", return_value=redirect) as fetch:
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://api.example.com/spec.json"]):
            assert check_openapi_drift.main() == 2
        fetch.assert_called_once()
    err = capsys.readouterr().err
    assert "Refusing to follow redirect" in err
    assert "Only HTTPS is permitted" in err

    safe_redirect = httpx.Response(
        302,
        headers={"location": "/other.json"},
        request=httpx.Request("GET", "https://93.184.216.34/spec.json"),
    )
    with patch("scripts.check_openapi_drift.fetch_pinned_https", return_value=safe_redirect):
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://api.example.com/spec.json"]):
            assert check_openapi_drift.main() == 2
    err = capsys.readouterr().err
    assert "Refusing to follow redirect" in err
    assert "-> /other.json" in err

    empty_redirect = httpx.Response(
        301,
        headers={},
        request=httpx.Request("GET", "https://93.184.216.34/spec.json"),
    )
    with patch("scripts.check_openapi_drift.fetch_pinned_https", return_value=empty_redirect):
        with patch("sys.argv", ["check_openapi_drift.py", "--spec-url", "https://api.example.com/spec.json"]):
            assert check_openapi_drift.main() == 2
