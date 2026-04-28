"""Daily slippage budget: halt when cumulative slippage exceeds the limit.

daily_slippage_usd(conn, since_ts) sums slippage * size for trades opened
after since_ts.  When max_daily_slippage_usd > 0, _current_halt_reason fires
once that threshold is crossed.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import daily_slippage_usd, insert_flagged_market, insert_trade


async def _seed_trade(conn, cid: str, slippage: float, size: float, opened_at: int) -> int:
    await insert_flagged_market(
        conn,
        FlaggedMarket(
            condition_id=cid,
            yes_token=f"tok_{cid}",
            no_token=f"no_{cid}",
            mid_price=0.5,
            spread=slippage * 2,
            volume_24h=1000.0,
            flagged_at=opened_at,
        ),
    )
    return await insert_trade(
        conn,
        Trade(
            condition_id=cid,
            token_id=f"tok_{cid}",
            side="BUY",
            size=size,
            limit_price=0.5,
            fill_price=0.5 + slippage,
            slippage=slippage,
            intended_size=size,
            is_paper=True,
            opened_at=opened_at,
        ),
    )


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_daily_slippage_zero_with_no_trades(conn):
    """No trades → 0 slippage."""
    now = int(time.time())
    assert await daily_slippage_usd(conn, now - 3600) == 0.0


async def test_daily_slippage_sums_slippage_times_size(conn):
    """Slippage cost = slippage * size per trade, summed."""
    now = int(time.time())
    # slippage=0.01, size=100 → cost=1.0; slippage=0.02, size=50 → cost=1.0
    await _seed_trade(conn, "t1", slippage=0.01, size=100.0, opened_at=now)
    await _seed_trade(conn, "t2", slippage=0.02, size=50.0, opened_at=now)
    total = await daily_slippage_usd(conn, now - 3600)
    assert abs(total - 2.0) < 1e-9


async def test_daily_slippage_excludes_old_trades(conn):
    """Trades opened before the cutoff are not counted."""
    now = int(time.time())
    old = now - 7200
    cutoff = now - 3600
    await _seed_trade(conn, "old_t", slippage=0.05, size=100.0, opened_at=old)
    assert await daily_slippage_usd(conn, cutoff) == 0.0


async def test_halt_fires_when_slippage_limit_reached(conn):
    """_current_halt_reason fires when daily slippage >= max_daily_slippage_usd."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    await _seed_trade(conn, "big_slip", slippage=0.05, size=100.0, opened_at=now)  # cost=5.0

    settings = RuntimeSettings(max_daily_slippage_usd=3.0)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is not None
    assert "slippage" in reason


async def test_halt_disabled_when_limit_zero(conn):
    """max_daily_slippage_usd=0 disables the guard."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    await _seed_trade(conn, "slip_t", slippage=1.0, size=1000.0, opened_at=now)

    settings = RuntimeSettings(max_daily_slippage_usd=0.0)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is None
