"""API spend tracking — TDD RED phase."""
from __future__ import annotations

import pytest

from bot.claude.client import ClaudeForecastClient, cost_usd_from_usage
from bot.storage.db import open_db
from bot.storage.repo import daily_api_cost_usd

# ---------------------------------------------------------------------------
# cost_usd_from_usage
# ---------------------------------------------------------------------------


def test_cost_zero_when_usage_is_none():
    assert cost_usd_from_usage(None, model="claude-sonnet-4-6") == pytest.approx(0.0)


def test_cost_from_input_tokens_only():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
    # claude-sonnet-4-6: $3.00/MTok input
    cost = cost_usd_from_usage(usage, model="claude-sonnet-4-6")
    assert cost == pytest.approx(3.00, rel=0.01)


def test_cost_from_output_tokens_only():
    usage = {"input_tokens": 0, "output_tokens": 1_000_000, "cache_read_input_tokens": 0}
    # claude-sonnet-4-6: $15.00/MTok output
    cost = cost_usd_from_usage(usage, model="claude-sonnet-4-6")
    assert cost == pytest.approx(15.00, rel=0.01)


def test_cost_from_cache_read_tokens():
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1_000_000}
    # claude-sonnet-4-6: $0.30/MTok cache read
    cost = cost_usd_from_usage(usage, model="claude-sonnet-4-6")
    assert cost == pytest.approx(0.30, rel=0.01)


def test_cost_combined_tokens():
    usage = {
        "input_tokens": 100_000,
        "output_tokens": 10_000,
        "cache_read_input_tokens": 50_000,
    }
    # 0.30 + 0.15 + 0.015 = 0.465
    cost = cost_usd_from_usage(usage, model="claude-sonnet-4-6")
    assert cost == pytest.approx(0.30 + 0.15 + 0.015, rel=0.01)


def test_cost_falls_back_to_sonnet_pricing_for_unknown_model():
    usage = {"input_tokens": 1_000_000, "output_tokens": 0, "cache_read_input_tokens": 0}
    cost = cost_usd_from_usage(usage, model="claude-unknown-99")
    assert cost > 0.0


def test_cost_handles_missing_keys_gracefully():
    usage = {"input_tokens": 100}  # missing output_tokens and cache key
    cost = cost_usd_from_usage(usage, model="claude-sonnet-4-6")
    assert cost >= 0.0


# ---------------------------------------------------------------------------
# ForecastResult.cost_usd computed from usage
# ---------------------------------------------------------------------------


async def test_forecast_client_populates_cost_when_api_key_absent():
    """Fallback path returns ForecastResult with cost_usd=0.0 (no tokens used)."""
    client = ClaudeForecastClient(api_key=None)
    result = await client.forecast_probability(
        market_question="Will X happen?",
        p_market=0.5,
        research_brief="test",
    )
    assert isinstance(result.cost_usd, float)
    assert result.cost_usd == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# run_once persists api_spend after each prediction
# ---------------------------------------------------------------------------


async def test_run_once_inserts_api_spend_for_each_prediction(tmp_path):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from bot.config import RuntimeSettings
    from bot.orchestrator import run_once
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot

    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", bankroll_usdc=10_000)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[
        Market(
            condition_id="c1", question="Q?", yes_token="t1", no_token="n1",
            end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
            volume_24h=5000, liquidity=2000, closed=False, raw={},
        )
    ])
    client.get_orderbook = AsyncMock(return_value=OrderBookSnapshot(
        token_id="t1", asks=[(0.55, 500)], bids=[(0.52, 100)], timestamp=0,
    ))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    # mock_ai=True uses deterministic local probs — no API call, cost=0
    await run_once(
        settings=settings, conn=conn,
        polymarket_client=client,
        mock_ai=True,
        max_markets=1,
    )

    cur = await conn.execute("SELECT COUNT(*) FROM api_spend")
    row = await cur.fetchone()
    assert row[0] == 1, f"expected 1 api_spend row, got {row[0]}"

    await conn.close()


async def test_run_once_api_spend_cost_zero_for_mock_ai(tmp_path):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from bot.config import RuntimeSettings
    from bot.orchestrator import run_once
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot

    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", bankroll_usdc=10_000)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[
        Market(
            condition_id="c1", question="Q?", yes_token="t1", no_token="n1",
            end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
            volume_24h=5000, liquidity=2000, closed=False, raw={},
        )
    ])
    client.get_orderbook = AsyncMock(return_value=OrderBookSnapshot(
        token_id="t1", asks=[(0.55, 500)], bids=[(0.52, 100)], timestamp=0,
    ))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    await run_once(
        settings=settings, conn=conn,
        polymarket_client=client,
        mock_ai=True,
        max_markets=1,
    )

    import time
    now = int(time.time())
    cost = await daily_api_cost_usd(conn, now - 86_400)
    assert cost == pytest.approx(0.0)  # mock_ai = no real API call

    await conn.close()
