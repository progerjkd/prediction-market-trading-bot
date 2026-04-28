"""Async SQLite storage with schema migration."""
from __future__ import annotations

import asyncio
import os
import sqlite3
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
    intended_size REAL,
    is_paper INTEGER NOT NULL DEFAULT 1,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER,
    pnl REAL,
    outcome TEXT,
    source TEXT NOT NULL DEFAULT 'paper_live',
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
    date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'paper_live',
    win_rate REAL,
    sharpe REAL,
    max_drawdown REAL,
    profit_factor REAL,
    brier_score REAL,
    n_trades INTEGER,
    pnl_usd REAL,
    api_cost_usd REAL,
    PRIMARY KEY (date, source)
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

TRADES_EXTRA_COLUMNS = {
    "intended_size": "REAL",
    "source": "TEXT NOT NULL DEFAULT 'paper_live'",
}

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
    conn = await aiosqlite.connect(str(p), timeout=30)
    await conn.execute("PRAGMA busy_timeout=30000")
    await _execute_with_lock_retry(conn, "PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await _ensure_markets_flagged_columns(conn)
    await _ensure_trades_columns(conn)
    await _ensure_metrics_daily_source(conn)
    await _backfill_provenance(conn)
    await conn.commit()
    return conn


def db_path_from_env() -> Path:
    return Path(os.environ.get("BOT_DB_PATH", str(DEFAULT_DB_PATH)))


async def _ensure_trades_columns(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(trades)")
    existing = {row[1] for row in await cur.fetchall()}
    for name, column_type in TRADES_EXTRA_COLUMNS.items():
        if name not in existing:
            await _add_column(conn, "trades", name, column_type)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_trades_source_outcome ON trades(source, outcome, closed_at)"
    )


async def _ensure_metrics_daily_source(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(metrics_daily)")
    rows = await cur.fetchall()
    columns = {row[1] for row in rows}
    pk_columns = [row[1] for row in sorted(rows, key=lambda r: r[5]) if row[5] > 0]
    if "source" in columns and pk_columns == ["date", "source"]:
        return

    await conn.execute("ALTER TABLE metrics_daily RENAME TO metrics_daily_legacy")
    await conn.execute(
        """
        CREATE TABLE metrics_daily (
            date TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'paper_live',
            win_rate REAL,
            sharpe REAL,
            max_drawdown REAL,
            profit_factor REAL,
            brier_score REAL,
            n_trades INTEGER,
            pnl_usd REAL,
            api_cost_usd REAL,
            PRIMARY KEY (date, source)
        )
        """
    )
    source_expr = "COALESCE(source, 'paper_live')" if "source" in columns else "'paper_live'"
    await conn.execute(
        f"""
        INSERT OR REPLACE INTO metrics_daily
            (date, source, win_rate, sharpe, max_drawdown, profit_factor,
             brier_score, n_trades, pnl_usd, api_cost_usd)
        SELECT date, {source_expr}, win_rate, sharpe, max_drawdown, profit_factor,
               brier_score, n_trades, pnl_usd, api_cost_usd
        FROM metrics_daily_legacy
        """
    )
    await conn.execute("DROP TABLE metrics_daily_legacy")


async def _ensure_markets_flagged_columns(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(markets_flagged)")
    existing = {row[1] for row in await cur.fetchall()}
    for name, column_type in MARKETS_FLAGGED_EXTRA_COLUMNS.items():
        if name not in existing:
            await _add_column(conn, "markets_flagged", name, column_type)


async def _backfill_provenance(conn: aiosqlite.Connection) -> None:
    await conn.execute(
        "UPDATE trades SET source='backtest' WHERE source='paper_live' AND condition_id LIKE 'bt_%'"
    )
    await conn.execute(
        """
        DELETE FROM metrics_daily
        WHERE source='paper_live'
          AND COALESCE(n_trades, 0) != (
              SELECT COUNT(*)
              FROM trades t
              WHERE t.source='paper_live'
                AND t.outcome IN ('YES', 'NO')
                AND t.is_paper=1
                AND t.closed_at IS NOT NULL
                AND date(t.closed_at, 'unixepoch', 'localtime') = metrics_daily.date
          )
        """
    )


async def _add_column(conn: aiosqlite.Connection, table: str, name: str, column_type: str) -> None:
    try:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc).lower():
            raise


async def _execute_with_lock_retry(
    conn: aiosqlite.Connection,
    sql: str,
    *,
    attempts: int = 10,
    base_delay_seconds: float = 0.05,
) -> None:
    for attempt in range(attempts):
        try:
            await conn.execute(sql)
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt == attempts - 1:
                raise
            await asyncio.sleep(base_delay_seconds * (attempt + 1))
