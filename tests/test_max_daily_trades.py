"""Max daily trades: halt when N trades have been opened today.

daily_trades_opened(conn, since_ts) counts trades with opened_at >= since_ts.
When that count reaches max_daily_trades (0 = disabled), _current_halt_reason
returns a halt string.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import daily_trades_opened, insert_flagged_market, insert_trade


async def _seed_trade(conn, cid: str, opened_at: int | None = None) -> int:
    now = opened_at or int(time.time())
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


async def test_daily_trades_opened_zero_with_no_trades(conn):
    """No trades → 0."""
    now = int(time.time())
    day_start = now - (now % 86_400)
    assert await daily_trades_opened(conn, day_start) == 0


async def test_daily_trades_opened_counts_trades_since_cutoff(conn):
    """Trades opened today are counted; yesterday's are not."""
    now = int(time.time())
    day_start = now - (now % 86_400)
    yesterday = day_start - 3600
    await _seed_trade(conn, "old", opened_at=yesterday)
    await _seed_trade(conn, "new1", opened_at=now)
    await _seed_trade(conn, "new2", opened_at=now)
    assert await daily_trades_opened(conn, day_start) == 2


async def test_halt_fires_when_daily_trade_limit_reached(conn):
    """_current_halt_reason returns a halt string when daily trades >= limit."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    for i in range(3):
        await _seed_trade(conn, f"dt_{i}", opened_at=now)

    settings = RuntimeSettings(max_daily_trades=3)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is not None
    assert "daily trades" in reason


async def test_halt_does_not_fire_when_below_limit(conn):
    """Fewer trades than the limit → no halt."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    await _seed_trade(conn, "only_one", opened_at=now)

    settings = RuntimeSettings(max_daily_trades=5)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is None


async def test_halt_disabled_when_limit_is_zero(conn):
    """max_daily_trades=0 means disabled — never halts regardless of trades."""
    from unittest.mock import patch

    from bot.config import RuntimeSettings
    from bot.orchestrator import _current_halt_reason

    now = int(time.time())
    for i in range(100):
        await _seed_trade(conn, f"many_{i}", opened_at=now)

    settings = RuntimeSettings(max_daily_trades=0)
    with patch("bot.orchestrator.effective_bankroll_usd"):
        reason = await _current_halt_reason(conn, settings)
    assert reason is None
