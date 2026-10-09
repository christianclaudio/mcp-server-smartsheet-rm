"""Structured error types and secret redaction for Smartsheet RM MCP server."""

from __future__ import annotations

import os
import re
from typing import Any

from fastmcp.exceptions import ToolError

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

# Repo extras that cover more than the house set; the whole match is replaced.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*"),
    re.compile(r"(?i)auth:\s*[a-zA-Z0-9\-\._~\+\/]+"),
    re.compile(r"(?i)(SMARTSHEET_RM_API_TOKEN=)[a-zA-Z0-9\-\._~\+\/]+"),
]

# The house standard's patterns 1-9, copied verbatim and applied in this order after the
# patterns above. Group 1 (the key, header or parameter name) is kept and only the value is
# replaced, so JSON stays valid.
_KEYED_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Bearer value: base64url and base64 characters (``~``, ``+``, ``/``) plus ``=`` padding.
    re.compile(r"(?i)(bearer\s+)[a-z0-9_\-\.~+/]{8,}=*", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(client[_-]?secret[\"'\s:=]+)[a-z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(password[\"'\s:=]+)[^\s\"',]{4,}", re.IGNORECASE),
    # api/access/refresh/auth/id/session tokens as key=value, key: value, an
    # ``X-Auth-Token:`` header and JSON ("key": "value", also backslash-escaped inside an
    # already-serialized JSON string).
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token(?:\\?[\"'])?\s*[:=]\s*"
        r"(?:\\?[\"'])?)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # The same keys URL-encoded (``access_token%3D...``); the value stops at an encoded
    # ``%26`` (&) or ``%23`` (#), so the parameters after it survive.
    re.compile(
        r"(?i)((?:api|access|refresh|auth|id|session)[_-]?token%3D)"
        r"(?:[^\s\"'\\&,;#%]|%(?!26|23))+",
        re.IGNORECASE,
    ),
    # ``Authorization: Token <value>`` scheme, also as a quoted JSON or dict entry.
    re.compile(
        r"(?i)(authorization(?:\\?[\"'])?\s*[:=]\s*(?:\\?[\"'])?token\s+)[^\s\"'\\&,;]+",
        re.IGNORECASE,
    ),
    # JSON ``"token": "value"``; the opening quote right before ``token`` keeps keys such
    # as ``"next_token"`` and ``"page_token"`` untouched.
    re.compile(r"(?i)(\\?[\"']token\\?[\"']\s*:\s*\\?[\"'])[^\s\"'\\&,;]+", re.IGNORECASE),
    # Bare ``token`` key with ``:`` or ``=``, optional spaces and an optional opening quote
    # (``token=``, ``token: x``, ``token = x``, ``token: "x"``); the lookbehind keeps
    # ``page_token``, ``next_token``, ``csrf_token`` and ``max_tokens`` untouched.
    re.compile(
        r"(?i)((?<![A-Za-z0-9_])token\s*[:=]\s*(?:\\?[\"'])?)[^\s\"'\\&#]+",
        re.IGNORECASE,
    ),
    # Repo extra after the house set: short passwords (1-3 characters, below house pattern 4's
    # minimum) as ``password=`` or JSON. Already-redacted text matches it unchanged.
    re.compile(r"(?i)(password=|\"password\":\s*\"?)[^\s,}\"]+"),
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
    for pattern in _KEYED_SECRET_PATTERNS:
        text = pattern.sub(r"\1***REDACTED***", text)
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


def _redact_value(value: Any) -> Any:
    """Recursively apply ``redact_secrets`` to every string in a nested value."""
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(v) for v in value]
    if isinstance(value, str):
        return redact_secrets(value)
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
        """Return the error as a dict with secrets redacted from ``path``, ``detail`` and ``message``.

        Batch tools put this dict in a partial result, which is returned as a normal result
        without whole-document redaction, so each string field is redacted here.
        """
        return {
            "type": "smartsheet_rm_api_error",
            "status_code": self.status_code,
            "method": self.method,
            "path": redact_secrets(self.path),
            "detail": _sanitize(_redact_value(self.detail)),
            "request_id": self.request_id,
            "message": _sanitize(redact_secrets(str(self))),
        }


class SafetyViolationError(ToolError):
    """Raised when a call violates the read-only or bulk-destructive gates.

    Also a FastMCP ``ToolError``, so a refusal raised from middleware reaches the client as
    a ``tools/call`` result with ``isError: true`` instead of a JSON-RPC internal error.
    """
