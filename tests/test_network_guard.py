"""The autouse guard in conftest.py stops any test from dialing a real host."""

from __future__ import annotations

import socket

import httpx
import pytest


def test_guard_blocks_a_raw_socket_connect() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(AssertionError, match="real network connect"):
            sock.connect(("192.0.2.1", 443))
        with pytest.raises(AssertionError, match="real network connect"):
            sock.connect_ex(("192.0.2.1", 443))
    with pytest.raises(AssertionError, match="real network connect"):
        socket.create_connection(("192.0.2.1", 443))
    with pytest.raises(AssertionError, match="real DNS lookup of 'api.rm.smartsheet.com'"):
        socket.getaddrinfo("api.rm.smartsheet.com", 443)
    with pytest.raises(AssertionError, match="real DNS lookup"):
        socket.getaddrinfo(b"api.rm.smartsheet.com", 443)
    # Literals and loopback are not a forbidden lookup. They resolve locally when the
    # resolver answers; a resolver refusal is not the guard classifying them as real DNS.
    for host in ("192.0.2.1", "localhost"):
        try:
            socket.getaddrinfo(host, 443)
        except OSError as exc:
            assert "real DNS lookup" not in str(exc)
            assert "real network connect" not in str(exc)


@pytest.mark.asyncio
async def test_guard_blocks_an_unmocked_httpx_request() -> None:
    """An unmocked async request (anyio under httpcore) is stopped before it reaches the network."""
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(BaseException) as caught:
            await client.get("https://192.0.2.1/x")
    # anyio may wrap the guard's AssertionError in an exception group.
    pending: list[BaseException] = [caught.value]
    messages: list[str] = []
    while pending:
        exc = pending.pop()
        messages.append(str(exc))
        pending.extend(getattr(exc, "exceptions", ()))
    assert "test tried a real network connect to ('192.0.2.1', 443)" in messages
