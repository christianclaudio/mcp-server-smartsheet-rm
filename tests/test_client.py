"""Unit and integration tests for SmartsheetRMClient."""

from __future__ import annotations

import os
import socket
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from smartsheet_rm_mcp.client import (
    DEFAULT_ALLOWED_HOSTS,
    DEFAULT_BASE_URL,
    SmartsheetRMClient,
    SSRFSafeAsyncTransport,
    _validate_base_url,
    _validate_hostname_dns,
    fetch_pinned_https,
    validate_outbound_url,
)
from smartsheet_rm_mcp.errors import SmartsheetRMAPIError


@pytest.mark.asyncio
async def test_client_init_and_context_manager() -> None:
    client = SmartsheetRMClient("test-token", "https://api.rm.smartsheet.com/api/v1/")
    assert client.api_token == "test-token"
    assert client.base_url == "https://api.rm.smartsheet.com/api/v1"
    assert client._owns_http is True

    async with client as c:
        assert c is client

    # Test with custom external httpx client
    custom_http = httpx.AsyncClient()
    client2 = SmartsheetRMClient("test-token-2", http_client=custom_http)
    assert client2._owns_http is False
    await client2.aclose()  # Shouldn't close custom_http
    await custom_http.aclose()


def test_client_headers() -> None:
    client = SmartsheetRMClient("my-secret-token")
    headers = client._headers()
    assert headers["auth"] == "my-secret-token"
    assert headers["Accept"] == "application/json"
    assert headers["Content-Type"] == "application/json"
    assert "mcp-server-smartsheet-rm" in headers["User-Agent"]


def test_parse_retry_after() -> None:
    assert SmartsheetRMClient._parse_retry_after(None) is None
    assert SmartsheetRMClient._parse_retry_after("") is None
    assert SmartsheetRMClient._parse_retry_after("5") == 5.0
    assert SmartsheetRMClient._parse_retry_after("2.5") == 2.5
    assert SmartsheetRMClient._parse_retry_after("invalid") is None
    assert SmartsheetRMClient._parse_retry_after("-10") == 0.0


@pytest.mark.asyncio
async def test_request_success_and_path_encoding() -> None:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"id": 123, "name": "Test Project"}
    mock_resp.content = b'{"id": 123, "name": "Test Project"}'
    mock_http.request.return_value = mock_resp

    client = SmartsheetRMClient("token", http_client=mock_http)
    res = await client.get_project("proj/with special")
    assert res["id"] == 123
    mock_http.request.assert_called_once()
    call_args = mock_http.request.call_args
    assert "with%20special" in call_args[0][1]
    assert call_args.kwargs["follow_redirects"] is False


@pytest.mark.asyncio
async def test_request_error_handling_json_and_text() -> None:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {"x-request-id": "req-404"}
    mock_resp.json.return_value = {"message": "Not Found"}
    mock_http.request.return_value = mock_resp

    client = SmartsheetRMClient("token", http_client=mock_http)
    with pytest.raises(SmartsheetRMAPIError) as exc_info:
        await client.get_project(999)
    assert exc_info.value.status_code == 404
    assert exc_info.value.request_id == "req-404"

    # Non-json response
    mock_resp.json.side_effect = Exception("Not JSON")
    mock_resp.text = "Raw error text"
    with pytest.raises(SmartsheetRMAPIError) as exc_info2:
        await client.get_project(999)
    assert exc_info2.value.detail == "Raw error text"


