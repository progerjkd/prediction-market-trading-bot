"""Tests for pm-scan deterministic market filtering."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from filter_markets import MarketCandidate, filter_tradeable_markets


def candidate(
    condition_id: str,
    *,
    volume_24h: float = 1_000,
    spread: float = 0.02,
    liquidity: float = 1_000,
    days: int = 7,
) -> MarketCandidate:
    return MarketCandidate(
        condition_id=condition_id,
        question=f"Market {condition_id}",
        yes_token=f"yes-{condition_id}",
        no_token=f"no-{condition_id}",
        mid_price=0.55,
        spread=spread,
        volume_24h=volume_24h,
        liquidity=liquidity,
        end_date_iso=(datetime.now(UTC) + timedelta(days=days)).isoformat(),
    )


def test_filter_tradeable_markets_applies_mvp_thresholds():
    markets = [
        candidate("ok"),
        candidate("low-volume", volume_24h=199),
        candidate("wide-spread", spread=0.06),
        candidate("low-liquidity", liquidity=49),
        candidate("too-far", days=31),
    ]

    result = filter_tradeable_markets(markets, min_liquidity=50)

    assert [m.condition_id for m in result] == ["ok"]


def test_filter_tradeable_markets_ranks_by_edge_proxy_descending():
    small = candidate("small", volume_24h=500, liquidity=200, spread=0.04, days=10)
    large = candidate("large", volume_24h=2_000, liquidity=1_000, spread=0.01, days=3)

    result = filter_tradeable_markets([small, large], min_liquidity=50)

    assert [m.condition_id for m in result] == ["large", "small"]
    assert result[0].edge_proxy > result[1].edge_proxy
