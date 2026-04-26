"""Deterministic market data for local smoke tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.polymarket.client import Market, OrderBookSnapshot


class MockPolymarketClient:
    async def close(self) -> None:
        return None

    async def list_markets(self, limit: int = 100, active_only: bool = True) -> list[Market]:
        return [
            Market(
                condition_id="mock-cond-1",
                question="Will the mock market resolve Yes?",
                yes_token="mock-yes-1",
                no_token="mock-no-1",
                end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
                volume_24h=5_000.0,
                liquidity=2_000.0,
                closed=False,
                raw={"source": "mock"},
            )
        ][:limit]

    async def get_orderbook(self, token_id: str) -> OrderBookSnapshot:
        return OrderBookSnapshot(
            token_id=token_id,
            asks=[(0.55, 100.0), (0.56, 100.0)],
            bids=[(0.52, 100.0)],
            timestamp=123,
        )
