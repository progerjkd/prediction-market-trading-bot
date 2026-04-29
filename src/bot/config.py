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
    daily_gain_pct: float = 1.0
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
    scan_fetch_limit: int = 50
    scan_fetch_max_pages: int = 5
    position_timeout_days: int = 30
    ws_orderbook_max_age_seconds: int = 300
    stop_loss_pct: float = 0.0
    max_consecutive_losses: int = 0  # 0 = disabled
    max_daily_trades: int = 0  # 0 = disabled
    market_cooldown_hours: int = 0  # 0 = disabled
    min_model_prob: float = 0.0  # 0.0 = disabled (no lower bound)
    max_model_prob: float = 1.0  # 1.0 = disabled (no upper bound)
    max_daily_slippage_usd: float = 0.0  # 0.0 = disabled
    adaptive_kelly_min_win_rate: float = 0.0  # 0.0 = disabled
    adaptive_kelly_lookback_n: int = 20
    adaptive_kelly_scale_factor: float = 0.5
    xgboost_model_path: Path = Path("data/models/xgboost.json")
    training_data_path: Path = Path("data/training_data.csv")

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not (0.0 < self.edge_threshold < 1.0):
            errors.append(f"edge_threshold must be in (0, 1), got {self.edge_threshold}")
        if not (0.0 < self.kelly_fraction <= 1.0):
            errors.append(f"kelly_fraction must be in (0, 1], got {self.kelly_fraction}")
        if self.bankroll_usdc <= 0:
            errors.append(f"bankroll_usdc must be positive, got {self.bankroll_usdc}")
        if not (0.0 < self.max_position_pct <= 1.0):
            errors.append(f"max_position_pct must be in (0, 1], got {self.max_position_pct}")
        if not (0.0 < self.max_exposure_pct <= 1.0):
            errors.append(f"max_exposure_pct must be in (0, 1], got {self.max_exposure_pct}")
        if not (0.0 < self.daily_loss_pct <= 1.0):
            errors.append(f"daily_loss_pct must be in (0, 1], got {self.daily_loss_pct}")
        if not (0.0 < self.max_drawdown_pct <= 1.0):
            errors.append(f"max_drawdown_pct must be in (0, 1], got {self.max_drawdown_pct}")
        if self.scan_max_spread <= 0.0:
            errors.append(f"scan_max_spread must be positive, got {self.scan_max_spread}")
        if self.scan_max_days <= 0:
            errors.append(f"scan_max_days must be positive, got {self.scan_max_days}")
        if self.scan_interval_seconds < 0:
            errors.append(f"scan_interval_seconds must be >= 0, got {self.scan_interval_seconds}")
        if self.scan_fetch_max_pages <= 0:
            errors.append(f"scan_fetch_max_pages must be positive, got {self.scan_fetch_max_pages}")
        if errors:
            raise ValueError("; ".join(errors))

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
        daily_gain_pct=_env_float("DAILY_GAIN_PCT", 1.0),
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
        scan_fetch_limit=_env_int("SCAN_FETCH_LIMIT", 50),
        scan_fetch_max_pages=_env_int("SCAN_FETCH_MAX_PAGES", 5),
        position_timeout_days=_env_int("POSITION_TIMEOUT_DAYS", 30),
        ws_orderbook_max_age_seconds=_env_int("WS_ORDERBOOK_MAX_AGE_SECONDS", 300),
        stop_loss_pct=_env_float("STOP_LOSS_PCT", 0.0),
        max_consecutive_losses=_env_int("MAX_CONSECUTIVE_LOSSES", 0),
        max_daily_trades=_env_int("MAX_DAILY_TRADES", 0),
        market_cooldown_hours=_env_int("MARKET_COOLDOWN_HOURS", 0),
        min_model_prob=_env_float("MIN_MODEL_PROB", 0.0),
        max_model_prob=_env_float("MAX_MODEL_PROB", 1.0),
        max_daily_slippage_usd=_env_float("MAX_DAILY_SLIPPAGE_USD", 0.0),
        adaptive_kelly_min_win_rate=_env_float("ADAPTIVE_KELLY_MIN_WIN_RATE", 0.0),
        adaptive_kelly_lookback_n=_env_int("ADAPTIVE_KELLY_LOOKBACK_N", 20),
        adaptive_kelly_scale_factor=_env_float("ADAPTIVE_KELLY_SCALE_FACTOR", 0.5),
        xgboost_model_path=Path(os.environ.get("XGBOOST_MODEL_PATH", "data/models/xgboost.json")),
        training_data_path=Path(os.environ.get("TRAINING_DATA_PATH", "data/training_data.csv")),
    )
