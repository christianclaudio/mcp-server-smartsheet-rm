"""Session guard: CI must run the Code Mode tests, never skip them (template v1.6.0)."""

import importlib.util
import ipaddress
import os
import socket
from typing import Any

import pytest

# The module-level ``smartsheet_rm_mcp.server.mcp`` reads the token when it is
# imported, which happens at collection, before any fixture runs. Clear the
# shell's values first.
for _name in (
    "SMARTSHEET_RM_MCP_AUTH_TOKEN",
    "SMARTSHEET_RM_MCP_ALLOW_UNAUTHENTICATED_BIND",
):
    os.environ.pop(_name, None)


@pytest.fixture(autouse=True)
def _clear_smartsheet_rm_auth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a token or opt-in already set in the shell out of every test."""
    monkeypatch.delenv("SMARTSHEET_RM_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("SMARTSHEET_RM_MCP_ALLOW_UNAUTHENTICATED_BIND", raising=False)
    # The module-level ``mcp`` was built at import, so a shell token may already
    # be attached to it even after the env vars are cleared.
    import smartsheet_rm_mcp.server as server_mod

    if getattr(server_mod, "mcp", None) is not None:
        monkeypatch.setattr(server_mod.mcp, "auth", None)


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(address: object) -> bool:
    """True for an AF_UNIX path or a loopback (host, port) tuple; anything else is a real host."""
    if isinstance(address, (str, bytes)):
        return True
    if isinstance(address, tuple) and address:
        host = str(address[0])
        return host in _LOOPBACK_HOSTS or host.startswith("127.")
    return False


@pytest.fixture(autouse=True)
def _no_real_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that opens a socket to, or looks up, a non-loopback host.

    Every outbound path must go through the offline fake network (``tests/fake_network.py``),
    respx or a mock transport. Only tests marked ``e2e`` (deselected by default) may dial out.
    """
    if request.node.get_closest_marker("e2e") is not None:
        return
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_connect(self: socket.socket, address: Any) -> None:
        if not _is_loopback(address):
            raise AssertionError(f"test tried a real network connect to {address!r}")
        real_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        if not _is_loopback(address):
            raise AssertionError(f"test tried a real network connect to {address!r}")
        return real_connect_ex(self, address)

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        raise AssertionError(f"test tried a real network connect to {address!r}")

    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        name = host.decode() if isinstance(host, bytes) else host
        if name is not None and not _is_loopback((name,)):
            try:
                ipaddress.ip_address(name)
            except ValueError:
                raise AssertionError(f"test tried a real DNS lookup of {name!r}") from None
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)


def code_mode_ci_guard_error(environ: dict[str, str] | None = None) -> str | None:
    """Return an error when CI runs without the Code Mode sandbox, else None.

    Code Mode tests ``pytest.importorskip("pydantic_monty")`` so a local run without the
    ``code-mode`` extra still passes. In CI (``CI`` set, as on GitHub Actions) a missing
    sandbox would turn those tests into silent skips, so the session aborts instead.
    """
    env = os.environ if environ is None else environ
    if not env.get("CI"):
        return None
    if importlib.util.find_spec("pydantic_monty") is not None:
        return None
    return (
        "CI is set but pydantic_monty is not installed: install the code-mode extra "
        "(uv sync --locked --extra dev --extra code-mode) so Code Mode tests run, not skip."
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Abort a CI session that would silently skip the Code Mode tests."""
    error = code_mode_ci_guard_error()
    if error is not None:
        pytest.exit(error, returncode=3)
