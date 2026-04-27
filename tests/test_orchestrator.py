"""Tests for one paper-only orchestrator pass."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import bot.orchestrator as orchestrator
from bot.config import RuntimeSettings
from bot.orchestrator import run_once
from bot.polymarket.client import Market, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.models import Prediction, Trade
from bot.storage.repo import insert_prediction, insert_trade


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


class FakeResolvedMarketClient(FakePolymarketClient):
    async def list_markets(self, limit: int = 100, active_only: bool = True):
        if active_only:
            return []
        return [
            Market(
                condition_id="cond-closed",
                question="Did the resolved market settle Yes?",
                yes_token="yes-closed",
                no_token="no-closed",
                end_date_iso=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
                volume_24h=0,
                liquidity=0,
                closed=True,
                raw={"outcomePrices": "[0, 1]"},
            )
        ]


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


@pytest.mark.asyncio
async def test_run_once_closes_resolved_losing_trade_and_records_lesson(tmp_path, monkeypatch):
    conn = await open_db(tmp_path / "bot.sqlite")
    failure_log = tmp_path / "failure_log.md"
    failure_log.write_text("# Failure Log\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator, "FAILURE_LOG_PATH", failure_log)
    settings = RuntimeSettings(
        db_path=tmp_path / "bot.sqlite",
        stop_file=tmp_path / "STOP",
        bankroll_usdc=10_000,
    )

    try:
        prediction_id = await insert_prediction(
            conn,
            Prediction(condition_id="cond-closed", token_id="yes-closed", p_model=0.7, p_market=0.55, edge=0.15),
        )
        await insert_trade(
            conn,
            Trade(
                condition_id="cond-closed",
                token_id="yes-closed",
                side="BUY",
                size=100,
                limit_price=0.55,
                fill_price=0.55,
                prediction_id=prediction_id,
            ),
        )

        summary = await run_once(
            settings=settings,
            conn=conn,
            polymarket_client=FakeResolvedMarketClient(),
            max_markets=1,
            mock_ai=True,
        )

        assert summary.closed_positions == 1
        assert summary.lessons_written == 1

        cur = await conn.execute("SELECT closed_at, pnl, outcome FROM trades WHERE condition_id='cond-closed'")
        closed_at, pnl, outcome = await cur.fetchone()
        assert closed_at is not None
        assert pnl == pytest.approx(-55.0)
        assert outcome == "NO"

        cur = await conn.execute("SELECT trade_id, cause, rule_proposed FROM lessons")
        lesson = await cur.fetchone()
        assert lesson[0] == 1
        assert lesson[1] == "bad-prediction"
        assert "stronger cross-source narrative" in lesson[2]
        assert "cond-closed" in failure_log.read_text(encoding="utf-8")
    finally:
        await conn.close()
