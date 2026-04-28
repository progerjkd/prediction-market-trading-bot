"""Brier score: model calibration metric over resolved predictions.

brier_score(conn) joins closed trades to their predictions and computes
mean((p_model - actual_outcome)^2), where actual_outcome is 1.0 for YES
and 0.0 for NO.  Trades with outcome TIMEOUT or STOP_LOSS are excluded
because their outcome doesn't reflect the market resolution.
Returns None when there are no qualifying closed trades.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Prediction, Trade
from bot.storage.repo import (
    close_trade,
    current_brier_score,
    insert_flagged_market,
    insert_prediction,
    insert_trade,
)


async def _seed(conn, cid: str, p_model: float, outcome: str) -> None:
    now = int(time.time())
    await insert_flagged_market(
        conn,
        FlaggedMarket(
            condition_id=cid,
            yes_token=f"tok_{cid}",
            no_token=f"no_{cid}",
            mid_price=0.5,
            spread=0.02,
            volume_24h=1000.0,
            flagged_at=now,
        ),
    )
    pred_id = await insert_prediction(
        conn,
        Prediction(
            condition_id=cid,
            token_id=f"tok_{cid}",
            p_model=p_model,
            p_market=0.5,
            edge=p_model - 0.5,
            components={},
        ),
    )
    trade_id = await insert_trade(
        conn,
        Trade(
            condition_id=cid,
            token_id=f"tok_{cid}",
            side="BUY",
            size=10.0,
            limit_price=0.5,
            fill_price=0.5,
            slippage=0.01,
            intended_size=10.0,
            is_paper=True,
            prediction_id=pred_id,
            opened_at=now,
        ),
    )
    pnl = 5.0 if outcome == "YES" else -5.0
    await close_trade(conn, trade_id, pnl=pnl, outcome=outcome, closed_at=now)


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_brier_score_none_with_no_resolved_trades(conn):
    """No resolved trades → brier_score returns None."""
    assert await current_brier_score(conn) is None


async def test_brier_score_perfect_prediction(conn):
    """p_model=1.0 for YES and p_model=0.0 for NO → brier score = 0."""
    await _seed(conn, "perfect_yes", p_model=1.0, outcome="YES")
    await _seed(conn, "perfect_no", p_model=0.0, outcome="NO")
    score = await current_brier_score(conn)
    assert score is not None
    assert abs(score) < 1e-9


async def test_brier_score_worst_prediction(conn):
    """p_model=0.0 for YES and p_model=1.0 for NO → brier score = 1."""
    await _seed(conn, "worst_yes", p_model=0.0, outcome="YES")
    await _seed(conn, "worst_no", p_model=1.0, outcome="NO")
    score = await current_brier_score(conn)
    assert score is not None
    assert abs(score - 1.0) < 1e-9


async def test_brier_score_excludes_timeout_and_stop_loss(conn):
    """TIMEOUT and STOP_LOSS outcomes are excluded from the calculation."""
    await _seed(conn, "timeout_t", p_model=0.0, outcome="TIMEOUT")
    await _seed(conn, "stop_t", p_model=0.0, outcome="STOP_LOSS")
    assert await current_brier_score(conn) is None


async def test_brier_score_midpoint(conn):
    """p_model=0.5 for YES → brier contribution = (0.5-1)^2 = 0.25."""
    await _seed(conn, "mid_yes", p_model=0.5, outcome="YES")
    score = await current_brier_score(conn)
    assert score is not None
    assert abs(score - 0.25) < 1e-9
