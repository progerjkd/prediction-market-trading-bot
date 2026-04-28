"""WebSocket subscriber for Polymarket CLOB book updates.

Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market and pushes
each book/price update onto an asyncio.Queue. Reconnects on disconnect with
exponential backoff. No authentication required for the public 'market' channel.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Iterable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from bot.polymarket.client import OrderBookSnapshot

DEFAULT_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

log = logging.getLogger(__name__)


class OrderBookCache:
    """Maintains the latest OrderBookSnapshot per token, updated from WS events.

    Accepts "book" events (full snapshots); ignores other event types.
    Thread-local — designed for a single asyncio task.
    """

    _HISTORY_TTL = 90_000  # 25 hours in seconds

    def __init__(self) -> None:
        self._cache: dict[str, OrderBookSnapshot] = {}
        self._price_history: dict[str, list[tuple[int, float]]] = {}
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def update(self, event: dict[str, Any]) -> None:
        if event.get("event_type") != "book":
            return
        token_id = event.get("asset_id") or event.get("market")
        if not token_id:
            return
        raw_bids = event.get("bids") or []
        raw_asks = event.get("asks") or []
        try:
            bids = sorted(
                ((float(b["price"]), float(b["size"])) for b in raw_bids),
                key=lambda x: -x[0],
            )
            asks = sorted(
                ((float(a["price"]), float(a["size"])) for a in raw_asks),
                key=lambda x: x[0],
            )
        except (KeyError, ValueError, TypeError):
            log.debug("malformed book event for %s, skipping", token_id)
            return
        ts = int(event.get("timestamp") or 0) or int(time.time())
        snap = OrderBookSnapshot(token_id=token_id, bids=bids, asks=asks, timestamp=ts)
        self._cache[token_id] = snap
        if snap.mid is not None:
            hist = self._price_history.setdefault(token_id, [])
            hist.append((ts, snap.mid))
            cutoff = ts - self._HISTORY_TTL
            self._price_history[token_id] = [(t, m) for t, m in hist if t >= cutoff]

    def get(self, token_id: str) -> OrderBookSnapshot | None:
        return self._cache.get(token_id)

    def momentum(self, token_id: str, lookback_seconds: int) -> float:
        """Return (current_mid - past_mid) / past_mid, or 0.0 if history insufficient."""
        hist = self._price_history.get(token_id, [])
        if len(hist) < 2:
            return 0.0
        current_ts, current_mid = hist[-1]
        target_ts = current_ts - lookback_seconds
        past_mid = next((m for t, m in reversed(hist) if t <= target_ts), None)
        if past_mid is None or past_mid == 0.0:
            return 0.0
        return (current_mid - past_mid) / past_mid

    async def run(self, queue: asyncio.Queue) -> None:
        """Consume events from queue until stop() is called."""
        while not self._stop.is_set():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                self.update(event)
            except TimeoutError:
                continue


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
    ):
        self.token_ids = list(token_ids)
        self.queue = out_queue
        self.url = url or os.environ.get("WS_HOST", DEFAULT_WS)
        self.max_backoff = max_backoff
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def update_tokens(self, token_ids: list[str]) -> None:
        """Replace the subscribed token list (deduped). Takes effect on next reconnect."""
        self.token_ids = list(dict.fromkeys(token_ids))

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
                "custom_feature_enabled": True,
            }
            await ws.send(json.dumps(sub))
            log.info("ws subscribed to %d tokens", len(self.token_ids))

            while not self._stop.is_set():
                msg = await ws.recv()
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", errors="replace")
                try:
                    payload = json.loads(msg)
                except ValueError:
                    log.debug("non-json ws msg ignored: %r", msg[:120])
                    continue
                # CLOB sends a list of events
                events = payload if isinstance(payload, list) else [payload]
                for event in events:
                    await self.queue.put(event)
