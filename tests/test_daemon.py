"""Tests for daemon CLI smoke mode."""
from __future__ import annotations

import asyncio
import logging
import signal

import pytest

from bot.config import RuntimeSettings
from bot.daemon import (
    _DaemonShutdown,
    _heartbeat_loop,
    _request_shutdown_from_signal,
    _run_repeating,
    async_main,
)
from bot.orchestrator import RunSummary
from bot.storage.db import open_db


@pytest.mark.asyncio
async def test_daemon_once_mock_ai_is_fully_local_smoke(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.sqlite"
    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))
    monkeypatch.setenv("CLOB_HOST", "http://127.0.0.1:1")
    monkeypatch.setenv("GAMMA_HOST", "http://127.0.0.1:1")

    code = await async_main(["--once", "--paper", "--mock-ai", "--max-markets", "1"])

    assert code == 0
    conn = await open_db(db_path)
    try:
        cur = await conn.execute("SELECT COUNT(*) FROM predictions")
        assert (await cur.fetchone())[0] == 1
        cur = await conn.execute("SELECT COUNT(*) FROM trades WHERE is_paper = 1")
        assert (await cur.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_daemon_repeating_loop_runs_until_shutdown_requested(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.sqlite"
    conn = await open_db(db_path)
    shutdown = _DaemonShutdown()
    settings = RuntimeSettings(
        db_path=db_path,
        stop_file=tmp_path / "STOP",
        scan_interval_seconds=0,
    )
    calls = 0

    async def fake_run_once(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            shutdown.request("test complete")
        return RunSummary()

    monkeypatch.setattr("bot.daemon.run_once", fake_run_once)

    try:
        code = await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
            heartbeat_seconds=60.0,
            stop_poll_seconds=60.0,
        )

        assert code == 0
        assert calls == 2
        assert shutdown.reason == "test complete"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_daemon_repeating_loop_exits_on_halt_summary(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.sqlite"
    conn = await open_db(db_path)
    shutdown = _DaemonShutdown()
    settings = RuntimeSettings(db_path=db_path, stop_file=tmp_path / "STOP")
    calls = 0

    async def fake_run_once(**kwargs):
        nonlocal calls
        calls += 1
        return RunSummary(halt_reason="daily loss limit")

    monkeypatch.setattr("bot.daemon.run_once", fake_run_once)

    try:
        code = await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
            heartbeat_seconds=60.0,
            stop_poll_seconds=60.0,
        )

        assert code == 0
        assert calls == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_daemon_stop_file_watcher_prevents_next_pass(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.sqlite"
    stop_file = tmp_path / "STOP"
    conn = await open_db(db_path)
    shutdown = _DaemonShutdown()
    settings = RuntimeSettings(
        db_path=db_path,
        stop_file=stop_file,
        scan_interval_seconds=60,
    )
    calls = 0

    async def fake_run_once(**kwargs):
        nonlocal calls
        calls += 1
        stop_file.write_text("halt")
        return RunSummary()

    monkeypatch.setattr("bot.daemon.run_once", fake_run_once)

    try:
        code = await _run_repeating(
            settings=settings,
            conn=conn,
            shutdown=shutdown,
            max_markets=1,
            mock_ai=True,
            scan_only=False,
            heartbeat_seconds=60.0,
            stop_poll_seconds=0.01,
        )

        assert code == 0
        assert calls == 1
        assert shutdown.reason == "STOP file present"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_signal_handler_path_requests_shutdown():
    shutdown = _DaemonShutdown()

    _request_shutdown_from_signal(shutdown, signal.SIGTERM)

    assert shutdown.event.is_set()
    assert shutdown.reason == "signal SIGTERM"


@pytest.mark.asyncio
async def test_heartbeat_loop_logs_until_shutdown(caplog):
    shutdown = _DaemonShutdown()
    caplog.set_level(logging.INFO, logger="bot.daemon")

    task = asyncio.create_task(_heartbeat_loop(shutdown, interval_seconds=60.0))
    await asyncio.sleep(0)
    shutdown.request("test complete")
    await task

    assert "daemon heartbeat" in caplog.text
