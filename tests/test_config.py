"""Tests for runtime configuration parsing."""
from __future__ import annotations

from pathlib import Path

from bot.config import RuntimeSettings, load_settings


def test_load_settings_parses_env_values(monkeypatch, tmp_path):
    db_path = tmp_path / "bot.sqlite"
    stop_path = tmp_path / "STOP"
    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("BANKROLL_USDC", "2500")
    monkeypatch.setenv("EDGE_THRESHOLD", "0.08")
    monkeypatch.setenv("DAILY_API_COST_LIMIT", "12.50")
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("WS_ORDERBOOK_ENABLED", "true")
    monkeypatch.setenv("STOP_FILE", str(stop_path))

    settings = load_settings()

    assert settings.db_path == db_path
    assert settings.bankroll_usdc == 2500
    assert settings.edge_threshold == 0.08
    assert settings.daily_api_cost_limit == 12.50
    assert settings.stop_file == stop_path
    assert settings.live_trading_requested is True
    assert settings.live_trading_enabled is False
    assert settings.ws_orderbook_enabled is True


def test_runtime_settings_defaults_to_paper_mode():
    settings = RuntimeSettings()

    assert settings.db_path == Path("data/bot.sqlite")
    assert settings.bankroll_usdc == 10_000
    assert settings.live_trading_requested is False
    assert settings.live_trading_enabled is False
    assert settings.ws_orderbook_enabled is False
