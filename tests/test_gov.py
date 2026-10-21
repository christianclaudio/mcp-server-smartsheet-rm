"""Tests for the opt-in Smartsheet Gov base URL.

Host and headers come from Smartsheet's RM API Introduction and Authentication pages:
https://developers.smartsheet.com/api/resource_management/introduction
https://developers.smartsheet.com/api/resource_management/getting-started/authentication
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from unittest.mock import patch

import httpx
import pytest
from tests.fake_network import FakeNetwork, answer

from smartsheet_rm_mcp import common
from smartsheet_rm_mcp import server as srv
from smartsheet_rm_mcp.client import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_BASE_URL,
    GOV_BASE_URL,
    GOV_HOST,
    SmartsheetRMClient,
)
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError, redact_secrets

GOV_ENV = (
    "SMARTSHEET_RM_API_TOKEN",
    "SMARTSHEET_RM_BASE_URL",
    "SMARTSHEET_RM_ALLOWED_HOSTS",
    "SMARTSHEET_RM_GOV_SMARTSHEET_API_TOKEN",
)


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No vendor env, no cached clients; restore the injected client afterwards."""
    for var in GOV_ENV:
        monkeypatch.delenv(var, raising=False)
    old = srv._client
    srv._client = None
    common._client = None
    common._HEADER_CLIENT_CACHE.clear()
    yield
    common._HEADER_CLIENT_CACHE.clear()
    srv._client = old
    common._client = old


def _recording_http(sent: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"data": []})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_gov_is_not_the_default(clean: None) -> None:
    """The default base URL and allowlist stay on api.rm.smartsheet.com."""
    assert DEFAULT_BASE_URL == "https://api.rm.smartsheet.com/api/v1"
    assert GOV_HOST not in DEFAULT_ALLOWED_HOSTS
    assert GOV_BASE_URL == "https://app.smartsheetgov.com/2.0/rm/api/v1"
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        SmartsheetRMClient("rm", GOV_BASE_URL, smartsheet_api_token="ss")


