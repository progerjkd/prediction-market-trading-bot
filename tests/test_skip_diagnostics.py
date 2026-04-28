"""Tests for persisted skip diagnostics."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bot.config import RuntimeSettings
from bot.orchestrator import run_once
from bot.polymarket.client import Market, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.models import SkipEvent
from bot.storage.repo import insert_skip_event, recent_skip_events, skip_reason_counts


class _OneMarketClient:
    def __init__(self, *, volume_24h: float = 5_000, liquidity: float = 2_000):
        self.volume_24h = volume_24h
        self.liquidity = liquidity

    async def list_markets(self, limit: int = 100, active_only: bool = True):
        return [
            Market(
                condition_id="skip-cond-1",
                question="Will skip diagnostics work?",
                yes_token="skip-yes-1",
                no_token="skip-no-1",
                end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
                volume_24h=self.volume_24h,
                liquidity=self.liquidity,
                closed=False,
                raw={},
            )
        ]

    async def get_orderbook(self, token_id: str):
        return OrderBookSnapshot(
            token_id=token_id,
            asks=[(0.55, 1_000)],
            bids=[(0.52, 1_000)],
            timestamp=123,
        )


@pytest.fixture
async def db(tmp_path):
    conn = await open_db(tmp_path / "bot.sqlite")
    yield conn
    await conn.close()


async def test_skip_events_schema_exists(db):
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in await cur.fetchall()}
    assert "skip_events" in tables


async def test_insert_and_fetch_recent_skip_event(db):
    await insert_skip_event(
        db,
        SkipEvent(
            condition_id="cond",
            token_id="tok",
            stage="decision",
            reason="decision_should_trade_false",
            detail={"edge": 0.01},
        ),
    )

    rows = await recent_skip_events(db, limit=5)

    assert len(rows) == 1
    assert rows[0].condition_id == "cond"
    assert rows[0].reason == "decision_should_trade_false"
    assert rows[0].detail["edge"] == 0.01


async def test_skip_reason_counts_groups_recent_events(db):
    await insert_skip_event(db, SkipEvent(stage="scan_filter", reason="low_volume"))
    await insert_skip_event(db, SkipEvent(stage="scan_filter", reason="low_volume"))
    await insert_skip_event(db, SkipEvent(stage="decision", reason="decision_should_trade_false"))

    counts = await skip_reason_counts(db, since_seconds_ago=3600)

    assert counts == {
        "low_volume": 2,
        "decision_should_trade_false": 1,
    }


async def test_run_once_records_scan_filter_skip_reason(db, tmp_path):
    settings = RuntimeSettings(
        db_path=tmp_path / "bot.sqlite",
        stop_file=tmp_path / "STOP",
        scan_min_volume=10_000,
    )

    await run_once(
        settings=settings,
        conn=db,
        polymarket_client=_OneMarketClient(volume_24h=100),
        max_markets=1,
        mock_ai=True,
    )

    rows = await recent_skip_events(db, limit=5)
    assert rows[0].stage == "scan_filter"
    assert rows[0].reason == "low_volume"
    assert rows[0].condition_id == "skip-cond-1"


async def test_run_once_records_prediction_decision_skip_reason(db, tmp_path):
    settings = RuntimeSettings(
        db_path=tmp_path / "bot.sqlite",
        stop_file=tmp_path / "STOP",
        edge_threshold=0.5,
    )

    summary = await run_once(
        settings=settings,
        conn=db,
        polymarket_client=_OneMarketClient(),
        max_markets=1,
        mock_ai=True,
    )

    rows = await recent_skip_events(db, limit=5)
    assert summary.skipped_signals == 1
    assert rows[0].stage == "decision"
    assert rows[0].reason == "decision_should_trade_false"


async def test_status_prints_recent_skip_diagnostics(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    db_path = tmp_path / "bot.sqlite"
    conn = await open_db(db_path)
    await insert_skip_event(conn, SkipEvent(stage="decision", reason="decision_should_trade_false"))
    await conn.close()

    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out

    assert "skip diagnostics" in out.lower()
    assert "decision_should_trade_false" in out
