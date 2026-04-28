"""Repository layer — typed CRUD for persisted records."""
from __future__ import annotations

import time
from datetime import date, datetime

import aiosqlite

from bot.metrics import brier_score, max_drawdown, profit_factor, sharpe_ratio, win_rate

from .models import (
    ApiSpend,
    FlaggedMarket,
    Lesson,
    OpenTradeRecord,
    PaperExecution,
    Prediction,
    ResearchBrief,
    Trade,
)


async def insert_flagged_market(conn: aiosqlite.Connection, m: FlaggedMarket) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO markets_flagged "
        "(condition_id, yes_token, no_token, mid_price, spread, volume_24h, question, "
        " end_date_iso, liquidity, edge_proxy, raw_json, flagged_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            m.condition_id,
            m.yes_token,
            m.no_token,
            m.mid_price,
            m.spread,
            m.volume_24h,
            m.question,
            m.end_date_iso,
            m.liquidity,
            m.edge_proxy,
            m.raw_json,
            m.flagged_at,
        ),
    )
    await conn.commit()


async def latest_flagged_markets(
    conn: aiosqlite.Connection, since_seconds_ago: int = 3600
) -> list[FlaggedMarket]:
    cutoff = int(time.time()) - since_seconds_ago
    cur = await conn.execute(
        "SELECT condition_id, yes_token, no_token, mid_price, spread, volume_24h, flagged_at, "
        "       COALESCE(question, ''), end_date_iso, COALESCE(liquidity, 0), "
        "       COALESCE(edge_proxy, 0), COALESCE(raw_json, '{}') "
        "FROM markets_flagged WHERE flagged_at >= ? ORDER BY flagged_at DESC",
        (cutoff,),
    )
    rows = await cur.fetchall()
    return [FlaggedMarket(*row) for row in rows]


async def open_condition_ids(conn: aiosqlite.Connection, source: str = "paper_live") -> set[str]:
    """Return condition_ids that have at least one open (unclosed) trade."""
    cur = await conn.execute(
        "SELECT DISTINCT condition_id FROM trades WHERE closed_at IS NULL AND source = ?",
        (source,),
    )
    rows = await cur.fetchall()
    return {row[0] for row in rows}


async def recently_flagged_condition_ids(conn: aiosqlite.Connection, since_ts: int) -> set[str]:
    """Return condition_ids flagged at or after since_ts (Unix seconds)."""
    cur = await conn.execute(
        "SELECT DISTINCT condition_id FROM markets_flagged WHERE flagged_at >= ?",
        (since_ts,),
    )
    rows = await cur.fetchall()
    return {row[0] for row in rows}


async def insert_research_brief(conn: aiosqlite.Connection, b: ResearchBrief) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO research_briefs "
        "(condition_id, brief_json, bullish_score, bearish_score, narrative_score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (b.condition_id, b.to_json(), b.bullish_score, b.bearish_score, b.narrative_score, b.created_at),
    )
    await conn.commit()


async def insert_prediction(conn: aiosqlite.Connection, p: Prediction) -> int:
    cur = await conn.execute(
        "INSERT INTO predictions "
        "(condition_id, token_id, p_model, p_market, edge, components_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (p.condition_id, p.token_id, p.p_model, p.p_market, p.edge, p.components_json(), p.created_at),
    )
    await conn.commit()
    p.id = cur.lastrowid
    return cur.lastrowid


async def insert_trade(conn: aiosqlite.Connection, t: Trade) -> int:
    cur = await conn.execute(
        "INSERT INTO trades "
        "(prediction_id, condition_id, token_id, side, size, limit_price, fill_price, "
        " slippage, intended_size, is_paper, opened_at, closed_at, pnl, outcome, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            t.prediction_id, t.condition_id, t.token_id, t.side, t.size, t.limit_price,
            t.fill_price, t.slippage, t.intended_size, int(t.is_paper), t.opened_at,
            t.closed_at, t.pnl, t.outcome, t.source,
        ),
    )
    await conn.commit()
    t.id = cur.lastrowid
    return cur.lastrowid


async def insert_paper_execution(conn: aiosqlite.Connection, e: PaperExecution) -> int:
    cur = await conn.execute(
        "INSERT INTO paper_executions "
        "(prediction_id, trade_id, condition_id, token_id, side, requested_size, filled_size, "
        " unfilled_size, limit_price, fill_price, slippage, status, is_paper, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            e.prediction_id,
            e.trade_id,
            e.condition_id,
            e.token_id,
            e.side,
            e.requested_size,
            e.filled_size,
            e.unfilled_size,
            e.limit_price,
            e.fill_price,
            e.slippage,
            e.status,
            int(e.is_paper),
            e.created_at,
        ),
    )
    await conn.commit()
    e.id = cur.lastrowid
    return cur.lastrowid


