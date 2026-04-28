"""Recent win rate: fraction of last-N closed YES/NO trades that were YES.

recent_win_rate(conn, n) fetches the N most recent YES/NO closed trades
ordered by closed_at DESC and computes win_rate. Returns None when there
are no qualifying trades.  TIMEOUT/STOP_LOSS are excluded.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import close_trade, insert_flagged_market, insert_trade, recent_win_rate


async def _seed(conn, cid: str, outcome: str, closed_at: int) -> None:
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
    tid = await insert_trade(
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
            opened_at=now - 3600,
        ),
    )
    pnl = 2.0 if outcome == "YES" else -2.0
    await close_trade(conn, tid, pnl=pnl, outcome=outcome, closed_at=closed_at)


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_recent_win_rate_none_with_no_trades(conn):
    """No closed trades → None."""
    assert await recent_win_rate(conn, 10) is None


async def test_recent_win_rate_all_wins(conn):
    """All YES → win rate = 1.0."""
    now = int(time.time())
    for i in range(3):
        await _seed(conn, f"win_{i}", "YES", now - 3 + i)
    assert await recent_win_rate(conn, 10) == 1.0


async def test_recent_win_rate_uses_last_n(conn):
    """Only the N most recent trades are counted."""
    now = int(time.time())
    # 3 old losses, then 2 wins
    for i in range(3):
        await _seed(conn, f"old_loss_{i}", "NO", now - 100 + i)
    for i in range(2):
        await _seed(conn, f"recent_win_{i}", "YES", now - 2 + i)
    # last 2 trades: both YES
    assert await recent_win_rate(conn, 2) == 1.0
    # last 5: 2 YES, 3 NO = 0.4
    wr = await recent_win_rate(conn, 5)
    assert abs(wr - 0.4) < 1e-9


async def test_recent_win_rate_excludes_timeout_and_stop_loss(conn):
    """TIMEOUT and STOP_LOSS are excluded from the count."""
    now = int(time.time())
    await _seed(conn, "timeout_t", "TIMEOUT", now - 2)
    await _seed(conn, "stop_t", "STOP_LOSS", now - 1)
    assert await recent_win_rate(conn, 10) is None
