"""Smoke tests for storage layer (in-memory SQLite)."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from bot.storage.db import open_db
from bot.storage.models import (
    ApiSpend,
    FlaggedMarket,
    PaperExecution,
    Prediction,
    ResearchBrief,
    Trade,
)
from bot.storage.repo import (
    close_trade,
    daily_api_cost_usd,
    insert_api_spend,
    insert_flagged_market,
    insert_paper_execution,
    insert_prediction,
    insert_research_brief,
    insert_trade,
    latest_flagged_markets,
    open_positions_count,
    total_open_exposure,
)


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "bot.sqlite"
    conn = await open_db(path)
    yield conn
    await conn.close()


async def test_schema_creates_tables(db):
    cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    rows = await cur.fetchall()
    names = {r[0] for r in rows}
    assert "markets_flagged" in names
    assert "predictions" in names
    assert "trades" in names
    assert "lessons" in names
    assert "metrics_daily" in names
    assert "api_spend" in names
    assert "paper_executions" in names


async def test_trades_schema_has_source_column(db):
    cur = await db.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in await cur.fetchall()}
    assert "source" in columns


async def test_metrics_daily_schema_has_source_column(db):
    cur = await db.execute("PRAGMA table_info(metrics_daily)")
    columns = {row[1] for row in await cur.fetchall()}
    assert "source" in columns


async def test_open_db_migrates_legacy_metrics_and_trades_source_columns(tmp_path):
    db_path = tmp_path / "legacy.sqlite"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prediction_id INTEGER,
            condition_id TEXT NOT NULL,
            token_id TEXT NOT NULL,
            side TEXT NOT NULL,
            size REAL NOT NULL,
            limit_price REAL NOT NULL,
            fill_price REAL,
            slippage REAL,
            intended_size REAL,
            is_paper INTEGER NOT NULL DEFAULT 1,
            opened_at INTEGER NOT NULL,
            closed_at INTEGER,
            pnl REAL,
            outcome TEXT
        );
        CREATE TABLE metrics_daily (
            date TEXT PRIMARY KEY,
            win_rate REAL,
            sharpe REAL,
            max_drawdown REAL,
            profit_factor REAL,
            brier_score REAL,
            n_trades INTEGER,
            pnl_usd REAL,
            api_cost_usd REAL
        );
        INSERT INTO metrics_daily
            (date, win_rate, sharpe, max_drawdown, profit_factor, brier_score, n_trades, pnl_usd, api_cost_usd)
        VALUES ('2026-04-27', 0.5, 0, 0, 0, 0.2, 10, 42, 0);
        INSERT INTO trades
            (condition_id, token_id, side, size, limit_price, is_paper, opened_at, closed_at, pnl, outcome)
        VALUES
            ('bt_0000001', 'bt_tok_0000001', 'BUY', 10, 0.5, 1, 1770000000, 1770000000, 5, 'YES');
        """
    )
    raw.close()

    conn = await open_db(db_path)
    try:
        cur = await conn.execute("PRAGMA table_info(trades)")
        trade_columns = {row[1] for row in await cur.fetchall()}
        assert "source" in trade_columns

        cur = await conn.execute("SELECT source FROM trades WHERE condition_id='bt_0000001'")
        row = await cur.fetchone()
        assert row == ("backtest",)

        cur = await conn.execute("SELECT COUNT(*) FROM metrics_daily WHERE source='paper_live'")
        row = await cur.fetchone()
        assert row[0] == 0
    finally:
        await conn.close()


async def test_open_db_allows_concurrent_initialization(tmp_path):
    db_path = tmp_path / "concurrent.sqlite"

    async def _open_and_close():
        conn = await open_db(db_path)
        await conn.close()

    await asyncio.gather(*[_open_and_close() for _ in range(4)])


async def test_insert_and_fetch_flagged_market(db):
    m = FlaggedMarket(
        condition_id="cond1",
        yes_token="yes1",
        no_token="no1",
        mid_price=0.55,
        spread=0.02,
        volume_24h=5000,
    )
    await insert_flagged_market(db, m)
    rows = await latest_flagged_markets(db)
    assert len(rows) == 1
    assert rows[0].condition_id == "cond1"


async def test_open_positions_and_exposure(db):
    pred = Prediction(
        condition_id="c1", token_id="t1", p_model=0.6, p_market=0.5, edge=0.1
    )
    pid = await insert_prediction(db, pred)
    trade = Trade(
        condition_id="c1",
        token_id="t1",
        side="BUY",
        size=100,
        limit_price=0.55,
        fill_price=0.55,
        prediction_id=pid,
    )
    await insert_trade(db, trade)
    assert await open_positions_count(db) == 1
    assert await total_open_exposure(db) == pytest.approx(100 * 0.55)


async def test_insert_trade_defaults_source_to_paper_live(db):
    trade = Trade(
        condition_id="c-source",
        token_id="t-source",
        side="BUY",
        size=10,
        limit_price=0.5,
    )
    tid = await insert_trade(db, trade)

    cur = await db.execute("SELECT source FROM trades WHERE id=?", (tid,))
    row = await cur.fetchone()
    assert row[0] == "paper_live"


async def test_close_trade_clears_open_count(db):
    pred = Prediction(condition_id="c2", token_id="t2", p_model=0.6, p_market=0.5, edge=0.1)
    pid = await insert_prediction(db, pred)
    trade = Trade(condition_id="c2", token_id="t2", side="BUY", size=100, limit_price=0.55, prediction_id=pid)
    tid = await insert_trade(db, trade)
    await close_trade(db, tid, pnl=10.5, outcome="YES")
    assert await open_positions_count(db) == 0


async def test_insert_paper_execution_persists_partial_fill_details(db):
    pred = Prediction(condition_id="c4", token_id="t4", p_model=0.7, p_market=0.5, edge=0.2)
    pid = await insert_prediction(db, pred)
    trade = Trade(condition_id="c4", token_id="t4", side="BUY", size=75, limit_price=0.55, prediction_id=pid)
    tid = await insert_trade(db, trade)

    execution = PaperExecution(
        condition_id="c4",
        token_id="t4",
        side="BUY",
        requested_size=150,
        filled_size=75,
        unfilled_size=75,
        limit_price=0.55,
        fill_price=0.55,
        slippage=0.01,
        status="PARTIAL_FILL",
        prediction_id=pid,
        trade_id=tid,
    )
    eid = await insert_paper_execution(db, execution)

    cur = await db.execute(
        "SELECT prediction_id, trade_id, requested_size, filled_size, unfilled_size, status "
        "FROM paper_executions WHERE id=?",
        (eid,),
    )
    row = await cur.fetchone()
    assert row == (pid, tid, 150, 75, 75, "PARTIAL_FILL")


async def test_api_spend_tracking(db):
    await insert_api_spend(db, ApiSpend(provider="anthropic", cost_usd=0.5, model="claude-opus-4-7"))
    await insert_api_spend(db, ApiSpend(provider="anthropic", cost_usd=1.25, model="claude-opus-4-7"))
    total = await daily_api_cost_usd(db, since_ts=0)
    assert total == pytest.approx(1.75)


async def test_research_brief_persistence(db):
    b = ResearchBrief(
        condition_id="c3",
        bullish_signals=["news article", "reddit thread"],
        bearish_signals=["counter-narrative"],
        narrative_score=0.4,
    )
    await insert_research_brief(db, b)
    cur = await db.execute("SELECT condition_id, narrative_score FROM research_briefs WHERE condition_id=?", ("c3",))
    row = await cur.fetchone()
    assert row[0] == "c3"
    assert row[1] == pytest.approx(0.4)
