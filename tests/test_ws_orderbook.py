"""Tests for OrderBookSubscriber WebSocket client."""
from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.polymarket.ws_orderbook import OrderBookSubscriber

_TOKEN = "21742633143463906290569050155826241533067272736897614950488156847949938836455"


def _make_ws(messages: list[str]) -> MagicMock:
    ws = AsyncMock()
    ws.__aenter__ = AsyncMock(return_value=ws)
    ws.__aexit__ = AsyncMock(return_value=False)
    ws.send = AsyncMock()
    ws.recv = AsyncMock(side_effect=messages)
    return ws


async def test_subscriber_puts_events_onto_queue():
    q: asyncio.Queue[dict] = asyncio.Queue()
    event = {"event_type": "book", "market": _TOKEN, "bids": [], "asks": []}
    ws = _make_ws([json.dumps([event]), asyncio.CancelledError()])

    with patch("bot.polymarket.ws_orderbook.websockets.connect", return_value=ws):
        sub = OrderBookSubscriber([_TOKEN], q, url="ws://mock")
        with pytest.raises((asyncio.CancelledError, Exception)):
            await sub._connect_and_stream()

    assert not q.empty()
    item = q.get_nowait()
    assert item["event_type"] == "book"


async def test_stop_halts_run_loop():
    q: asyncio.Queue[dict] = asyncio.Queue()
    sub = OrderBookSubscriber([_TOKEN], q, url="ws://mock", max_backoff=0.0)
    sub.stop()

    with patch("bot.polymarket.ws_orderbook.websockets.connect") as mock_connect:
        await sub.run()
        mock_connect.assert_not_called()


@pytest.mark.integration
async def test_live_ws_receives_message():
    q: asyncio.Queue[dict] = asyncio.Queue()
    sub = OrderBookSubscriber([_TOKEN], q)

    async def _run_then_stop():
        await sub.run()

    task = asyncio.create_task(_run_then_stop())
    try:
        msg = await asyncio.wait_for(q.get(), timeout=30.0)
    finally:
        sub.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    assert "event_type" in msg
