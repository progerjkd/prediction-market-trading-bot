"""Market cooldown: skip markets with a recent bad exit (STOP_LOSS/TIMEOUT).

bad_exit_condition_ids(conn, since_ts) returns condition_ids that had a
STOP_LOSS or TIMEOUT outcome closed after since_ts.  When market_cooldown_hours
> 0, these condition_ids are excluded from the prediction loop in run_once.
"""
from __future__ import annotations

import time

import pytest

from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import (
    bad_exit_condition_ids,
    close_trade,
    insert_flagged_market,
    insert_trade,
)


async def _seed_closed_trade(conn, cid: str, outcome: str, closed_at: int) -> None:
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
    pnl = -2.0 if outcome in {"STOP_LOSS", "TIMEOUT"} else 2.0
    await close_trade(conn, tid, pnl=pnl, outcome=outcome, closed_at=closed_at)


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_bad_exit_ids_empty_with_no_trades(conn):
    """No trades → empty set."""
    now = int(time.time())
    assert await bad_exit_condition_ids(conn, now - 3600) == set()


async def test_bad_exit_ids_includes_stop_loss_and_timeout(conn):
    """STOP_LOSS and TIMEOUT are included; YES/NO are not."""
    now = int(time.time())
    await _seed_closed_trade(conn, "stop", "STOP_LOSS", now - 60)
    await _seed_closed_trade(conn, "timeout", "TIMEOUT", now - 60)
    await _seed_closed_trade(conn, "yes_win", "YES", now - 60)
    await _seed_closed_trade(conn, "no_lose", "NO", now - 60)
    ids = await bad_exit_condition_ids(conn, now - 3600)
    assert ids == {"stop", "timeout"}


async def test_bad_exit_ids_excludes_old_exits(conn):
    """Exits older than since_ts are not included."""
    now = int(time.time())
    old_time = now - 7200
    cutoff = now - 3600
    await _seed_closed_trade(conn, "old_stop", "STOP_LOSS", old_time)
    ids = await bad_exit_condition_ids(conn, cutoff)
    assert ids == set()


async def test_prediction_loop_skips_cooled_down_markets(conn):
    """Markets with recent bad exits are skipped even if flagged."""
    from unittest.mock import AsyncMock, MagicMock

    from bot.config import RuntimeSettings
    from bot.orchestrator import run_once
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot

    now = int(time.time())
    await _seed_closed_trade(conn, "bad_market", "STOP_LOSS", now - 60)

    market = Market(
        condition_id="bad_market",
        question="Was this market bad?",
        yes_token="tok_bad",
        no_token="no_bad",
        volume_24h=5000.0,
        liquidity=500.0,
        end_date_iso=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).replace(year=__import__("datetime").datetime.now().year + 1).isoformat(),
        closed=False,
        raw={},
    )
    book = OrderBookSnapshot(
        token_id="tok_bad",
        bids=[(0.54, 100.0)],
        asks=[(0.56, 100.0)],
        timestamp=now,
    )
    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[market])
    client.get_orderbook = AsyncMock(return_value=book)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(
        scan_min_volume=100.0,
        scan_min_liquidity=50.0,
        edge_threshold=0.04,
        scan_interval_seconds=0,
        market_cooldown_hours=1,
    )
    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
    )
    assert summary.predictions_written == 0
