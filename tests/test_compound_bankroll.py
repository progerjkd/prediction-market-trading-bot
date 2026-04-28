"""Compound bankroll: Kelly sizing uses realized P&L to adjust effective bankroll.

net_realized_pnl returns SUM(pnl) for all closed trades; effective_bankroll_usd
floors at 10% of the base to prevent degenerate sizing.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import (
    close_trade,
    insert_flagged_market,
    insert_trade,
    net_realized_pnl,
)


async def _seed_market(conn, cid: str) -> None:
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


async def _seed_trade(conn, cid: str) -> int:
    await _seed_market(conn, cid)
    now = int(time.time())
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


async def test_net_realized_pnl_zero_with_no_closed_trades(conn):
    """With no trades closed, net P&L is 0."""
    assert await net_realized_pnl(conn) == 0.0


async def test_net_realized_pnl_sums_all_closed_pnl(conn):
    """Net P&L is the sum of pnl across all closed trades."""
    t1 = await _seed_trade(conn, "cid_a")
    t2 = await _seed_trade(conn, "cid_b")
    now = int(time.time())
    await close_trade(conn, t1, pnl=5.0, outcome="YES", closed_at=now)
    await close_trade(conn, t2, pnl=-2.0, outcome="NO", closed_at=now)
    pnl = await net_realized_pnl(conn)
    assert abs(pnl - 3.0) < 1e-9


async def test_net_realized_pnl_ignores_open_trades(conn):
    """Open trades (no closed_at) do not count toward net P&L."""
    await _seed_trade(conn, "open_cid")
    assert await net_realized_pnl(conn) == 0.0


async def test_effective_bankroll_grows_with_profit(conn):
    """After profitable trades, effective bankroll > base bankroll."""
    from bot.orchestrator import effective_bankroll_usd

    t1 = await _seed_trade(conn, "win_a")
    now = int(time.time())
    await close_trade(conn, t1, pnl=100.0, outcome="YES", closed_at=now)

    base = 1000.0
    result = await effective_bankroll_usd(conn, base_bankroll=base)
    assert result > base


async def test_effective_bankroll_shrinks_with_losses(conn):
    """After losing trades, effective bankroll < base bankroll but >= floor."""
    from bot.orchestrator import effective_bankroll_usd

    t1 = await _seed_trade(conn, "loss_a")
    now = int(time.time())
    await close_trade(conn, t1, pnl=-950.0, outcome="NO", closed_at=now)

    base = 1000.0
    result = await effective_bankroll_usd(conn, base_bankroll=base)
    floor = base * 0.10
    assert result == floor


async def test_effective_bankroll_equals_base_with_no_trades(conn):
    """With no closed trades, effective bankroll equals base bankroll."""
    from bot.orchestrator import effective_bankroll_usd

    base = 1000.0
    result = await effective_bankroll_usd(conn, base_bankroll=base)
    assert result == base
