"""Tests for one paper-only orchestrator pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bot.config import RuntimeSettings
from bot.orchestrator import run_once
from bot.polymarket.client import Market, OrderBookSnapshot
from bot.storage.db import open_db


class FakePolymarketClient:
    def __init__(self):
        self.posted_orders = []

    async def list_markets(self, limit: int = 100, active_only: bool = True):
        return [
            Market(
                condition_id="cond-1",
                question="Will test market resolve Yes?",
                yes_token="yes-1",
                no_token="no-1",
                end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
                volume_24h=5_000,
                liquidity=2_000,
                closed=False,
                raw={"source": "fake"},
            )
        ]

    async def get_orderbook(self, token_id: str):
        return OrderBookSnapshot(
            token_id=token_id,
            asks=[(0.55, 100), (0.56, 100)],
            bids=[(0.52, 100)],
            timestamp=123,
        )

    async def create_and_post_order(self, *args, **kwargs):  # pragma: no cover - must not be called
        self.posted_orders.append((args, kwargs))
        raise AssertionError("live order placement must not be called in paper mode")


@pytest.mark.asyncio
async def test_run_once_paper_mode_writes_prediction_and_paper_trade(tmp_path):
    conn = await open_db(tmp_path / "bot.sqlite")
    client = FakePolymarketClient()
    settings = RuntimeSettings(
        db_path=tmp_path / "bot.sqlite",
        stop_file=tmp_path / "STOP",
        bankroll_usdc=10_000,
        edge_threshold=0.04,
    )

    try:
        summary = await run_once(
            settings=settings,
            conn=conn,
            polymarket_client=client,
            max_markets=1,
            mock_ai=True,
        )

        assert summary.scanned_markets == 1
        assert summary.predictions_written == 1
        assert summary.paper_trades_written == 1
        assert client.posted_orders == []

        cur = await conn.execute("SELECT COUNT(*) FROM predictions")
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute("SELECT is_paper FROM trades")
        assert (await cur.fetchone())[0] == 1
    finally:
        await conn.close()
