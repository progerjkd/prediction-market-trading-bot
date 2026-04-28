"""Tests for --check-retrain daemon mode — TDD RED phase."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# retrain_needed() helper
# ---------------------------------------------------------------------------


def test_retrain_needed_true_when_no_meta_and_csv_has_rows(tmp_path):
    from retrain import retrain_needed

    csv = tmp_path / "data.csv"
    csv.write_text("current_mid,spread,volume_24h,days_to_resolution,narrative_score,momentum_1h,momentum_24h,label\n0.5,0.01,1000,5,0,0,0,1\n" * 600)

    assert retrain_needed(csv_path=csv, meta_path=tmp_path / "xgboost.meta.json", min_new_rows=500) is True


def test_retrain_needed_false_when_not_enough_new_rows(tmp_path):
    from retrain import retrain_needed

    csv = tmp_path / "data.csv"
    # 300 rows total
    csv.write_text("current_mid,spread,volume_24h,days_to_resolution,narrative_score,momentum_1h,momentum_24h,label\n" + "0.5,0.01,1000,5,0,0,0,1\n" * 300)

    meta = tmp_path / "xgboost.meta.json"
    meta.write_text(json.dumps({"n_rows": 200, "accuracy": 0.85}))

    # 300 - 200 = 100 new rows, below min_new_rows=500
    assert retrain_needed(csv_path=csv, meta_path=meta, min_new_rows=500) is False


def test_retrain_needed_true_when_500_plus_new_rows(tmp_path):
    from retrain import retrain_needed

    csv = tmp_path / "data.csv"
    csv.write_text("current_mid,spread,volume_24h,days_to_resolution,narrative_score,momentum_1h,momentum_24h,label\n" + "0.5,0.01,1000,5,0,0,0,1\n" * 800)

    meta = tmp_path / "xgboost.meta.json"
    meta.write_text(json.dumps({"n_rows": 200, "accuracy": 0.85}))

    # 800 - 200 = 600 new rows ≥ 500
    assert retrain_needed(csv_path=csv, meta_path=meta, min_new_rows=500) is True


def test_retrain_needed_false_when_csv_missing(tmp_path):
    from retrain import retrain_needed

    assert retrain_needed(
        csv_path=tmp_path / "missing.csv",
        meta_path=tmp_path / "xgboost.meta.json",
        min_new_rows=500,
    ) is False


def test_retrain_needed_true_when_meta_missing_but_csv_large(tmp_path):
    from retrain import retrain_needed

    csv = tmp_path / "data.csv"
    csv.write_text("current_mid,spread,volume_24h,days_to_resolution,narrative_score,momentum_1h,momentum_24h,label\n" + "0.5,0.01,1000,5,0,0,0,1\n" * 600)

    assert retrain_needed(
        csv_path=csv,
        meta_path=tmp_path / "xgboost.meta.json",  # doesn't exist
        min_new_rows=500,
    ) is True


# ---------------------------------------------------------------------------
# --check-retrain CLI mode
# ---------------------------------------------------------------------------


async def test_check_retrain_exits_zero(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    with patch("bot.daemon.retrain_needed", return_value=False):
        code = await async_main(["--check-retrain"])

    assert code == 0


async def test_check_retrain_prints_skip_when_not_needed(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    with patch("bot.daemon.retrain_needed", return_value=False):
        await async_main(["--check-retrain"])

    out = capsys.readouterr().out
    assert "skip" in out.lower() or "not needed" in out.lower() or "sufficient" in out.lower()


async def test_check_retrain_runs_retrain_when_needed(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    with (
        patch("bot.daemon.retrain_needed", return_value=True),
        patch("bot.daemon.run_retrain_pipeline") as mock_pipeline,
    ):
        mock_pipeline.return_value = {"ok": True, "reason": "", "metrics": {"accuracy": 0.85}}
        await async_main(["--check-retrain"])

    mock_pipeline.assert_called_once()


async def test_check_retrain_prints_result_on_success(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    with (
        patch("bot.daemon.retrain_needed", return_value=True),
        patch("bot.daemon.run_retrain_pipeline") as mock_pipeline,
    ):
        mock_pipeline.return_value = {"ok": True, "reason": "", "metrics": {"accuracy": 0.85}}
        await async_main(["--check-retrain"])

    out = capsys.readouterr().out
    assert "0.85" in out or "deployed" in out.lower() or "ok" in out.lower()


async def test_check_retrain_prints_failure_reason(tmp_path, monkeypatch, capsys):
    from bot.daemon import async_main

    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot.sqlite"))
    monkeypatch.setenv("STOP_FILE", str(tmp_path / "STOP"))

    with (
        patch("bot.daemon.retrain_needed", return_value=True),
        patch("bot.daemon.run_retrain_pipeline") as mock_pipeline,
    ):
        mock_pipeline.return_value = {"ok": False, "reason": "accuracy 72.00% below threshold 80%", "metrics": {}}
        await async_main(["--check-retrain"])

    out = capsys.readouterr().out
    assert "72" in out or "accuracy" in out.lower() or "skip" in out.lower()
