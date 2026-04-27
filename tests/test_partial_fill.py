"""Tests for partial-fill and no-fill persistence — TDD RED phase."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.config import RuntimeSettings
from bot.orchestrator import run_once
from bot.polymarket.client import Market, MarketResolution, OrderBookSnapshot
from bot.storage.db import open_db
from bot.storage.repo import open_positions_count

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _market(end_days: int = 7) -> Market:
    return Market(
        condition_id="cond-1",
        question="Will this resolve Yes?",
        yes_token="yes-1",
        no_token="no-1",
        end_date_iso=(datetime.now(UTC) + timedelta(days=end_days)).isoformat(),
        volume_24h=5_000,
        liquidity=2_000,
        closed=False,
        raw={},
    )


def _book_with_liquidity() -> OrderBookSnapshot:
    """Book with enough asks to partially fill a ~100-share order."""
    return OrderBookSnapshot(
        token_id="yes-1",
        asks=[(0.55, 50)],  # only 50 shares available (partial fill)
        bids=[(0.52, 100)],
        timestamp=0,
    )


def _empty_book() -> OrderBookSnapshot:
    """Book with no asks — triggers a no-fill."""
    return OrderBookSnapshot(
        token_id="yes-1",
        asks=[],
        bids=[(0.52, 100)],
        timestamp=0,
    )


def _full_book() -> OrderBookSnapshot:
    """Book with plenty of liquidity — full fill."""
    return OrderBookSnapshot(
        token_id="yes-1",
        asks=[(0.55, 500), (0.56, 500)],
        bids=[(0.52, 100)],
        timestamp=0,
    )


async def _run(conn, book: OrderBookSnapshot, tmp_path=None, scan_book: OrderBookSnapshot | None = None) -> tuple:
    """Run one pass and return (summary, trades_rows).

    scan_book: book returned during orderbook scan (defaults to book).
    book: book returned at execution time.
    This lets tests simulate liquidity disappearing between scan and execution.
    """
    first = scan_book if scan_book is not None else book
    client = MagicMock()
    client.list_markets = AsyncMock(return_value=[_market()])
    client.get_orderbook = AsyncMock(side_effect=[first, book])
    client.get_market_resolution = AsyncMock(return_value=MarketResolution(resolved=False, final_yes_price=None))
    client.close = AsyncMock()

    settings = RuntimeSettings(
        stop_file=(tmp_path or __import__("pathlib").Path("/tmp")) / "STOP",
        bankroll_usdc=10_000,
        edge_threshold=0.04,
    )

    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=client,
        mock_ai=True,
        max_markets=1,
    )
    cur = await conn.execute(
        "SELECT size, fill_price, intended_size, outcome, closed_at, pnl FROM trades ORDER BY id"
    )
    rows = await cur.fetchall()
    return summary, rows


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    conn = await open_db(tmp_path / "bot.sqlite")
    yield conn
    await conn.close()


# ---------------------------------------------------------------------------
# No-fill tests
# ---------------------------------------------------------------------------


async def test_no_fill_trade_is_persisted(db, tmp_path):
    summary, rows = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert len(rows) == 1, "no-fill should be persisted as a trade record"


async def test_no_fill_trade_has_zero_size(db, tmp_path):
    _, rows = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert rows[0][0] == pytest.approx(0.0)  # size


async def test_no_fill_trade_outcome_is_no_fill(db, tmp_path):
    _, rows = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert rows[0][3] == "no_fill"  # outcome


async def test_no_fill_trade_is_immediately_closed(db, tmp_path):
    _, rows = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert rows[0][4] is not None  # closed_at is set


async def test_no_fill_trade_has_zero_pnl(db, tmp_path):
    _, rows = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert rows[0][5] == pytest.approx(0.0)  # pnl


async def test_no_fill_trade_records_intended_size(db, tmp_path):
    _, rows = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert rows[0][2] is not None  # intended_size populated
    assert rows[0][2] > 0


async def test_no_fill_does_not_count_as_paper_trade_written(db, tmp_path):
    summary, _ = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert summary.paper_trades_written == 0


async def test_no_fill_counted_in_summary(db, tmp_path):
    summary, _ = await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert summary.no_fill_trades == 1


async def test_no_fill_not_counted_as_open_position(db, tmp_path):
    await _run(db, _empty_book(), tmp_path, scan_book=_full_book())
    assert await open_positions_count(db) == 0


# ---------------------------------------------------------------------------
# Partial-fill tests
# ---------------------------------------------------------------------------


async def test_partial_fill_records_filled_size(db, tmp_path):
    _, rows = await _run(db, _book_with_liquidity(), tmp_path)
    assert rows[0][0] == pytest.approx(50.0)  # size = filled shares


async def test_partial_fill_records_intended_size(db, tmp_path):
    _, rows = await _run(db, _book_with_liquidity(), tmp_path)
    intended = rows[0][2]
    assert intended is not None
    assert intended > rows[0][0]  # intended > filled (partial)


async def test_partial_fill_stays_open(db, tmp_path):
    _, rows = await _run(db, _book_with_liquidity(), tmp_path)
    assert rows[0][4] is None  # closed_at is None (position open)


async def test_partial_fill_counts_as_paper_trade_written(db, tmp_path):
    summary, _ = await _run(db, _book_with_liquidity(), tmp_path)
    assert summary.paper_trades_written == 1


async def test_partial_fill_not_counted_as_no_fill(db, tmp_path):
    summary, _ = await _run(db, _book_with_liquidity(), tmp_path)
    assert summary.no_fill_trades == 0


# ---------------------------------------------------------------------------
# Full-fill: intended_size == filled_size
# ---------------------------------------------------------------------------


async def test_full_fill_records_intended_size_equal_to_filled(db, tmp_path):
    _, rows = await _run(db, _full_book(), tmp_path)
    assert rows[0][2] == pytest.approx(rows[0][0])  # intended_size == size
