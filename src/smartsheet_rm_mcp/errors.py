"""Structured error types and secret redaction for Smartsheet RM MCP server."""

from __future__ import annotations

import os
import re
from typing import Any

_REDACT_KEYS = {
    "auth",
    "token",
    "access_token",
    "api_key",
    "api_token",
    "secret",
    "client_secret",
    "password",
    "authorization",
}

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)auth:\s*[a-zA-Z0-9\-\._~\+\/]+"),
    re.compile(r"(?i)(api_token=|\"api_token\":\s*\")[a-zA-Z0-9\-\._~\+\/]+=*\"?"),
    re.compile(r"(?i)(SMARTSHEET_RM_API_TOKEN=)[a-zA-Z0-9\-\._~\+\/]+"),
]


def redact_secrets(text: str, extra_secret: str | None = None) -> str:
    """Redact tokens, credentials, and API secrets from output and logs."""
    if not text:
        return text
    secret = os.environ.get("SMARTSHEET_RM_API_TOKEN", "")
    if secret and secret in text:
        text = text.replace(secret, "***REDACTED***")
    if extra_secret and extra_secret in text:
        text = text.replace(extra_secret, "***REDACTED***")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("***REDACTED***", text)
    return text


_redact_secrets = redact_secrets


def _is_secret_key(key: str) -> bool:
    """Check whether a mapping key represents a credential or secret."""
    lowered = key.lower()
    return lowered in _REDACT_KEYS or any(marker in lowered for marker in _REDACT_KEYS)


def _sanitize(value: Any) -> Any:
    """Recursively sanitize sensitive values and truncate lengthy text."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if _is_secret_key(k) else _sanitize(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return value[:500]
    return value


class SmartsheetRMAPIError(Exception):
    """Raised when the Smartsheet RM REST API returns a non-2xx response."""

    def __init__(
        self,
        status_code: int,
        path: str,
        method: str,
        detail: Any = None,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.method = method
        self.detail = detail
        self.request_id = request_id
        super().__init__(f"Smartsheet RM API {method} {path} returned {status_code}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "smartsheet_rm_api_error",
            "status_code": self.status_code,
            "method": self.method,
            "path": self.path,
            "detail": _sanitize(self.detail),
            "request_id": self.request_id,
        }
