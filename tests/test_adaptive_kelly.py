"""Adaptive Kelly: reduce position sizing when recent win rate is below threshold.

When adaptive_kelly_min_win_rate > 0 and recent_win_rate < threshold,
the effective kelly_fraction is multiplied by adaptive_kelly_scale_factor
(default 0.5). When win rate is above the threshold or disabled (threshold=0),
the full kelly_fraction is used.
"""
from __future__ import annotations

import time

import pytest

from bot.orchestrator import _adaptive_kelly_fraction
from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import close_trade, insert_flagged_market, insert_trade


async def _seed_outcome(conn, cid: str, outcome: str) -> None:
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
    await close_trade(conn, tid, pnl=pnl, outcome=outcome, closed_at=now)


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_adaptive_kelly_returns_full_fraction_when_disabled(conn):
    """adaptive_kelly_min_win_rate=0 → full kelly_fraction always."""
    from bot.config import RuntimeSettings
    s = RuntimeSettings(kelly_fraction=0.25, adaptive_kelly_min_win_rate=0.0)
    fraction = await _adaptive_kelly_fraction(conn, s)
    assert fraction == 0.25


async def test_adaptive_kelly_returns_full_fraction_above_threshold(conn):
    """Win rate above threshold → full kelly_fraction."""
    from bot.config import RuntimeSettings

    for i in range(8):
        await _seed_outcome(conn, f"win_{i}", "YES")
    for i in range(2):
        await _seed_outcome(conn, f"loss_{i}", "NO")

    s = RuntimeSettings(
        kelly_fraction=0.25,
        adaptive_kelly_min_win_rate=0.5,
        adaptive_kelly_lookback_n=10,
        adaptive_kelly_scale_factor=0.5,
    )
    fraction = await _adaptive_kelly_fraction(conn, s)
    assert fraction == 0.25


async def test_adaptive_kelly_scales_down_below_threshold(conn):
    """Win rate below threshold → kelly_fraction * scale_factor."""
    from bot.config import RuntimeSettings

    for i in range(3):
        await _seed_outcome(conn, f"win_{i}", "YES")
    for i in range(7):
        await _seed_outcome(conn, f"loss_{i}", "NO")

    s = RuntimeSettings(
        kelly_fraction=0.25,
        adaptive_kelly_min_win_rate=0.5,
        adaptive_kelly_lookback_n=10,
        adaptive_kelly_scale_factor=0.5,
    )
    fraction = await _adaptive_kelly_fraction(conn, s)
    assert abs(fraction - 0.125) < 1e-9


async def test_adaptive_kelly_returns_full_when_no_history(conn):
    """No closed trades → full kelly_fraction (not enough data to penalize)."""
    from bot.config import RuntimeSettings

    s = RuntimeSettings(
        kelly_fraction=0.25,
        adaptive_kelly_min_win_rate=0.6,
        adaptive_kelly_lookback_n=10,
    )
    fraction = await _adaptive_kelly_fraction(conn, s)
    assert fraction == 0.25
