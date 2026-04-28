"""Consecutive-loss circuit breaker.

consecutive_losses(conn) counts how many of the most-recent closed trades
have pnl < 0, stopping the count at the first non-loss.  When this count
reaches max_consecutive_losses in RuntimeSettings, _current_halt_reason
returns a halt string so no new trades are opened.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import close_trade, consecutive_losses, insert_flagged_market, insert_trade


async def _seed_trade(conn, cid: str) -> int:
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
    return await insert_trade(
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
            opened_at=now,
        ),
    )


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_consecutive_losses_zero_with_no_closed_trades(conn):
    """No closed trades → 0 consecutive losses."""
    assert await consecutive_losses(conn) == 0


async def test_consecutive_losses_counts_from_most_recent(conn):
    """Three consecutive losses after a win → streak is 3."""
    now = int(time.time())
    win = await _seed_trade(conn, "win")
    await close_trade(conn, win, pnl=5.0, outcome="YES", closed_at=now - 4)
    for i in range(3):
        tid = await _seed_trade(conn, f"loss_{i}")
        await close_trade(conn, tid, pnl=-2.0, outcome="NO", closed_at=now - 3 + i)
    assert await consecutive_losses(conn) == 3


async def test_consecutive_losses_resets_after_win(conn):
    """A win in the middle resets the streak."""
    now = int(time.time())
    for i in range(2):
        tid = await _seed_trade(conn, f"early_loss_{i}")
        await close_trade(conn, tid, pnl=-1.0, outcome="NO", closed_at=now - 10 + i)
    win = await _seed_trade(conn, "mid_win")
    await close_trade(conn, win, pnl=3.0, outcome="YES", closed_at=now - 5)
    tid = await _seed_trade(conn, "latest_loss")
    await close_trade(conn, tid, pnl=-1.0, outcome="NO", closed_at=now - 1)
    assert await consecutive_losses(conn) == 1


async def test_halt_reason_fires_when_streak_reaches_limit(conn):
    """_current_halt_reason returns a non-None string when streak >= max."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    for i in range(3):
        tid = await _seed_trade(conn, f"halt_loss_{i}")
        await close_trade(conn, tid, pnl=-2.0, outcome="NO", closed_at=now - 3 + i)

    settings = RuntimeSettings(max_consecutive_losses=3)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is not None
    assert "consecutive" in reason


async def test_halt_reason_does_not_fire_when_below_limit(conn):
    """When streak < max_consecutive_losses, no halt is raised."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    tid = await _seed_trade(conn, "one_loss")
    await close_trade(conn, tid, pnl=-2.0, outcome="NO", closed_at=now)

    settings = RuntimeSettings(max_consecutive_losses=5)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is None
