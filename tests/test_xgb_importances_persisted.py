"""XGBoost feature importances are stored in prediction components JSON.

When the real xgb_infer path runs (not mock_ai), the returned importances dict
must appear under 'xgb_importances' in the prediction's components stored to DB.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import RuntimeSettings
from bot.storage.db import open_db


def _make_polymarket_client(mid: float = 0.55):
    """Minimal stub for PolymarketClient."""
    from bot.polymarket.client import Market, OrderBookSnapshot

    market = Market(
        condition_id="cid_imp_test",
        question="Will importances be persisted?",
        yes_token="tok_imp",
        no_token="no_imp",
        volume_24h=5000.0,
        liquidity=500.0,
        end_date_iso=(
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            + __import__("datetime").timedelta(days=10)
        ).isoformat(),
        closed=False,
        raw={},
    )
    book = OrderBookSnapshot(
        token_id="tok_imp",
        bids=[(mid - 0.01, 100.0)],
        asks=[(mid + 0.01, 100.0)],
        timestamp=int(time.time()),
    )
    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[market])
    client.get_orderbook = AsyncMock(return_value=book)
    client.close = AsyncMock()
    return client


@pytest.fixture
async def conn(tmp_path):
    db = await open_db(tmp_path / "bot.sqlite")
    yield db
    await db.close()


async def test_xgb_importances_stored_in_prediction_components(conn):
    """When xgb_infer returns importances, they appear in the stored prediction."""
    from bot.orchestrator import run_once

    fake_importances = {"current_mid": 0.40, "spread": 0.25, "volume_24h": 0.15}

    settings = RuntimeSettings(
        scan_min_volume=100.0,
        scan_min_liquidity=50.0,
        edge_threshold=0.04,
        scan_interval_seconds=0,
    )
    client = _make_polymarket_client(mid=0.55)

    with (
        patch("bot.orchestrator.xgb_infer", return_value=(0.70, "xgboost_model", fake_importances)),
        patch("bot.orchestrator.ClaudeForecastClient") as mock_claude_cls,
    ):
        mock_forecaster = MagicMock()
        forecast_result = MagicMock()
        forecast_result.probability = 0.65
        forecast_result.reasoning = "looks likely"
        forecast_result.cost_usd = 0.01
        mock_forecaster.forecast_probability = AsyncMock(return_value=forecast_result)
        mock_forecaster.model = "claude-mock"
        mock_claude_cls.return_value = mock_forecaster

        await run_once(
            settings=settings,
            conn=conn,
            polymarket_client=client,
            mock_ai=False,
        )

    cur = await conn.execute("SELECT components_json FROM predictions ORDER BY id DESC LIMIT 1")
    row = await cur.fetchone()
    assert row is not None, "No prediction was written"
    components = json.loads(row[0])
    assert "xgb_importances" in components
    assert components["xgb_importances"] == fake_importances
