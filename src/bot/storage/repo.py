"""Repository layer — typed CRUD for persisted records."""
from __future__ import annotations

import time

import aiosqlite

from .models import (
    ApiSpend,
    FlaggedMarket,
    Lesson,
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
        " slippage, is_paper, opened_at, closed_at, pnl, outcome) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            t.prediction_id, t.condition_id, t.token_id, t.side, t.size, t.limit_price,
            t.fill_price, t.slippage, int(t.is_paper), t.opened_at, t.closed_at, t.pnl, t.outcome,
        ),
    )
    await conn.commit()
    t.id = cur.lastrowid
    return cur.lastrowid


async def open_paper_trades(conn: aiosqlite.Connection) -> list[Trade]:
    cur = await conn.execute(
        "SELECT id, prediction_id, condition_id, token_id, side, size, limit_price, "
        "       fill_price, slippage, is_paper, opened_at, closed_at, pnl, outcome "
        "FROM trades WHERE closed_at IS NULL AND is_paper = 1 ORDER BY opened_at ASC"
    )
    rows = await cur.fetchall()
    return [
        Trade(
            id=row[0],
            prediction_id=row[1],
            condition_id=row[2],
            token_id=row[3],
            side=row[4],
            size=row[5],
            limit_price=row[6],
            fill_price=row[7],
            slippage=row[8],
            is_paper=bool(row[9]),
            opened_at=row[10],
            closed_at=row[11],
            pnl=row[12],
            outcome=row[13],
        )
        for row in rows
    ]


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


async def open_positions_count(conn: aiosqlite.Connection) -> int:
    cur = await conn.execute("SELECT COUNT(*) FROM trades WHERE closed_at IS NULL")
    row = await cur.fetchone()
    return int(row[0]) if row else 0


async def total_open_exposure(conn: aiosqlite.Connection) -> float:
    cur = await conn.execute(
        "SELECT COALESCE(SUM(size * COALESCE(fill_price, limit_price)), 0) "
        "FROM trades WHERE closed_at IS NULL AND is_paper = 1"
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


async def daily_loss_usd(conn: aiosqlite.Connection, since_ts: int) -> float:
    cur = await conn.execute(
        "SELECT COALESCE(-SUM(MIN(pnl, 0)), 0) FROM trades WHERE closed_at >= ?",
        (since_ts,),
    )
    # SQLite doesn't have MIN as an aggregate-of-aggregates without subquery
    cur = await conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl < 0 THEN -pnl ELSE 0 END), 0) "
        "FROM trades WHERE closed_at >= ?",
        (since_ts,),
    )
    row = await cur.fetchone()
    return float(row[0]) if row else 0.0


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
