"""Trailing stop — close a trade if current price dropped too far below fill price.

For each open trade during the settlement sweep, if the current orderbook mid
is lower than fill_price * (1 - stop_loss_pct), close the trade at current
price to prevent further losses.  This is a paper stop-loss, not a real order.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import insert_flagged_market, insert_trade


def _ob(token_id: str, mid: float) -> OrderBookSnapshot:
    spread = 0.02
    return OrderBookSnapshot(
        token_id=token_id,
        bids=[(mid - spread / 2, 100)],
        asks=[(mid + spread / 2, 100)],
        timestamp=int(time.time()),
    )


async def _insert_open_trade(conn, condition_id: str, fill_price: float, size: float = 100.0) -> int:
    end_iso = (datetime.now(UTC) + timedelta(days=7)).isoformat()
    trade = Trade(
        condition_id=condition_id,
        token_id=f"y_{condition_id}",
        side="BUY",
        size=size,
        limit_price=fill_price,
        fill_price=fill_price,
        slippage=0.005,
        intended_size=size,
        is_paper=True,
        prediction_id=None,
    )
    trade_id = await insert_trade(conn, trade)
    await insert_flagged_market(
        conn,
        FlaggedMarket(
            condition_id=condition_id,
            yes_token=f"y_{condition_id}",
            no_token=f"n_{condition_id}",
            mid_price=fill_price,
            spread=0.02,
            volume_24h=1000.0,
            question=f"Q {condition_id}?",
            end_date_iso=end_iso,
        ),
    )
    return trade_id


async def test_trade_closed_when_price_drops_below_stop(tmp_path):
    """A trade is closed when current mid < fill_price * (1 - stop_loss_pct)."""
    from bot.orchestrator import run_once

    fill_price = 0.60
    stop_loss_pct = 0.15
    # current mid at 0.40 → 0.40 < 0.60 * (1 - 0.15) = 0.51 → stop triggered
    current_mid = 0.40

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[])
    client.get_orderbook = AsyncMock(return_value=_ob("y_stop_mkt", current_mid))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", stop_loss_pct=stop_loss_pct)
    conn = await open_db(tmp_path / "bot.sqlite")
    trade_id = await _insert_open_trade(conn, "stop_mkt", fill_price=fill_price)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=0)

    cur = await conn.execute("SELECT closed_at, outcome, pnl FROM trades WHERE id = ?", (trade_id,))
    row = await cur.fetchone()
    assert row[0] is not None, "trade should be closed by stop"
    assert row[1] == "STOP_LOSS"
    assert row[2] < 0, "pnl should be negative (stopped out at a loss)"
    await conn.close()


async def test_trade_not_closed_when_price_above_stop(tmp_path):
    """A trade is NOT closed when current mid is above the stop threshold."""
    from bot.orchestrator import run_once

    fill_price = 0.60
    stop_loss_pct = 0.15
    # current mid at 0.55 → 0.55 > 0.60 * (1 - 0.15) = 0.51 → no stop
    current_mid = 0.55

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[])
    client.get_orderbook = AsyncMock(return_value=_ob("y_ok_mkt", current_mid))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", stop_loss_pct=stop_loss_pct)
    conn = await open_db(tmp_path / "bot.sqlite")
    trade_id = await _insert_open_trade(conn, "ok_mkt", fill_price=fill_price)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=0)

    cur = await conn.execute("SELECT closed_at FROM trades WHERE id = ?", (trade_id,))
    row = await cur.fetchone()
    assert row[0] is None, "trade above stop threshold must remain open"
    await conn.close()


async def test_stop_loss_pct_env_override(monkeypatch):
    """STOP_LOSS_PCT env var is wired into load_settings."""
    from bot.config import load_settings
    monkeypatch.setenv("STOP_LOSS_PCT", "0.20")
    s = load_settings()
    assert s.stop_loss_pct == 0.20
