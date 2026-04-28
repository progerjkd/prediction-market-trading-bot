"""Market deduplication — skip recently-flagged markets to avoid redundant predictions.

run_once should not re-fetch orderbooks for markets that were already flagged
within the last scan_interval_seconds.  Only stale or never-seen markets are
processed, saving Polymarket API calls and Claude budget.
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(cid: str) -> Market:
    return Market(
        condition_id=cid,
        question=f"Q {cid}?",
        yes_token=f"y_{cid}",
        no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=1_000.0,
        liquidity=1_000.0,
        closed=False,
        raw={},
    )


def _ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 10)], asks=[(0.52, 10)], timestamp=0)


async def _insert_flagged(conn, condition_id: str, flagged_at: int) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO markets_flagged "
        "(condition_id, yes_token, no_token, mid_price, spread, volume_24h, "
        " question, end_date_iso, liquidity, edge_proxy, raw_json, flagged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            condition_id, f"y_{condition_id}", f"n_{condition_id}",
            0.50, 0.04, 1000.0, f"Q {condition_id}?",
            (datetime.now(UTC) + timedelta(days=7)).isoformat(),
            1000.0, 0.02, "{}", flagged_at,
        ),
    )
    await conn.commit()


async def test_recently_flagged_market_orderbook_not_fetched(tmp_path):
    """A market flagged within scan_interval_seconds is skipped — no orderbook call."""
    from bot.orchestrator import run_once

    fetched: list[str] = []

    async def fake_ob(token_id: str) -> OrderBookSnapshot:
        fetched.append(token_id)
        return _ob(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("recent"), _market("new")])
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=900)
    conn = await open_db(tmp_path / "bot.sqlite")

    # "recent" was flagged 60 s ago — within the 900 s window
    await _insert_flagged(conn, "recent", int(time.time()) - 60)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=10)

    # Only "new" should be fetched; "recent" was already processed
    assert "y_new" in fetched
    assert "y_recent" not in fetched
    await conn.close()


async def test_stale_flagged_market_is_refetched(tmp_path):
    """A market flagged outside scan_interval_seconds is re-evaluated."""
    from bot.orchestrator import run_once

    fetched: list[str] = []

    async def fake_ob(token_id: str) -> OrderBookSnapshot:
        fetched.append(token_id)
        return _ob(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("stale")])
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=900)
    conn = await open_db(tmp_path / "bot.sqlite")

    # "stale" was flagged 2000 s ago — outside the 900 s window
    await _insert_flagged(conn, "stale", int(time.time()) - 2000)

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=10)

    assert "y_stale" in fetched
    await conn.close()


async def test_never_flagged_market_is_always_fetched(tmp_path):
    """Markets with no prior flag record are always processed."""
    from bot.orchestrator import run_once

    fetched: list[str] = []

    async def fake_ob(token_id: str) -> OrderBookSnapshot:
        fetched.append(token_id)
        return _ob(token_id)

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("brand_new")])
    client.get_orderbook = AsyncMock(side_effect=fake_ob)
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=900)
    conn = await open_db(tmp_path / "bot.sqlite")
    # No prior flag rows inserted

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=10)

    assert "y_brand_new" in fetched
    await conn.close()


async def test_dedup_count_reflected_in_run_summary(tmp_path):
    """RunSummary.scanned_markets counts all API markets; deduped ones don't inflate flagged count."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("dup"), _market("fresh")])
    client.get_orderbook = AsyncMock(return_value=_ob("y_fresh"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=900)
    conn = await open_db(tmp_path / "bot.sqlite")
    await _insert_flagged(conn, "dup", int(time.time()) - 30)

    summary = await run_once(settings=settings, conn=conn, polymarket_client=client,
                             mock_ai=True, scan_only=True, max_markets=10)

    # All API markets counted; deduped market doesn't show up as newly flagged
    assert summary.scanned_markets == 2
    # "dup" was skipped so can't be in fresh flagged results
    assert "y_dup" not in summary.flagged_yes_tokens
    await conn.close()
