"""Momentum signal tracking in OrderBookCache — TDD RED phase."""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.polymarket.ws_orderbook import OrderBookCache


def _book_event(token_id: str, *, bid: float = 0.52, ask: float = 0.55, ts: int | None = None) -> dict:
    return {
        "event_type": "book",
        "asset_id": token_id,
        "bids": [{"price": str(bid), "size": "100"}],
        "asks": [{"price": str(ask), "size": "50"}],
        "timestamp": str(ts or int(time.time())),
    }


# ---------------------------------------------------------------------------
# OrderBookCache.momentum()
# ---------------------------------------------------------------------------


def test_momentum_returns_zero_with_no_history():
    cache = OrderBookCache()
    assert cache.momentum("tok1", lookback_seconds=3600) == 0.0


def test_momentum_returns_zero_with_single_update():
    cache = OrderBookCache()
    cache.update(_book_event("tok1", bid=0.50, ask=0.60))
    assert cache.momentum("tok1", lookback_seconds=3600) == 0.0


def test_momentum_positive_when_price_rose():
    cache = OrderBookCache()
    now = int(time.time())
    # First update 2 hours ago: mid = 0.45
    cache.update(_book_event("tok1", bid=0.42, ask=0.48, ts=now - 7200))
    # Current update: mid = 0.55
    cache.update(_book_event("tok1", bid=0.52, ask=0.58, ts=now))
    mom = cache.momentum("tok1", lookback_seconds=3600)
    assert mom > 0.0


def test_momentum_negative_when_price_fell():
    cache = OrderBookCache()
    now = int(time.time())
    cache.update(_book_event("tok1", bid=0.72, ask=0.78, ts=now - 7200))
    cache.update(_book_event("tok1", bid=0.42, ask=0.48, ts=now))
    mom = cache.momentum("tok1", lookback_seconds=3600)
    assert mom < 0.0


def test_momentum_approximately_correct():
    cache = OrderBookCache()
    now = int(time.time())
    # mid 2h ago = 0.50 (bid=0.48, ask=0.52)
    cache.update(_book_event("tok1", bid=0.48, ask=0.52, ts=now - 7200))
    # mid now = 0.60 (bid=0.58, ask=0.62)
    cache.update(_book_event("tok1", bid=0.58, ask=0.62, ts=now))
    mom = cache.momentum("tok1", lookback_seconds=3600)
    # (0.60 - 0.50) / 0.50 = 0.20
    assert mom == pytest.approx(0.20, abs=0.01)


def test_momentum_returns_zero_when_lookback_exceeds_history():
    cache = OrderBookCache()
    now = int(time.time())
    # Two updates 30 minutes apart — both within 1h window
    cache.update(_book_event("tok1", bid=0.48, ask=0.52, ts=now - 1800))
    cache.update(_book_event("tok1", bid=0.52, ask=0.58, ts=now))
    # Asking for 2h momentum but history only goes back 30m
    mom = cache.momentum("tok1", lookback_seconds=7200)
    assert mom == 0.0


def test_momentum_prunes_entries_older_than_25h():
    cache = OrderBookCache()
    now = int(time.time())
    # Insert an entry 26 hours ago
    cache.update(_book_event("tok1", bid=0.30, ask=0.40, ts=now - 93_600))
    # Insert current entry
    cache.update(_book_event("tok1", bid=0.52, ask=0.58, ts=now))
    # History should not include the 26h-old entry
    history = cache._price_history.get("tok1", [])
    assert all(ts >= now - 90_000 for ts, _ in history)


# ---------------------------------------------------------------------------
# _candidates_from_markets populates momentum from cache history
# ---------------------------------------------------------------------------


async def test_candidates_populate_momentum_from_cache():

    from bot.orchestrator import _candidates_from_markets
    from bot.polymarket.client import Market

    now = int(time.time())
    market = Market(
        condition_id="c1", question="Q?", yes_token="tok-mom", no_token="tok-no",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=5000, liquidity=2000, closed=False, raw={},
    )

    cache = OrderBookCache()
    # Insert a past price so momentum can be computed
    cache.update(_book_event("tok-mom", bid=0.38, ask=0.42, ts=now - 7200))
    # Current snapshot (higher price)
    cache.update(_book_event("tok-mom", bid=0.52, ask=0.58, ts=now))

    client = MagicMock()
    client.get_orderbook = AsyncMock()

    candidates = await _candidates_from_markets(client, [market], book_cache=cache)

    assert len(candidates) == 1
    assert candidates[0].momentum_1h > 0.0  # price rose
    client.get_orderbook.assert_not_called()


async def test_candidates_momentum_zero_when_only_one_cache_entry():

    from bot.orchestrator import _candidates_from_markets
    from bot.polymarket.client import Market

    market = Market(
        condition_id="c2", question="Q?", yes_token="tok-single", no_token="tok-no",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=5000, liquidity=2000, closed=False, raw={},
    )

    cache = OrderBookCache()
    cache.update(_book_event("tok-single", bid=0.52, ask=0.58))

    client = MagicMock()
    client.get_orderbook = AsyncMock()

    candidates = await _candidates_from_markets(client, [market], book_cache=cache)
    assert candidates[0].momentum_1h == pytest.approx(0.0)
    assert candidates[0].momentum_24h == pytest.approx(0.0)


async def test_candidates_momentum_zero_when_no_cache():

    from bot.orchestrator import _candidates_from_markets
    from bot.polymarket.client import Market, OrderBookSnapshot

    market = Market(
        condition_id="c3", question="Q?", yes_token="tok-http", no_token="tok-no",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=5000, liquidity=2000, closed=False, raw={},
    )
    http_snap = OrderBookSnapshot(token_id="tok-http", asks=[(0.55, 100)], bids=[(0.52, 100)], timestamp=0)
    client = MagicMock()
    client.get_orderbook = AsyncMock(return_value=http_snap)

    candidates = await _candidates_from_markets(client, [market], book_cache=None)
    assert candidates[0].momentum_1h == pytest.approx(0.0)
    assert candidates[0].momentum_24h == pytest.approx(0.0)
