"""Runtime configuration for the paper-trading daemon."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw in (None, "") else float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeSettings:
    db_path: Path = Path("data/bot.sqlite")
    stop_file: Path = Path("data/STOP")
    bankroll_usdc: float = 10_000.0
    edge_threshold: float = 0.04
    daily_api_cost_limit: float = 50.0
    daily_loss_pct: float = 0.15
    max_drawdown_pct: float = 0.08
    max_open_positions: int = 15
    max_position_pct: float = 0.05
    max_exposure_pct: float = 0.50
    kelly_fraction: float = 0.25
    live_trading_requested: bool = False
    clob_host: str = "https://clob.polymarket.com"
    gamma_host: str = "https://gamma-api.polymarket.com"
    ws_host: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_orderbook_enabled: bool = False
    chain_id: int = 137
    scan_min_volume: float = 200.0
    scan_min_liquidity: float = 50.0
    scan_max_spread: float = 0.05
    scan_max_days: int = 30
    scan_interval_seconds: int = 900
    xgboost_model_path: Path = Path("data/models/xgboost.json")

    @property
    def live_trading_enabled(self) -> bool:
        """Live trading is intentionally disabled for the v1 MVP."""
        return False


def load_settings() -> RuntimeSettings:
    return RuntimeSettings(
        db_path=Path(os.environ.get("BOT_DB_PATH", "data/bot.sqlite")),
        stop_file=Path(os.environ.get("STOP_FILE", "data/STOP")),
        bankroll_usdc=_env_float("BANKROLL_USDC", 10_000.0),
        edge_threshold=_env_float("EDGE_THRESHOLD", 0.04),
        daily_api_cost_limit=_env_float("DAILY_API_COST_LIMIT", 50.0),
        daily_loss_pct=_env_float("DAILY_LOSS_LIMIT_PCT", 0.15),
        max_drawdown_pct=_env_float("MAX_DRAWDOWN_PCT", 0.08),
        max_open_positions=_env_int("MAX_OPEN_POSITIONS", 15),
        max_position_pct=_env_float("MAX_POSITION_PCT", 0.05),
        max_exposure_pct=_env_float("MAX_EXPOSURE_PCT", 0.50),
        kelly_fraction=_env_float("KELLY_FRACTION", 0.25),
        live_trading_requested=_env_bool("LIVE_TRADING", False),
        clob_host=os.environ.get("CLOB_HOST", "https://clob.polymarket.com"),
        gamma_host=os.environ.get("GAMMA_HOST", "https://gamma-api.polymarket.com"),
        ws_host=os.environ.get("WS_HOST", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
        ws_orderbook_enabled=_env_bool("WS_ORDERBOOK_ENABLED", False),
        chain_id=_env_int("CHAIN_ID", 137),
        scan_min_volume=_env_float("SCAN_MIN_VOLUME", 200.0),
        scan_min_liquidity=_env_float("SCAN_MIN_LIQUIDITY", 50.0),
        scan_max_spread=_env_float("SCAN_MAX_SPREAD", 0.05),
        scan_max_days=_env_int("SCAN_MAX_DAYS", 30),
        scan_interval_seconds=_env_int("SCAN_INTERVAL_SECONDS", 900),
        xgboost_model_path=Path(os.environ.get("XGBOOST_MODEL_PATH", "data/models/xgboost.json")),
    )
