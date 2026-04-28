"""Structured log output after each run_once pass.

The daemon should emit a single JSON log line after every scan so operators
can tail -f the log and pipe it to jq/Grafana without parsing prose output.
Fields: ts (unix), scanned, flagged, predictions, trades, settled, halt.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from bot.config import RuntimeSettings
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db


def _market(cid: str) -> Market:
    return Market(
        condition_id=cid, question="Q?", yes_token=f"y_{cid}", no_token=f"n_{cid}",
        end_date_iso=(datetime.now(UTC) + timedelta(days=7)).isoformat(),
        volume_24h=1000.0, liquidity=1000.0, closed=False, raw={},
    )


def _ob(token_id: str) -> OrderBookSnapshot:
    return OrderBookSnapshot(token_id=token_id, bids=[(0.48, 10)], asks=[(0.52, 10)], timestamp=0)


async def test_run_once_emits_structured_log_line(tmp_path, caplog):
    """run_once writes one JSON summary line at INFO level after completion."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market("a")])
    client.get_orderbook = AsyncMock(return_value=_ob("y_a"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")

    with caplog.at_level(logging.INFO, logger="bot.orchestrator"):
        await run_once(settings=settings, conn=conn, polymarket_client=client,
                       mock_ai=True, scan_only=True, max_markets=5)

    json_lines = [r.message for r in caplog.records if r.message.startswith("{")]
    assert len(json_lines) >= 1, "expected at least one JSON summary log line"

    summary = json.loads(json_lines[-1])
    assert "ts" in summary
    assert "scanned" in summary
    assert "flagged" in summary
    assert "trades" in summary
    assert "settled" in summary
    assert summary["scanned"] == 1
    await conn.close()


async def test_structured_log_includes_halt_when_stopped(tmp_path, caplog):
    """When run_once halts early, the JSON log includes a non-null halt field."""
    from bot.orchestrator import run_once

    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[])
    client.get_orderbook = AsyncMock(return_value=_ob("x"))
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(stop_file=tmp_path / "STOP")
    conn = await open_db(tmp_path / "bot.sqlite")
    # Create the STOP file so the halt fires
    (tmp_path / "STOP").touch()

    with caplog.at_level(logging.INFO, logger="bot.orchestrator"):
        await run_once(settings=settings, conn=conn, polymarket_client=client,
                       mock_ai=True, scan_only=True, max_markets=5)

    json_lines = [r.message for r in caplog.records if r.message.startswith("{")]
    assert len(json_lines) >= 1
    summary = json.loads(json_lines[-1])
    assert summary.get("halt") is not None
    await conn.close()
