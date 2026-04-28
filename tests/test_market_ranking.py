"""Market candidate ranking by volume — TDD RED phase.

run_once should sort fetched markets by volume_24h descending before slicing
to max_markets, ensuring the daemon always predicts on the most liquid opportunities.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(condition_id: str, volume: float) -> Market:
    return Market(
        condition_id=condition_id,
        question=f"Q {condition_id}?",
        yes_token=f"yes_{condition_id}",
        no_token=f"no_{condition_id}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=volume,
        liquidity=1000.0,
        closed=False,
        raw={},
    )


def _orderbook(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 100)], asks=[(0.52, 100)], timestamp=0)


# ---------------------------------------------------------------------------
# Highest-volume markets are selected when pool > max_markets
# ---------------------------------------------------------------------------


async def test_run_once_selects_highest_volume_markets(tmp_path):
    """When the API returns more markets than max_markets, the highest-volume ones are used."""
    from bot.orchestrator import run_once

    # 5 markets with clearly distinct volumes; max_markets=2 → should pick vol=9000 and vol=8000
    markets = [
        _market("low1", volume=100.0),
        _market("high1", volume=9000.0),
        _market("low2", volume=200.0),
        _market("high2", volume=8000.0),
        _market("low3", volume=50.0),
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

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    # Orderbooks fetched should be the two highest-volume tokens
    assert "yes_high1" in fetched_orderbooks
    assert "yes_high2" in fetched_orderbooks
    assert "yes_low1" not in fetched_orderbooks
    assert "yes_low2" not in fetched_orderbooks
    assert "yes_low3" not in fetched_orderbooks

    await conn.close()


async def test_run_once_uses_all_markets_when_pool_le_max(tmp_path):
    """When pool size ≤ max_markets, all markets are used regardless of volume."""
    from bot.orchestrator import run_once

    markets = [
        _market("a", volume=500.0),
        _market("b", volume=100.0),
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

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=10,
    )

    assert "yes_a" in fetched_orderbooks
    assert "yes_b" in fetched_orderbooks

    await conn.close()


async def test_run_once_ranking_is_descending_by_volume(tmp_path):
    """When max_markets=1, the single highest-volume market is selected."""
    from bot.orchestrator import run_once

    markets = [
        _market("small", volume=10.0),
        _market("large", volume=50000.0),
        _market("medium", volume=1000.0),
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

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=1,
    )

    assert fetched_orderbooks == ["yes_large"]

    await conn.close()