async def close_trade(
    conn: aiosqlite.Connection, trade_id: int, pnl: float, outcome: str, closed_at: int | None = None
) -> None:
    await conn.execute(
        "UPDATE trades SET closed_at=?, pnl=?, outcome=? WHERE id=?",
        (closed_at or int(time.time()), pnl, outcome, trade_id),
    )
    await conn.commit()


async def open_positions_count(conn: aiosqlite.Connection, source: str = "paper_live") -> int:
    cur = await conn.execute(
        "SELECT COUNT(*) FROM trades WHERE closed_at IS NULL AND source = ?",
        (source,),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def total_open_exposure(conn: aiosqlite.Connection, source: str = "paper_live") -> float:
    cur = await conn.execute(
        "SELECT COALESCE(SUM(size * COALESCE(fill_price, limit_price)), 0) "
        "FROM trades WHERE closed_at IS NULL AND is_paper = 1 AND source = ?",
        (source,),
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


async def daily_loss_usd(conn: aiosqlite.Connection, since_ts: int, source: str = "paper_live") -> float:
    cur = await conn.execute(
        "SELECT COALESCE(-SUM(MIN(pnl, 0)), 0) FROM trades WHERE closed_at >= ? AND source = ?",
        (since_ts, source),
    )
    # SQLite doesn't have MIN as an aggregate-of-aggregates without subquery
    cur = await conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 0) "
        "FROM trades WHERE closed_at >= ? AND source = ?",
        (since_ts, source),
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


async def daily_gain_usd(conn: aiosqlite.Connection, since_ts: int, source: str = "paper_live") -> float:
    cur = await conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END), 0) "
        "FROM trades WHERE closed_at >= ? AND source = ?",
        (since_ts, source),
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


