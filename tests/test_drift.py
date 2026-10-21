"""Tests for the OpenAPI route and parameter drift check (ported from the Forge template).

The fixture tests/fixtures/rm-openapi-paths.json keeps only the paths, methods and parameter
names of Smartsheet's published spec:
https://developers.smartsheet.com/_spec/api/resource_management/openapi.json
Its top-level `_source` key records that URL and the fetch date; the drift check reads only `paths`.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from scripts import check_openapi_drift as drift
from scripts.check_openapi_drift import (
    SpecError,
    extract_client_calls,
    is_parameter_deprecated,
    load_spec,
    main,
    normalize_path,
    parse_spec,
    run_drift_check,
    scan_source,
    validate_spec,
)

ROOT = Path(__file__).resolve().parent.parent
RM_SPEC_FILE = ROOT / "tests" / "fixtures" / "rm-openapi-paths.json"
RM_SPEC: dict[str, Any] = json.loads(RM_SPEC_FILE.read_text(encoding="utf-8"))
SPEC: dict[str, Any] = {
    "openapi": "3.1.0",
    "paths": {
        "/items": {
            "get": {
                "parameters": [
                    {"name": "limit", "in": "query"},
                    {"name": "search", "in": "query", "deprecated": True},
                ]
            },
            "post": {},
        },
        "/items/{itemId}": {"get": {}, "delete": {}},
        "/health": {"get": {}},
    },
}
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
SPEC_URL = "https://specs.vendor.test/openapi.json"


def _src(tmp_path: Path, code: str) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "mock_client.py").write_text(code, encoding="utf-8")
    return src


def _response(status: int, request: httpx.Request, **kwargs: Any) -> httpx.Response:
    return httpx.Response(status, request=request, **kwargs)


# ---------------------------------------------------------------------------
# Spec parsing and validation
# ---------------------------------------------------------------------------


def test_normalize_path() -> None:
    assert normalize_path("items") == "/items"
    assert normalize_path("/items/") == "/items"
    assert normalize_path("/projects/{project_id}/phases/{id}") == "/projects/{}/phases/{}"
    assert normalize_path("/items?limit=50") == "/items"


def test_is_parameter_deprecated() -> None:
    assert is_parameter_deprecated({"name": "a", "deprecated": True}) is True
    assert is_parameter_deprecated({"name": "a", "description": "**[Deprecated]** use b"}) is True
    assert is_parameter_deprecated({"name": "a", "description": "reaches end of support"}) is True
    assert is_parameter_deprecated({"name": "a", "description": "active"}) is False


def test_parse_spec_and_malformed_entries() -> None:
    spec = {
        "paths": {
            "/m": {
                "parameters": [{"name": "org", "in": "query"}],
                "get": {"parameters": [{"name": "id", "in": "path", "required": True}, "x", {"in": "query"}]},
                "summary": "not an operation",
            },
            "/a": "not-a-dict",
            "/b": {"parameters": "bad", "get": "bad", "post": {"parameters": "bad"}},
        }
    }
    endpoints = parse_spec(spec)
    assert sorted(endpoints) == [("GET", "/m"), ("POST", "/b")]
    assert set(endpoints[("GET", "/m")].query_params) == {"org"}
    assert endpoints[("GET", "/m")].required_params == {"id"}
    assert parse_spec({"paths": []}) == {}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "not a JSON object"),
        ({"paths": {"/a": {}}}, "no 'openapi' or 'swagger'"),
        ({"openapi": "3.1.0"}, "no 'paths'"),
        ({"openapi": "3.1.0", "paths": {}}, "no 'paths'"),
    ],
)
def test_validate_spec_fails_closed(raw: Any, message: str) -> None:
    with pytest.raises(SpecError, match=message):
        validate_spec(raw)


# ---------------------------------------------------------------------------
# This repo against Smartsheet's spec
# ---------------------------------------------------------------------------


def test_repo_is_clean_against_rm_spec() -> None:
    """Every documented call matches the spec and every undocumented one is marked, even strict."""
    code, lines = run_drift_check(RM_SPEC, ROOT / "src", strict=True)
    assert code == 0, "\n".join(lines)
    ignored = [line for line in lines if line.startswith("  src/")]
    assert len([line for line in ignored if "undocumented route" in line]) == 46
    assert len([line for line in ignored if "maintainer spec fetch" in line]) == 1
    assert not any("PHANTOM" in line or "UNRESOLVED" in line for line in lines)


def test_fixture_source_is_recorded_and_ignored() -> None:
    """`_source` names the spec URL and fetch date, and loading or parsing never reads it."""
    assert RM_SPEC["_source"]["url"] == "https://developers.smartsheet.com/_spec/api/resource_management/openapi.json"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", RM_SPEC["_source"]["fetched"])
    loaded = load_spec(str(RM_SPEC_FILE), None)
    without = {key: value for key, value in loaded.items() if key != "_source"}
    assert parse_spec(loaded) == parse_spec(without)
    assert run_drift_check(loaded, ROOT / "src", strict=True) == run_drift_check(without, ROOT / "src", strict=True)


def test_client_self_request_calls_are_detected() -> None:
    """The SmartsheetRMClient ``self._request`` wrapper is scanned, including the new create routes."""
    calls = {(c.method, c.normalized_path) for c in extract_client_calls(ROOT / "src")}
    assert ("POST", "/projects/{}/time_entries") in calls
    assert ("POST", "/users/{}/time_entries") in calls
    assert ("POST", "/projects/{}/assignments") in calls
    assert ("POST", "/users/{}/assignments") in calls
    assert ("POST", "/reports/rows") in calls
    assert len(calls) >= 57


def test_removing_the_pragmas_exposes_the_undocumented_routes(tmp_path: Path) -> None:
    """Without ``# drift-ignore`` the 46 undocumented routes are phantom routes."""
    src = tmp_path / "src"
    shutil.copytree(ROOT / "src" / "smartsheet_rm_mcp", src / "smartsheet_rm_mcp")
    client = src / "smartsheet_rm_mcp" / "client.py"
    client.write_text(
        re.sub(r"  # drift-ignore: undocumented route[^\n]*", "", client.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    code, lines = run_drift_check(RM_SPEC, src)
    assert code == 1
    assert len([line for line in lines if "PHANTOM ROUTE" in line]) == 46


def test_endpoint_map_matches_client_verbs() -> None:
    """ENDPOINT_TO_METHOD uses the verbs the client sends (reports rows and totals are POST)."""
    calls = {(c.method, c.normalized_path) for c in scan_source(ROOT / "src").calls}
    assert ("reports/rows", "POST") in drift.ENDPOINT_TO_METHOD
    assert ("reports/totals", "POST") in drift.ENDPOINT_TO_METHOD
    assert ("reports/rows", "GET") not in drift.ENDPOINT_TO_METHOD
    assert ("POST", "/reports/totals") in calls


# ---------------------------------------------------------------------------
# Detector behavior (template cases)
# ---------------------------------------------------------------------------


def test_deprecated_and_undocumented_params(tmp_path: Path) -> None:
    src = _src(tmp_path, 'client.request("GET", "items", params={"search": "q", "limit": 1, "cursor": "x"})\n')
    code, lines = run_drift_check(SPEC, src)
    assert code == 0
    assert any("DEPRECATED PARAMETER IN USE: 'search'" in line for line in lines)
    assert any("UNDOCUMENTED PARAMETER: 'cursor'" in line for line in lines)
    assert not any("'limit'" in line for line in lines)
    assert run_drift_check(SPEC, src, strict=True)[0] == 1
    positional = _src(tmp_path, 'client.get("items", {"cursor": "x"})\nclient.post("items", {"a": 1})\n')
    assert any("UNDOCUMENTED PARAMETER: 'cursor'" in line for line in run_drift_check(SPEC, positional)[1])


def test_deprecated_route(tmp_path: Path) -> None:
    spec = {"openapi": "3.1.0", "paths": {"/old": {"get": {"deprecated": True}}}}
    src = _src(tmp_path, 'client.get("old")\n')
    code, lines = run_drift_check(spec, src)
    assert code == 0
    assert any("DEPRECATED ROUTE" in line for line in lines)
    assert run_drift_check(spec, src, strict=True)[0] == 1


def test_phantom_route_names_methods_the_spec_has(tmp_path: Path) -> None:
    src = _src(tmp_path, 'self._request("PATCH", "/items", json_data={"a": 1})\nclient.get("nowhere")\n')
    code, lines = run_drift_check(SPEC, src)
    assert code == 1
    assert any("PHANTOM ROUTE" in line and "The spec has GET, POST" in line for line in lines)
    assert any("PHANTOM ROUTE" in line and "nowhere" in line for line in lines)


def test_unresolved_calls_fail(tmp_path: Path) -> None:
    src = _src(
        tmp_path,
        "client.request(method, 'items')\n"
        "client.request('GET', path)\n"
        "client.request(method=verb, url=f'{base}/{path}')\n"
        "self._request('BREW', 'items')\n"
        "client.get()\n"
        "other._request('GET', 'items')\n"
        "self.helper('GET', 'items')\n"
        "client.get('items')\n",
    )
    code, lines = run_drift_check(SPEC, src)
    assert code == 1
    unresolved = [line for line in lines if "UNRESOLVED CALL" in line]
    assert [line.split("mock_client.py:")[1].split(":")[0] for line in unresolved] == ["1", "2", "3", "4", "5"]


def test_keyword_method_and_url(tmp_path: Path) -> None:
    src = _src(tmp_path, "client.request(method='DELETE', url=f'items/{i}')\nclient.request('GET', path='health')\n")
    assert [(c.method, c.normalized_path) for c in extract_client_calls(src)] == [
        ("DELETE", "/items/{}"),
        ("GET", "/health"),
    ]


def test_drift_ignore_needs_colon_and_reason(tmp_path: Path) -> None:
    src = _src(
        tmp_path,
        "client.request(  # drift-ignore: generic transport\n    method=m,\n    url=u,\n)\n"
        "client.request(m, u)  # drift-ignore\n"
        "client.request(m, u)  # drift-ignore no colon\n"
        "client.get('health')\n",
    )
    scan = scan_source(src)
    assert [(i.line_number, i.reason) for i in scan.ignored] == [(1, "generic transport")]
    assert [u.line_number for u in scan.unresolved] == [5, 6]
    code, lines = run_drift_check(SPEC, src)
    assert code == 1
    assert any("needs a reason" in line for line in lines)


def test_unscannable_source_exits_2_not_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = _src(tmp_path, 'client.get("health")\n')
    (src / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    code, lines = run_drift_check(SPEC, src)
    assert code == 2
    assert any("UNSCANNABLE SOURCE: src/broken.py" in line for line in lines)
    assert not any("BREAKING DRIFT" in line for line in lines)
    assert main(["--spec-file", str(RM_SPEC_FILE), "--source-dir", str(src)]) == 2
    assert "UNSCANNABLE SOURCE" in capsys.readouterr().out


def test_no_client_calls_exits_2_not_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = _src(tmp_path, "print('none')\nclient.close()\n")
    code, lines = run_drift_check(SPEC, empty)
    assert code == 2
    assert any("NO CLIENT CALLS" in line for line in lines)
    assert not any("BREAKING DRIFT" in line for line in lines)
    assert main(["--spec-file", str(RM_SPEC_FILE), "--source-dir", str(empty)]) == 2
    assert "NO CLIENT CALLS" in capsys.readouterr().out


def test_uncovered_endpoints_listed_and_path_prefix(tmp_path: Path) -> None:
    src = _src(tmp_path, 'client.get("health")\n')
    code, lines = run_drift_check(SPEC, src)
    assert code == 0
    start = lines.index("\nℹ️ SPEC ENDPOINTS NO CLIENT CALL COVERS:")
    assert lines[start + 1] == "  DELETE /items/{itemId}"
    prefixed = {"openapi": "3.1.0", "paths": {"/v2/health": {"get": {}}}}
    assert run_drift_check(prefixed, src)[0] == 1
    assert run_drift_check(prefixed, src, path_prefix="/v2/")[0] == 0


# ---------------------------------------------------------------------------
# main(): repo surface checks and spec loading
# ---------------------------------------------------------------------------


def test_main_spec_file_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--spec-file", str(RM_SPEC_FILE), "--strict"]) == 0
    out = capsys.readouterr().out
    assert "No breaking drift" in out
    assert f"103/{len(drift.ENDPOINT_TO_METHOD)}" in out


def test_main_requires_a_spec(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "no vendor spec given" in capsys.readouterr().err
    with pytest.raises(SpecError, match="no vendor spec given"):
        load_spec(None, None)


def test_main_spec_file_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--spec-file", str(tmp_path / "missing.json")]) == 2
    assert "spec file not found" in capsys.readouterr().err
    bad = tmp_path / "spec.yaml"
    bad.write_text("openapi: 3.1.0\n", encoding="utf-8")
    assert main(["--spec-file", str(bad)]) == 2
    assert "cannot read spec file as JSON" in capsys.readouterr().err
    assert main(["--spec-file", str(RM_SPEC_FILE), "--source-dir", str(tmp_path / "nope")]) == 2
    assert "source directory not found" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["--spec-file", str(RM_SPEC_FILE), "--spec-url", SPEC_URL])


def test_main_exits_1_on_drift(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Drift, here a phantom route in the scanned source, is the only failure that exits 1."""
    src = _src(tmp_path, 'self._request("GET", "/nowhere")\n')
    assert main(["--spec-file", str(RM_SPEC_FILE), "--source-dir", str(src)]) == 1
    assert "PHANTOM ROUTE" in capsys.readouterr().out


def test_main_exits_2_on_unexpected_exception(capsys: pytest.CaptureFixture[str]) -> None:
    """An uncaught exception is caught in main and exits 2, so the monitor opens no drift issue."""
    with patch.object(drift, "run_drift_check", side_effect=RuntimeError("boom")):
        assert main(["--spec-file", str(RM_SPEC_FILE)]) == 2
    assert "drift check failed unexpectedly: RuntimeError: boom" in capsys.readouterr().err
    with patch.object(drift, "check_client_surface", side_effect=KeyError("tools")):
        assert main(["--spec-file", str(RM_SPEC_FILE)]) == 2


def test_main_fails_on_missing_client_method(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.dict(drift.ENDPOINT_TO_METHOD, {("nowhere", "GET"): "no_such_method"}):
        assert main(["--spec-file", str(RM_SPEC_FILE)]) == 2
    assert "Missing SmartsheetRMClient.no_such_method for GET nowhere" in capsys.readouterr().err


def test_main_fails_on_too_few_tools(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(drift, "registered_tool_names", return_value=["one"]):
        assert main(["--spec-file", str(RM_SPEC_FILE)]) == 2
    assert f"Expected at least {drift.MIN_TOOLS} MCP tools, found 1" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# --spec-url: one lookup, pinned connection, no redirects
# ---------------------------------------------------------------------------


class _Lookups:
    """getaddrinfo stand-in that fails the test on a second lookup of any host."""

    def __init__(self, answer: list[Any]) -> None:
        self.answer = answer
        self.hosts: list[str] = []

    def __call__(self, host: str, *args: Any, **kwargs: Any) -> list[Any]:
        self.hosts.append(host)
        assert len(self.hosts) == 1, f"second DNS lookup: {self.hosts}"
        return self.answer


def test_spec_url_resolves_once_and_connects_to_checked_ip() -> None:
    """One lookup; TCP to the checked IP; Host, SNI and certificate checks stay on the name."""
    lookups = _Lookups(PUBLIC)
    sent: list[httpx.Request] = []

    def transport(_self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _response(200, request, json=RM_SPEC)

    with (
        patch("socket.getaddrinfo", side_effect=lookups),
        patch.object(httpx.HTTPTransport, "handle_request", transport),
    ):
        assert main(["--spec-url", SPEC_URL]) == 0
    assert lookups.hosts == ["specs.vendor.test"]
    (req,) = sent
    assert req.url.host == "93.184.216.34"
    assert req.headers["Host"] == "specs.vendor.test"
    assert req.extensions["sni_hostname"] == "specs.vendor.test"


def test_spec_url_refuses_redirects_without_resolving_the_target(capsys: pytest.CaptureFixture[str]) -> None:
    """A 3xx is reported, never followed, and its Location is not looked up."""
    lookups = _Lookups(PUBLIC)
    sent: list[httpx.Request] = []

    def transport(_self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return _response(302, request, headers={"location": "https://evil.vendor.test/x.json"})

    with (
        patch("socket.getaddrinfo", side_effect=lookups),
        patch.object(httpx.HTTPTransport, "handle_request", transport),
    ):
        assert main(["--spec-url", SPEC_URL]) == 2
    assert len(sent) == 1
    assert lookups.hosts == ["specs.vendor.test"]
    assert "refused to follow redirect for spec URL (HTTP 302 -> https://evil.vendor.test/x.json)" in (
        capsys.readouterr().err
    )


def test_spec_url_refuses_private_answers_and_bad_urls(capsys: pytest.CaptureFixture[str]) -> None:
    """Private DNS answers, metadata literals and plain HTTP are refused before any connection."""
    with (
        patch("socket.getaddrinfo", side_effect=_Lookups(PRIVATE)),
        patch.object(httpx.HTTPTransport, "handle_request") as transport,
    ):
        assert main(["--spec-url", SPEC_URL]) == 2
        assert main(["--spec-url", "https://169.254.169.254/latest/meta-data"]) == 2
        assert main(["--spec-url", "http://specs.vendor.test/openapi.json"]) == 2
        transport.assert_not_called()
    err = capsys.readouterr().err
    assert err.count("refused to fetch spec URL") == 3
    assert "private/reserved" in err
    assert "Only HTTPS" in err


def test_spec_url_fetch_and_body_errors(capsys: pytest.CaptureFixture[str]) -> None:
    """Transport failures, HTTP errors and non-OpenAPI bodies exit 2 with the reason."""

    def boom(_self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    def not_found(_self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        return _response(404, request, text="nope")

    def empty(_self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        return _response(200, request, json={"openapi": "3.1.0", "paths": {}})

    for handler, message in (
        (boom, "cannot fetch spec from URL: boom"),
        (not_found, "cannot fetch spec from URL: Client error '404"),
        (empty, "no 'paths'"),
    ):
        with (
            patch("socket.getaddrinfo", side_effect=_Lookups(PUBLIC)),
            patch.object(httpx.HTTPTransport, "handle_request", handler),
        ):
            assert main(["--spec-url", SPEC_URL]) == 2
        assert message in capsys.readouterr().err
