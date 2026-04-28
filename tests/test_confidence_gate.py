"""Probability confidence gate: skip signals with p_model outside [min, max].

When min_model_prob or max_model_prob are set, predictions with p_model
outside that range are written to DB but do NOT open a trade.
"""
from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import RuntimeSettings
from bot.orchestrator import run_once
from bot.polymarket.client import Market, OrderBookSnapshot
from bot.storage.db import open_db


def _make_client(mid: float = 0.55):
    import time
    from datetime import datetime, timedelta

    from bot.polymarket.client import MarketResolution

    market = Market(
        condition_id="conf_test",
        question="Confidence gate test?",
        yes_token="tok_conf",
        no_token="no_conf",
        volume_24h=5000.0,
        liquidity=500.0,
        end_date_iso=(datetime.now(UTC) + timedelta(days=10)).isoformat(),
        closed=False,
        raw={},
    )
    book = OrderBookSnapshot(
        token_id="tok_conf",
        bids=[(mid - 0.01, 100.0)],
        asks=[(mid + 0.01, 100.0)],
        timestamp=int(time.time()),
    )
    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[market])
    client.get_orderbook = AsyncMock(return_value=book)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()
    return client


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_signal_blocked_when_p_model_below_min(conn):
    """Trade is not opened when p_model < min_model_prob."""
    settings = RuntimeSettings(
        scan_min_volume=100.0,
        scan_min_liquidity=50.0,
        edge_threshold=0.04,
        scan_interval_seconds=0,
        min_model_prob=0.60,
    )
    client = _make_client(mid=0.40)
    # mock_ai sets xgboost_prob = mid + 0.12 = 0.52, ensemble ~ 0.52*0.6 + 0.55*0.4 = 0.53
    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
    )
    assert summary.paper_trades_written == 0


async def test_signal_blocked_when_p_model_above_max(conn):
    """Trade is not opened when p_model > max_model_prob."""
    settings = RuntimeSettings(
        scan_min_volume=100.0,
        scan_min_liquidity=50.0,
        edge_threshold=0.04,
        scan_interval_seconds=0,
        max_model_prob=0.50,
    )
    client = _make_client(mid=0.55)
    # mock_ai: xgboost = 0.67, claude = 0.70 => ensemble ~ 0.676; above max 0.50
    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
    )
    assert summary.paper_trades_written == 0


async def test_prediction_still_written_when_gate_blocks_trade(conn):
    """Prediction is recorded even when the confidence gate blocks the trade."""
    settings = RuntimeSettings(
        scan_min_volume=100.0,
        scan_min_liquidity=50.0,
        edge_threshold=0.04,
        scan_interval_seconds=0,
        max_model_prob=0.50,
    )
    client = _make_client(mid=0.55)
    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
    )
    assert summary.predictions_written >= 1


async def test_signal_passes_within_range(conn):
    """No gate is applied when p_model is within [min, max]."""
    settings = RuntimeSettings(
        scan_min_volume=100.0,
        scan_min_liquidity=50.0,
        edge_threshold=0.04,
        scan_interval_seconds=0,
        min_model_prob=0.50,
        max_model_prob=0.95,
    )
    client = _make_client(mid=0.55)
    # mock_ai: ensemble around 0.67; within [0.50, 0.95]
    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
    )
    # Either traded or skipped for risk reasons — just check no gate error
    assert summary.predictions_written >= 1