@pytest.mark.asyncio
async def test_request_429_rate_limit_retry_and_exhaustion() -> None:
    mock_http = AsyncMock()
    mock_resp_429 = MagicMock()
    mock_resp_429.status_code = 429
    mock_resp_429.headers = {"Retry-After": "0.01", "x-request-id": "req-429"}

    mock_resp_200 = MagicMock()
    mock_resp_200.status_code = 200
    mock_resp_200.json.return_value = {"data": []}
    mock_resp_200.content = b'{"data": []}'

    # Case 1: Succeed on retry 2
    mock_http.request.side_effect = [mock_resp_429, mock_resp_200]
    client = SmartsheetRMClient("token", max_retries=2, base_delay=0.01, http_client=mock_http)
    res = await client.list_projects()
    assert res == {"data": []}
    assert mock_http.request.call_count == 2

    # Case 2: Exhaust retries (without Retry-After header)
    mock_resp_429.headers = {}
    mock_http.request.side_effect = [mock_resp_429, mock_resp_429, mock_resp_429]
    mock_http.request.reset_mock()
    with pytest.raises(SmartsheetRMAPIError) as exc:
        await client.list_projects()
    assert exc.value.status_code == 429
    assert exc.value.detail == "Rate limit exceeded after max retries"


@pytest.mark.asyncio
async def test_all_client_api_methods() -> None:
    mock_http = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"success": True}
    mock_resp.content = b'{"success": True}'
    mock_http.request.return_value = mock_resp

    client = SmartsheetRMClient("token", http_client=mock_http)

    # 1. Time tracking
    assert await client.list_time_entries({"page": 1}) == {"success": True}
    assert await client.get_time_entry(1) == {"success": True}
    assert await client.create_time_entry({"hours": 8}) == {"success": True}
    assert await client.update_time_entry(1, {"hours": 7}) == {"success": True}
    assert await client.delete_time_entry(1) == {"success": True}
    assert await client.list_project_time_entries(10) == {"success": True}
    assert await client.create_project_time_entry(10, {"hours": 4}) == {"success": True}
    assert await client.list_user_time_entries(20) == {"success": True}
    assert await client.create_user_time_entry(20, {"hours": 5}) == {"success": True}
    assert await client.update_user_approval_status(20, {"status": "approved"}) == {"success": True}
    assert await client.lock_user_timesheet(20, {"date": "2026-08-01"}) == {"success": True}

    # Empty content delete returns dict
    mock_resp.content = b""
    assert await client.delete_time_entry(2) == {"status": "deleted", "id": 2}
    assert await client.update_user_approval_status(20, {}) == {"status": "updated", "user_id": 20}
    assert await client.lock_user_timesheet(20, {}) == {"status": "locked", "user_id": 20}
    mock_resp.content = b'{"success": True}'

    # 2. Projects & phases
    assert await client.list_projects() == {"success": True}
    assert await client.get_project(100) == {"success": True}
    assert await client.create_project({"name": "P"}) == {"success": True}
    assert await client.update_project(100, {"name": "P2"}) == {"success": True}
    assert await client.delete_project(100) == {"success": True}
    assert await client.list_project_users(100) == {"success": True}
    assert await client.list_project_phases(100) == {"success": True}
    assert await client.get_project_phase(100, 101) == {"success": True}
    assert await client.create_project_phase(100, {"name": "Phase 1"}) == {"success": True}
    assert await client.update_project_phase(100, 101, {"name": "Phase 1b"}) == {"success": True}
    assert await client.delete_project_phase(100, 101) == {"success": True}

    # 3. Assignments
    assert await client.list_assignments() == {"success": True}
    assert await client.get_assignment(200) == {"success": True}
    assert await client.create_assignment({"user_id": 1}) == {"success": True}
    assert await client.update_assignment(200, {"percent": 50}) == {"success": True}
    assert await client.delete_assignment(200) == {"success": True}
    assert await client.list_project_assignments(100) == {"success": True}
    assert await client.create_project_assignment(100, {}) == {"success": True}
    assert await client.list_user_assignments(1) == {"success": True}
    assert await client.create_user_assignment(1, {}) == {"success": True}

    # 4. Users, Roles & Disciplines
    assert await client.list_users() == {"success": True}
    assert await client.get_user(1) == {"success": True}
    assert await client.create_user({"email": "a@b.com"}) == {"success": True}
    assert await client.update_user(1, {"first_name": "F"}) == {"success": True}
    assert await client.delete_user(1) == {"success": True}
    assert await client.list_user_bill_rates(1) == {"success": True}
    assert await client.create_user_bill_rate(1, {"rate": 150}) == {"success": True}
    assert await client.get_user_availability(1) == {"success": True}
    assert await client.get_user_utilization(1) == {"success": True}
    assert await client.list_roles() == {"success": True}
    assert await client.create_role({"name": "Dev"}) == {"success": True}
    assert await client.update_role(1, {"name": "Dev Senior"}) == {"success": True}
    assert await client.delete_role(1) == {"success": True}
    assert await client.list_disciplines() == {"success": True}
    assert await client.create_discipline({"name": "Eng"}) == {"success": True}
    assert await client.update_discipline(1, {"name": "Eng Core"}) == {"success": True}
    assert await client.delete_discipline(1) == {"success": True}

    # 5. Clients & Contacts
    assert await client.list_clients() == {"success": True}
    assert await client.get_client(1) == {"success": True}
    assert await client.create_client({"name": "C"}) == {"success": True}
    assert await client.update_client(1, {"name": "C2"}) == {"success": True}
    assert await client.delete_client(1) == {"success": True}
    assert await client.list_client_contacts(1) == {"success": True}
    assert await client.create_client_contact(1, {"first_name": "Bob"}) == {"success": True}
    assert await client.delete_client_contact(1, 10) == {"success": True}

    # 6. Leaves & Holidays
    assert await client.list_leave_types() == {"success": True}
    assert await client.get_leave_type(1) == {"success": True}
    assert await client.create_leave_type({"name": "Vacation"}) == {"success": True}
    assert await client.update_leave_type(1, {"name": "PTO"}) == {"success": True}
    assert await client.delete_leave_type(1) == {"success": True}
    assert await client.list_holidays() == {"success": True}
    assert await client.get_holiday(1) == {"success": True}
    assert await client.create_holiday({"name": "New Year"}) == {"success": True}
    assert await client.update_holiday(1, {"name": "Holiday"}) == {"success": True}
    assert await client.delete_holiday(1) == {"success": True}

    # 7. Expenses
    assert await client.list_expenses() == {"success": True}
    assert await client.get_expense(1) == {"success": True}
    assert await client.create_expense({"amount": 50}) == {"success": True}
    assert await client.update_expense(1, {"amount": 75}) == {"success": True}
    assert await client.delete_expense(1) == {"success": True}
    assert await client.list_project_expenses(100) == {"success": True}
    assert await client.list_user_expenses(1) == {"success": True}
    assert await client.list_expense_categories() == {"success": True}
    assert await client.create_expense_category({"name": "Travel"}) == {"success": True}
    assert await client.delete_expense_category(1) == {"success": True}

    # 8. Tags & Custom Fields
    assert await client.list_tags() == {"success": True}
    assert await client.create_tag({"name": "VIP"}) == {"success": True}
    assert await client.delete_tag(1) == {"success": True}
    assert await client.list_custom_fields() == {"success": True}
    assert await client.get_custom_field(1) == {"success": True}
    assert await client.create_custom_field({"name": "Tier"}) == {"success": True}
    assert await client.update_custom_field(1, {"name": "Tier 2"}) == {"success": True}
    assert await client.delete_custom_field(1) == {"success": True}
    assert await client.list_custom_field_values() == {"success": True}
    assert await client.set_custom_field_values({"1": "A"}) == {"success": True}

    # 9. OpenAPI Extension Entities
    assert await client.list_approvals() == {"success": True}
    assert await client.create_approval({"status": "approved"}) == {"success": True}
    assert await client.delete_approval(1) == {"success": True}
    assert await client.list_status_options() == {"success": True}
    assert await client.get_user_statuses(1) == {"success": True}
    assert await client.set_user_status(1, {"status": "WFH"}) == {"success": True}
    assert await client.list_placeholder_resources() == {"success": True}
    assert await client.create_placeholder_resource({"title": "Dev"}) == {"success": True}
    assert await client.delete_placeholder_resource(1) == {"success": True}
    assert await client.list_subtasks(100, 10) == {"success": True}
    assert await client.create_subtask(100, 10, {"description": "Task"}) == {"success": True}
    assert await client.delete_subtask(100, 10, 5) == {"success": True}
    assert await client.get_report_rows({"report_type": "time"}) == {"success": True}
    assert await client.get_report_totals({"report_type": "time"}) == {"success": True}
    assert await client.list_webhooks() == {"success": True}
    assert await client.create_webhook({"url": "https://test.com"}) == {"success": True}
    assert await client.delete_webhook(1) == {"success": True}


