"""Unit tests for errors and secret redaction."""

from __future__ import annotations

import pytest

from smartsheet_rm_mcp.errors import SmartsheetRMAPIError, _sanitize


def test_sanitize_dict_and_redaction() -> None:
    data = {
        "auth": "secret-token-123",
        "token": "tok-456",
        "access_token": "acc-789",
        "api_token": "api-999",
        "refresh_token": "ref-111",
        "auth_token": "auth-222",
        "my_password_field": "pass-333",
        "name": "Project Apollo",
        "user": {
            "password": "mypassword",
            "email": "user@example.com",
        },
        "nested_list": [
            {"secret": "hiddendata", "value": 42},
            "short text",
            "x" * 600,
        ],
        "count": 10,
        "is_active": True,
    }

    sanitized = _sanitize(data)
    assert sanitized["auth"] == "[redacted]"
    assert sanitized["token"] == "[redacted]"
    assert sanitized["access_token"] == "[redacted]"
    assert sanitized["api_token"] == "[redacted]"
    assert sanitized["refresh_token"] == "[redacted]"
    assert sanitized["auth_token"] == "[redacted]"
    assert sanitized["my_password_field"] == "[redacted]"
    assert sanitized["name"] == "Project Apollo"
    assert sanitized["user"]["password"] == "[redacted]"
    assert sanitized["user"]["email"] == "user@example.com"
    assert sanitized["nested_list"][0]["secret"] == "[redacted]"
    assert sanitized["nested_list"][0]["value"] == 42
    assert sanitized["nested_list"][1] == "short text"
    assert len(sanitized["nested_list"][2]) == 500
    assert sanitized["count"] == 10
    assert sanitized["is_active"] is True


def test_smartsheet_rm_api_error_to_dict() -> None:
    err = SmartsheetRMAPIError(
        status_code=404,
        path="/projects/999",
        method="GET",
        detail={"error": "Not Found", "token": "secret123"},
        request_id="req-abc-123",
    )

    assert "GET /projects/999 returned 404" in str(err)
    data = err.to_dict()
    assert data["type"] == "smartsheet_rm_api_error"
    assert data["status_code"] == 404
    assert data["method"] == "GET"
    assert data["path"] == "/projects/999"
    assert data["request_id"] == "req-abc-123"
    assert data["detail"]["token"] == "[redacted]"
    assert data["detail"]["error"] == "Not Found"
    assert data["message"] == "Smartsheet RM API GET /projects/999 returned 404"
    assert "message" in data


def test_to_dict_redacts_secret_in_detail_key() -> None:
    """A secret in a ``detail`` dict key is redacted, since a partial result gets no document pass."""
    import json

    err = SmartsheetRMAPIError(400, "/time_entries", "POST", detail={"api_token=FAKEVALUE123": "v"})
    data = err.to_dict()
    rendered = json.dumps(data)
    assert "FAKEVALUE123" not in rendered
    assert "api_token=***REDACTED***" in data["detail"]


