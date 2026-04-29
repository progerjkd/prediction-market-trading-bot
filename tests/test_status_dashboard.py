"""Tests for --status dashboard CLI mode — TDD RED phase."""
from __future__ import annotations

import pytest

from bot.storage.db import open_db
from bot.storage.models import Prediction, Trade
from bot.storage.repo import (
    close_trade,
    insert_prediction,
    insert_trade,
    persist_daily_metrics,
    recent_daily_metrics,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    conn = await open_db(tmp_path / "bot.sqlite")
    yield conn
    await conn.close()


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


async def _settled_trade(conn, *, pnl: float, p_model: float, outcome: str) -> None:
    pred = Prediction(condition_id="cx", token_id="tx", p_model=p_model, p_market=0.5, edge=0.1)
    pid = await insert_prediction(conn, pred)
    t = Trade(condition_id="cx", token_id="tx", side="BUY", size=100, limit_price=0.5, fill_price=0.5, prediction_id=pid)
    tid = await insert_trade(conn, t)
    await close_trade(conn, tid, pnl=pnl, outcome=outcome)


# ---------------------------------------------------------------------------
# recent_daily_metrics
# ---------------------------------------------------------------------------


async def test_recent_daily_metrics_returns_empty_on_no_data(db):
    rows = await recent_daily_metrics(db, days=7)
    assert rows == []


async def test_recent_daily_metrics_returns_persisted_rows(db):
    await _settled_trade(db, pnl=5.0, p_model=0.7, outcome="YES")
    await persist_daily_metrics(db, _today())

    rows = await recent_daily_metrics(db, days=7)
    assert len(rows) == 1
    assert rows[0]["date"] == _today()


async def test_recent_daily_metrics_row_has_required_fields(db):
    await _settled_trade(db, pnl=5.0, p_model=0.7, outcome="YES")
    await persist_daily_metrics(db, _today())

    rows = await recent_daily_metrics(db, days=7)
    row = rows[0]
    for field in ("date", "win_rate", "brier_score", "n_trades", "pnl_usd"):
        assert field in row, f"missing field: {field}"


async def test_recent_daily_metrics_excludes_old_rows(db):
    # Insert a row for a date older than the window
    await db.execute(
        "INSERT INTO metrics_daily (date, win_rate, sharpe, max_drawdown, profit_factor, "
        "brier_score, n_trades, pnl_usd, api_cost_usd) VALUES (?, 0, 0, 0, 0, 0, 0, 0, 0)",
        ("2020-01-01",),
    )
    await db.commit()

    rows = await recent_daily_metrics(db, days=7)
    assert all(r["date"] != "2020-01-01" for r in rows)


# ---------------------------------------------------------------------------
# --status CLI mode via async_main
# ---------------------------------------------------------------------------


async def test_status_exits_zero(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    code = await async_main(["--status"])
    assert code == 0


async def test_status_prints_acceptance_gate(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out
    assert "acceptance" in out.lower() or "gate" in out.lower() or "not met" in out.lower()


async def test_status_labels_acceptance_gate_as_paper_live(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out
    assert "paper-live acceptance gate" in out.lower()


async def test_status_prints_reason_when_not_met(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out
    # With zero trades the reason should mention the 50-trade requirement
    assert "50" in out


async def test_status_prints_recent_metrics_header(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out
    assert "metrics" in out.lower() or "win" in out.lower() or "brier" in out.lower() or "trades" in out.lower()


async def test_status_prints_open_paper_position_count_and_exposure(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    db_path = tmp_path / "bot.sqlite"
    conn = await open_db(db_path)
    await insert_trade(
        conn,
        Trade(
            condition_id="open-cx",
            token_id="open-tx",
            side="BUY",
            size=100,
            limit_price=0.5,
            fill_price=0.5,
        ),
    )
    await conn.close()

    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out

    assert "open paper positions" in out.lower()
    assert "1" in out
    assert "50.00" in out


# ---------------------------------------------------------------------------
# fetch_open_trades includes question
# ---------------------------------------------------------------------------


async def test_fetch_open_trades_includes_question(db):
    from bot.storage.models import FlaggedMarket
    from bot.storage.repo import fetch_open_trades, insert_flagged_market

    await insert_trade(
        db,
        Trade(condition_id="cq-1", token_id="tq-1", side="BUY", size=100, limit_price=0.5, fill_price=0.5),
    )
    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="cq-1", yes_token="tq-1", no_token="nq-1",
            mid_price=0.5, spread=0.04, volume_24h=1000.0,
            question="Will this market resolve Yes?",
            end_date_iso="2026-06-01T00:00:00Z",
            liquidity=500.0, edge_proxy=0.05,
        ),
    )

    records = await fetch_open_trades(db)
    assert len(records) == 1
    assert records[0].question == "Will this market resolve Yes?"


# ---------------------------------------------------------------------------
# --status shows position detail table
# ---------------------------------------------------------------------------


async def test_status_shows_open_position_question_and_end_date(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main
    from bot.storage.models import FlaggedMarket
    from bot.storage.repo import insert_flagged_market

    db_path = tmp_path / "bot.sqlite"
    conn = await open_db(db_path)
    await insert_trade(
        conn,
        Trade(condition_id="cq-2", token_id="tq-2", side="BUY", size=200, limit_price=0.55, fill_price=0.55),
    )
    await insert_flagged_market(
        conn,
        FlaggedMarket(
            condition_id="cq-2", yes_token="tq-2", no_token="nq-2",
            mid_price=0.55, spread=0.04, volume_24h=2000.0,
            question="Will the featured candidate win?",
            end_date_iso="2026-05-15T00:00:00Z",
            liquidity=800.0, edge_proxy=0.06,
        ),
    )
    await conn.close()

    monkeypatch.setenv("BOT_DB_PATH", str(db_path))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    await async_main(["--status"])
    out = capsys.readouterr().out

    assert "Will the featured candidate win?" in out
    assert "2026-05-15" in out
