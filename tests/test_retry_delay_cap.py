"""``max_retry_delay`` caps only the computed backoff; a server-sent Retry-After is honored."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smartsheet_rm_mcp.client import SmartsheetRMClient


def _resp(status: int, headers: dict[str, str]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = headers
    resp.json.return_value = {"data": []}
    resp.content = b'{"data": []}'
    return resp


async def _sleep_after_one_429(headers: dict[str, str]) -> float:
    http = AsyncMock()
    http.request.side_effect = [_resp(429, headers), _resp(200, {})]
    client = SmartsheetRMClient("token", max_retries=1, base_delay=10.0, max_retry_delay=1.0, http_client=http)
    sleep = AsyncMock()
    with (
        patch("smartsheet_rm_mcp.client.asyncio.sleep", sleep),
        patch("smartsheet_rm_mcp.client.random.uniform", return_value=1.0),
    ):
        assert await client.list_projects() == {"data": []}
    sleep.assert_awaited_once()
    return float(sleep.await_args.args[0])


@pytest.mark.asyncio
async def test_server_retry_after_is_not_capped() -> None:
    assert await _sleep_after_one_429({"Retry-After": "5"}) == 5.0


@pytest.mark.asyncio
async def test_computed_backoff_is_capped() -> None:
    assert await _sleep_after_one_429({}) == 1.0
