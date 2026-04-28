"""Position timeout — force-close trades whose end_date_iso has passed by more than
a configurable grace period, even if the resolution API doesn't return a settled price.

This prevents capital from being locked indefinitely in markets that fail to resolve
cleanly (e.g., API outages, ambiguous outcomes).  The trade is written off at price=0
(worst-case loss) and logged as cause='timeout'.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import aiosqlite

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.models import Trade
from bot.storage.repo import insert_trade


def _market(cid: str) -> Market:
    return Market(
        condition_id=cid, question="Q?", yes_token=f"y_{cid}", no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=1000.0, liquidity=1000.0, closed=False, raw={},
    )


def _ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 10)], asks=[(0.52, 10)], timestamp=0)


async def _insert_open_trade(conn: aiosqlite.Connection, condition_id: str, end_date_iso: str) -> int:
    trade = Trade(
        condition_id=condition_id,
        token_id=f"y_{condition_id}",
        side="BUY",
        size=100.0,
        limit_price=0.55,
        fill_price=0.55,
        slippage=0.01,
        intended_size=100.0,
        is_paper=True,
        prediction_id=None,
    )
    trade_id = await insert_trade(conn, trade)
    # fetch_open_trades joins markets_flagged for end_date_iso — insert a record there
    await conn.execute(
        "INSERT OR REPLACE INTO markets_flagged "
        "(condition_id, yes_token, no_token, mid_price, spread, volume_24h, "
        " question, end_date_iso, liquidity, edge_proxy, raw_json, flagged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            condition_id, f"y_{condition_id}", f"n_{condition_id}",
            0.50, 0.04, 1000.0, f"Q {condition_id}?", end_date_iso,
            1000.0, 0.02, "{}", int(time.time()),
        ),
    )
    await conn.commit()
    return trade_id


async def test_timed_out_trade_is_closed_when_unresolved(tmp_path):
    """A trade past its end_date by more than the grace period is force-closed at pnl=0."""
    from bot.orchestrator import run_once

    # Market resolution says unresolved (e.g., API not updated yet)
    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[])
    client.get_orderbook = AsyncMock(return_value=_ob("y_expired"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", position_timeout_days=3)
    conn = await open_db(tmp_path / "bot.sqlite")

    # Trade ended 5 days ago — beyond the 3-day timeout
    expired_end = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    trade_id = await _insert_open_trade(conn, "expired_mkt", expired_end)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=0)

    # Trade should now be closed (closed_at is set)
    cur = await conn.execute("SELECT closed_at, pnl, outcome FROM trades WHERE id = ?", (trade_id,))
    row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None, "closed_at should be set for timed-out trade"
    assert row[1] is not None, "pnl should be recorded (0 or negative)"
    assert row[2] == "TIMEOUT"
    await conn.close()


async def test_trade_within_grace_period_is_not_closed(tmp_path):
    """A trade past end_date but within the grace period is left open."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[])
    client.get_orderbook = AsyncMock(return_value=_ob("x"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", position_timeout_days=7)
    conn = await open_db(tmp_path / "bot.sqlite")

    # Trade ended 2 days ago — within the 7-day timeout
    recent_end = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    trade_id = await _insert_open_trade(conn, "grace_mkt", recent_end)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=0)

    cur = await conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
    row = await cur.fetchone()
    assert row[0] is None, "trade within grace period must stay open"
    await conn.close()


async def test_resolved_trade_not_affected_by_timeout(tmp_path):
    """Already-resolved trades (closed_at set) are not touched by the timeout sweep."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[])
    client.get_orderbook = AsyncMock(return_value=_ob("x"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=True, final_yes_price=1.0))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", position_timeout_days=3)
    conn = await open_db(tmp_path / "bot.sqlite")

    expired_end = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    trade_id = await _insert_open_trade(conn, "resolved_mkt", expired_end)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=0)

    cur = await conn.execute("SELECT outcome FROM trades WHERE id = ?", (trade_id,))
    row = await cur.fetchone()
    # Should be resolved via normal settlement, not TIMEOUT
    assert row[0] != "TIMEOUT"
    await conn.close()


async def test_position_timeout_days_env_override(monkeypatch):
    """POSITION_TIMEOUT_DAYS env var is wired into load_settings."""
    from bot.config import load_settings
    monkeypatch.setenv("POSITION_TIMEOUT_DAYS", "14")
    s = load_settings()
    assert s.position_timeout_days == 14
