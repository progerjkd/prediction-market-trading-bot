"""Backtest harness — TDD RED phase.

run_backtest replays resolved markets from a DataFrame through XGBoost,
writes settled paper trades to the DB, and returns a summary dict.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bot.config import RuntimeSettings
from bot.storage.db import open_db
from bot.storage.repo import acceptance_criteria_met

FEATURE_COLS = [
    "current_mid", "spread", "volume_24h", "days_to_resolution",
    "narrative_score", "momentum_1h", "momentum_24h",
]


def _make_df(rows: list[dict]) -> pd.DataFrame:
    defaults = {c: 0.0 for c in FEATURE_COLS}
    defaults["label"] = 1
    return pd.DataFrame([{**defaults, **r} for r in rows])


def _settings(**kwargs) -> RuntimeSettings:
    return RuntimeSettings(stop_file=Path("/tmp/STOP_bt"), **kwargs)


# ---------------------------------------------------------------------------
# run_backtest signature + import
# ---------------------------------------------------------------------------


def test_backtest_importable():
    from backtest import run_backtest  # noqa: F401


# ---------------------------------------------------------------------------
# Rows below edge threshold are skipped
# ---------------------------------------------------------------------------


async def test_backtest_skips_rows_below_edge_threshold(tmp_path):
    from backtest import run_backtest

    # current_mid=0.55, no model → xgb_infer falls back to current_mid=0.55
    # edge proxy = 0.55 - 0.55 = 0 < edge_threshold=0.04 → skip
    df = _make_df([{"current_mid": 0.55, "spread": 0.02, "volume_24h": 500.0}])
    conn = await open_db(tmp_path / "bt.sqlite")
    settings = _settings(edge_threshold=0.04)

    result = await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=settings)

    assert result["trades_written"] == 0
    await conn.close()


# ---------------------------------------------------------------------------
# Rows with positive edge produce settled trades
# ---------------------------------------------------------------------------


async def test_backtest_writes_trade_for_positive_edge_row(tmp_path):
    # Manually craft a row that will produce edge > 0.04 even with fallback inference
    # We'll mock xgb_infer to return a known value
    from unittest.mock import patch

    from backtest import run_backtest

    df = _make_df([{"current_mid": 0.40, "spread": 0.02, "volume_24h": 500.0, "label": 1}])
    conn = await open_db(tmp_path / "bt.sqlite")
    settings = _settings(edge_threshold=0.04)

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        result = await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=settings)

    assert result["trades_written"] == 1
    await conn.close()


async def test_backtest_outcome_yes_when_label_1(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    df = _make_df([{"current_mid": 0.40, "spread": 0.02, "volume_24h": 500.0, "label": 1}])
    conn = await open_db(tmp_path / "bt.sqlite")
    settings = _settings(edge_threshold=0.04)

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=settings)

    cur = await conn.execute("SELECT outcome FROM trades LIMIT 1")
    row = await cur.fetchone()
    assert row[0] == "YES"
    await conn.close()


async def test_backtest_outcome_no_when_label_0(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    df = _make_df([{"current_mid": 0.40, "spread": 0.02, "volume_24h": 500.0, "label": 0}])
    conn = await open_db(tmp_path / "bt.sqlite")
    settings = _settings(edge_threshold=0.04)

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=settings)

    cur = await conn.execute("SELECT outcome FROM trades LIMIT 1")
    row = await cur.fetchone()
    assert row[0] == "NO"
    await conn.close()


async def test_backtest_pnl_positive_for_correct_yes_prediction(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    # Buy YES at 0.41 (mid 0.40 + half spread 0.01), outcome YES → final_price=1.0
    # pnl = (1.0 - 0.41) * size > 0
    df = _make_df([{"current_mid": 0.40, "spread": 0.02, "volume_24h": 500.0, "label": 1}])
    conn = await open_db(tmp_path / "bt.sqlite")
    settings = _settings(edge_threshold=0.04)

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=settings)

    cur = await conn.execute("SELECT pnl FROM trades LIMIT 1")
    row = await cur.fetchone()
    assert row[0] > 0.0
    await conn.close()


async def test_backtest_pnl_negative_for_wrong_prediction(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    # Predicted YES (high xgb_prob), but label=0 → outcome NO → pnl < 0
    df = _make_df([{"current_mid": 0.40, "spread": 0.02, "volume_24h": 500.0, "label": 0}])
    conn = await open_db(tmp_path / "bt.sqlite")
    settings = _settings(edge_threshold=0.04)

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=settings)

    cur = await conn.execute("SELECT pnl FROM trades LIMIT 1")
    row = await cur.fetchone()
    assert row[0] < 0.0
    await conn.close()


# ---------------------------------------------------------------------------
# Summary dict contents
# ---------------------------------------------------------------------------


async def test_backtest_returns_summary_dict(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    df = _make_df([
        {"current_mid": 0.40, "label": 1},
        {"current_mid": 0.40, "label": 0},
    ])
    conn = await open_db(tmp_path / "bt.sqlite")

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        result = await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=_settings())

    assert "trades_written" in result
    assert "win_count" in result
    assert "win_rate" in result
    assert "rows_skipped" in result
    await conn.close()


async def test_backtest_win_rate_reflects_outcomes(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    # 3 YES, 1 NO → win_rate = 0.75
    df = _make_df([
        {"current_mid": 0.40, "label": 1},
        {"current_mid": 0.40, "label": 1},
        {"current_mid": 0.40, "label": 1},
        {"current_mid": 0.40, "label": 0},
    ])
    conn = await open_db(tmp_path / "bt.sqlite")

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        result = await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=_settings())

    assert result["trades_written"] == 4
    assert result["win_count"] == 3
    assert result["win_rate"] == pytest.approx(0.75)
    await conn.close()


# ---------------------------------------------------------------------------
# Acceptance gate integration
# ---------------------------------------------------------------------------


async def test_backtest_writes_backtest_source_for_trades(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    df = _make_df([{"current_mid": 0.35, "label": 1}])
    conn = await open_db(tmp_path / "bt.sqlite")

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=_settings())

    cur = await conn.execute("SELECT source FROM trades LIMIT 1")
    row = await cur.fetchone()
    assert row[0] == "backtest"
    await conn.close()


async def test_backtest_acceptance_gate_passes_with_explicit_backtest_source(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    # 60 YES, 10 NO → win_rate=0.857, n=70 ≥ 50 → should pass acceptance
    rows = [{"current_mid": 0.35, "label": 1}] * 60 + [{"current_mid": 0.35, "label": 0}] * 10
    df = _make_df(rows)
    conn = await open_db(tmp_path / "bt.sqlite")

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        result = await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=_settings())

    assert result["trades_written"] == 70
    accepted, reason = await acceptance_criteria_met(conn, source="backtest")
    assert accepted, f"expected acceptance gate to pass: {reason}"
    await conn.close()


async def test_backtest_rows_do_not_satisfy_default_paper_live_gate(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    rows = [{"current_mid": 0.35, "label": 1}] * 60 + [{"current_mid": 0.35, "label": 0}] * 10
    df = _make_df(rows)
    conn = await open_db(tmp_path / "bt.sqlite")

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=_settings())

    accepted, reason = await acceptance_criteria_met(conn)
    assert not accepted
    assert "have 0" in reason
    await conn.close()


async def test_backtest_acceptance_gate_fails_with_too_few_trades(tmp_path):
    from unittest.mock import patch

    from backtest import run_backtest

    df = _make_df([{"current_mid": 0.35, "label": 1}] * 10)
    conn = await open_db(tmp_path / "bt.sqlite")

    with patch("backtest.xgb_infer", return_value=(0.55, "mock", {})):
        await run_backtest(conn, df, model_path=tmp_path / "nomodel.json", settings=_settings())

    accepted, reason = await acceptance_criteria_met(conn)
    assert not accepted
    assert "50" in reason
    await conn.close()
