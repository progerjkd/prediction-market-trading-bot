"""Smoke tests for storage layer (in-memory SQLite)."""
from __future__ import annotations

import pytest

from bot.storage.db import open_db
from bot.storage.models import (
    ApiSpend,
    FlaggedMarket,
    Prediction,
    ResearchBrief,
    Trade,
)
from bot.storage.repo import (
    close_trade,
    daily_api_cost_usd,
    insert_api_spend,
    insert_flagged_market,
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


async def test_close_trade_clears_open_count(db):
    pred = Prediction(condition_id="c2", token_id="t2", p_model=0.6, p_market=0.5, edge=0.1)
    pid = await insert_prediction(db, pred)
    trade = Trade(condition_id="c2", token_id="t2", side="BUY", size=100, limit_price=0.55, prediction_id=pid)
    tid = await insert_trade(db, trade)
    await close_trade(db, tid, pnl=10.5, outcome="YES")
    assert await open_positions_count(db) == 0


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
