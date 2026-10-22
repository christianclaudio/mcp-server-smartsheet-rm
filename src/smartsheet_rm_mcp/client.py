"""Smartsheet Resource Management (10,000ft) REST API Client (async).

Covers part of the Resource Management API v1 for these areas (not every documented
operation; see scripts/check_openapi_drift.py):
- Time Tracking & Timesheet Reconciliation
- Projects, Phases & Milestones
- Resource Scheduling & Allocations
- Users, Roles, Disciplines & Capacity
- Clients & Contacts
- Leaves, Holidays & PTO
- Expenses & Expense Categories
- Tags & Custom Fields

46 of the routes called here are in neither Smartsheet's Resource Management API reference
(https://developers.smartsheet.com/api/resource_management/openapi) nor the archived 10,000ft
API docs (https://github.com/10Kft/10kft-api). Their methods say so in their docstrings, and
README.md lists them with the documented equivalents where one exists.
"""

from __future__ import annotations

import asyncio
import contextvars
import ipaddress
import os
import random
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import quote, urlparse

import httpcore
import httpx
from httpx import Response

from smartsheet_rm_mcp import __version__

from .errors import SmartsheetRMAPIError

JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None

DEFAULT_BASE_URL = "https://api.rm.smartsheet.com/api/v1"
# Credentialed calls stay on this host unless SMARTSHEET_RM_ALLOWED_HOSTS replaces it.
DEFAULT_ALLOWED_HOSTS = "api.rm.smartsheet.com"
# Smartsheet Gov Resource Management endpoint. Opt-in only: it is not the default and is
# not in the default allowlist. Source: "Smartsheet Gov" in the RM API Introduction
# (https://developers.smartsheet.com/api/resource_management/introduction) and the
# Authentication article
# (https://developers.smartsheet.com/api/resource_management/getting-started/authentication),
# which also require a Smartsheet API token as ``Authorization: Bearer`` next to ``auth``.
GOV_BASE_URL = "https://app.smartsheetgov.com/2.0/rm/api/v1"
GOV_HOST = "app.smartsheetgov.com"


def _resolve_allowed_hosts(allowed_hosts_str: str | None) -> set[str]:
    """Resolve the hostname allowlist for credentialed base URLs.

    A non-blank ``SMARTSHEET_RM_ALLOWED_HOSTS`` value (or ``allowed_hosts_str``)
    replaces the default. An unset or blank value fail-closes to
    ``api.rm.smartsheet.com``.
    """
    if allowed_hosts_str is None:
        raw = os.environ.get("SMARTSHEET_RM_ALLOWED_HOSTS", "")
    else:
        raw = allowed_hosts_str
    raw = raw.strip()
    if not raw:
        raw = DEFAULT_ALLOWED_HOSTS
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for loopback, private, link-local, metadata, mapped, or non-global addresses."""
    if getattr(ip, "ipv4_mapped", None) is not None:
        return True
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or not ip.is_global
    )


def _looks_like_ipv4_literal(hostname: str) -> bool:
    """Return True for decimal, hex, octal, and shortened IPv4 spellings."""
    labels = hostname.split(".")
    if not 1 <= len(labels) <= 4:
        return False
    for label in labels:
        lowered = label.lower()
        if lowered.startswith("0x"):
            hex_body = lowered[2:]
            if not hex_body or any(char not in "0123456789abcdef" for char in hex_body):
                return False
        elif not label.isdigit():
            return False
    return True


def _literal_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP literal, including forms that bypass ``ip_address`` strings."""
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    if not _looks_like_ipv4_literal(hostname):
        return None
    try:
        packed = socket.inet_aton(hostname)
    except OSError:
        return None
    return ipaddress.ip_address(packed)


def _raise_if_blocked_destination(hostname: str, *, context: str) -> None:
    """Fail closed on loopback, link-local, private, and other non-global targets."""
    if hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith((".local", ".internal")):
        raise ValueError(f"Blocked internal/loopback hostname in {context}: {hostname}")
    literal = _literal_ip(hostname)
    if literal is not None and _is_blocked_ip(literal):
        raise ValueError(f"Blocked private/reserved IP address in {context}: {hostname}")


def _reject_userinfo(parsed_username: str | None, parsed_password: str | None, *, context: str) -> None:
    """Reject userinfo so the allowlisted host cannot be hidden in the user part."""
    if parsed_username is not None or parsed_password is not None:
        raise ValueError(f"{context} must not include userinfo.")


