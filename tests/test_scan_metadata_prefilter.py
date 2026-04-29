"""Scan selection applies metadata filters before spending orderbook slots."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.repo import skip_reason_counts


def _market(condition_id: str, *, volume: float, liquidity: float, days: int) -> Market:
    return Market(
        condition_id=condition_id,
        question=f"Q {condition_id}?",
        yes_token=f"yes_{condition_id}",
        no_token=f"no_{condition_id}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=days)).isoformat(),
        volume_24h=volume,
        liquidity=liquidity,
        closed=False,
        raw={},
    )


def _orderbook(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 100)], asks=[(0.52, 100)], timestamp=0)


async def test_far_future_markets_do_not_consume_orderbook_scan_slots(tmp_path):
    """Metadata-invalid high-ranked markets are skipped before max_markets slicing."""
    from bot.orchestrator import run_once

    markets = [
        _market("far-high-1", volume=50_000.0, liquidity=50_000.0, days=90),
        _market("far-high-2", volume=40_000.0, liquidity=40_000.0, days=80),
        _market("near-valid-1", volume=5_000.0, liquidity=5_000.0, days=7),
        _market("near-valid-2", volume=4_000.0, liquidity=4_000.0, days=10),
    ]
    fetched_orderbooks: list[str] = []

    async def fake_get_orderbook(token_id: str) -> OrderBookSnapshot:
        fetched_orderbooks.append(token_id)
        return _orderbook(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=markets)
    client.get_orderbook = AsyncMock(side_effect=fake_get_orderbook)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_max_days=30)
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    assert fetched_orderbooks == ["yes_near-valid-1", "yes_near-valid-2"]
    assert await skip_reason_counts(conn, since_seconds_ago=3600) == {"too_far_to_resolution": 2}

    await conn.close()


async def test_wide_spread_markets_do_not_stop_scan_before_tradeable_candidates(tmp_path):
    """Orderbook filtering keeps trying bounded extra markets until max_markets can be filled."""
    from bot.orchestrator import run_once

    markets = [
        _market("wide-1", volume=8_000.0, liquidity=8_000.0, days=7),
        _market("wide-2", volume=7_000.0, liquidity=7_000.0, days=7),
        _market("tight-1", volume=6_000.0, liquidity=6_000.0, days=7),
        _market("tight-2", volume=5_000.0, liquidity=5_000.0, days=7),
    ]
    fetched_orderbooks: list[str] = []

    async def fake_get_orderbook(token_id: str) -> OrderBookSnapshot:
        fetched_orderbooks.append(token_id)
        if "wide" in token_id:
            return OrderBookSnapshot(token_id=token_id, bids=[(0.40, 100)], asks=[(0.60, 100)], timestamp=0)
        return _orderbook(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=markets)
    client.get_orderbook = AsyncMock(side_effect=fake_get_orderbook)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_max_spread=0.05)
    conn = await open_db(tmp_path / "bot.sqlite")

    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    assert fetched_orderbooks == ["yes_wide-1", "yes_wide-2", "yes_tight-1", "yes_tight-2"]
    assert summary.flagged_markets == 2
    assert summary.flagged_yes_tokens == ["yes_tight-1", "yes_tight-2"]
    assert await skip_reason_counts(conn, since_seconds_ago=3600) == {"wide_spread": 2}

    await conn.close()


async def test_near_expiry_markets_do_not_consume_orderbook_scan_slots(tmp_path):
    """Markets expiring in fewer than scan_min_days are skipped before orderbook fetch."""
    from bot.orchestrator import run_once

    markets = [
        _market("dying-1", volume=50_000.0, liquidity=50_000.0, days=0),
        _market("dying-2", volume=40_000.0, liquidity=40_000.0, days=0),
        _market("valid-1", volume=5_000.0, liquidity=5_000.0, days=7),
        _market("valid-2", volume=4_000.0, liquidity=4_000.0, days=10),
    ]
    fetched_orderbooks: list[str] = []

    async def fake_get_orderbook(token_id: str) -> OrderBookSnapshot:
        fetched_orderbooks.append(token_id)
        return _orderbook(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=markets)
    client.get_orderbook = AsyncMock(side_effect=fake_get_orderbook)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_min_days=1)
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    assert fetched_orderbooks == ["yes_valid-1", "yes_valid-2"]
    assert await skip_reason_counts(conn, since_seconds_ago=3600) == {"too_close_to_resolution": 2}

    await conn.close()


async def test_scan_min_days_respects_env_override(tmp_path):
    """scan_min_days default is 1; overridable via RuntimeSettings."""
    import os

    from bot.config import load_settings

    os.environ["SCAN_MIN_DAYS"] = "3"
    try:
        s = load_settings()
        assert s.scan_min_days == 3
    finally:
        del os.environ["SCAN_MIN_DAYS"]
