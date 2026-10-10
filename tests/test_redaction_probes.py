"""The gap-audit redaction probes (template v1.6.0 house rules): no part of a secret survives."""

from __future__ import annotations

import json

import pytest

from smartsheet_rm_mcp.errors import MASK, _redact_value, redact_message, redact_payload, redact_secrets


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("password=ab cd user=bob", f"password={MASK}"),
        ('{"token": "ab cd ef"}', f'{{"token": "{MASK}"}}'),
        ('"password": "a,b\\"c d" next', f'"password": "{MASK}" next'),
        ("password=x", f"password={MASK}"),
        ('"api_key":"SECRETVAL"', f'"api_key":"{MASK}"'),
        ("client_secret=ab cd", f"client_secret={MASK}"),
        ('"access_token": "ab cd"', f'"access_token": "{MASK}"'),
        ("api_token=abcdefgh1234&x=1", f"api_token={MASK}&x=1"),
        ("SMARTSHEET_RM_API_TOKEN=abc123", f"SMARTSHEET_RM_API_TOKEN={MASK}"),
        ("auth: abc123", f"auth: {MASK}"),
        ("Bearer abc", f"Bearer {MASK}"),
    ],
)
def test_probe_is_masked_whole(raw: str, expected: str) -> None:
    assert redact_secrets(raw) == expected


@pytest.mark.parametrize("kind", ["CERTIFICATE", "PUBLIC KEY", "OPENSSH PRIVATE KEY", "PGP MESSAGE"])
def test_any_pem_block_is_masked(kind: str) -> None:
    pem = f"cert: -----BEGIN {kind}-----\nMIIabc\ndef==\n-----END {kind}-----\ntrailer"
    out = redact_secrets(pem)
    assert "MIIabc" not in out and "def==" not in out
    assert out.endswith("trailer")


def test_short_secret_gets_the_fixed_mask() -> None:
    assert redact_secrets("password=x") == redact_secrets("password=" + "y" * 64)


def test_api_token_env_and_extra_secret_are_replaced_literally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", "literal-env-secret")
    assert redact_secrets("got literal-env-secret back") == f"got {MASK} back"
    assert redact_secrets("raw custom-value here", extra_secret="custom-value") == f"raw {MASK} here"


def test_payload_masks_credential_keys_of_any_type() -> None:
    assert redact_payload({"api_token": 123, "page_token": "keep", "password": None}) == {
        "api_token": MASK,
        "page_token": "keep",
        "password": None,
    }


def test_redact_message_keeps_json_valid() -> None:
    out = redact_message('HTTP 400: {"token": "ab cd ef", "password": "x", "note": "password=ab cd"}')
    assert out.startswith("HTTP 400: ")
    assert json.loads(out.removeprefix("HTTP 400: ")) == {"token": MASK, "password": MASK, "note": f"password={MASK}"}


def test_redact_value_still_redacts_string_dict_keys() -> None:
    """ssrm's partial-result detail keeps redacting secrets that sit in a dict key."""
    out = _redact_value({"password=ab cd": "v", 7: ["token=abcdefgh"]})
    assert out == {f"password={MASK}": "v", 7: [f"token={MASK}"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [987654321, ["list-secret-1", 42], {"nested": "dict-secret"}])
async def test_tool_call_masks_non_string_values_under_credential_keys(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """A real tools/call: an upstream error body with a number/list/dict under a credential key."""
    from unittest.mock import AsyncMock

    from fastmcp import Client

    from smartsheet_rm_mcp import server
    from smartsheet_rm_mcp.errors import SmartsheetRMAPIError

    api = AsyncMock()
    api.get_project.side_effect = SmartsheetRMAPIError(
        401, "/projects/1", "GET", {"password": value, "private_key": value, "note": "password=ab cd"}
    )
    monkeypatch.setattr(server, "_client", api)
    async with Client(server.create_server(profile="full")) as client:
        res = await client.call_tool("projects_get_project", {"project_id": 1}, raise_on_error=False)
    assert res.is_error is True
    text = res.content[0].text  # type: ignore[union-attr]
    for fragment in ("987654321", "list-secret-1", "dict-secret", "ab cd"):
        assert fragment not in text
    detail = json.loads(text)["error"]["detail"]
    assert detail["password"] in (MASK, "[redacted]")
    assert detail["private_key"] == MASK
    assert detail["note"] == f"password={MASK}"
