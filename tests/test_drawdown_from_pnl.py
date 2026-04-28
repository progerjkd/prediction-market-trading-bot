"""Drawdown computed from cumulative closed-trade P&L.

current_drawdown_pct(conn, base_bankroll) walks all closed trades ordered by
closed_at and computes the peak-to-trough drawdown of running equity.
Returns 0.0 when there are no closed trades.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import close_trade, current_drawdown_pct, insert_flagged_market, insert_trade


async def _seed_closed(conn, cid: str, pnl: float, closed_at: int) -> None:
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
    await close_trade(conn, tid, pnl=pnl, outcome="YES" if pnl > 0 else "NO", closed_at=closed_at)


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_drawdown_zero_with_no_trades(conn):
    """No closed trades → drawdown = 0.0."""
    assert await current_drawdown_pct(conn, base_bankroll=1000.0) == 0.0


async def test_drawdown_zero_with_only_gains(conn):
    """Pure winning streak → drawdown = 0.0."""
    now = int(time.time())
    for i, pnl in enumerate([100.0, 50.0, 200.0]):
        await _seed_closed(conn, f"win_{i}", pnl, now - 300 + i)
    assert await current_drawdown_pct(conn, base_bankroll=1000.0) == 0.0


async def test_drawdown_from_peak_to_trough(conn):
    """Drawdown = (peak_equity - trough_equity) / peak_equity."""
    now = int(time.time())
    # equity: 1000 → 1100 (peak) → 1000 → 900 (trough = drawdown 200/1100 ≈ 0.1818)
    await _seed_closed(conn, "gain", 100.0, now - 3)
    await _seed_closed(conn, "loss1", -100.0, now - 2)
    await _seed_closed(conn, "loss2", -100.0, now - 1)
    dd = await current_drawdown_pct(conn, base_bankroll=1000.0)
    expected = 200.0 / 1100.0
    assert abs(dd - expected) < 1e-9


async def test_drawdown_recovers_after_new_high(conn):
    """After a new equity high, the drawdown resets to 0."""
    now = int(time.time())
    # equity: 1000 → 900 → 1100 (new peak) → drawdown = 0
    await _seed_closed(conn, "loss", -100.0, now - 2)
    await _seed_closed(conn, "gain", 200.0, now - 1)
    dd = await current_drawdown_pct(conn, base_bankroll=1000.0)
    assert dd == 0.0


async def test_halt_fires_when_drawdown_exceeds_limit(conn):
    """_current_halt_reason returns halt string when drawdown >= max_drawdown_pct."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    yesterday = now - 86_400 - 3600  # older than today's day_start
    # bankroll=1000; equity: 1000 → 1200 (peak) → 950 (trough): dd = 250/1200 ≈ 0.208
    await _seed_closed(conn, "gain_dd", 200.0, yesterday)
    await _seed_closed(conn, "big_loss_dd", -250.0, yesterday + 60)

    settings = RuntimeSettings(bankroll_usdc=1000.0, max_drawdown_pct=0.15)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is not None
    assert "drawdown" in reason
