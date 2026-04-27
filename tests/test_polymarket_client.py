"""Tests for hardened Polymarket CLOB client: retry, paging, active filtering."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from bot.polymarket.client import PolymarketClient, _parse_clob_token_ids


def _ok_response(data: list | dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=data)
    return resp


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            f"error {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=MagicMock(spec=httpx.Response),
        )
    )
    resp.json = MagicMock(return_value={})
    return resp


def _market_record(condition_id: str = "cond-1") -> dict:
    return {
        "conditionId": condition_id,
        "question": f"Market {condition_id}",
        "clobTokenIds": json.dumps([f"yes-{condition_id}", f"no-{condition_id}"]),
        "outcomes": json.dumps(["Yes", "No"]),
        "endDate": "2025-12-31",
        "volume24hr": "1000",
        "liquidity": "500",
        "closed": False,
    }


# ---------------------------------------------------------------------------
# Retry on transport error
# ---------------------------------------------------------------------------


async def test_list_markets_retries_on_transport_error():
    """Client retries up to max_retries on transient network failures."""
    client = PolymarketClient(gamma_host="http://fake.test")
    call_count = 0

    async def fake_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise httpx.TransportError("connection reset")
        return _ok_response([_market_record()])

    client._http.get = fake_get
    with patch("asyncio.sleep", AsyncMock()):
        result = await client.list_markets(limit=10)

    assert len(result) == 1
    assert call_count == 3


async def test_list_markets_raises_after_max_retries_exhausted():
    """Client re-raises the transport error after all retry attempts fail."""
    client = PolymarketClient(gamma_host="http://fake.test")

    async def always_fails(url, **kwargs):
        raise httpx.TransportError("network gone")

    client._http.get = always_fails
    with patch("asyncio.sleep", AsyncMock()), pytest.raises(httpx.TransportError):
        await client.list_markets(limit=10)


async def test_list_markets_retries_on_5xx_response():
    """Client retries once after a server error, then returns the successful result."""
    client = PolymarketClient(gamma_host="http://fake.test")
    call_count = 0

    async def fake_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _error_response(503)
        return _ok_response([_market_record()])

    client._http.get = fake_get
    with patch("asyncio.sleep", AsyncMock()):
        result = await client.list_markets(limit=10)

    assert len(result) == 1
    assert call_count == 2


# ---------------------------------------------------------------------------
# Offset-based pagination
# ---------------------------------------------------------------------------


async def test_list_markets_fetches_second_page_when_first_page_is_full():
    """When the first page is at the limit, a second page is fetched."""
    client = PolymarketClient(gamma_host="http://fake.test")
    pages_fetched: list[int] = []

    async def fake_get(url, params=None, **kwargs):
        offset = int((params or {}).get("offset", 0))
        pages_fetched.append(offset)
        if offset == 0:
            return _ok_response([_market_record(f"c{i}") for i in range(2)])
        return _ok_response([_market_record("page2")])

    client._http.get = fake_get
    result = await client.list_markets(limit=2)

    assert pages_fetched == [0, 2]
    assert len(result) == 3


async def test_list_markets_stops_pagination_when_page_is_not_full():
    """When a page returns fewer items than the limit, no further pages are fetched."""
    client = PolymarketClient(gamma_host="http://fake.test")
    call_count = 0

    async def fake_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        return _ok_response([_market_record()])  # 1 < limit=10

    client._http.get = fake_get
    result = await client.list_markets(limit=10)

    assert call_count == 1
    assert len(result) == 1


async def test_list_markets_respects_max_pages_cap():
    """Pagination stops at max_pages even if every page is full."""
    client = PolymarketClient(gamma_host="http://fake.test")
    call_count = 0

    async def fake_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        return _ok_response([_market_record(f"c{call_count}")] * 2)  # always full

    client._http.get = fake_get
    result = await client.list_markets(limit=2, max_pages=2)

    assert call_count == 2
    assert len(result) == 4


# ---------------------------------------------------------------------------
# Active-only filtering correctness
# ---------------------------------------------------------------------------


async def test_list_markets_active_only_true_sends_closed_false_and_active_true():
    """active_only=True sends closed=false and active=true parameters."""
    client = PolymarketClient(gamma_host="http://fake.test")
    captured: list[dict] = []

    async def fake_get(url, params=None, **kwargs):
        captured.append(dict(params or {}))
        return _ok_response([_market_record()])

    client._http.get = fake_get
    await client.list_markets(limit=10, active_only=True)

    assert captured[0].get("closed") == "false"
    assert captured[0].get("active") == "true"


async def test_list_markets_active_only_false_does_not_send_closed_true():
    """active_only=False should show all markets, not filter to only closed ones."""
    client = PolymarketClient(gamma_host="http://fake.test")
    captured: list[dict] = []

    async def fake_get(url, params=None, **kwargs):
        captured.append(dict(params or {}))
        return _ok_response([_market_record()])

    client._http.get = fake_get
    await client.list_markets(limit=10, active_only=False)

    assert "active" not in captured[0], "active filter must not be set when not active_only"
    assert captured[0].get("closed") != "true", (
        "active_only=False must not filter to closed-only markets"
    )


# ---------------------------------------------------------------------------
# Parse-failure tolerance
# ---------------------------------------------------------------------------


async def test_list_markets_skips_market_missing_token_ids():
    """Markets with no clobTokenIds are silently skipped; others are returned."""
    client = PolymarketClient(gamma_host="http://fake.test")
    bad = {"conditionId": "bad", "question": "Q?"}
    good = _market_record("good")

    async def fake_get(url, **kwargs):
        return _ok_response([bad, good])

    client._http.get = fake_get
    result = await client.list_markets(limit=10)

    assert len(result) == 1
    assert result[0].condition_id == "good"


# ---------------------------------------------------------------------------
# get_orderbook retry
# ---------------------------------------------------------------------------


async def test_get_orderbook_retries_on_transport_error():
    """get_orderbook also retries on transport failures."""
    client = PolymarketClient(host="http://fake.test")
    call_count = 0

    async def fake_get(url, params=None, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise httpx.TransportError("timeout")
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"asks": [{"price": "0.55", "size": "100"}], "bids": [], "timestamp": 1})
        return resp

    client._http.get = fake_get
    with patch("asyncio.sleep", AsyncMock()):
        book = await client.get_orderbook("tok-1")

    assert book.best_ask == pytest.approx(0.55)
    assert call_count == 2


# ---------------------------------------------------------------------------
# _parse_clob_token_ids unit tests
# ---------------------------------------------------------------------------


def test_parse_clob_token_ids_maps_yes_no_by_label():
    m = {
        "clobTokenIds": json.dumps(["yes-tok", "no-tok"]),
        "outcomes": json.dumps(["Yes", "No"]),
    }
    assert _parse_clob_token_ids(m) == ("yes-tok", "no-tok")


def test_parse_clob_token_ids_falls_back_to_position_order():
    m = {
        "clobTokenIds": json.dumps(["tok-a", "tok-b"]),
        "outcomes": json.dumps(["Single"]),
    }
    assert _parse_clob_token_ids(m) == ("tok-a", "tok-b")


def test_parse_clob_token_ids_returns_none_when_missing():
    assert _parse_clob_token_ids({}) is None


def test_parse_clob_token_ids_returns_none_when_fewer_than_two_tokens():
    assert _parse_clob_token_ids({"clobTokenIds": json.dumps(["only-one"])}) is None
