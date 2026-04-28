"""Spread-weighted candidate ordering — tighter spread improves composite score.

After orderbooks are fetched, candidates are re-sorted by
volume_24h * liquidity / (1 + spread) so that tighter-spread markets appear
first in the flagged list and get prediction priority.  This reduces expected
transaction costs when only a subset of candidates can be predicted.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(cid: str, volume: float, liquidity: float) -> Market:
    return Market(
        condition_id=cid, question=f"Q {cid}?",
        yes_token=f"y_{cid}", no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=volume, liquidity=liquidity, closed=False, raw={},
    )


def _ob(token_id: str, spread: float = 0.04) -> OrderBookSnapshot:
    mid = 0.50
    return OrderBookSnapshot(
        token_id=token_id,
        bids=[(mid - spread / 2, 100)],
        asks=[(mid + spread / 2, 100)],
        timestamp=int(time.time()),
    )


async def _run(tmp_path, markets: list[Market], obs: dict, max_markets: int = 2):
    from bot.orchestrator import run_once

    async def fake_ob(token_id: str) -> OrderBookSnapshot:
        return obs.get(token_id, _ob(token_id))

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=markets)
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    conn = await open_db(tmp_path / "bot.sqlite")
    summary = await run_once(
        settings=RuntimeSettings(stop_file=tmp_path / "STOP"),
        conn=conn, polymarket_client=client, mock_ai=True, scan_only=True, max_markets=max_markets,
    )
    await conn.close()
    return summary


async def test_tight_spread_candidate_ranked_first(tmp_path):
    """Equal volume+liquidity: the tighter-spread candidate appears first in flagged_yes_tokens."""
    markets = [
        _market("wide",  volume=10_000.0, liquidity=10_000.0),
        _market("tight", volume=10_000.0, liquidity=10_000.0),
    ]
    obs = {
        "y_wide":  _ob("y_wide",  spread=0.04),  # score = 10k*10k/(1.04) ≈ 96.2M
        "y_tight": _ob("y_tight", spread=0.01),  # score = 10k*10k/(1.01) ≈ 99.0M — higher
    }
    summary = await _run(tmp_path, markets, obs, max_markets=2)
    tokens = summary.flagged_yes_tokens
    assert "y_tight" in tokens
    assert "y_wide" in tokens
    # tight-spread comes first in the ordered list
    assert tokens.index("y_tight") < tokens.index("y_wide")


async def test_high_volume_wins_when_spreads_equal(tmp_path):
    """When spreads are equal, higher volume*liquidity score still ranks first."""
    markets = [
        _market("small", volume=1_000.0, liquidity=1_000.0),
        _market("large", volume=5_000.0, liquidity=5_000.0),
    ]
    obs = {
        "y_small": _ob("y_small", spread=0.04),
        "y_large": _ob("y_large", spread=0.04),
    }
    summary = await _run(tmp_path, markets, obs, max_markets=2)
    tokens = summary.flagged_yes_tokens
    assert tokens.index("y_large") < tokens.index("y_small")