def test_gov_requires_smartsheet_token(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """An allowlisted Gov base URL without the Smartsheet token fails at construction."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    with pytest.raises(ValueError, match="Smartsheet Gov endpoint needs a Smartsheet API token"):
        SmartsheetRMClient("rm", GOV_BASE_URL)
    with pytest.raises(ValueError, match="Smartsheet Gov endpoint"):
        SmartsheetRMClient("rm", GOV_BASE_URL, smartsheet_api_token="   ")


def test_gov_http_rejected(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Gov host keeps the HTTPS-only rule."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    with pytest.raises(ValueError, match="Only HTTPS"):
        SmartsheetRMClient("rm", GOV_BASE_URL.replace("https", "http"), smartsheet_api_token="ss")


@pytest.mark.asyncio
async def test_gov_sends_both_tokens_to_gov_path(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """auth carries the RM token and Authorization the Smartsheet token, under /2.0/rm/api/v1."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    sent: list[httpx.Request] = []
    client = SmartsheetRMClient(
        "rm-tok", GOV_BASE_URL, smartsheet_api_token=" ss-tok ", http_client=_recording_http(sent)
    )
    assert client.is_gov
    await client.list_projects()
    (req,) = sent
    assert str(req.url) == "https://app.smartsheetgov.com/2.0/rm/api/v1/projects"
    assert req.headers["auth"] == "rm-tok"
    assert req.headers["Authorization"] == "Bearer ss-tok"


@pytest.mark.asyncio
async def test_smartsheet_token_never_sent_to_other_hosts(clean: None) -> None:
    """A Smartsheet token given for a non-Gov host is dropped, not sent."""
    sent: list[httpx.Request] = []
    client = SmartsheetRMClient("rm-tok", smartsheet_api_token="ss-tok", http_client=_recording_http(sent))
    assert not client.is_gov
    await client.list_projects()
    assert "authorization" not in sent[0].headers
    assert sent[0].headers["auth"] == "rm-tok"


@pytest.mark.asyncio
async def test_gov_transport_pins_and_blocks_private(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SSRF transport applies to the Gov host: a private DNS answer is refused."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    client = SmartsheetRMClient("rm", GOV_BASE_URL, smartsheet_api_token="ss")
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]
    with patch("socket.getaddrinfo", return_value=private), pytest.raises(SmartsheetRMAPIError) as err:
        await client.list_projects()
    assert err.value.status_code == 400
    assert "SSRF validation blocked" in str(err.value.detail)
    await client.aclose()


@pytest.mark.asyncio
async def test_gov_transport_connects_to_checked_ip(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """TCP goes to the validated IP; the URL, Host and SNI stay on app.smartsheetgov.com."""
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    client = SmartsheetRMClient("rm", GOV_BASE_URL, smartsheet_api_token="ss")
    net = FakeNetwork(body={"data": []})
    with net.patches(), patch("socket.getaddrinfo", return_value=answer("93.184.216.34")) as lookup:
        await client.list_projects()
    assert lookup.call_count == 1
    assert lookup.call_args.args[0] == GOV_HOST
    assert net.connects == [("93.184.216.34", 443)]
    assert net.tls_server_names == [GOV_HOST]
    assert net.sent.startswith(b"GET /2.0/rm/api/v1/projects")
    assert f"\r\nHost: {GOV_HOST}\r\n".encode() in net.sent
    assert b"\r\nAuthorization: Bearer ss\r\n" in net.sent
    await client.aclose()


@pytest.mark.asyncio
async def test_get_client_env_gov(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The env path pairs the env RM token with the env Gov token."""
    monkeypatch.setenv("SMARTSHEET_RM_API_TOKEN", "rm-env")
    monkeypatch.setenv("SMARTSHEET_RM_BASE_URL", GOV_BASE_URL)
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    monkeypatch.setenv("SMARTSHEET_RM_GOV_SMARTSHEET_API_TOKEN", "ss-env")
    client = await common.get_client({})
    assert client.is_gov
    assert client._headers()["Authorization"] == "Bearer ss-env"
    await client.aclose()


@pytest.mark.asyncio
async def test_get_client_header_gov_never_uses_env_token(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller's RM token needs the caller's own Gov token; the env Gov token is not borrowed."""
    monkeypatch.setenv("SMARTSHEET_RM_BASE_URL", GOV_BASE_URL)
    monkeypatch.setenv("SMARTSHEET_RM_ALLOWED_HOSTS", GOV_HOST)
    monkeypatch.setenv("SMARTSHEET_RM_GOV_SMARTSHEET_API_TOKEN", "ss-env")
    with pytest.raises(ValueError, match="Smartsheet Gov endpoint"):
        await common.get_client({"headers": {"x-smartsheet-rm-token": "rm-caller"}})

    a = await common.get_client({"headers": {"auth": "rm-caller", "x-smartsheet-gov-api-token": "ss-a"}})
    b = await common.get_client({"headers": {"auth": "rm-caller", "x-smartsheet-gov-api-token": "ss-b"}})
    assert a is not b
    assert a._headers()["Authorization"] == "Bearer ss-a"
    assert b._headers()["Authorization"] == "Bearer ss-b"
    again = await common.get_client({"headers": {"auth": "rm-caller", "x-smartsheet-gov-api-token": "ss-a"}})
    assert again is a


def test_redaction_masks_env_gov_token(clean: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The literal env Gov token and the Bearer header are masked."""
    monkeypatch.setenv("SMARTSHEET_RM_GOV_SMARTSHEET_API_TOKEN", "plainsecretvalue42")
    out = redact_secrets("failed with plainsecretvalue42 and Authorization: Bearer abcdefghijklmnop")
    assert "plainsecretvalue42" not in out
    assert "abcdefghijklmnop" not in out
