"""WS subscriber reconnect-on-token-update — TDD RED phase."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.polymarket.ws_orderbook import OrderBookSubscriber

# ---------------------------------------------------------------------------
# update_tokens triggers reconnect
# ---------------------------------------------------------------------------


def test_update_tokens_sets_reconnect_flag_when_tokens_change():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=["tok1"], out_queue=q)
    sub.update_tokens(["tok1", "tok2"])
    assert sub._reconnect.is_set()


def test_update_tokens_does_not_set_reconnect_when_tokens_unchanged():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=["tok1", "tok2"], out_queue=q)
    sub._reconnect.clear()
    sub.update_tokens(["tok1", "tok2"])
    assert not sub._reconnect.is_set()


def test_update_tokens_sets_reconnect_on_empty_to_nonempty():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=[], out_queue=q)
    sub.update_tokens(["tok1"])
    assert sub._reconnect.is_set()


def test_update_tokens_does_not_reconnect_empty_to_empty():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=[], out_queue=q)
    sub._reconnect.clear()
    sub.update_tokens([])
    assert not sub._reconnect.is_set()


# ---------------------------------------------------------------------------
# _connect_and_stream exits when reconnect is requested
# ---------------------------------------------------------------------------


async def test_connect_and_stream_exits_on_reconnect_flag():
    """If _reconnect is set mid-stream, _connect_and_stream should return cleanly."""
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=["tok1"], out_queue=q)

    recv_count = 0

    async def mock_recv():
        nonlocal recv_count
        recv_count += 1
        if recv_count == 1:
            # Signal reconnect on first receive
            sub._reconnect.set()
        await asyncio.sleep(0.05)
        raise TimeoutError

    mock_ws = MagicMock()
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)
    mock_ws.send = AsyncMock()
    mock_ws.recv = mock_recv

    with patch("bot.polymarket.ws_orderbook.websockets.connect", return_value=mock_ws):
        await asyncio.wait_for(sub._connect_and_stream(), timeout=2.0)

    assert not sub._reconnect.is_set()  # cleared after handled


async def test_subscriber_reconnects_when_reconnect_flag_set(tmp_path):
    """run() calls _connect_and_stream() twice when reconnect is triggered."""
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=["tok1"], out_queue=q)

    connect_count = 0

    async def fake_connect_and_stream():
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            sub._reconnect.set()
        else:
            sub.stop()

    sub._connect_and_stream = fake_connect_and_stream

    await asyncio.wait_for(sub.run(), timeout=2.0)
    assert connect_count == 2
