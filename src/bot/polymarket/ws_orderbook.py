"""WebSocket subscriber for Polymarket CLOB book updates.

Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market and pushes
each book/price update onto an asyncio.Queue. Reconnects on disconnect with
exponential backoff. No authentication required for the public 'market' channel.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Iterable
from typing import Any, Protocol

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from bot.polymarket.client import Market, OrderBookSnapshot

DEFAULT_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

log = logging.getLogger(__name__)


class _MarketDataClient(Protocol):
    async def list_markets(
        self,
        limit: int = 100,
        active_only: bool = True,
        max_pages: int = 5,
    ) -> list[Market]: ...

    async def get_orderbook(self, token_id: str) -> OrderBookSnapshot: ...

    async def close(self) -> None: ...


class OrderBookSubscriber:
    """Pushes book/price updates from Polymarket CLOB onto an asyncio.Queue.

    Each item placed on the queue is the raw JSON dict received. Callers should
    inspect `event_type` to dispatch (book, price_change, last_trade_price).
    """

    def __init__(
        self,
        token_ids: Iterable[str],
        out_queue: asyncio.Queue[dict[str, Any]],
        url: str | None = None,
        max_backoff: float = 60.0,
        heartbeat_interval: float = 10.0,
        custom_feature_enabled: bool = True,
    ):
        self.token_ids = list(token_ids)
        self.queue = out_queue
        self.url = url or os.environ.get("WS_HOST", DEFAULT_WS)
        self.max_backoff = max_backoff
        self.heartbeat_interval = heartbeat_interval
        self.custom_feature_enabled = custom_feature_enabled
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_and_stream()
                backoff = 1.0  # reset on clean disconnect
            except (TimeoutError, ConnectionClosed, WebSocketException, OSError) as e:
                log.warning("ws disconnect: %s; reconnecting in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.max_backoff)

    async def _connect_and_stream(self) -> None:
        async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
            sub = {
                "type": "market",
                "assets_ids": self.token_ids,
                "custom_feature_enabled": self.custom_feature_enabled,
            }
            await ws.send(json.dumps(sub))
            log.info("ws subscribed to %d tokens", len(self.token_ids))

            heartbeat = asyncio.create_task(self._heartbeat(ws))
            try:
                while not self._stop.is_set():
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        msg = msg.decode("utf-8", errors="replace")
                    if msg == "PONG":
                        continue
                    try:
                        payload = json.loads(msg)
                    except ValueError:
                        log.debug("non-json ws msg ignored: %r", msg[:120])
                        continue
                    # CLOB sends a list of events
                    events = payload if isinstance(payload, list) else [payload]
                    for event in events:
                        await self.queue.put(event)
            finally:
                heartbeat.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat

    async def _heartbeat(self, ws: Any) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.heartbeat_interval)
            if self._stop.is_set():
                return
            await ws.send("PING")


class OrderBookEventCache:
    """Maintains latest full orderbook snapshots from WebSocket queue events."""

    def __init__(self) -> None:
        self._books: dict[str, OrderBookSnapshot] = {}

    def get(self, token_id: str) -> OrderBookSnapshot | None:
        return self._books.get(token_id)

    def set(self, book: OrderBookSnapshot) -> None:
        self._books[book.token_id] = book

    def apply(self, event: dict[str, Any]) -> None:
        event_type = event.get("event_type")
        if event_type == "book":
            book = _book_event_to_snapshot(event)
            if book is not None:
                self.set(book)
        elif event_type == "price_change":
            self._apply_price_change(event)

    def _apply_price_change(self, event: dict[str, Any]) -> None:
        timestamp = _to_int(event.get("timestamp")) or 0
        for change in event.get("price_changes", []):
            token_id = str(change.get("asset_id") or "")
            if not token_id or token_id not in self._books:
                continue
            book = self._books[token_id]
            price = _to_float(change.get("price"))
            size = _to_float(change.get("size"))
            side = str(change.get("side") or "").upper()
            if price is None or size is None:
                continue
            if side == "BUY":
                bids = _update_level(book.bids, price, size, reverse=True)
                asks = book.asks
            elif side == "SELL":
                asks = _update_level(book.asks, price, size, reverse=False)
                bids = book.bids
            else:
                continue
            self._books[token_id] = OrderBookSnapshot(
                token_id=token_id,
                asks=asks,
                bids=bids,
                timestamp=timestamp or book.timestamp,
            )


class WebSocketOrderBookClient:
    """Polymarket client wrapper that consumes WebSocket book events from a queue."""

    def __init__(
        self,
        fallback_client: _MarketDataClient,
        queue: asyncio.Queue[dict[str, Any]],
        *,
        url: str | None = None,
        enabled: bool = True,
        subscriber_factory: Any = OrderBookSubscriber,
        cache: OrderBookEventCache | None = None,
    ):
        self._fallback = fallback_client
        self._queue = queue
        self._url = url
        self._enabled = enabled
        self._subscriber_factory = subscriber_factory
        self._cache = cache or OrderBookEventCache()
        self._subscriber: Any | None = None
        self._subscriber_task: asyncio.Task[None] | None = None
        self._subscribed_tokens: tuple[str, ...] = ()

    async def list_markets(
        self,
        limit: int = 100,
        active_only: bool = True,
        max_pages: int = 5,
    ) -> list[Market]:
        self._drain_queue()
        markets = await self._fallback.list_markets(
            limit=limit,
            active_only=active_only,
            max_pages=max_pages,
        )
        await self._ensure_subscription([m.yes_token for m in markets[:limit] if not m.closed])
        return markets

    async def get_orderbook(self, token_id: str) -> OrderBookSnapshot:
        self._drain_queue()
        cached = self._cache.get(token_id)
        if cached is not None:
            return cached
        book = await self._fallback.get_orderbook(token_id)
        self._cache.set(book)
        return book

    async def close(self) -> None:
        await self._stop_subscriber()
        await self._fallback.close()

    async def _ensure_subscription(self, token_ids: Iterable[str]) -> None:
        if not self._enabled:
            return
        unique_tokens = tuple(dict.fromkeys(token_ids))
        if not unique_tokens or unique_tokens == self._subscribed_tokens:
            return
        await self._stop_subscriber()
        self._subscriber = self._subscriber_factory(
            unique_tokens,
            self._queue,
            url=self._url,
            custom_feature_enabled=False,
        )
        self._subscriber_task = asyncio.create_task(self._subscriber.run())
        self._subscribed_tokens = unique_tokens

    async def _stop_subscriber(self) -> None:
        if self._subscriber is not None:
            self._subscriber.stop()
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._subscriber_task
        self._subscriber = None
        self._subscriber_task = None
        self._subscribed_tokens = ()

    def _drain_queue(self, max_events: int = 1000) -> None:
        for _ in range(max_events):
            try:
                event = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._cache.apply(event)
            self._queue.task_done()


def _book_event_to_snapshot(event: dict[str, Any]) -> OrderBookSnapshot | None:
    token_id = str(event.get("asset_id") or "")
    if not token_id:
        return None
    return OrderBookSnapshot(
        token_id=token_id,
        asks=sorted(_levels_from_event(event.get("asks", [])), key=lambda level: level[0]),
        bids=sorted(_levels_from_event(event.get("bids", [])), key=lambda level: -level[0]),
        timestamp=_to_int(event.get("timestamp")) or 0,
    )


def _levels_from_event(levels: Any) -> list[tuple[float, float]]:
    parsed: list[tuple[float, float]] = []
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _to_float(level.get("price"))
        size = _to_float(level.get("size"))
        if price is None or size is None:
            continue
        parsed.append((price, size))
    return parsed


def _update_level(
    levels: list[tuple[float, float]],
    price: float,
    size: float,
    *,
    reverse: bool,
) -> list[tuple[float, float]]:
    kept = [(p, s) for p, s in levels if p != price]
    if size > 0:
        kept.append((price, size))
    return sorted(kept, key=lambda level: level[0], reverse=reverse)


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