def test_redact_secrets_api_key_and_password() -> None:
    from smartsheet_rm_mcp.errors import redact_secrets

    assert redact_secrets("api_key=secret-key-123") == "api_key=***REDACTED***"
    assert redact_secrets('{"api_key": "secret-key-123"}') == '{"api_key": "***REDACTED***"}'
    assert redact_secrets("password=mypassword123") == "password=***REDACTED***"
    assert redact_secrets('{"password": "mypassword123"}') == '{"password": "***REDACTED***"}'


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param('password: "Fake!pw#9"', 'password: "***REDACTED***"', id="password_colon_quoted"),
        pytest.param("{'password': 'fakepw99'}", "{'password': '***REDACTED***'}", id="password_dict"),
        pytest.param("api_key FAKEKEY12345", "api_key ***REDACTED***", id="api_key_space"),
        pytest.param("api-key: FAKEKEY12345", "api-key: ***REDACTED***", id="api_key_header"),
        pytest.param("client_secret FAKESEC12345", "client_secret ***REDACTED***", id="client_secret_space"),
        pytest.param("client_secret=FAKESEC12345", "client_secret=***REDACTED***", id="client_secret_equals"),
        pytest.param(
            '{"client_secret": "FAKESEC12345"}', '{"client_secret": "***REDACTED***"}', id="client_secret_json"
        ),
        pytest.param("password=abc", "password=***REDACTED***", id="short_password"),
        pytest.param('{"password": "x"}', '{"password": "***REDACTED***"}', id="short_password_json"),
        pytest.param('{"password": "fakepw99"}', '{"password": "***REDACTED***"}', id="password_json"),
        pytest.param('{"api_key": "FAKEKEY12345"}', '{"api_key": "***REDACTED***"}', id="api_key_json"),
        pytest.param('{"api_token": "SECRET9"}', '{"api_token": "***REDACTED***"}', id="api_token_json"),
        pytest.param("/x?token=SECRET#frag", "/x?token=***REDACTED***#frag", id="query_token_fragment"),
    ],
)
def test_redact_secrets_house_credential_forms_keep_key(raw: str, expected: str) -> None:
    """Password, api_key and client_secret values are redacted; the key and its quotes stay."""
    from smartsheet_rm_mcp.errors import redact_secrets

    assert redact_secrets(raw) == expected


def test_redact_secrets_bearer_b64token_tail_does_not_survive() -> None:
    """The repo's broad bearer pattern runs first and removes the whole b64token, tail included."""
    from smartsheet_rm_mcp.errors import redact_secrets

    out = redact_secrets("Authorization: Bearer abc.def~ghi/jk+l== next")
    assert out == "Authorization: Bearer ***REDACTED*** next"
    for fragment in ("abc.def", "~ghi", "/jk", "+l=="):
        assert fragment not in out
    assert redact_secrets("Bearer abc.def~ghi/jk+l==") == "Bearer ***REDACTED***"


def test_redact_secrets_env_token_assignment_is_redacted() -> None:
    from smartsheet_rm_mcp.errors import redact_secrets

    assert redact_secrets("SMARTSHEET_RM_API_TOKEN=abc123") == "SMARTSHEET_RM_API_TOKEN=***REDACTED***"


def test_redact_secrets_token_forms() -> None:
    """api/access/refresh tokens and a bare token= query parameter are redacted in every form."""
    from smartsheet_rm_mcp.errors import redact_secrets

    cases = {
        "api_token=SECRET1": "api_token=***REDACTED***",
        "api-token: SECRET1": "api-token: ***REDACTED***",
        "access_token: SECRET2": "access_token: ***REDACTED***",
        "access_token=SECRET2": "access_token=***REDACTED***",
        '{"refresh_token": "SECRET4"}': '{"refresh_token": "***REDACTED***"}',
        '{"m": "{\\"access_token\\": \\"SECRET5\\"}"}': ('{"m": "{\\"access_token\\": \\"***REDACTED***\\"}"}'),
        "GET https://api.example.com/x?token=SECRET3&page=2": (
            "GET https://api.example.com/x?token=***REDACTED***&page=2"
        ),
        "url=/x?a=1&TOKEN=SECRET6": "url=/x?a=1&TOKEN=***REDACTED***",
        "refresh_token=a.b-c_d/e+f==": "refresh_token=***REDACTED***",
    }
    for raw, expected in cases.items():
        assert redact_secrets(raw) == expected, raw


