"""Narrative score wiring from Claude reasoning — TDD RED phase."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from sentiment import lexical_sentiment_score

# ---------------------------------------------------------------------------
# lexical_sentiment_score (existing function — verify contract)
# ---------------------------------------------------------------------------


def test_sentiment_positive_words_give_positive_score():
    score = lexical_sentiment_score("The market is likely to win with strong support")
    assert score > 0.0


def test_sentiment_negative_words_give_negative_score():
    score = lexical_sentiment_score("Resolution is unlikely after rejection and delay")
    assert score < 0.0


def test_sentiment_neutral_text_returns_zero():
    score = lexical_sentiment_score("The market closes in seven days")
    assert score == pytest.approx(0.0)


def test_sentiment_score_bounded_negative_one_to_one():
    score = lexical_sentiment_score("unlikely unlikely unlikely unlikely rejected failed failed")
    assert -1.0 <= score <= 1.0


def test_sentiment_score_bounded_negative_for_extreme_bearish():
    score = lexical_sentiment_score("unlikely unlikely rejected rejected failed bearish")
    assert score == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# orchestrator._predict passes narrative_score to XGBoost features
# ---------------------------------------------------------------------------


async def test_predict_passes_narrative_score_from_claude_reasoning(tmp_path):
    """narrative_score in xgb feature dict should reflect Claude's reasoning sentiment."""
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, MagicMock

    from bot.claude.client import ForecastResult
    from bot.config import RuntimeSettings
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
    from bot.storage.db import open_db

    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", bankroll_usdc=10_000, edge_threshold=0.04)

    # Claude returns a strongly bullish reasoning
    bullish_reasoning = "This market is very likely to win with strong support and approval"
    forecaster = MagicMock()
    forecaster.forecast_probability = AsyncMock(
        return_value=ForecastResult(probability=0.75, reasoning=bullish_reasoning)
    )

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

    from bot.orchestrator import run_once
    await run_once(
        settings=settings, conn=conn,
        polymarket_client=client,
        claude_client=forecaster,
        mock_ai=False,
        max_markets=1,
    )

    # Check that the prediction components include a non-zero narrative_score
    cur = await conn.execute("SELECT components_json FROM predictions LIMIT 1")
    row = await cur.fetchone()
    import json
    components = json.loads(row[0])
    assert "narrative_score" in components
    assert components["narrative_score"] > 0.0, f"expected positive score, got {components['narrative_score']}"

    await conn.close()


async def test_predict_narrative_score_negative_for_bearish_reasoning(tmp_path):
    from datetime import UTC, datetime, timedelta

    from bot.claude.client import ForecastResult
    from bot.config import RuntimeSettings
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
    from bot.storage.db import open_db

    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", bankroll_usdc=10_000, edge_threshold=0.04)

    bearish_reasoning = "This outcome is very unlikely following rejection and delay"
    forecaster = MagicMock()
    forecaster.forecast_probability = AsyncMock(
        return_value=ForecastResult(probability=0.25, reasoning=bearish_reasoning)
    )

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[
        Market(
            condition_id="c2", question="Q?", yes_token="t2", no_token="n2",
            end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
            volume_24h=5000, liquidity=2000, closed=False, raw={},
        )
    ])
    client.get_orderbook = AsyncMock(return_value=OrderBookSnapshot(
        token_id="t2", asks=[(0.55, 500)], bids=[(0.52, 100)], timestamp=0,
    ))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    from bot.orchestrator import run_once
    await run_once(
        settings=settings, conn=conn,
        polymarket_client=client,
        claude_client=forecaster,
        mock_ai=False,
        max_markets=1,
    )

    cur = await conn.execute("SELECT components_json FROM predictions LIMIT 1")
    row = await cur.fetchone()
    import json
    components = json.loads(row[0])
    assert components["narrative_score"] < 0.0

    await conn.close()


async def test_predict_narrative_score_zero_for_mock_ai(tmp_path):
    """mock_ai mode should still set narrative_score (0.0 is fine as mock reason has no keywords)."""
    from datetime import UTC, datetime, timedelta

    from bot.config import RuntimeSettings
    from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
    from bot.storage.db import open_db

    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", bankroll_usdc=10_000, edge_threshold=0.04)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[
        Market(
            condition_id="c3", question="Q?", yes_token="t3", no_token="n3",
            end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
            volume_24h=5000, liquidity=2000, closed=False, raw={},
        )
    ])
    client.get_orderbook = AsyncMock(return_value=OrderBookSnapshot(
        token_id="t3", asks=[(0.55, 500)], bids=[(0.52, 100)], timestamp=0,
    ))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    from bot.orchestrator import run_once
    await run_once(
        settings=settings, conn=conn,
        polymarket_client=client,
        mock_ai=True,
        max_markets=1,
    )

    cur = await conn.execute("SELECT components_json FROM predictions LIMIT 1")
    row = await cur.fetchone()
    import json
    components = json.loads(row[0])
    assert "narrative_score" in components

    await conn.close()