def test_extract_list_and_extract_dict() -> None:
    """Verify defensive container extraction for lists and dictionaries across polymorphic inputs."""
    # extract_list with direct container (key=None)
    assert SmartsheetRMClient.extract_list([1, 2, 3]) == [1, 2, 3]
    assert SmartsheetRMClient.extract_list({"key": "val"}) == []
    assert SmartsheetRMClient.extract_list(None) == []
    assert SmartsheetRMClient.extract_list("string") == []

    # extract_list with key
    assert SmartsheetRMClient.extract_list({"data": ["a", "b"]}, key="data") == ["a", "b"]
    assert SmartsheetRMClient.extract_list({"data": "not-a-list"}, key="data") == []
    assert SmartsheetRMClient.extract_list({"other": [1]}, key="data") == []
    assert SmartsheetRMClient.extract_list(["not", "a", "dict"], key="data") == []
    assert SmartsheetRMClient.extract_list(None, key="data") == []

    # extract_dict with direct container (key=None)
    assert SmartsheetRMClient.extract_dict({"a": 1}) == {"a": 1}
    assert SmartsheetRMClient.extract_dict([1, 2]) == {}
    assert SmartsheetRMClient.extract_dict(None) == {}
    assert SmartsheetRMClient.extract_dict("string") == {}

    # extract_dict with key
    assert SmartsheetRMClient.extract_dict({"item": {"id": 10}}, key="item") == {"id": 10}
    assert SmartsheetRMClient.extract_dict({"item": [1, 2]}, key="item") == {}
    assert SmartsheetRMClient.extract_dict({"other": {}}, key="item") == {}
    assert SmartsheetRMClient.extract_dict(["not", "a", "dict"], key="item") == {}
    assert SmartsheetRMClient.extract_dict(None, key="item") == {}


