"""Tests for compound/postmortem loop — TDD RED phase."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from postmortem import append_to_failure_log

from bot.polymarket.client import MarketResolution, PolymarketClient
from bot.storage.db import open_db
from bot.storage.models import FlaggedMarket, Trade
from bot.storage.repo import (
    close_trade,
    fetch_open_trades,
    insert_flagged_market,
    insert_trade,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path):
    conn = await open_db(tmp_path / "bot.sqlite")
    yield conn
    await conn.close()


@pytest.fixture
def past_end_date():
    """ISO date string in the past (yesterday)."""
    return "2020-01-01T00:00:00Z"


@pytest.fixture
def future_end_date():
    """ISO date string far in the future."""
    return "2099-12-31T00:00:00Z"


# ---------------------------------------------------------------------------
# repo: fetch_open_trades
# ---------------------------------------------------------------------------


async def test_fetch_open_trades_returns_open_only(db):
    t_open = Trade(condition_id="c1", token_id="t1", side="BUY", size=50, limit_price=0.5, fill_price=0.50)
    t_closed = Trade(condition_id="c2", token_id="t2", side="BUY", size=50, limit_price=0.5, fill_price=0.50)
    tid_open = await insert_trade(db, t_open)
    tid_closed = await insert_trade(db, t_closed)
    await close_trade(db, tid_closed, pnl=5.0, outcome="YES")

    records = await fetch_open_trades(db)

    assert len(records) == 1
    assert records[0].trade_id == tid_open


async def test_fetch_open_trades_includes_end_date_from_flagged(db, past_end_date):
    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c1",
            yes_token="t1",
            no_token="n1",
            mid_price=0.5,
            spread=0.02,
            volume_24h=1000,
            end_date_iso=past_end_date,
        ),
    )
    t = Trade(condition_id="c1", token_id="t1", side="BUY", size=50, limit_price=0.5, fill_price=0.50)
    await insert_trade(db, t)

    records = await fetch_open_trades(db)

    assert records[0].end_date_iso == past_end_date


async def test_fetch_open_trades_end_date_none_when_not_flagged(db):
    t = Trade(condition_id="c99", token_id="t99", side="BUY", size=10, limit_price=0.5, fill_price=0.5)
    await insert_trade(db, t)

    records = await fetch_open_trades(db)

    assert records[0].end_date_iso is None


# ---------------------------------------------------------------------------
# PolymarketClient.get_market_resolution
# ---------------------------------------------------------------------------


def _mock_gamma_response(data: list[dict]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _make_client(gamma_data: list[dict]) -> PolymarketClient:
    client = PolymarketClient(host="http://fake", gamma_host="http://fake-gamma")
    client._http = MagicMock()
    client._http.get = AsyncMock(return_value=_mock_gamma_response(gamma_data))
    client._http.aclose = AsyncMock()
    return client


async def test_get_market_resolution_resolved_yes():
    client = _make_client([{"conditionId": "c1", "outcomePrices": '["1.0", "0.0"]', "closed": True}])
    result = await client.get_market_resolution("c1")
    assert result.resolved is True
    assert result.final_yes_price == pytest.approx(1.0)
    await client.close()


async def test_get_market_resolution_resolved_no():
    client = _make_client([{"conditionId": "c1", "outcomePrices": '["0.0", "1.0"]', "closed": True}])
    result = await client.get_market_resolution("c1")
    assert result.resolved is True
    assert result.final_yes_price == pytest.approx(0.0)
    await client.close()


async def test_get_market_resolution_ambiguous_returns_unresolved():
    client = _make_client([{"conditionId": "c1", "outcomePrices": '["0.55", "0.45"]', "closed": False}])
    result = await client.get_market_resolution("c1")
    assert result.resolved is False
    assert result.final_yes_price is None
    await client.close()


async def test_get_market_resolution_not_found_returns_unresolved():
    client = _make_client([])
    result = await client.get_market_resolution("c-nonexistent")
    assert result.resolved is False
    assert result.final_yes_price is None
    await client.close()


# ---------------------------------------------------------------------------
# postmortem: append_to_failure_log
# ---------------------------------------------------------------------------


def test_append_to_failure_log_creates_entry(tmp_path):
    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    append_to_failure_log(
        log_path=log_path,
        condition_id="cond-abc",
        trade_id=42,
        outcome="NO",
        pnl=-12.5,
        cause="bad-prediction",
        rule_proposed="Require stronger narrative cross-source signal.",
    )

    content = log_path.read_text()
    assert "cond-abc" in content
    assert "trade_id=42" in content
    assert "NO" in content
    assert "-12.5" in content or "-12.50" in content
    assert "bad-prediction" in content


def test_append_to_failure_log_appends_multiple_entries(tmp_path):
    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    for i in range(3):
        append_to_failure_log(
            log_path=log_path,
            condition_id=f"cond-{i}",
            trade_id=i,
            outcome="NO",
            pnl=-5.0,
            cause="bad-prediction",
            rule_proposed="Rule.",
        )

    content = log_path.read_text()
    assert content.count("cond-") == 3


# ---------------------------------------------------------------------------
# orchestrator: _settle_expired_trades
# ---------------------------------------------------------------------------


async def test_settle_expired_trades_closes_resolved_yes_trade(db, past_end_date, tmp_path):
    from bot.orchestrator import _settle_expired_trades

    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c1", yes_token="t1", no_token="n1",
            mid_price=0.5, spread=0.02, volume_24h=1000, end_date_iso=past_end_date,
        ),
    )
    t = Trade(condition_id="c1", token_id="t1", side="BUY", size=100, limit_price=0.5, fill_price=0.5)
    tid = await insert_trade(db, t)

    mock_client = MagicMock()
    mock_client.get_market_resolution = AsyncMock(
        return_value=MarketResolution(resolved=True, final_yes_price=1.0)
    )

    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    count = await _settle_expired_trades(db, mock_client, failure_log_path=log_path)

    assert count == 1

    # Trade should now be closed
    cur = await db.execute("SELECT closed_at, pnl, outcome FROM trades WHERE id=?", (tid,))
    row = await cur.fetchone()
    assert row[0] is not None
    assert row[1] == pytest.approx((1.0 - 0.5) * 100)
    assert row[2] == "YES"


async def test_settle_expired_trades_closes_resolved_no_trade(db, past_end_date, tmp_path):
    from bot.orchestrator import _settle_expired_trades

    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c2", yes_token="t2", no_token="n2",
            mid_price=0.6, spread=0.02, volume_24h=1000, end_date_iso=past_end_date,
        ),
    )
    t = Trade(condition_id="c2", token_id="t2", side="BUY", size=100, limit_price=0.6, fill_price=0.6)
    tid = await insert_trade(db, t)

    mock_client = MagicMock()
    mock_client.get_market_resolution = AsyncMock(
        return_value=MarketResolution(resolved=True, final_yes_price=0.0)
    )

    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    await _settle_expired_trades(db, mock_client, failure_log_path=log_path)

    cur = await db.execute("SELECT pnl, outcome FROM trades WHERE id=?", (tid,))
    row = await cur.fetchone()
    assert row[0] == pytest.approx((0.0 - 0.6) * 100)
    assert row[1] == "NO"


async def test_settle_expired_trades_inserts_lesson(db, past_end_date, tmp_path):
    from bot.orchestrator import _settle_expired_trades

    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c3", yes_token="t3", no_token="n3",
            mid_price=0.5, spread=0.02, volume_24h=1000, end_date_iso=past_end_date,
        ),
    )
    t = Trade(condition_id="c3", token_id="t3", side="BUY", size=100, limit_price=0.5, fill_price=0.5)
    await insert_trade(db, t)

    mock_client = MagicMock()
    mock_client.get_market_resolution = AsyncMock(
        return_value=MarketResolution(resolved=True, final_yes_price=0.0)
    )

    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    await _settle_expired_trades(db, mock_client, failure_log_path=log_path)

    cur = await db.execute("SELECT COUNT(*) FROM lessons")
    row = await cur.fetchone()
    assert row[0] == 1


async def test_settle_expired_trades_appends_to_failure_log(db, past_end_date, tmp_path):
    from bot.orchestrator import _settle_expired_trades

    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c4", yes_token="t4", no_token="n4",
            mid_price=0.5, spread=0.02, volume_24h=1000, end_date_iso=past_end_date,
        ),
    )
    t = Trade(condition_id="c4", token_id="t4", side="BUY", size=100, limit_price=0.5, fill_price=0.5)
    await insert_trade(db, t)

    mock_client = MagicMock()
    mock_client.get_market_resolution = AsyncMock(
        return_value=MarketResolution(resolved=True, final_yes_price=0.0)
    )

    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    await _settle_expired_trades(db, mock_client, failure_log_path=log_path)

    content = log_path.read_text()
    assert "c4" in content


async def test_settle_expired_trades_skips_active_trade(db, future_end_date, tmp_path):
    from bot.orchestrator import _settle_expired_trades

    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c5", yes_token="t5", no_token="n5",
            mid_price=0.5, spread=0.02, volume_24h=1000, end_date_iso=future_end_date,
        ),
    )
    t = Trade(condition_id="c5", token_id="t5", side="BUY", size=100, limit_price=0.5, fill_price=0.5)
    await insert_trade(db, t)

    mock_client = MagicMock()
    mock_client.get_market_resolution = AsyncMock(
        return_value=MarketResolution(resolved=False, final_yes_price=None)
    )

    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    count = await _settle_expired_trades(db, mock_client, failure_log_path=log_path)

    assert count == 0
    mock_client.get_market_resolution.assert_not_called()


async def test_settle_expired_trades_skips_unresolved_expired_trade(db, past_end_date, tmp_path):
    from bot.orchestrator import _settle_expired_trades

    await insert_flagged_market(
        db,
        FlaggedMarket(
            condition_id="c6", yes_token="t6", no_token="n6",
            mid_price=0.5, spread=0.02, volume_24h=1000, end_date_iso=past_end_date,
        ),
    )
    t = Trade(condition_id="c6", token_id="t6", side="BUY", size=100, limit_price=0.5, fill_price=0.5)
    tid = await insert_trade(db, t)

    mock_client = MagicMock()
    mock_client.get_market_resolution = AsyncMock(
        return_value=MarketResolution(resolved=False, final_yes_price=None)
    )

    log_path = tmp_path / "failure_log.md"
    log_path.write_text("# Failure Log\n\n")

    count = await _settle_expired_trades(db, mock_client, failure_log_path=log_path)

    assert count == 0
    cur = await db.execute("SELECT closed_at FROM trades WHERE id=?", (tid,))
    row = await cur.fetchone()
    assert row[0] is None


# ---------------------------------------------------------------------------
# orchestrator: run_once includes trades_settled
# ---------------------------------------------------------------------------


async def test_run_once_summary_includes_trades_settled(tmp_path):

    from bot.config import RuntimeSettings
    from bot.orchestrator import run_once

    db_path = tmp_path / "bot.sqlite"
    conn = await open_db(db_path)

    settings = RuntimeSettings()
    mock_pm = MagicMock()
    mock_pm.list_markets = AsyncMock(return_value=[])
    mock_pm.close = AsyncMock()

    summary = await run_once(
        settings=settings,
        conn=conn,
        polymarket_client=mock_pm,
        mock_ai=True,
        max_markets=0,
    )

    assert hasattr(summary, "trades_settled")
    assert summary.trades_settled == 0
    await conn.close()
