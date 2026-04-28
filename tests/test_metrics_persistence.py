"""Tests for metrics persistence and acceptance gate — TDD RED phase."""
from __future__ import annotations

import pytest

from bot.storage.db import open_db
from bot.storage.models import Prediction, Trade
from bot.storage.repo import (
    acceptance_criteria_met,
    close_trade,
    insert_prediction,
    insert_trade,
    persist_daily_metrics,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    conn = await open_db(tmp_path / "bot.sqlite")
    yield conn
    await conn.close()


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


async def _settled_trade(conn, *, pnl: float, p_model: float, outcome: str, source: str = "paper_live") -> int:
    """Insert a prediction + closed trade pair and return trade id."""
    pred = Prediction(condition_id="cx", token_id="tx", p_model=p_model, p_market=0.5, edge=0.1)
    pid = await insert_prediction(conn, pred)
    t = Trade(
        condition_id="cx",
        token_id="tx",
        side="BUY",
        size=100,
        limit_price=0.5,
        fill_price=0.5,
        prediction_id=pid,
        source=source,
    )
    tid = await insert_trade(conn, t)
    await close_trade(conn, tid, pnl=pnl, outcome=outcome)
    return tid


# ---------------------------------------------------------------------------
# persist_daily_metrics
# ---------------------------------------------------------------------------


async def test_persist_daily_metrics_writes_row(db):
    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT date FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row is not None


async def test_persist_daily_metrics_win_rate_two_wins_one_loss(db):
    await _settled_trade(db, pnl=10.0, p_model=0.7, outcome="YES")
    await _settled_trade(db, pnl=8.0, p_model=0.6, outcome="YES")
    await _settled_trade(db, pnl=-5.0, p_model=0.4, outcome="NO")

    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT win_rate FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(2 / 3)


async def test_persist_daily_metrics_brier_from_prediction_outcomes(db):
    # p_model=1.0 on YES outcome → BS=0.0; p_model=0.0 on YES outcome → BS=1.0
    await _settled_trade(db, pnl=10.0, p_model=1.0, outcome="YES")
    await _settled_trade(db, pnl=-5.0, p_model=0.0, outcome="YES")

    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT brier_score FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(0.5)  # mean of 0.0 and 1.0


async def test_persist_daily_metrics_n_trades_count(db):
    await _settled_trade(db, pnl=5.0, p_model=0.6, outcome="YES")
    await _settled_trade(db, pnl=-3.0, p_model=0.4, outcome="NO")

    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT n_trades FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == 2


async def test_persist_daily_metrics_filters_to_requested_source(db):
    await _settled_trade(db, pnl=5.0, p_model=0.7, outcome="YES", source="paper_live")
    await _settled_trade(db, pnl=5.0, p_model=0.7, outcome="YES", source="backtest")

    await persist_daily_metrics(db, _today(), source="paper_live")
    await persist_daily_metrics(db, _today(), source="backtest")

    cur = await db.execute(
        "SELECT source, n_trades FROM metrics_daily WHERE date=? ORDER BY source",
        (_today(),),
    )
    rows = await cur.fetchall()
    assert rows == [("backtest", 1), ("paper_live", 1)]


async def test_persist_daily_metrics_excludes_no_fill_trades(db):
    await _settled_trade(db, pnl=5.0, p_model=0.6, outcome="YES")
    # no_fill trade — outcome="no_fill", should not count
    t = Trade(condition_id="c2", token_id="t2", side="BUY", size=0, limit_price=0.5, fill_price=None, outcome="no_fill", pnl=0.0)
    tid = await insert_trade(db, t)
    await close_trade(db, tid, pnl=0.0, outcome="no_fill")

    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT n_trades FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == 1


async def test_persist_daily_metrics_upserts_on_second_call(db):
    await _settled_trade(db, pnl=5.0, p_model=0.6, outcome="YES")
    await persist_daily_metrics(db, _today())

    await _settled_trade(db, pnl=3.0, p_model=0.7, outcome="YES")
    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT COUNT(*) FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == 1  # still just one row — upserted


async def test_persist_daily_metrics_pnl_total(db):
    await _settled_trade(db, pnl=10.0, p_model=0.7, outcome="YES")
    await _settled_trade(db, pnl=-4.0, p_model=0.3, outcome="NO")

    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT pnl_usd FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == pytest.approx(6.0)


async def test_persist_daily_metrics_zero_trades_still_writes(db):
    await persist_daily_metrics(db, _today())

    cur = await db.execute("SELECT n_trades, win_rate, brier_score FROM metrics_daily WHERE date=?", (_today(),))
    row = await cur.fetchone()
    assert row[0] == 0
    assert row[1] == pytest.approx(0.0)
    assert row[2] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# acceptance_criteria_met
# ---------------------------------------------------------------------------


async def test_acceptance_not_met_with_zero_trades(db):
    met, reason = await acceptance_criteria_met(db)
    assert met is False
    assert "50" in reason


async def test_acceptance_not_met_below_50_trades(db):
    for _i in range(30):
        await _settled_trade(db, pnl=5.0, p_model=0.7, outcome="YES")

    met, reason = await acceptance_criteria_met(db)
    assert met is False
    assert "50" in reason


async def test_acceptance_not_met_low_win_rate(db):
    # 50 trades, only 40% wins
    for _ in range(20):
        await _settled_trade(db, pnl=5.0, p_model=0.7, outcome="YES")
    for _ in range(30):
        await _settled_trade(db, pnl=-3.0, p_model=0.3, outcome="NO")

    met, reason = await acceptance_criteria_met(db)
    assert met is False
    assert "win rate" in reason.lower() or "60" in reason


async def test_acceptance_not_met_high_brier(db):
    # 50 trades with >60% win rate but terrible predictions (high Brier)
    for _ in range(35):
        # Predicted 0.1 but YES resolved → high Brier
        await _settled_trade(db, pnl=5.0, p_model=0.1, outcome="YES")
    for _ in range(15):
        await _settled_trade(db, pnl=-3.0, p_model=0.9, outcome="NO")

    met, reason = await acceptance_criteria_met(db)
    assert met is False
    assert "brier" in reason.lower() or "0.25" in reason


async def test_acceptance_met_when_all_criteria_pass(db):
    # 50 trades, >60% win rate, low Brier (good predictions)
    for _ in range(35):
        await _settled_trade(db, pnl=5.0, p_model=0.85, outcome="YES")
    for _ in range(15):
        await _settled_trade(db, pnl=-3.0, p_model=0.15, outcome="NO")

    met, reason = await acceptance_criteria_met(db)
    assert met is True
    assert reason == ""


async def test_acceptance_criteria_met_returns_false_with_reason_string(db):
    met, reason = await acceptance_criteria_met(db)
    assert isinstance(met, bool)
    assert isinstance(reason, str)
    assert not met
    assert len(reason) > 0


async def test_acceptance_criteria_default_ignores_backtest_trades(db):
    for _ in range(60):
        await _settled_trade(db, pnl=5.0, p_model=0.85, outcome="YES", source="backtest")

    met, reason = await acceptance_criteria_met(db)

    assert met is False
    assert "have 0" in reason


async def test_acceptance_criteria_can_check_backtest_source_explicitly(db):
    for _ in range(60):
        await _settled_trade(db, pnl=5.0, p_model=0.85, outcome="YES", source="backtest")

    met, reason = await acceptance_criteria_met(db, source="backtest")

    assert met is True
    assert reason == ""


# ---------------------------------------------------------------------------
# run_once integration: daily metrics persisted each pass
# ---------------------------------------------------------------------------


async def test_run_once_persists_daily_metrics(tmp_path):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from bot.config import RuntimeSettings
    from bot.orchestrator import run_once
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot

    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", bankroll_usdc=10_000, edge_threshold=0.04)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[
        Market(
            condition_id="c1", question="Q?", yes_token="t1", no_token="n1",
            end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
            volume_24h=5000, liquidity=2000, closed=False, raw={},
        )
    ])
    client.get_orderbook = AsyncMock(return_value=OrderBookSnapshot(
        token_id="t1", asks=[(0.55, 500)], bids=[(0.52, 100)], timestamp=0,
    ))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    await run_once(settings=settings, conn=conn, polymarket_client=client, mock_ai=True, max_markets=1)

    from datetime import date
    today = date.today().isoformat()
    cur = await conn.execute("SELECT date FROM metrics_daily WHERE date=?", (today,))
    row = await cur.fetchone()
    assert row is not None, "metrics_daily should have a row for today after run_once"

    await conn.close()
