"""scan_fetch_limit decouples pool size from max_markets — TDD."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(cid: str) -> Market:
    return Market(
        condition_id=cid, question="Q?", yes_token=f"y_{cid}", no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=100.0, liquidity=100.0, closed=False, raw={},
    )


def _ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 10)], asks=[(0.52, 10)], timestamp=0)


async def test_fetch_limit_used_instead_of_max_markets_as_page_size(tmp_path):
    """list_markets is called with scan_fetch_limit, not max_markets."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("a")])
    client.get_orderbook = AsyncMock(return_value=_ob("y_a"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_fetch_limit=50)
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=3)

    # list_markets should be called with limit=50 (scan_fetch_limit), not limit=3 (max_markets)
    client.list_markets.assert_called_once_with(limit=50, active_only=True, max_pages=5)
    await conn.close()


async def test_fetch_limit_at_least_max_markets(tmp_path):
    """When max_markets > scan_fetch_limit, fetch limit is max_markets."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("a")])
    client.get_orderbook = AsyncMock(return_value=_ob("y_a"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_fetch_limit=10)
    conn = await open_db(tmp_path / "bot.sqlite")

    await run_once(settings=settings, conn=conn, polymarket_client=client,
                   mock_ai=True, scan_only=True, max_markets=100)

    # max_markets=100 > scan_fetch_limit=10 → fetch 100
    client.list_markets.assert_called_once_with(limit=100, active_only=True, max_pages=5)
    await conn.close()


def test_scan_fetch_limit_env_override(monkeypatch):
    """SCAN_FETCH_LIMIT env var is wired into load_settings."""
    from bot.config import load_settings
    monkeypatch.setenv("SCAN_FETCH_LIMIT", "200")
    s = load_settings()
    assert s.scan_fetch_limit == 200
