"""Async SQLite storage with schema migration."""
from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

DEFAULT_DB_PATH = Path("data/bot.sqlite")


SCHEMA = """
CREATE TABLE IF NOT EXISTS markets_flagged (
    condition_id TEXT NOT NULL,
    yes_token TEXT NOT NULL,
    no_token TEXT NOT NULL,
    mid_price REAL,
    spread REAL,
    volume_24h REAL,
    question TEXT,
    end_date_iso TEXT,
    liquidity REAL,
    edge_proxy REAL,
    raw_json TEXT,
    flagged_at INTEGER NOT NULL,
    PRIMARY KEY (condition_id, flagged_at)
);

CREATE TABLE IF NOT EXISTS research_briefs (
    condition_id TEXT NOT NULL,
    brief_json TEXT NOT NULL,
    bullish_score REAL,
    bearish_score REAL,
    narrative_score REAL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (condition_id, created_at)
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    p_model REAL NOT NULL,
    p_market REAL NOT NULL,
    edge REAL NOT NULL,
    components_json TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_condition ON predictions(condition_id, created_at DESC);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    limit_price REAL NOT NULL,
    fill_price REAL,
    slippage REAL,
    is_paper INTEGER NOT NULL DEFAULT 1,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    pnl REAL,
    outcome TEXT,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id)
);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades(closed_at) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS paper_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER,
    trade_id INTEGER,
    condition_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    requested_size REAL NOT NULL,
    filled_size REAL NOT NULL,
    unfilled_size REAL NOT NULL,
    limit_price REAL NOT NULL,
    fill_price REAL,
    slippage REAL,
    status TEXT NOT NULL,
    is_paper INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (prediction_id) REFERENCES predictions(id),
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);
CREATE INDEX IF NOT EXISTS idx_paper_executions_prediction
    ON paper_executions(prediction_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_executions_status
    ON paper_executions(status, created_at DESC);

CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    cause TEXT NOT NULL,
    rule_proposed TEXT NOT NULL,
    notes TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS metrics_daily (
    date TEXT PRIMARY KEY,
    win_rate REAL,
    sharpe REAL,
    max_drawdown REAL,
    profit_factor REAL,
    brier_score REAL,
    n_trades INTEGER,
    pnl_usd REAL,
    api_cost_usd REAL
);

CREATE TABLE IF NOT EXISTS api_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cost_usd REAL NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_spend_created ON api_spend(created_at);

CREATE TABLE IF NOT EXISTS book_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id TEXT NOT NULL,
    best_bid REAL,
    best_ask REAL,
    mid REAL,
    spread REAL,
    book_json TEXT,
    captured_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_book_token_time ON book_snapshots(token_id, captured_at DESC);
"""

MARKETS_FLAGGED_EXTRA_COLUMNS = {
    "question": "TEXT",
    "end_date_iso": "TEXT",
    "liquidity": "REAL",
    "edge_proxy": "REAL",
    "raw_json": "TEXT",
}


async def open_db(path: Path | str = DEFAULT_DB_PATH) -> aiosqlite.Connection:
    """Open or create the SQLite DB at `path`. Applies schema idempotently."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(p))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await _ensure_markets_flagged_columns(conn)
    await conn.commit()
    return conn


def db_path_from_env() -> Path:
    return Path(os.environ.get("BOT_DB_PATH", str(DEFAULT_DB_PATH)))


async def _ensure_markets_flagged_columns(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(markets_flagged)")
    existing = {row[1] for row in await cur.fetchall()}
    for name, column_type in MARKETS_FLAGGED_EXTRA_COLUMNS.items():
        if name not in existing:
            await conn.execute(f"ALTER TABLE markets_flagged ADD COLUMN {name} {column_type}")