def test_redact_secrets_token_value_in_json_keeps_document_valid() -> None:
    import json

    from smartsheet_rm_mcp.errors import redact_secrets

    doc = json.dumps({"path": "/x?token=a1&access_token=b2", "note": "refresh_token=c3"})
    assert json.loads(redact_secrets(doc)) == {
        "path": "/x?token=***REDACTED***&access_token=***REDACTED***",
        "note": "refresh_token=***REDACTED***",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("auth_token=SECRET7", "auth_token=***REDACTED***", id="auth_token"),
        pytest.param('{"id_token": "SECRET8"}', '{"id_token": "***REDACTED***"}', id="id_token"),
        pytest.param("session-token: SECRET9&x=1", "session-token: ***REDACTED***&x=1", id="session_token"),
        pytest.param(
            "X-Auth-Token: SECRET10\nAccept: */*",
            "X-Auth-Token: ***REDACTED***\nAccept: */*",
            id="x_auth_token_header",
        ),
        pytest.param(
            "Authorization: Token SECRET11 rejected",
            "Authorization: Token ***REDACTED*** rejected",
            id="authorization_token",
        ),
        pytest.param(
            "{'Authorization': 'Token SECRET12'}",
            "{'Authorization': 'Token ***REDACTED***'}",
            id="authorization_token_dict",
        ),
        pytest.param('{"token": "SECRET13"}', '{"token": "***REDACTED***"}', id="json_token"),
        pytest.param(
            '{"m": "{\\"token\\": \\"SECRET14\\"}"}',
            '{"m": "{\\"token\\": \\"***REDACTED***\\"}"}',
            id="json_token_escaped",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET15%26x%3D1%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D***REDACTED***%26x%3D1%23frag",
            id="url_encoded_access_token",
        ),
        pytest.param(
            "q=api_token%3DS16%26refresh_token%3DS17%26auth_token%3DS18%26id_token%3DS19%26session_token%3DS20",
            "q=api_token%3D***REDACTED***%26refresh_token%3D***REDACTED***%26auth_token%3D***REDACTED***"
            "%26id_token%3D***REDACTED***%26session_token%3D***REDACTED***",
            id="url_encoded_other_keys",
        ),
        pytest.param(
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3DSECRET21%23frag",
            "cb=https%3A%2F%2Fh%2Fx%3Faccess_token%3D***REDACTED***%23frag",
            id="url_encoded_access_token_fragment",
        ),
    ],
)
def test_redact_secrets_more_token_forms(raw: str, expected: str) -> None:
    """auth/id/session tokens, X-Auth-Token, Authorization: Token, JSON "token" and %3D."""
    from smartsheet_rm_mcp.errors import redact_secrets

    assert redact_secrets(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("token: SECRET22", "token: ***REDACTED***", id="token_colon_space"),
        pytest.param("token:SECRET23", "token:***REDACTED***", id="token_colon"),
        pytest.param("token = SECRET24", "token = ***REDACTED***", id="token_spaced_equals"),
        pytest.param("Token: abcdefgh12345", "Token: ***REDACTED***", id="token_capitalized"),
        pytest.param('token: "SECRET25"', 'token: "***REDACTED***"', id="token_colon_double_quote"),
        pytest.param("token='SECRET26'", "token='***REDACTED***'", id="token_equals_single_quote"),
    ],
)
def test_redact_secrets_bare_token_colon_and_spaced(raw: str, expected: str) -> None:
    """A bare token key takes ``:`` or ``=``, optional spaces and a quote; all are kept."""
    from smartsheet_rm_mcp.errors import redact_secrets

    assert redact_secrets(raw) == expected


def test_redact_secrets_leaves_token_words_alone() -> None:
    """Ordinary words and pagination fields that contain "token" are not redacted."""
    from smartsheet_rm_mcp.errors import redact_secrets

    for text in (
        "tokenizer failed on input",
        "next_page_token_count=5",
        "page_token=abc123 is a pagination cursor",
        "next_token=abc123&x=1",
        "csrf_token=abc123#frag",
        "refresh_token_expires_in=3600",
        "the token expired",
        "max_tokens=1024",
        '{"page_token": "x", "next_token": "x", "csrf_token": "x", "max_tokens": 5}',
        "X-Auth-Token-Expires: 2026-10-09T00:00:00Z",
        "session_token_ttl=3600",
        "id_token_hint_count=2",
        "Token x is invalid",
        "Authorization failed: token expired",
        "max_tokens: 5",
        "next_token: abc",
        "page_token: abc",
        "X-Auth-Token-Expires: 5",
        '{"token": null}',
        "page_token=abc123",
    ):
        assert redact_secrets(text) == text, text
