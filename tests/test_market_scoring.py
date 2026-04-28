"""Composite market scoring — TDD RED phase.

run_once ranks markets by volume_24h * liquidity (descending) before slicing
to max_markets, preferring deep+active markets over shallow high-volume ones.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(condition_id: str, volume: float, liquidity: float) -> Market:
    return Market(
        condition_id=condition_id,
        question=f"Q {condition_id}?",
        yes_token=f"yes_{condition_id}",
        no_token=f"no_{condition_id}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=volume,
        liquidity=liquidity,
        closed=False,
        raw={},
    )


def _ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 100)], asks=[(0.52, 100)], timestamp=0)


async def _run(tmp_path, markets, max_markets=2):
    from bot.orchestrator import run_once
    fetched: list[str] = []

    async def fake_ob(token_id):
        fetched.append(token_id)
        return _ob(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=markets)
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    conn = await open_db(tmp_path / "bot.sqlite")
    await run_once(
        settings=RuntimeSettings(stop_file=tmp_path / "STOP"),
        conn=conn, polymarket_client=client, mock_ai=True, scan_only=True, max_markets=max_markets,
    )
    await conn.close()
    return fetched


async def test_composite_score_prefers_high_liquidity_over_pure_volume(tmp_path):
    """High volume but zero liquidity loses to moderate volume + high liquidity."""
    markets = [
        _market("vol_only",   volume=100_000.0, liquidity=0.0),    # score=0
        _market("balanced",   volume=5_000.0,   liquidity=5_000.0), # score=25M — winner
        _market("small",      volume=100.0,     liquidity=100.0),   # score=10k
    ]
    fetched = await _run(tmp_path, markets, max_markets=1)
    assert fetched == ["yes_balanced"]


async def test_composite_score_selects_top_n_by_volume_times_liquidity(tmp_path):
    """Top 2 by score are fetched, others skipped."""
    markets = [
        _market("a", volume=1000.0, liquidity=1000.0),  # score=1M  — 2nd
        _market("b", volume=500.0,  liquidity=100.0),   # score=50k — 4th
        _market("c", volume=2000.0, liquidity=2000.0),  # score=4M  — 1st
        _market("d", volume=800.0,  liquidity=200.0),   # score=160k — 3rd
    ]
    fetched = await _run(tmp_path, markets, max_markets=2)
    assert set(fetched) == {"yes_c", "yes_a"}
    assert "yes_b" not in fetched
    assert "yes_d" not in fetched


async def test_zero_liquidity_markets_rank_last(tmp_path):
    """Markets with liquidity=0 sink to the bottom regardless of volume."""
    markets = [
        _market("zero_liq",  volume=999_999.0, liquidity=0.0),
        _market("low_both",  volume=300.0,     liquidity=100.0),  # score=30k — wins
    ]
    fetched = await _run(tmp_path, markets, max_markets=1)
    assert fetched == ["yes_low_both"]