def _validate_base_url(
    url: str,
    allowed_hosts_str: str | None = None,
    check_dns: bool = False,
) -> str:
    """Validate a credentialed API base URL against SSRF and the host allowlist.

    Requires HTTPS. Loopback, private, link-local, and other non-global targets
    are rejected even when listed in the allowlist. The hostname must be in
    ``SMARTSHEET_RM_ALLOWED_HOSTS`` when that variable is set; otherwise only
    ``api.rm.smartsheet.com`` is permitted. The default path prefix remains
    ``/api/v1``.
    """
    if not url:
        return DEFAULT_BASE_URL

    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS is permitted for base URL.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Invalid base URL: missing hostname.")

    _raise_if_blocked_destination(hostname, context="base URL")
    _reject_userinfo(parsed.username, parsed.password, context="Base URL")

    allowed = _resolve_allowed_hosts(allowed_hosts_str)
    if hostname not in allowed:
        raise ValueError(f"Hostname '{hostname}' is not in SMARTSHEET_RM_ALLOWED_HOSTS.")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        if check_dns:
            _validate_hostname_dns(hostname)
        return url.rstrip("/")
    return url.rstrip("/")


def _parse_outbound_https(url: str) -> tuple[str, str]:
    """Return ``(url, hostname)`` after HTTPS, userinfo, and literal-address checks."""
    candidate = url.strip()
    if not candidate:
        raise ValueError("Outbound URL is required.")

    parsed = urlparse(candidate)
    if parsed.scheme.lower() != "https":
        raise ValueError("Only HTTPS is permitted for outbound URL.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Invalid outbound URL: missing hostname.")

    _raise_if_blocked_destination(hostname, context="outbound URL")
    _reject_userinfo(parsed.username, parsed.password, context="Outbound URL")
    return candidate, hostname


def validate_outbound_url(url: str) -> str:
    """Validate a maintainer-supplied outbound URL before a non-following GET.

    Enforces HTTPS and the same fail-closed loopback, private, and metadata
    blocks as credentialed base URLs. Does not apply the API host allowlist,
    because this fetch does not send the Resource Management token. Connecting
    must go through ``fetch_pinned_https`` so TCP uses a checked address.
    """
    candidate, hostname = _parse_outbound_https(url)
    _validate_hostname_dns(hostname)
    return candidate


def _validate_hostname_dns(hostname: str) -> tuple[str, ...]:
    """Reject a hostname that is, or resolves to, a non-global address.

    Returns the checked addresses in resolver order: the address itself for an IP
    literal, and an empty tuple for ``example.com`` and names under it, which skip
    DNS so placeholder URLs stay usable offline (the transport refuses to connect to
    them). Every resolved address must be global; IPv4-mapped IPv6 and alternative
    integer spellings of private addresses are rejected. Run this in a worker thread
    when calling from an event loop.
    """
    if hostname == "example.com" or hostname.endswith(".example.com"):
        return ()

    literal = _literal_ip(hostname)
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError(f"Blocked private/reserved IP address: {hostname}")
        return (str(literal),)

    try:
        resolved_addrs = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname in base URL: {hostname}") from exc

    checked: list[str] = []
    for _, _, _, _, sockaddr in resolved_addrs:
        resolved_ip = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_ip(resolved_ip):
            msg = f"Blocked hostname '{hostname}' resolving to private/reserved IP: {sockaddr[0]}"
            raise ValueError(msg)
        if str(resolved_ip) not in checked:
            checked.append(str(resolved_ip))
    if not checked:
        raise ValueError(f"No IP addresses resolved for hostname: {hostname}")
    return tuple(checked)


# ---- Connect to the checked IP --------------------------------------------------------
# The transport checks the host once per request and hands httpcore the addresses that
# passed; the network backend dials one of them, so httpcore never resolves the name
# again. The URL keeps the hostname, so the Host header, SNI and certificate
# verification all use the original name, and httpcore pools connections per hostname.

