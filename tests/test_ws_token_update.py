"""Tests for dynamic WS token subscription update after each scan pass — TDD RED phase."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.config import RuntimeSettings
from bot.orchestrator import RunSummary
from bot.polymarket.ws_orderbook import OrderBookCache, OrderBookSubscriber
from bot.storage.db import open_db


def _fake_summary(flagged_tokens: list[str] | None = None, halt_reason: str | None = None):
    return RunSummary(
        scanned_markets=1,
        flagged_markets=len(flagged_tokens or []),
        halt_reason=halt_reason,
    )


async def _run_with_patches(tmp_path, *, flagged_tokens=None, summaries=None):
    from bot.daemon import _DaemonShutdown, _run_repeating

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=0)
    conn = MagicMock()
    conn.close = AsyncMock()
    shutdown = _DaemonShutdown()

    call_count = 0
    captured_subscriber = None

    async def fake_subscriber_run():
        await asyncio.sleep(999)

    def make_subscriber(*args, **kwargs):
        nonlocal captured_subscriber
        inst = MagicMock()
        inst.run = fake_subscriber_run
        inst.stop = MagicMock()
        inst.update_tokens = MagicMock()
        captured_subscriber = inst
        return inst

    summary_list = summaries or [_fake_summary(flagged_tokens, halt_reason="stop")]

    with (
        patch("bot.daemon.run_once", new_callable=AsyncMock) as mock_run_once,
        patch("bot.daemon.OrderBookSubscriber", side_effect=make_subscriber),
    ):
        mock_run_once.side_effect = summary_list
        await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
        )

    return mock_run_once, captured_subscriber


# ---------------------------------------------------------------------------
# OrderBookSubscriber.update_tokens()
# ---------------------------------------------------------------------------


def test_subscriber_update_tokens_replaces_token_list():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=["tok1", "tok2"], out_queue=q)
    sub.update_tokens(["tok3", "tok4", "tok5"])
    assert sub.token_ids == ["tok3", "tok4", "tok5"]


def test_subscriber_update_tokens_accepts_empty_list():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=["tok1"], out_queue=q)
    sub.update_tokens([])
    assert sub.token_ids == []


def test_subscriber_update_tokens_deduplicates():
    q: asyncio.Queue = asyncio.Queue()
    sub = OrderBookSubscriber(token_ids=[], out_queue=q)
    sub.update_tokens(["tok1", "tok1", "tok2"])
    assert len(sub.token_ids) == 2
    assert set(sub.token_ids) == {"tok1", "tok2"}


# ---------------------------------------------------------------------------
# run_once returns flagged yes_tokens so daemon can update subscriber
# ---------------------------------------------------------------------------


def test_run_summary_has_flagged_yes_tokens_field():
    """RunSummary should carry the yes_tokens of flagged markets."""
    s = RunSummary(flagged_yes_tokens=["tok1", "tok2"])
    assert s.flagged_yes_tokens == ["tok1", "tok2"]


def test_run_summary_flagged_yes_tokens_defaults_empty():
    s = RunSummary()
    assert s.flagged_yes_tokens == []


# ---------------------------------------------------------------------------
# daemon._run_repeating calls subscriber.update_tokens after each pass
# ---------------------------------------------------------------------------


async def test_run_repeating_calls_update_tokens_after_pass(tmp_path):
    from bot.daemon import _DaemonShutdown, _run_repeating

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=0)
    conn = MagicMock()
    conn.close = AsyncMock()
    shutdown = _DaemonShutdown()

    captured_subscriber = None

    async def fake_subscriber_run():
        await asyncio.sleep(999)

    def make_subscriber(*args, **kwargs):
        nonlocal captured_subscriber
        inst = MagicMock()
        inst.run = fake_subscriber_run
        inst.stop = MagicMock()
        inst.update_tokens = MagicMock()
        captured_subscriber = inst
        return inst

    with (
        patch("bot.daemon.run_once", new_callable=AsyncMock) as mock_run_once,
        patch("bot.daemon.OrderBookSubscriber", side_effect=make_subscriber),
    ):
        mock_run_once.return_value = RunSummary(
            flagged_yes_tokens=["tok-a", "tok-b"], halt_reason="stop"
        )
        await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
        )

    captured_subscriber.update_tokens.assert_called_once_with(["tok-a", "tok-b"])


async def test_run_repeating_does_not_crash_when_flagged_tokens_empty(tmp_path):
    from bot.daemon import _DaemonShutdown, _run_repeating

    settings = RuntimeSettings(stop_file=tmp_path / "STOP", scan_interval_seconds=0)
    conn = MagicMock()
    conn.close = AsyncMock()
    shutdown = _DaemonShutdown()

    async def fake_subscriber_run():
        await asyncio.sleep(999)

    with (
        patch("bot.daemon.run_once", new_callable=AsyncMock) as mock_run_once,
        patch("bot.daemon.OrderBookSubscriber") as MockSub,
    ):
        inst = MagicMock()
        inst.run = fake_subscriber_run
        inst.stop = MagicMock()
        inst.update_tokens = MagicMock()
        MockSub.return_value = inst
        mock_run_once.return_value = RunSummary(halt_reason="stop")  # empty tokens

        await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
        )

    inst.update_tokens.assert_called_once_with([])