def test_validate_base_url_ssrf_protections() -> None:
    """Verify base URL validation protects against SSRF and non-global destinations."""
    # Default fallback keeps the official path prefix
    assert _validate_base_url("") == DEFAULT_BASE_URL
    assert DEFAULT_ALLOWED_HOSTS == "api.rm.smartsheet.com"

    # HTTPS enforcement
    with pytest.raises(ValueError, match="Only HTTPS is permitted"):
        _validate_base_url("http://api.rm.smartsheet.com")

    # Missing hostname
    with pytest.raises(ValueError, match="missing hostname"):
        _validate_base_url("https://")

    # Userinfo cannot hide a different host
    with pytest.raises(ValueError, match="must not include userinfo"):
        _validate_base_url("https://api.rm.smartsheet.com@evil.com/api/v1")
    with pytest.raises(ValueError, match="must not include userinfo"):
        _validate_base_url("https://user:secret@api.rm.smartsheet.com/api/v1")

    # Internal/loopback hostnames, including when someone tries to allowlist them
    for host in ["localhost", "127.0.0.1", "[::1]", "service.local", "db.internal"]:
        with pytest.raises(ValueError, match="Blocked internal/loopback"):
            _validate_base_url(f"https://{host}")
    with pytest.raises(ValueError, match="Blocked internal/loopback"):
        _validate_base_url("https://localhost/api/v1", allowed_hosts_str="localhost")
    with pytest.raises(ValueError, match="Blocked internal/loopback"):
        _validate_base_url(
            "https://metadata.google.internal/",
            allowed_hosts_str="metadata.google.internal",
        )

    # Private IP literals stay blocked even if named in the allowlist
    for ip in ["10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "127.0.0.2"]:
        with pytest.raises(ValueError, match="Blocked private/reserved"):
            _validate_base_url(f"https://{ip}")
    with pytest.raises(ValueError, match="Blocked private/reserved"):
        _validate_base_url("https://169.254.169.254", allowed_hosts_str="169.254.169.254")

    # Public IPs and other hosts are denied unless the allowlist names them
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://93.184.216.34")
    assert _validate_base_url("https://93.184.216.34", allowed_hosts_str="93.184.216.34") == "https://93.184.216.34"

    # Default allowlist is the official host; blank config does not open the gate
    assert _validate_base_url("https://API.RM.SMARTSHEET.COM/api/v1/") == "https://API.RM.SMARTSHEET.COM/api/v1"
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://api.custom.com")
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://api.rm.smartsheet.com.evil.com/api/v1")
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://evil.api.rm.smartsheet.com/api/v1")
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://api.custom.com", allowed_hosts_str="")
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://api.custom.com", allowed_hosts_str="   ")
    with patch.dict(os.environ, {"SMARTSHEET_RM_ALLOWED_HOSTS": "  "}):
        assert _validate_base_url("https://api.rm.smartsheet.com/api/v1/") == "https://api.rm.smartsheet.com/api/v1"

    # Explicit allowlist replaces the default (comma gaps are ignored)
    with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
        _validate_base_url("https://untrusted.example.com", allowed_hosts_str="api.rm.smartsheet.com, ,")
    assert (
        _validate_base_url(
            "https://api.custom.com",
            allowed_hosts_str="api.custom.com, , api.rm.smartsheet.com",
        )
        == "https://api.custom.com"
    )
    with patch.dict(os.environ, {"SMARTSHEET_RM_ALLOWED_HOSTS": "api.custom.com"}):
        with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
            _validate_base_url("https://api.rm.smartsheet.com/api/v1")
        assert _validate_base_url("https://api.custom.com") == "https://api.custom.com"

    # Valid global domain name via DNS resolution, only after the allowlist admits it
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        with pytest.raises(ValueError, match="not in SMARTSHEET_RM_ALLOWED_HOSTS"):
            _validate_base_url("https://api.customdomain.org", check_dns=True)
        assert (
            _validate_base_url(
                "https://api.customdomain.org",
                allowed_hosts_str="api.customdomain.org",
                check_dns=True,
            )
            == "https://api.customdomain.org"
        )

    # DNS resolving to private IP
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))],
    ):
        with pytest.raises(ValueError, match="resolving to private/reserved IP"):
            _validate_base_url(
                "https://malicious-rebinding.com",
                allowed_hosts_str="malicious-rebinding.com",
                check_dns=True,
            )

    # DNS resolution failure (fail-closed)
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(ValueError, match="Could not resolve hostname in base URL"):
            _validate_base_url(
                "https://unresolvable-domain.com",
                allowed_hosts_str="unresolvable-domain.com",
                check_dns=True,
            )

    # Private IP literal in _validate_hostname_dns
    with pytest.raises(ValueError, match="Blocked private/reserved"):
        _validate_hostname_dns("10.0.0.1")

    # Example.com and global public IP in _validate_hostname_dns
    assert _validate_hostname_dns("example.com") == "93.184.216.34"
    assert _validate_hostname_dns("api.example.com") == "93.184.216.34"
    assert _validate_hostname_dns("93.184.216.34") == "93.184.216.34"

    # Alternative spellings and IPv4-mapped addresses stay fail-closed
    for literal in ("2130706433", "0x7f000001", "0177.0.0.1", "127.1", "127.0.1", "[::ffff:127.0.0.1]"):
        host = literal.strip("[]")
        with pytest.raises(ValueError, match="Blocked private/reserved"):
            _validate_hostname_dns(host)
        with pytest.raises(ValueError, match="Blocked private/reserved"):
            _validate_base_url(f"https://{literal}/", allowed_hosts_str=host)
    with pytest.raises(ValueError, match="Blocked private/reserved"):
        _validate_base_url(
            "https://[::ffff:93.184.216.34]/",
            allowed_hosts_str="::ffff:93.184.216.34",
        )
    assert _validate_hostname_dns("0x5d581622") == "93.88.22.34"
    assert _validate_base_url("https://0x5d581622/api", allowed_hosts_str="0x5d581622") == "https://0x5d581622/api"

    # Non-addresses that resemble literals fall through to DNS and still fail closed
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        for host in ("999.999.999.999", "0x", "0xzz", "1.2.3.4.5"):
            with pytest.raises(ValueError, match="Could not resolve hostname"):
                _validate_hostname_dns(host)

    # Every resolved address must be global; the first good answer is the connect target
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ],
    ):
        with pytest.raises(ValueError, match="resolving to private/reserved IP"):
            _validate_hostname_dns("mixed.example.net")
    with patch(
        "socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0)),
        ],
    ):
        assert _validate_hostname_dns("dual.example.net") == "1.1.1.1"
    with patch("socket.getaddrinfo", return_value=[]):
        with pytest.raises(ValueError, match="No IP addresses resolved"):
            _validate_hostname_dns("empty.example.net")