_PINNED_IPS: contextvars.ContextVar[dict[str, tuple[str, ...]] | None] = contextvars.ContextVar(
    "smartsheet_rm_pinned_ips", default=None
)


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Dial only an address that passed ``_validate_hostname_dns`` for the requested host.

    The connect timeout httpcore passes in is one deadline for the whole call, shared by
    every checked address, not a fresh timeout per address: each attempt gets only the
    time left, and no further address is tried once it has run out.
    """

    def __init__(
        self,
        inner: httpcore.AsyncNetworkBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner or httpcore.AnyIOBackend()
        self._clock = clock

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        pins = _PINNED_IPS.get() or {}
        ips = pins.get(host.lower())
        if not ips:
            raise SmartsheetRMAPIError(
                status_code=400,
                path=f"{host}:{port}",
                method="CONNECT",
                detail=f"SSRF validation blocked request to {host}: no checked address",
            )
        deadline = None if timeout is None else self._clock() + timeout
        last: Exception | None = None
        for ip in ips:
            attempt_timeout = timeout
            if deadline is not None:
                remaining = deadline - self._clock()
                if last is not None and remaining <= 0:
                    break
                # The first address is always tried; later ones only while time is left.
                attempt_timeout = max(remaining, 0.0)
            try:
                return await self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=attempt_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last = exc
        assert last is not None
        raise last

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise SmartsheetRMAPIError(
            status_code=400,
            path=path,
            method="CONNECT",
            detail="SSRF validation blocked request: unix sockets are not allowed",
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SSRFSafeAsyncTransport(httpx.AsyncHTTPTransport):
    """Async transport that connects only to an address checked for the request's host.

    The hostname is resolved once per request, in a worker thread, and every address is
    checked. The connection pool is built with a network backend that dials only those
    addresses, in resolver order, so a second DNS answer cannot send the request
    somewhere unchecked. A host with no checked address (an ``example.com`` placeholder)
    is refused at connect. Proxies and Unix sockets are not supported: both would bypass
    the pin.
    """

    def __init__(
        self,
        verify: ssl.SSLContext | str | bool = True,
        cert: Any = None,
        trust_env: bool = True,
        http1: bool = True,
        http2: bool = False,
        limits: httpx.Limits | None = None,
        retries: int = 0,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> None:
        limits = limits or httpx.Limits(max_connections=100, max_keepalive_connections=20)
        ssl_context = httpx.create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        super().__init__(verify=ssl_context, limits=limits)
        # httpcore documents ``network_backend`` as a public AsyncConnectionPool argument
        # (https://www.encode.io/httpcore/network-backends/). httpx.AsyncHTTPTransport does
        # not take one, so the pool it serves requests through is rebuilt here with the
        # same settings plus the pinned backend.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=http1,
            http2=http2,
            retries=retries,
            local_address=local_address,
            socket_options=socket_options,
            network_backend=_PinnedNetworkBackend(),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Check the destination off the event loop, then connect only to a checked address."""
        # httpcore dials, and TLS checks, the ASCII (IDNA) form of the host, so the check
        # resolves that same name and the pin is keyed by it.
        hostname = request.url.raw_host.decode("ascii")
        if (request.url.scheme or "").lower() != "https":
            raise SmartsheetRMAPIError(
                status_code=400,
                path=str(request.url),
                method=request.method,
                detail=f"Only HTTPS is permitted for outbound requests to {hostname}.",
            )
        if not hostname:
            return await super().handle_async_request(request)
        try:
            ips = await asyncio.to_thread(_validate_hostname_dns, hostname)
        except ValueError as exc:
            raise SmartsheetRMAPIError(
                status_code=400,
                path=str(request.url),
                method=request.method,
                detail=f"SSRF validation blocked request to {hostname}: {exc}",
            ) from exc
        token = _PINNED_IPS.set({hostname.lower(): ips})
        try:
            return await super().handle_async_request(request)
        finally:
            _PINNED_IPS.reset(token)


