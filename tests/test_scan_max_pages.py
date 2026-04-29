"""Configurable scan pagination depth."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(condition_id: str, *, days: int, volume: float = 5_000.0, liquidity: float = 5_000.0) -> Market:
    return Market(
        condition_id=condition_id,
        question=f"Q {condition_id}?",
        yes_token=f"yes_{condition_id}",
        no_token=f"no_{condition_id}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=days)).isoformat(),
        volume_24h=volume,
        liquidity=liquidity,
        closed=False,
        raw={},
    )


class _PagedMarketClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_markets(self, *, limit: int = 100, active_only: bool = True, max_pages: int = 5):
        self.calls.append({"limit": limit, "active_only": active_only, "max_pages": max_pages})
        markets = [
            _market("far-page-1", days=90, volume=50_000.0, liquidity=50_000.0),
            _market("far-page-2", days=80, volume=40_000.0, liquidity=40_000.0),
        ]
        if max_pages >= 3:
            markets.extend(
                [
                    _market("valid-page-3-a", days=7),
                    _market("valid-page-3-b", days=8),
                ]
            )
        return markets

    async def get_orderbook(self, token_id: str):
        return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 100)], asks=[(0.52, 100)], timestamp=0)

    async def get_market_resolution(self, condition_id: str):
        return MarketResolution(resolved=False, final_yes_price=None)


def test_scan_fetch_max_pages_env_override(monkeypatch):
    from bot.config import load_settings

    monkeypatch.setenv("SCAN_FETCH_MAX_PAGES", "9")

    assert load_settings().scan_fetch_max_pages == 9


async def test_run_once_passes_configured_scan_fetch_max_pages(tmp_path):
    from bot.orchestrator import run_once

    client = _PagedMarketClient()
    conn = await open_db(tmp_path / "bot.sqlite")
    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_fetch_max_pages=3)

    await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    assert client.calls == [{"limit": 50, "active_only": True, "max_pages": 3}]

    await conn.close()


async def test_run_once_finds_valid_markets_only_when_page_depth_reaches_them(tmp_path):
    from bot.orchestrator import run_once

    shallow_client = _PagedMarketClient()
    shallow_conn = await open_db(tmp_path / "shallow.sqlite")
    shallow_summary = await run_once(
        settings=RuntimeSettings(stop_file=tmp_path / "STOP", scan_fetch_max_pages=2),
        conn=shallow_conn,
        polymarket_client=shallow_client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    deep_client = _PagedMarketClient()
    deep_conn = await open_db(tmp_path / "deep.sqlite")
    deep_summary = await run_once(
        settings=RuntimeSettings(stop_file=tmp_path / "STOP", scan_fetch_max_pages=3),
        conn=deep_conn,
        polymarket_client=deep_client,
        mock_ai=True,
        scan_only=True,
        max_markets=2,
    )

    assert shallow_summary.flagged_markets == 0
    assert deep_summary.flagged_markets == 2
    assert deep_summary.flagged_yes_tokens == ["yes_valid-page-3-a", "yes_valid-page-3-b"]

    await shallow_conn.close()
    await deep_conn.close()
