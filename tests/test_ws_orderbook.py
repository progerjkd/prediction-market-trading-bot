"""Tests for OrderBookSubscriber WebSocket client."""
from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.polymarket.client import Market, OrderBookSnapshot, PolymarketClient
from bot.polymarket.ws_orderbook import OrderBookSubscriber, WebSocketOrderBookClient

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


async def test_subscriber_sends_text_ping_heartbeats():
    q: asyncio.Queue[dict] = asyncio.Queue()
    ws = AsyncMock()
    sub = OrderBookSubscriber([_TOKEN], q, url="ws://mock", heartbeat_interval=0.01)

    task = asyncio.create_task(sub._heartbeat(ws))
    try:
        await asyncio.sleep(0.03)
    finally:
        sub.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    ws.send.assert_any_call("PING")


async def test_stop_halts_run_loop():
    q: asyncio.Queue[dict] = asyncio.Queue()
    sub = OrderBookSubscriber([_TOKEN], q, url="ws://mock", max_backoff=0.0)
    sub.stop()

    with patch("bot.polymarket.ws_orderbook.websockets.connect") as mock_connect:
        await sub.run()
        mock_connect.assert_not_called()


class _FallbackClient:
    def __init__(self):
        self.orderbook_calls: list[str] = []
        self.closed = False

    async def list_markets(
        self,
        limit: int = 100,
        active_only: bool = True,
        max_pages: int = 5,
    ):
        return [
            Market(
                condition_id="cond-1",
                question="Will test market resolve Yes?",
                yes_token="yes-1",
                no_token="no-1",
                end_date_iso=None,
                volume_24h=1000.0,
                liquidity=500.0,
                closed=False,
                raw={},
            )
        ]

    async def get_orderbook(self, token_id: str):
        self.orderbook_calls.append(token_id)
        return OrderBookSnapshot(
            token_id=token_id,
            asks=[(0.53, 10.0)],
            bids=[(0.50, 8.0)],
            timestamp=123,
        )

    async def close(self):
        self.closed = True


class _FakeSubscriber:
    instances: list[_FakeSubscriber] = []

    def __init__(self, token_ids, out_queue, url=None, max_backoff=60.0, custom_feature_enabled=False):
        self.token_ids = list(token_ids)
        self.out_queue = out_queue
        self.url = url
        self.max_backoff = max_backoff
        self.custom_feature_enabled = custom_feature_enabled
        self.stopped = False
        _FakeSubscriber.instances.append(self)

    async def run(self):
        while not self.stopped:
            await asyncio.sleep(1)

    def stop(self):
        self.stopped = True


async def test_websocket_orderbook_client_uses_queued_book_before_http():
    q: asyncio.Queue[dict] = asyncio.Queue()
    await q.put(
        {
            "event_type": "book",
            "asset_id": "yes-1",
            "bids": [{"price": "0.51", "size": "12"}],
            "asks": [{"price": "0.54", "size": "9"}],
            "timestamp": "456",
        }
    )
    fallback = _FallbackClient()
    client = WebSocketOrderBookClient(fallback, q, enabled=False)

    book = await client.get_orderbook("yes-1")

    assert book.token_id == "yes-1"
    assert book.best_bid == 0.51
    assert book.best_ask == 0.54
    assert book.timestamp == 456
    assert fallback.orderbook_calls == []


async def test_websocket_orderbook_client_starts_subscriber_from_listed_markets():
    _FakeSubscriber.instances.clear()
    q: asyncio.Queue[dict] = asyncio.Queue()
    fallback = _FallbackClient()
    client = WebSocketOrderBookClient(
        fallback,
        q,
        url="ws://mock",
        subscriber_factory=_FakeSubscriber,
    )

    markets = await client.list_markets(limit=1)
    await client.close()

    assert [m.yes_token for m in markets] == ["yes-1"]
    assert len(_FakeSubscriber.instances) == 1
    assert _FakeSubscriber.instances[0].token_ids == ["yes-1"]
    assert _FakeSubscriber.instances[0].custom_feature_enabled is False
    assert _FakeSubscriber.instances[0].stopped is True
    assert fallback.closed is True


@pytest.mark.integration
async def test_live_ws_receives_message():
    q: asyncio.Queue[dict] = asyncio.Queue()
    token = await _find_active_orderbook_token()
    sub = OrderBookSubscriber([token], q, custom_feature_enabled=False)

    async def _run_then_stop():
        await sub.run()

    task = asyncio.create_task(_run_then_stop())
    try:
        deadline = asyncio.get_running_loop().time() + 30.0
        msg = None
        while asyncio.get_running_loop().time() < deadline:
            event = await asyncio.wait_for(q.get(), timeout=deadline - asyncio.get_running_loop().time())
            if event.get("event_type") == "book" and event.get("asset_id") == token:
                msg = event
                break
    finally:
        sub.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    assert msg is not None
    assert msg["bids"]
    assert msg["asks"]


async def _find_active_orderbook_token() -> str:
    client = PolymarketClient(timeout=10.0, max_retries=1)
    try:
        markets = await client.list_markets(limit=25, active_only=True, max_pages=1)
        for market in markets:
            try:
                book = await client.get_orderbook(market.yes_token)
            except Exception:
                continue
            if book.bids and book.asks:
                return market.yes_token
    finally:
        await client.close()
    pytest.skip("no active Polymarket orderbook token found")