async def fetch_pinned_https(url: str, *, timeout: float = 30.0) -> httpx.Response:
    """GET ``url`` over HTTPS through ``SSRFSafeAsyncTransport``, without following redirects.

    The transport checks every address the host resolves to and connects only to a
    checked one, keeping the hostname for the Host header, TLS SNI and certificate
    verification. A 3xx is returned, not followed: its target would skip the check.
    httpx ignores ``HTTP(S)_PROXY`` / ``ALL_PROXY`` whenever a transport is passed in,
    so the fetch always connects directly, through the pin, and an environment proxy is
    never used. ``trust_env=False`` is belt-and-braces. A refusal raises ``ValueError``.
    """
    candidate, _hostname = _parse_outbound_https(url)
    try:
        async with httpx.AsyncClient(
            transport=SSRFSafeAsyncTransport(verify=True),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.get(candidate)  # drift-ignore: maintainer spec fetch, not an RM API route
            await response.aread()
    except SmartsheetRMAPIError as exc:
        raise ValueError(str(exc.detail)) from exc
    return response


class SmartsheetRMClient:
    """Asynchronous HTTP Client for Smartsheet Resource Management API."""

    def __init__(
        self,
        api_token: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_retry_delay: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
        smartsheet_api_token: str | None = None,
    ) -> None:
        """Build a client for ``base_url``, which must pass the HTTPS, SSRF and allowlist checks.

        ``smartsheet_api_token`` is used only when the base URL host is ``GOV_HOST``: Smartsheet
        Gov requires it as ``Authorization: Bearer`` in addition to the RM ``auth`` token. It
        is required there and never sent to any other host.
        """
        self.api_token = api_token.strip()
        self.base_url = _validate_base_url(base_url or DEFAULT_BASE_URL, check_dns=False)
        self.is_gov = (urlparse(self.base_url).hostname or "").lower() == GOV_HOST
        gov_token = (smartsheet_api_token or "").strip()
        if self.is_gov and not gov_token:
            raise ValueError(
                "The Smartsheet Gov endpoint needs a Smartsheet API token as well as the RM token: "
                "set SMARTSHEET_RM_GOV_SMARTSHEET_API_TOKEN or send the x-smartsheet-gov-api-token header."
            )
        self._gov_token = gov_token if self.is_gov else None
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_retry_delay = max_retry_delay
        self._owns_http = http_client is None
        if http_client is not None:
            self._http = http_client
        else:
            transport = SSRFSafeAsyncTransport(verify=True)
            self._http = httpx.AsyncClient(transport=transport, timeout=60.0, follow_redirects=False)

    async def aclose(self) -> None:
        """Close the underlying HTTP transport if owned by this client."""
        if self._owns_http:
            await self._http.aclose()

    async def close(self) -> None:
        """Alias for aclose() to standardize connection cleanup."""
        await self.aclose()

    async def __aenter__(self) -> SmartsheetRMClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    @staticmethod
    def extract_list(container: Any, key: str | None = None) -> list[Any]:
        """Safely extract a list from a polymorphic upstream JSON response or field."""
        if key is not None:
            target = container.get(key) if isinstance(container, dict) else None
        else:
            target = container
        return target if isinstance(target, list) else []

    @staticmethod
    def extract_dict(container: Any, key: str | None = None) -> dict[str, Any]:
        """Safely extract a dictionary from a polymorphic upstream JSON response or field."""
        if key is not None:
            target = container.get(key) if isinstance(container, dict) else None
        else:
            target = container
        return target if isinstance(target, dict) else {}

    def _headers(self) -> dict[str, str]:
        """Headers required by Smartsheet RM (10,000ft) API, plus the Bearer token on Smartsheet Gov."""
        headers = {
            "auth": self.api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"mcp-server-smartsheet-rm/{__version__}",
        }
        if self._gov_token:
            headers["Authorization"] = f"Bearer {self._gov_token}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: JSONValue = None,
        allow_statuses: frozenset[int] | None = None,
    ) -> Response:
        """Execute an HTTP request with exponential backoff on 429 and transient errors."""
        cleaned_path = "/" + path.lstrip("/")
        safe_segments = [quote(seg, safe="") if seg else "" for seg in cleaned_path.split("/")]
        safe_path = "/".join(safe_segments)
        url = f"{self.base_url}{safe_path}"

        clean_params = None
        if params is not None:
            clean_params = {k: v for k, v in params.items() if v is not None}

        for attempt in range(self.max_retries + 1):
            r = await self._http.request(
                method,
                url,
                headers=self._headers(),
                params=clean_params,
                json=json_data,
                timeout=60.0,
                follow_redirects=False,
            )

            if r.status_code != 429:
                if r.status_code >= 400 and not (allow_statuses and r.status_code in allow_statuses):
                    detail: Any = None
                    try:
                        detail = r.json()
                    except Exception:
                        detail = r.text[:500] if r.text else None
                    request_id = r.headers.get("x-request-id") or r.headers.get("request-id")
                    raise SmartsheetRMAPIError(
                        status_code=r.status_code,
                        path=path,
                        method=method,
                        detail=detail,
                        request_id=request_id,
                    )
                return r

            if attempt == self.max_retries:
                raise SmartsheetRMAPIError(
                    status_code=429,
                    path=path,
                    method=method,
                    detail="Rate limit exceeded after max retries",
                    request_id=r.headers.get("x-request-id") or r.headers.get("request-id"),
                )

            retry_after = r.headers.get("Retry-After")
            parsed_delay = self._parse_retry_after(retry_after)
            if parsed_delay is not None:
                delay = parsed_delay * random.uniform(1.0, 1.3)
            else:
                delay = self.base_delay * (2**attempt) * random.uniform(0.5, 1.5)
            delay = min(delay, self.max_retry_delay)
            await asyncio.sleep(delay)

        raise SmartsheetRMAPIError(
            status_code=429, path=path, method=method, detail="Rate limit exceeded"
        )  # pragma: no cover

    @staticmethod
    def _parse_retry_after(header_val: str | None) -> float | None:
        """Parse Retry-After header as integer or float seconds."""
        if not header_val:
            return None
        try:
            val = float(header_val)
            return max(0.0, val)
        except (ValueError, OverflowError):
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # 1. TIME TRACKING & APPROVALS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_time_entries(self, params: dict[str, Any] | None = None) -> Any:
        """List time entries across organization."""
        r = await self._request("GET", "/time_entries", params=params)
        return r.json()

    async def get_time_entry(self, entry_id: int | str) -> Any:
        """Get details for a specific time entry."""
        r = await self._request("GET", f"/time_entries/{entry_id}")
        return r.json()

    async def create_time_entry(self, data: dict[str, Any]) -> Any:
        """Create a new time entry.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/time_entries", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_time_entry(self, entry_id: int | str, data: dict[str, Any]) -> Any:
        """Update an existing time entry.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/time_entries/{entry_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_time_entry(self, entry_id: int | str) -> Any:
        """Delete a time entry.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/time_entries/{entry_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": entry_id}

    async def list_project_time_entries(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List time entries associated with a project."""
        r = await self._request("GET", f"/projects/{project_id}/time_entries", params=params)
        return r.json()

    async def create_project_time_entry(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Create a time entry directly under a project."""
        r = await self._request("POST", f"/projects/{project_id}/time_entries", json_data=data)
        return r.json()

    async def list_user_time_entries(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List time entries logged by a specific user."""
        r = await self._request("GET", f"/users/{user_id}/time_entries", params=params)
        return r.json()

    async def create_user_time_entry(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Create a time entry directly under a user."""
        r = await self._request("POST", f"/users/{user_id}/time_entries", json_data=data)
        return r.json()

    async def update_user_approval_status(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Approve or reject time entries for a user.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/users/{user_id}/time_entries/approval_status", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "updated", "user_id": user_id}

    async def lock_user_timesheet(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Lock or unlock timesheet records for a user up to a given date.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", f"/users/{user_id}/time_entries/lock", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "locked", "user_id": user_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 2. PROJECTS & PHASES
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_projects(self, params: dict[str, Any] | None = None) -> Any:
        """List projects in the organization."""
        r = await self._request("GET", "/projects", params=params)
        return r.json()

    async def get_project(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get details of a single project."""
        r = await self._request("GET", f"/projects/{project_id}", params=params)
        return r.json()

    async def create_project(self, data: dict[str, Any]) -> Any:
        """Create a new project."""
        r = await self._request("POST", "/projects", json_data=data)
        return r.json()

    async def update_project(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Update project details, budget, dates, or state."""
        r = await self._request("PUT", f"/projects/{project_id}", json_data=data)
        return r.json()

    async def delete_project(self, project_id: int | str) -> Any:
        """Delete or archive a project."""
        r = await self._request("DELETE", f"/projects/{project_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": project_id}

    async def list_project_users(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List users associated with or assigned to a project."""
        r = await self._request("GET", f"/projects/{project_id}/users", params=params)
        return r.json()

    async def list_project_phases(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List phases for a given project."""
        r = await self._request("GET", f"/projects/{project_id}/phases", params=params)
        return r.json()

    async def get_project_phase(self, project_id: int | str, phase_id: int | str) -> Any:
        """Get details of a specific project phase.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/projects/{project_id}/phases/{phase_id}"
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_project_phase(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Create a new phase under a project."""
        r = await self._request("POST", f"/projects/{project_id}/phases", json_data=data)
        return r.json()

    async def update_project_phase(self, project_id: int | str, phase_id: int | str, data: dict[str, Any]) -> Any:
        """Update a project phase."""
        r = await self._request("PUT", f"/projects/{project_id}/phases/{phase_id}", json_data=data)
        return r.json()

    async def delete_project_phase(self, project_id: int | str, phase_id: int | str) -> Any:
        """Delete a project phase."""
        r = await self._request(
            "DELETE", f"/projects/{project_id}/phases/{phase_id}", allow_statuses=frozenset({200, 204})
        )
        return r.json() if r.content else {"status": "deleted", "phase_id": phase_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 3. ASSIGNMENTS & SCHEDULING
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_assignments(self, params: dict[str, Any] | None = None) -> Any:
        """List resource scheduling assignments."""
        r = await self._request("GET", "/assignments", params=params)
        return r.json()

    async def get_assignment(self, assignment_id: int | str) -> Any:
        """Get a specific assignment."""
        r = await self._request("GET", f"/assignments/{assignment_id}")
        return r.json()

    async def create_assignment(self, data: dict[str, Any]) -> Any:
        """Create a new resource assignment."""
        r = await self._request("POST", "/assignments", json_data=data)
        return r.json()

    async def update_assignment(self, assignment_id: int | str, data: dict[str, Any]) -> Any:
        """Update an existing resource assignment."""
        r = await self._request("PUT", f"/assignments/{assignment_id}", json_data=data)
        return r.json()

    async def delete_assignment(self, assignment_id: int | str) -> Any:
        """Delete a resource assignment."""
        r = await self._request("DELETE", f"/assignments/{assignment_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": assignment_id}

    async def list_project_assignments(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List assignments for a specific project."""
        r = await self._request("GET", f"/projects/{project_id}/assignments", params=params)
        return r.json()

    async def create_project_assignment(self, project_id: int | str, data: dict[str, Any]) -> Any:
        """Create an assignment directly under a project."""
        r = await self._request("POST", f"/projects/{project_id}/assignments", json_data=data)
        return r.json()

    async def list_user_assignments(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List assignments for a specific user."""
        r = await self._request("GET", f"/users/{user_id}/assignments", params=params)
        return r.json()

    async def create_user_assignment(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Create an assignment directly under a user."""
        r = await self._request("POST", f"/users/{user_id}/assignments", json_data=data)
        return r.json()

    # ═══════════════════════════════════════════════════════════════════════════
    # 4. USERS, ROLES, DISCIPLINES & CAPACITY
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_users(self, params: dict[str, Any] | None = None) -> Any:
        """List users in the organization."""
        r = await self._request("GET", "/users", params=params)
        return r.json()

    async def get_user(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get details for a specific user."""
        r = await self._request("GET", f"/users/{user_id}", params=params)
        return r.json()

    async def create_user(self, data: dict[str, Any]) -> Any:
        """Create a new user profile."""
        r = await self._request("POST", "/users", json_data=data)
        return r.json()

    async def update_user(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Update a user profile."""
        r = await self._request("PUT", f"/users/{user_id}", json_data=data)
        return r.json()

    async def delete_user(self, user_id: int | str) -> Any:
        """Delete / archive a user."""
        r = await self._request("DELETE", f"/users/{user_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": user_id}

    async def list_user_bill_rates(self, user_id: int | str) -> Any:
        """List bill rate tiers for a user.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/users/{user_id}/bill_rates"
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_user_bill_rate(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Add a bill rate tier for a user.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", f"/users/{user_id}/bill_rates", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def get_user_availability(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get user availability and scheduled capacity.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/users/{user_id}/availability", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def get_user_utilization(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """Get user utilization metrics.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/users/{user_id}/utilization", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def list_roles(self, params: dict[str, Any] | None = None) -> Any:
        """List configured user roles."""
        r = await self._request("GET", "/roles", params=params)
        return r.json()

    async def create_role(self, data: dict[str, Any]) -> Any:
        """Create a new role.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/roles", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_role(self, role_id: int | str, data: dict[str, Any]) -> Any:
        """Update a role.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/roles/{role_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_role(self, role_id: int | str) -> Any:
        """Delete a role.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/roles/{role_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": role_id}

    async def list_disciplines(self, params: dict[str, Any] | None = None) -> Any:
        """List configured disciplines."""
        r = await self._request("GET", "/disciplines", params=params)
        return r.json()

    async def create_discipline(self, data: dict[str, Any]) -> Any:
        """Create a new discipline.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/disciplines", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_discipline(self, discipline_id: int | str, data: dict[str, Any]) -> Any:
        """Update a discipline.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/disciplines/{discipline_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_discipline(self, discipline_id: int | str) -> Any:
        """Delete a discipline.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/disciplines/{discipline_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": discipline_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 5. CLIENTS & CONTACTS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_clients(self, params: dict[str, Any] | None = None) -> Any:
        """List clients.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", "/clients", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def get_client(self, client_id: int | str) -> Any:
        """Get details for a client.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/clients/{client_id}"
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_client(self, data: dict[str, Any]) -> Any:
        """Create a new client.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/clients", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_client(self, client_id: int | str, data: dict[str, Any]) -> Any:
        """Update client details.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/clients/{client_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_client(self, client_id: int | str) -> Any:
        """Delete a client.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/clients/{client_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": client_id}

    async def list_client_contacts(self, client_id: int | str) -> Any:
        """List contacts for a client.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/clients/{client_id}/contacts"
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_client_contact(self, client_id: int | str, data: dict[str, Any]) -> Any:
        """Add a contact for a client.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", f"/clients/{client_id}/contacts", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_client_contact(self, client_id: int | str, contact_id: int | str) -> Any:
        """Delete a client contact.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/clients/{client_id}/contacts/{contact_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "contact_id": contact_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 6. LEAVES & HOLIDAYS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_leave_types(self, params: dict[str, Any] | None = None) -> Any:
        """List leave types (Vacation, Sick, etc.)."""
        r = await self._request("GET", "/leave_types", params=params)
        return r.json()

    async def get_leave_type(self, leave_type_id: int | str) -> Any:
        """Get leave type details."""
        r = await self._request("GET", f"/leave_types/{leave_type_id}")
        return r.json()

    async def create_leave_type(self, data: dict[str, Any]) -> Any:
        """Create a new leave type.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/leave_types", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_leave_type(self, leave_type_id: int | str, data: dict[str, Any]) -> Any:
        """Update a leave type.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/leave_types/{leave_type_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_leave_type(self, leave_type_id: int | str) -> Any:
        """Delete a leave type.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/leave_types/{leave_type_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": leave_type_id}

    async def list_holidays(self, params: dict[str, Any] | None = None) -> Any:
        """List regional and company holidays."""
        r = await self._request("GET", "/holidays", params=params)
        return r.json()

    async def get_holiday(self, holiday_id: int | str) -> Any:
        """Get holiday details.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/holidays/{holiday_id}"
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_holiday(self, data: dict[str, Any]) -> Any:
        """Create a new holiday.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/holidays", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_holiday(self, holiday_id: int | str, data: dict[str, Any]) -> Any:
        """Update a holiday.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/holidays/{holiday_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_holiday(self, holiday_id: int | str) -> Any:
        """Delete a holiday.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/holidays/{holiday_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": holiday_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 7. EXPENSES & CATEGORIES
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_expenses(self, params: dict[str, Any] | None = None) -> Any:
        """List logged expenses.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", "/expenses", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def get_expense(self, expense_id: int | str) -> Any:
        """Get details for an expense.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/expenses/{expense_id}"
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_expense(self, data: dict[str, Any]) -> Any:
        """Log a new expense.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/expenses", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def update_expense(self, expense_id: int | str, data: dict[str, Any]) -> Any:
        """Update an expense.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", f"/expenses/{expense_id}", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_expense(self, expense_id: int | str) -> Any:
        """Delete an expense.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/expenses/{expense_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": expense_id}

    async def list_project_expenses(self, project_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List expenses for a project.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/projects/{project_id}/expenses", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def list_user_expenses(self, user_id: int | str, params: dict[str, Any] | None = None) -> Any:
        """List expenses submitted by a user.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", f"/users/{user_id}/expenses", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def list_expense_categories(self, params: dict[str, Any] | None = None) -> Any:
        """List expense categories.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", "/expense_categories", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_expense_category(self, data: dict[str, Any]) -> Any:
        """Create an expense category.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/expense_categories", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_expense_category(self, category_id: int | str) -> Any:
        """Delete an expense category.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/expense_categories/{category_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": category_id}

    # ═══════════════════════════════════════════════════════════════════════════
    # 8. TAGS & CUSTOM FIELDS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_tags(self, params: dict[str, Any] | None = None) -> Any:
        """List tags.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", "/tags", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def create_tag(self, data: dict[str, Any]) -> Any:
        """Create a tag.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "POST", "/tags", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def delete_tag(self, tag_id: int | str) -> Any:
        """Delete a tag.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "DELETE", f"/tags/{tag_id}", allow_statuses=frozenset({200, 204})
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json() if r.content else {"status": "deleted", "id": tag_id}

    async def list_custom_fields(self, params: dict[str, Any] | None = None) -> Any:
        """List custom field definitions."""
        r = await self._request("GET", "/custom_fields", params=params)
        return r.json()

    async def get_custom_field(self, custom_field_id: int | str) -> Any:
        """Get a custom field definition."""
        r = await self._request("GET", f"/custom_fields/{custom_field_id}")
        return r.json()

    async def create_custom_field(self, data: dict[str, Any]) -> Any:
        """Create a custom field definition."""
        r = await self._request("POST", "/custom_fields", json_data=data)
        return r.json()

    async def update_custom_field(self, custom_field_id: int | str, data: dict[str, Any]) -> Any:
        """Update a custom field definition."""
        r = await self._request("PUT", f"/custom_fields/{custom_field_id}", json_data=data)
        return r.json()

    async def delete_custom_field(self, custom_field_id: int | str) -> Any:
        """Delete a custom field definition."""
        r = await self._request("DELETE", f"/custom_fields/{custom_field_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": custom_field_id}

    async def list_custom_field_values(self, params: dict[str, Any] | None = None) -> Any:
        """List custom field values.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "GET", "/custom_field_values", params=params
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    async def set_custom_field_values(self, data: dict[str, Any]) -> Any:
        """Set or update custom field values.

        Undocumented route: not in the RM API reference or the archived 10,000ft API docs.
        """
        r = await self._request(
            "PUT", "/custom_field_values", json_data=data
        )  # drift-ignore: undocumented route, kept on purpose (README: Undocumented routes)
        return r.json()

    # ═══════════════════════════════════════════════════════════════════════════
    # 9. APPROVALS, STATUS OPTIONS, USER STATUSES & PLACEHOLDERS
    # ═══════════════════════════════════════════════════════════════════════════

    async def list_approvals(self, params: dict[str, Any] | None = None) -> Any:
        """List submitted approvals across organization."""
        r = await self._request("GET", "/approvals", params=params)
        return r.json()

    async def create_approval(self, data: dict[str, Any]) -> Any:
        """Submit or approve approvable records."""
        r = await self._request("POST", "/approvals", json_data=data)
        return r.json()

    async def delete_approval(self, approval_id: int | str) -> Any:
        """Delete a pending approval record."""
        r = await self._request("DELETE", f"/approvals/{approval_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": approval_id}

    async def list_status_options(self, params: dict[str, Any] | None = None) -> Any:
        """List assignment work status options."""
        r = await self._request("GET", "/status_options", params=params)
        return r.json()

    async def get_user_statuses(self, user_id: int | str) -> Any:
        """Get work statuses for a user (ITO, WFH, SIC, OOO, VAC, OOF)."""
        r = await self._request("GET", f"/users/{user_id}/statuses")
        return r.json()

    async def set_user_status(self, user_id: int | str, data: dict[str, Any]) -> Any:
        """Set current status for a user."""
        r = await self._request("POST", f"/users/{user_id}/statuses", json_data=data)
        return r.json()

    async def list_placeholder_resources(self, params: dict[str, Any] | None = None) -> Any:
        """List placeholder resources."""
        r = await self._request("GET", "/placeholder_resources", params=params)
        return r.json()

    async def create_placeholder_resource(self, data: dict[str, Any]) -> Any:
        """Create a placeholder resource."""
        r = await self._request("POST", "/placeholder_resources", json_data=data)
        return r.json()

    async def delete_placeholder_resource(self, placeholder_id: int | str) -> Any:
        """Delete a placeholder resource."""
        r = await self._request(
            "DELETE", f"/placeholder_resources/{placeholder_id}", allow_statuses=frozenset({200, 204})
        )
        return r.json() if r.content else {"status": "deleted", "id": placeholder_id}

    async def list_subtasks(self, project_id: int | str, assignment_id: int | str) -> Any:
        """List subtasks for an assignment."""
        r = await self._request("GET", f"/projects/{project_id}/assignments/{assignment_id}/subtasks")
        return r.json()

    async def create_subtask(self, project_id: int | str, assignment_id: int | str, data: dict[str, Any]) -> Any:
        """Create a subtask on an assignment."""
        r = await self._request("POST", f"/projects/{project_id}/assignments/{assignment_id}/subtasks", json_data=data)
        return r.json()

    async def delete_subtask(self, project_id: int | str, assignment_id: int | str, subtask_id: int | str) -> Any:
        """Delete a subtask."""
        r = await self._request(
            "DELETE",
            f"/projects/{project_id}/assignments/{assignment_id}/subtasks/{subtask_id}",
            allow_statuses=frozenset({200, 204}),
        )
        return r.json() if r.content else {"status": "deleted", "id": subtask_id}

    async def get_report_rows(self, data: dict[str, Any]) -> Any:
        """Generate and retrieve report rows."""
        r = await self._request("POST", "/reports/rows", json_data=data)
        return r.json()

    async def get_report_totals(self, data: dict[str, Any]) -> Any:
        """Generate and retrieve report aggregated totals."""
        r = await self._request("POST", "/reports/totals", json_data=data)
        return r.json()

    async def list_webhooks(self, params: dict[str, Any] | None = None) -> Any:
        """List configured webhooks."""
        r = await self._request("GET", "/webhooks", params=params)
        return r.json()

    async def create_webhook(self, data: dict[str, Any]) -> Any:
        """Create a webhook."""
        r = await self._request("POST", "/webhooks", json_data=data)
        return r.json()

    async def delete_webhook(self, webhook_id: int | str) -> Any:
        """Delete a webhook."""
        r = await self._request("DELETE", f"/webhooks/{webhook_id}", allow_statuses=frozenset({200, 204}))
        return r.json() if r.content else {"status": "deleted", "id": webhook_id}
