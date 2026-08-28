"""Unit tests for errors and secret redaction."""

from __future__ import annotations

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
