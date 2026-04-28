"""Tests for WebSocket orderbook cache runtime integration — TDD RED phase."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.polymarket.client import OrderBookSnapshot
from bot.polymarket.ws_orderbook import OrderBookCache

# ---------------------------------------------------------------------------
# OrderBookCache unit tests
# ---------------------------------------------------------------------------


def _book_event(token_id: str, *, bid: float = 0.52, ask: float = 0.55, key: str = "asset_id") -> dict:
    return {
        "event_type": "book",
        key: token_id,
        "bids": [{"price": str(bid), "size": "100"}],
        "asks": [{"price": str(ask), "size": "50"}],
        "timestamp": "1700000000",
    }


def test_cache_stores_snapshot_from_book_event_with_asset_id_key():
    cache = OrderBookCache()
    cache.update(_book_event("tok1", bid=0.52, ask=0.55))
    snap = cache.get("tok1")
    assert snap is not None
    assert snap.best_bid == pytest.approx(0.52)
    assert snap.best_ask == pytest.approx(0.55)


def test_cache_stores_snapshot_from_book_event_with_market_key():
    cache = OrderBookCache()
    cache.update(_book_event("tok2", bid=0.48, ask=0.51, key="market"))
    snap = cache.get("tok2")
    assert snap is not None
    assert snap.best_bid == pytest.approx(0.48)


def test_cache_returns_none_for_unknown_token():
    cache = OrderBookCache()
    assert cache.get("does-not-exist") is None


def test_cache_ignores_non_book_event_types():
    cache = OrderBookCache()
    cache.update({"event_type": "price_change", "asset_id": "tok3", "changes": []})
    assert cache.get("tok3") is None


def test_cache_latest_update_overwrites_previous():
    cache = OrderBookCache()
    cache.update(_book_event("tok4", bid=0.50, ask=0.60))
    cache.update(_book_event("tok4", bid=0.53, ask=0.56))
    snap = cache.get("tok4")
    assert snap.best_bid == pytest.approx(0.53)
    assert snap.best_ask == pytest.approx(0.56)


def test_cache_snapshot_has_correct_token_id():
    cache = OrderBookCache()
    cache.update(_book_event("tok5"))
    assert cache.get("tok5").token_id == "tok5"


def test_cache_empty_bids_and_asks_stored():
    cache = OrderBookCache()
    cache.update({"event_type": "book", "asset_id": "tok6", "bids": [], "asks": [], "timestamp": "0"})
    snap = cache.get("tok6")
    assert snap is not None
    assert snap.best_bid is None
    assert snap.best_ask is None


# ---------------------------------------------------------------------------
# OrderBookCache.run — async queue consumption
# ---------------------------------------------------------------------------


async def test_cache_run_processes_events_from_queue():
    cache = OrderBookCache()
    q: asyncio.Queue[dict] = asyncio.Queue()
    await q.put(_book_event("tokA"))

    task = asyncio.create_task(cache.run(q))
    # Let the loop tick to process the event
    await asyncio.sleep(0.05)
    cache.stop()
    await task

    assert cache.get("tokA") is not None


async def test_cache_run_stops_when_stop_is_called():
    cache = OrderBookCache()
    q: asyncio.Queue[dict] = asyncio.Queue()
    cache.stop()
    task = asyncio.create_task(cache.run(q))
    await asyncio.wait_for(task, timeout=1.0)  # should exit quickly


# ---------------------------------------------------------------------------
# Orchestrator: _candidates_from_markets uses cache
# ---------------------------------------------------------------------------


async def test_candidates_uses_cache_hit_instead_of_http():
    """When the cache has a snapshot for a token, get_orderbook must NOT be called."""
    from datetime import UTC, datetime, timedelta

    from bot.orchestrator import _candidates_from_markets
    from bot.polymarket.client import Market

    market = Market(
        condition_id="c1",
        question="Q?",
        yes_token="tok-yes",
        no_token="tok-no",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=5000,
        liquidity=2000,
        closed=False,
        raw={},
    )

    cache = OrderBookCache()
    cache.update(_book_event("tok-yes", bid=0.52, ask=0.55))

    client = MagicMock()
    client.get_orderbook = AsyncMock()

    candidates = await _candidates_from_markets(client, [market], book_cache=cache)

    assert len(candidates) == 1
    assert candidates[0].mid_price == pytest.approx((0.52 + 0.55) / 2)
    client.get_orderbook.assert_not_called()


async def test_candidates_falls_back_to_http_on_cache_miss():
    """When cache has no snapshot for a token, get_orderbook IS called."""
    from datetime import UTC, datetime, timedelta

    from bot.orchestrator import _candidates_from_markets
    from bot.polymarket.client import Market

    market = Market(
        condition_id="c2",
        question="Q?",
        yes_token="tok-miss",
        no_token="tok-no",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=5000,
        liquidity=2000,
        closed=False,
        raw={},
    )

    cache = OrderBookCache()  # empty

    http_snap = OrderBookSnapshot(
        token_id="tok-miss",
        asks=[(0.55, 100)],
        bids=[(0.52, 100)],
        timestamp=0,
    )
    client = MagicMock()
    client.get_orderbook = AsyncMock(return_value=http_snap)

    candidates = await _candidates_from_markets(client, [market], book_cache=cache)

    assert len(candidates) == 1
    client.get_orderbook.assert_called_once_with("tok-miss")


async def test_candidates_works_without_book_cache():
    """book_cache=None should work exactly as before (HTTP for all)."""
    from datetime import UTC, datetime, timedelta

    from bot.orchestrator import _candidates_from_markets
    from bot.polymarket.client import Market

    market = Market(
        condition_id="c3",
        question="Q?",
        yes_token="tok-http",
        no_token="tok-no",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=5000,
        liquidity=2000,
        closed=False,
        raw={},
    )

    http_snap = OrderBookSnapshot(
        token_id="tok-http",
        asks=[(0.55, 100)],
        bids=[(0.52, 100)],
        timestamp=0,
    )
    client = MagicMock()
    client.get_orderbook = AsyncMock(return_value=http_snap)

    candidates = await _candidates_from_markets(client, [market], book_cache=None)

    assert len(candidates) == 1
    client.get_orderbook.assert_called_once()


# ---------------------------------------------------------------------------
# Daemon: _run_repeating starts WS subscriber background task
# ---------------------------------------------------------------------------


def _fake_summary(halt_reason="stop"):
    from bot.orchestrator import RunSummary
    return RunSummary(halt_reason=halt_reason)


async def test_run_repeating_starts_ws_subscriber_task(tmp_path):
    from bot.config import RuntimeSettings
    from bot.daemon import _DaemonShutdown, _run_repeating

    settings = RuntimeSettings(
        stop_file=tmp_path / "STOP",
        scan_interval_seconds=0,
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    shutdown = _DaemonShutdown()

    async def fake_subscriber_run():
        await asyncio.sleep(999)

    with (
        patch("bot.daemon.run_once", new_callable=AsyncMock) as mock_run_once,
        patch("bot.daemon.OrderBookSubscriber") as MockSubscriber,
    ):
        mock_run_once.return_value = _fake_summary()
        instance = MagicMock()
        instance.run = fake_subscriber_run
        instance.stop = MagicMock()
        MockSubscriber.return_value = instance

        await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
        )

    MockSubscriber.assert_called_once()


async def test_run_repeating_passes_book_cache_to_run_once(tmp_path):
    from bot.config import RuntimeSettings
    from bot.daemon import _DaemonShutdown, _run_repeating
    from bot.polymarket.ws_orderbook import OrderBookCache

    settings = RuntimeSettings(
        stop_file=tmp_path / "STOP",
        scan_interval_seconds=0,
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    shutdown = _DaemonShutdown()

    async def fake_subscriber_run():
        await asyncio.sleep(999)

    with (
        patch("bot.daemon.run_once", new_callable=AsyncMock) as mock_run_once,
        patch("bot.daemon.OrderBookSubscriber") as MockSubscriber,
    ):
        mock_run_once.return_value = _fake_summary()
        instance = MagicMock()
        instance.run = fake_subscriber_run
        instance.stop = MagicMock()
        MockSubscriber.return_value = instance

        await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
        )

    call_kwargs = mock_run_once.call_args.kwargs
    assert "book_cache" in call_kwargs
    assert isinstance(call_kwargs["book_cache"], OrderBookCache)
