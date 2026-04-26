"""Walk-the-book paper-fill simulator.

Given a live orderbook snapshot and a limit order, walk levels and
record a simulated fill (or partial / no-fill) with realistic slippage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    """Asks: lowest price first. Bids: highest price first."""
    asks: list[OrderBookLevel] = field(default_factory=list)
    bids: list[OrderBookLevel] = field(default_factory=list)

    @property
    def mid(self) -> float:
        if not self.asks or not self.bids:
            return 0.0
        return (self.asks[0].price + self.bids[0].price) / 2


@dataclass(frozen=True)
class Fill:
    filled_size: float
    avg_price: float
    unfilled_size: float
    slippage: float  # avg_price - mid for buy; mid - avg_price for sell


def simulate_fill(
    orderbook: OrderBook,
    side: Side,
    size: float,
    limit_price: float,
) -> Fill:
    if size < 0:
        raise ValueError(f"size must be >= 0, got {size}")
    if not 0 <= limit_price <= 1:
        raise ValueError(f"limit_price must be in [0, 1] for binary outcomes, got {limit_price}")

    if side == Side.BUY:
        levels = orderbook.asks
        slip_sign = 1

        def accept(price: float) -> bool:
            return price <= limit_price

    else:
        levels = orderbook.bids
        slip_sign = -1

        def accept(price: float) -> bool:
            return price >= limit_price

    remaining = size
    filled = 0.0
    cost = 0.0
    for lvl in levels:
        if not accept(lvl.price):
            break
        take = min(remaining, lvl.size)
        filled += take
        cost += take * lvl.price
        remaining -= take
        if remaining <= 0:
            break

    avg = cost / filled if filled > 0 else 0.0
    mid = orderbook.mid
    slippage = (avg - mid) * slip_sign if filled > 0 and mid > 0 else 0.0
    return Fill(
        filled_size=filled,
        avg_price=avg,
        unfilled_size=remaining,
        slippage=slippage,
    )