def test_validate_outbound_url() -> None:
    """Maintainer spec fetches block private targets and do not use the API allowlist."""
    with pytest.raises(ValueError, match="Outbound URL is required"):
        validate_outbound_url("   ")
    with pytest.raises(ValueError, match="Only HTTPS is permitted for outbound URL"):
        validate_outbound_url("http://api.example.com/spec.json")
    with pytest.raises(ValueError, match="missing hostname"):
        validate_outbound_url("https://")
    with pytest.raises(ValueError, match="Blocked internal/loopback"):
        validate_outbound_url("https://localhost/spec.json")
    with pytest.raises(ValueError, match="Blocked private/reserved"):
        validate_outbound_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError, match="must not include userinfo"):
        validate_outbound_url("https://user@api.example.com/spec.json")

    assert validate_outbound_url("  https://api.example.com/spec.json  ") == "https://api.example.com/spec.json"
    assert validate_outbound_url("https://93.184.216.34/openapi.json") == "https://93.184.216.34/openapi.json"

    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        assert (
            validate_outbound_url("https://developer.smartsheet.com/openapi.json")
            == "https://developer.smartsheet.com/openapi.json"
        )

    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],
    ):
        with pytest.raises(ValueError, match="resolving to private/reserved IP"):
            validate_outbound_url("https://metadata.example.net/spec.json")

    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(ValueError, match="Could not resolve hostname"):
            validate_outbound_url("https://unresolvable-spec.example.net/spec.json")


