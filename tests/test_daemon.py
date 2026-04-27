"""Tests for daemon CLI smoke mode."""
from __future__ import annotations

import pytest

from bot.daemon import async_main
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
async def test_daemon_once_mock_ai_does_not_start_ws_runtime(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.sqlite"
    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))
    monkeypatch.setenv("WS_ORDERBOOK_ENABLED", "true")

    class ExplodingWsClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("mock-ai smoke must stay fully local")

    monkeypatch.setattr("bot.daemon.WebSocketOrderBookClient", ExplodingWsClient)

    code = await async_main(["--once", "--paper", "--mock-ai", "--max-markets", "1"])

    assert code == 0
