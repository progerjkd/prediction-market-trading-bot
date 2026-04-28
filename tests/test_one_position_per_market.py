"""One open position per market — don't stack on the same condition_id.

run_once should skip prediction for a market that already has an open (unclosed)
trade, even if the global open_positions count is below the limit.  This prevents
doubling-down on positions that haven't resolved.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.models import Trade
from bot.storage.repo import insert_trade


def _market(cid: str) -> Market:
    return Market(
        condition_id=cid, question="Q?", yes_token=f"y_{cid}", no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=1_000.0, liquidity=1_000.0, closed=False, raw={},
    )


def _ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 100)], asks=[(0.52, 100)], timestamp=0)


async def _open_trade(conn, condition_id: str) -> None:
    trade = Trade(
        condition_id=condition_id, token_id=f"y_{condition_id}",
        side="BUY", size=50.0, limit_price=0.52, fill_price=0.52,
        slippage=0.01, intended_size=50.0, is_paper=True, prediction_id=None,
    )
    await insert_trade(conn, trade)


async def test_market_with_open_trade_not_predicted(tmp_path):
    """A market already held open must not generate a new prediction."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("dup"), _market("fresh")])
    client.get_orderbook = AsyncMock(side_effect=lambda tid: _ob(tid))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")

    # Open a trade on "dup" — should block new prediction
    await _open_trade(conn, "dup")

    summary = await run_once(settings=settings, conn=conn, polymarket_client=client,
                             mock_ai=True, scan_only=False, max_markets=10)

    # "dup" is in flagged (passed filter), but prediction skipped due to open position
    # "fresh" should be predicted
    # predictions_written should be 1 (only fresh), not 2
    assert summary.predictions_written <= 1, (
        f"expected at most 1 prediction (for fresh), got {summary.predictions_written}"
    )
    await conn.close()


async def test_market_without_open_trade_is_predicted(tmp_path):
    """A market with no open trade proceeds to prediction normally."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("new_mkt")])
    client.get_orderbook = AsyncMock(side_effect=lambda tid: _ob(tid))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")

    summary = await run_once(settings=settings, conn=conn, polymarket_client=client,
                             mock_ai=True, scan_only=False, max_markets=10)

    assert summary.predictions_written >= 1
    await conn.close()