def test_fetch_pinned_https_connects_to_validated_ip() -> None:
    """Spec fetches connect to the checked IP and keep Host plus SNI on the name."""
    response = httpx.Response(
        200,
        json={"ok": True},
        request=httpx.Request("GET", "https://93.184.216.34/spec.json"),
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.get.return_value = response

    with patch("httpx.Client", return_value=client) as client_cls:
        fetched = fetch_pinned_https("https://api.example.com/spec.json?x=1", timeout=30.0)

    assert fetched.status_code == 200
    client_cls.assert_called_once()
    assert client_cls.call_args.kwargs["follow_redirects"] is False
    client.get.assert_called_once()
    target = client.get.call_args.args[0]
    assert str(target) == "https://93.184.216.34/spec.json?x=1"
    assert client.get.call_args.kwargs["headers"]["Host"] == "api.example.com"
    assert client.get.call_args.kwargs["extensions"]["sni_hostname"] == "api.example.com"
    assert client.get.call_args.kwargs["follow_redirects"] is False

    calls = {"n": 0}

    def once(*_args: Any, **_kwargs: Any) -> list[Any]:
        calls["n"] += 1
        if calls["n"] > 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with patch("socket.getaddrinfo", side_effect=once):
        with patch("httpx.Client", return_value=client):
            fetch_pinned_https("https://developer.smartsheet.com:8443/openapi.json")
    assert calls["n"] == 1
    pinned = client.get.call_args.args[0]
    assert str(pinned) == "https://93.184.216.34:8443/openapi.json"
    assert client.get.call_args.kwargs["headers"]["Host"] == "developer.smartsheet.com:8443"
    assert client.get.call_args.kwargs["extensions"]["sni_hostname"] == "developer.smartsheet.com"

    with patch("httpx.Client") as blocked_client:
        with pytest.raises(ValueError, match="Blocked private/reserved"):
            fetch_pinned_https("https://169.254.169.254/latest/meta-data")
        blocked_client.assert_not_called()


@pytest.mark.asyncio
async def test_ssrf_safe_async_transport() -> None:
    """Verify SSRFSafeAsyncTransport blocks outbound requests to private/reserved destinations."""
    transport = SSRFSafeAsyncTransport()

    # Valid public resolution passes to base transport and offloads to worker thread
    import threading

    loop_thread = threading.get_ident()
    dns_thread = None

    def fake_getaddrinfo(*args: Any, **kwargs: Any) -> list[Any]:
        nonlocal dns_thread
        dns_thread = threading.get_ident()
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    with patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        with patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            return_value=httpx.Response(200, json={"ok": True}),
        ):
            req = httpx.Request("GET", "https://api.customdomain.org/data")
            resp = await transport.handle_async_request(req)
            assert resp.status_code == 200
            assert dns_thread is not None
            assert dns_thread != loop_thread
            assert req.url.host == "93.184.216.34"
            assert req.headers["Host"] == "api.customdomain.org"
            assert req.extensions["sni_hostname"] == "api.customdomain.org"

    # Non-HTTPS never reaches DNS
    with pytest.raises(SmartsheetRMAPIError) as scheme_exc:
        await transport.handle_async_request(httpx.Request("GET", "http://api.customdomain.org/data"))
    assert "Only HTTPS is permitted" in str(scheme_exc.value.detail)

    # Explicit port stays on the Host header; SNI stays the bare hostname
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    ):
        with patch.object(
            httpx.AsyncHTTPTransport,
            "handle_async_request",
            return_value=httpx.Response(200),
        ):
            port_req = httpx.Request("GET", "https://api.customdomain.org:8443/data")
            await transport.handle_async_request(port_req)
            assert port_req.url.host == "93.184.216.34"
            assert port_req.url.port == 8443
            assert port_req.headers["Host"] == "api.customdomain.org:8443"
            assert port_req.extensions["sni_hostname"] == "api.customdomain.org"

    # Private IP resolution raises SmartsheetRMAPIError at request time
    with patch(
        "socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 443))],
    ):
        req = httpx.Request("GET", "https://malicious.com/data")
        with pytest.raises(SmartsheetRMAPIError) as exc_info:
            await transport.handle_async_request(req)
        assert exc_info.value.status_code == 400
        assert "SSRF validation blocked request" in exc_info.value.detail


@pytest.mark.asyncio
async def test_client_default_transport_ssrf() -> None:
    """Verify SmartsheetRMClient instantiates SSRFSafeAsyncTransport by default and closes it cleanly."""
    client = SmartsheetRMClient("token")
    assert isinstance(client._http._transport, SSRFSafeAsyncTransport)
    await client.aclose()
