"""Orderbook staleness guard — skip markets with cached books older than max_age_seconds.

When the WS orderbook cache provides a snapshot, its timestamp is checked.
If it is older than settings.ws_orderbook_max_age_seconds, fall back to a
fresh HTTP fetch rather than trading on stale data.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.polymarket.ws_orderbook import OrderBookCache
from bot.storage.db import open_db


def _market(cid: str) -> Market:
    return Market(
        condition_id=cid, question="Q?", yes_token=f"y_{cid}", no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=1000.0, liquidity=1000.0, closed=False, raw={},
    )


def _fresh_ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 10)], asks=[(0.52, 10)],
                             timestamp=int(time.time()))


def _stale_ob(token_id: str, age_seconds: int = 600) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 10)], asks=[(0.52, 10)],
                             timestamp=int(time.time()) - age_seconds)


async def test_fresh_cache_book_used_without_http_fetch(tmp_path):
    """A cached book within max_age_seconds is used directly; HTTP get_orderbook not called."""
    from bot.orchestrator import run_once

    fetched_via_http: list[str] = []

    async def fake_ob(token_id: str) -> OrderBookSnapshot:
        fetched_via_http.append(token_id)
        return _fresh_ob(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("a")])
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    cache = OrderBookCache()
    cache.set(_fresh_ob("y_a"))  # 0 seconds old — fresh

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", ws_orderbook_max_age_seconds=300)
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=5, book_cache=cache)

    assert "y_a" not in fetched_via_http, "fresh cached book should not trigger HTTP fetch"
    await conn.close()


async def test_stale_cache_book_triggers_http_fallback(tmp_path):
    """A cached book older than max_age_seconds causes a fresh HTTP fetch."""
    from bot.orchestrator import run_once

    fetched_via_http: list[str] = []

    async def fake_ob(token_id: str) -> OrderBookSnapshot:
        fetched_via_http.append(token_id)
        return _fresh_ob(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("b")])
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    cache = OrderBookCache()
    cache.set(_stale_ob("y_b", age_seconds=600))  # 10 min old — stale

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", ws_orderbook_max_age_seconds=300)
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=5, book_cache=cache)

    assert "y_b" in fetched_via_http, "stale cached book must trigger HTTP fallback"
    await conn.close()


async def test_ws_orderbook_max_age_env_override(monkeypatch):
    """WS_ORDERBOOK_MAX_AGE_SECONDS env var is wired into load_settings."""
    from bot.config import load_settings
    monkeypatch.setenv("WS_ORDERBOOK_MAX_AGE_SECONDS", "120")
    s = load_settings()
    assert s.ws_orderbook_max_age_seconds == 120