async def net_realized_pnl(conn: aiosqlite.Connection) -> float:
    """Sum of pnl across all closed trades (any source)."""
    cur = await conn.execute(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE closed_at IS NOT NULL"
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


async def daily_trades_opened(conn: aiosqlite.Connection, since_ts: int, source: str = "paper_live") -> int:
    """Count trades with opened_at >= since_ts (i.e. opened today)."""
    cur = await conn.execute(
        "SELECT COUNT(*) FROM trades WHERE opened_at >= ? AND source = ?",
        (since_ts, source),
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def consecutive_losses(conn: aiosqlite.Connection) -> int:
    """Count of the most-recent consecutive closed trades with pnl < 0."""
    cur = await conn.execute(
        "SELECT pnl FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC"
    )
    rows = await cur.fetchall()
    streak = 0
    for (pnl,) in rows:
        if pnl is not None and pnl < 0:
            streak += 1
        else:
            break
    return streak


async def fetch_open_trades(conn: aiosqlite.Connection, source: str = "paper_live") -> list[OpenTradeRecord]:
    """Return all open (unclosed) paper trades with their market end_date_iso."""
    cur = await conn.execute(
        """
        SELECT
            t.id,
            t.condition_id,
            t.token_id,
            t.fill_price,
            t.size,
            t.slippage,
            (
                SELECT mf.end_date_iso
                FROM markets_flagged mf
                WHERE mf.condition_id = t.condition_id
                ORDER BY mf.flagged_at DESC
                LIMIT 1
            ) AS end_date_iso
        FROM trades t
        WHERE t.closed_at IS NULL AND t.source = ?
        """,
        (source,),
    )
    rows = await cur.fetchall()
    return [
        OpenTradeRecord(
            trade_id=r[0], condition_id=r[1], token_id=r[2],
            fill_price=r[3], size=r[4], slippage=r[5], end_date_iso=r[6],
        )
        for r in rows
    ]


async def insert_lesson(conn: aiosqlite.Connection, lesson: Lesson) -> int:
    cur = await conn.execute(
        "INSERT INTO lessons (trade_id, cause, rule_proposed, notes, created_at) VALUES (?, ?, ?, ?, ?)",
        (lesson.trade_id, lesson.cause, lesson.rule_proposed, lesson.notes, lesson.created_at),
    )
    await conn.commit()
    lesson.id = cur.lastrowid
    return cur.lastrowid


async def insert_api_spend(conn: aiosqlite.Connection, s: ApiSpend) -> None:
    await conn.execute(
        "INSERT INTO api_spend "
        "(provider, model, input_tokens, output_tokens, cache_read_tokens, cost_usd, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (s.provider, s.model, s.input_tokens, s.output_tokens, s.cache_read_tokens, s.cost_usd, s.created_at),
    )
    await conn.commit()


async def daily_api_cost_usd(conn: aiosqlite.Connection, since_ts: int) -> float:
    cur = await conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM api_spend WHERE created_at >= ?",
        (since_ts,),
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


async def persist_daily_metrics(conn: aiosqlite.Connection, date_str: str, source: str = "paper_live") -> None:
    """Compute and upsert metrics for date_str/source into metrics_daily."""
    d = date.fromisoformat(date_str)
    # Use local midnight so trade timestamps (which use time.time()) align with the date window
    day_start = int(datetime(d.year, d.month, d.day).timestamp())
    day_end = day_start + 86_400

    cur = await conn.execute(
        """
        SELECT t.pnl, p.p_model, t.outcome
        FROM trades t
        LEFT JOIN predictions p ON t.prediction_id = p.id
        WHERE t.closed_at >= ? AND t.closed_at < ?
          AND t.outcome IN ('YES', 'NO')
          AND t.is_paper = 1
          AND t.source = ?
        """,
        (day_start, day_end, source),
    )
    rows = await cur.fetchall()

    pnls = [float(r[0]) for r in rows if r[0] is not None]
    predicted = [float(r[1]) for r in rows if r[1] is not None]
    actual = [1 if r[2] == "YES" else 0 for r in rows if r[1] is not None]

    n_trades = len(rows)
    wr = win_rate(pnls)
    bs = brier_score(predicted, actual)
    pnl_total = sum(pnls)
    sr = sharpe_ratio(pnls)
    equity = []
    running = 0.0
    for p in pnls:
        running += p
        equity.append(running)
    dd = max_drawdown(equity)
    pf = profit_factor(pnls)
    api_cost = await daily_api_cost_usd(conn, day_start)

    await conn.execute(
        """
        INSERT INTO metrics_daily
            (date, source, win_rate, sharpe, max_drawdown, profit_factor,
             brier_score, n_trades, pnl_usd, api_cost_usd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, source) DO UPDATE SET
            win_rate=excluded.win_rate, sharpe=excluded.sharpe, max_drawdown=excluded.max_drawdown,
            profit_factor=excluded.profit_factor, brier_score=excluded.brier_score,
            n_trades=excluded.n_trades, pnl_usd=excluded.pnl_usd, api_cost_usd=excluded.api_cost_usd
        """,
        (date_str, source, wr, sr, dd, pf, bs, n_trades, pnl_total, api_cost),
    )
    await conn.commit()


async def acceptance_criteria_met(conn: aiosqlite.Connection, source: str = "paper_live") -> tuple[bool, str]:
    """Check whether a trade source meets the paper gate (50 trades, >60% win, Brier <0.25)."""
    cur = await conn.execute(
        """
        SELECT t.pnl, p.p_model, t.outcome
        FROM trades t
        LEFT JOIN predictions p ON t.prediction_id = p.id
        WHERE t.outcome IN ('YES', 'NO') AND t.is_paper = 1 AND t.source = ?
        """,
        (source,),
    )
    rows = await cur.fetchall()

    n = len(rows)
    if n < 50:
        return False, f"need 50 settled trades, have {n}"

    pnls = [float(r[0]) for r in rows if r[0] is not None]
    wr = win_rate(pnls)
    if wr <= 0.60:
        return False, f"win rate {wr:.1%} must exceed 60%"

    predicted = [float(r[1]) for r in rows if r[1] is not None]
    actual = [1 if r[2] == "YES" else 0 for r in rows if r[1] is not None]
    bs = brier_score(predicted, actual)
    if bs >= 0.25:
        return False, f"Brier score {bs:.3f} must be below 0.25"

    return True, ""


async def recent_daily_metrics(conn: aiosqlite.Connection, days: int = 7, source: str = "paper_live") -> list[dict]:
    """Return the most recent `days` rows for a source from metrics_daily, newest first."""
    from datetime import date, timedelta
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    cur = await conn.execute(
        "SELECT date, win_rate, brier_score, n_trades, pnl_usd, sharpe, api_cost_usd "
        "FROM metrics_daily WHERE date >= ? AND source = ? ORDER BY date DESC",
        (cutoff, source),
    )
    rows = await cur.fetchall()
    return [
        {
            "date": r[0], "win_rate": r[1], "brier_score": r[2],
            "n_trades": r[3], "pnl_usd": r[4], "sharpe": r[5], "api_cost_usd": r[6],
        }
        for r in rows
    ]


async def insert_book_snapshot(
    conn: aiosqlite.Connection,
    token_id: str,
    best_bid: float | None,
    best_ask: float | None,
    book_json: str,
    captured_at: int | None = None,
) -> None:
    mid = ((best_bid or 0) + (best_ask or 0)) / 2 if (best_bid is not None and best_ask is not None) else None
    spread = (best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
    await conn.execute(
        "INSERT INTO book_snapshots (token_id, best_bid, best_ask, mid, spread, book_json, captured_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token_id, best_bid, best_ask, mid, spread, book_json, captured_at or int(time.time())),
    )
    await conn.commit()
