"""Deterministic market filters for pm-scan."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class MarketCandidate:
    condition_id: str
    question: str
    yes_token: str
    no_token: str
    mid_price: float
    spread: float
    volume_24h: float
    liquidity: float
    end_date_iso: str | None
    raw: dict[str, Any] = field(default_factory=dict)
    edge_proxy: float = 0.0
    momentum_1h: float = 0.0
    momentum_24h: float = 0.0


def filter_tradeable_markets(
    markets: list[MarketCandidate],
    *,
    min_volume: float = 200.0,
    min_days_to_resolution: int = 1,
    max_days_to_resolution: int = 30,
    max_spread: float = 0.05,
    min_liquidity: float = 50.0,
    now: datetime | None = None,
) -> list[MarketCandidate]:
    now = now or datetime.now(UTC)
    accepted: list[MarketCandidate] = []

    for market in markets:
        days = days_to_resolution(market.end_date_iso, now=now)
        if market.volume_24h < min_volume:
            continue
        if days > max_days_to_resolution:
            continue
        if days < min_days_to_resolution:
            continue
        if market.spread > max_spread:
            continue
        if market.liquidity < min_liquidity:
            continue
        edge_proxy = calculate_edge_proxy(
            volume_24h=market.volume_24h,
            liquidity=market.liquidity,
            spread=market.spread,
            days_to_resolution=max(days, 0.0),
        )
        accepted.append(_with_edge_proxy(market, edge_proxy))

    return sorted(accepted, key=lambda m: m.edge_proxy, reverse=True)


def days_to_resolution(end_date_iso: str | None, *, now: datetime | None = None) -> float:
    if not end_date_iso:
        return float("inf")
    now = now or datetime.now(UTC)
    raw = end_date_iso.replace("Z", "+00:00")
    try:
        end = datetime.fromisoformat(raw)
    except ValueError:
        return float("inf")
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return (end - now).total_seconds() / 86_400


def calculate_edge_proxy(
    *,
    volume_24h: float,
    liquidity: float,
    spread: float,
    days_to_resolution: float,
) -> float:
    liquidity_factor = liquidity / max(liquidity + 1_000.0, 1.0)
    spread_factor = max(0.0, 1.0 - (spread / 0.05))
    time_factor = 1.0 / max(days_to_resolution, 1.0)
    return volume_24h * liquidity_factor * (1.0 + spread_factor) * time_factor


def to_flagged_market_kwargs(candidate: MarketCandidate) -> dict[str, Any]:
    return {
        "condition_id": candidate.condition_id,
        "yes_token": candidate.yes_token,
        "no_token": candidate.no_token,
        "mid_price": candidate.mid_price,
        "spread": candidate.spread,
        "volume_24h": candidate.volume_24h,
        "question": candidate.question,
        "end_date_iso": candidate.end_date_iso,
        "liquidity": candidate.liquidity,
        "edge_proxy": candidate.edge_proxy,
        "raw_json": json.dumps(candidate.raw),
    }


def _with_edge_proxy(candidate: MarketCandidate, edge_proxy: float) -> MarketCandidate:
    return MarketCandidate(
        condition_id=candidate.condition_id,
        question=candidate.question,
        yes_token=candidate.yes_token,
        no_token=candidate.no_token,
        mid_price=candidate.mid_price,
        spread=candidate.spread,
        volume_24h=candidate.volume_24h,
        liquidity=candidate.liquidity,
        end_date_iso=candidate.end_date_iso,
        raw=candidate.raw,
        edge_proxy=edge_proxy,
    )
