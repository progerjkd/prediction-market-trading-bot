"""Retrain automation guardrails — TDD RED phase."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from retrain import check_guardrails, retrain

FEATURE_COLS = [
    "current_mid", "spread", "volume_24h", "days_to_resolution",
    "narrative_score", "momentum_1h", "momentum_24h",
]


def _make_df(n: int, yes_fraction: float = 0.5) -> pd.DataFrame:
    n_yes = int(n * yes_fraction)
    n_no = n - n_yes
    rows = []
    for label in [1] * n_yes + [0] * n_no:
        rows.append({col: 0.5 for col in FEATURE_COLS} | {"label": label})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# check_guardrails
# ---------------------------------------------------------------------------


def test_guardrails_pass_with_sufficient_balanced_data():
    df = _make_df(300, yes_fraction=0.55)
    ok, reason = check_guardrails(df, min_rows=200, min_minority_ratio=0.20)
    assert ok is True
    assert reason == ""


def test_guardrails_fail_below_min_rows():
    df = _make_df(100, yes_fraction=0.5)
    ok, reason = check_guardrails(df, min_rows=200, min_minority_ratio=0.20)
    assert ok is False
    assert "100" in reason or "200" in reason


def test_guardrails_fail_with_imbalanced_labels():
    df = _make_df(300, yes_fraction=0.92)
    ok, reason = check_guardrails(df, min_rows=200, min_minority_ratio=0.20)
    assert ok is False
    assert "imbalance" in reason.lower() or "balance" in reason.lower() or "minority" in reason.lower()


def test_guardrails_pass_at_exact_min_rows():
    df = _make_df(200, yes_fraction=0.5)
    ok, _ = check_guardrails(df, min_rows=200, min_minority_ratio=0.20)
    assert ok is True


def test_guardrails_pass_at_exact_minority_boundary():
    df = _make_df(300, yes_fraction=0.80)  # 20% minority — exactly at boundary
    ok, _ = check_guardrails(df, min_rows=200, min_minority_ratio=0.20)
    assert ok is True


# ---------------------------------------------------------------------------
# retrain — full pipeline with mocked train_from_dataframe
# ---------------------------------------------------------------------------


def _mock_train(accuracy: float, tmp_path: Path):
    """Return a side_effect for train_from_dataframe that writes a stub model file."""
    def _side_effect(df, *, model_path, **kwargs):
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        Path(model_path).write_text("stub-model")
        return {"n_train": 240, "n_test": 60, "accuracy": accuracy, "model_path": str(model_path)}
    return _side_effect


def test_retrain_deploys_model_when_all_pass(tmp_path):
    df = _make_df(300)
    model_path = tmp_path / "xgboost.json"

    with patch("retrain.train_from_dataframe", side_effect=_mock_train(0.82, tmp_path)):
        result = retrain(df, model_path=model_path, min_rows=200, min_accuracy=0.80)

    assert result["ok"] is True
    assert result["reason"] == ""
    assert model_path.exists()


def test_retrain_does_not_deploy_on_low_accuracy(tmp_path):
    df = _make_df(300)
    model_path = tmp_path / "xgboost.json"

    with patch("retrain.train_from_dataframe", side_effect=_mock_train(0.72, tmp_path)):
        result = retrain(df, model_path=model_path, min_rows=200, min_accuracy=0.80)

    assert result["ok"] is False
    assert "accuracy" in result["reason"].lower() or "0.72" in result["reason"] or "72" in result["reason"]
    assert not model_path.exists()


def test_retrain_does_not_train_on_guardrail_failure(tmp_path):
    df = _make_df(50)  # below min_rows
    model_path = tmp_path / "xgboost.json"

    with patch("retrain.train_from_dataframe") as mock_train:
        result = retrain(df, model_path=model_path, min_rows=200, min_accuracy=0.80)

    assert result["ok"] is False
    mock_train.assert_not_called()
    assert not model_path.exists()


def test_retrain_returns_metrics_on_success(tmp_path):
    df = _make_df(300)
    model_path = tmp_path / "xgboost.json"

    with patch("retrain.train_from_dataframe", side_effect=_mock_train(0.85, tmp_path)):
        result = retrain(df, model_path=model_path, min_rows=200, min_accuracy=0.80)

    assert "accuracy" in result["metrics"]
    assert result["metrics"]["accuracy"] == pytest.approx(0.85)


def test_retrain_returns_metrics_even_on_accuracy_failure(tmp_path):
    df = _make_df(300)
    model_path = tmp_path / "xgboost.json"

    with patch("retrain.train_from_dataframe", side_effect=_mock_train(0.70, tmp_path)):
        result = retrain(df, model_path=model_path, min_rows=200, min_accuracy=0.80)

    assert result["ok"] is False
    assert result["metrics"].get("accuracy") == pytest.approx(0.70)


def test_retrain_writes_sidecar_json_on_success(tmp_path):
    df = _make_df(300)
    model_path = tmp_path / "xgboost.json"

    with patch("retrain.train_from_dataframe", side_effect=_mock_train(0.82, tmp_path)):
        retrain(df, model_path=model_path, min_rows=200, min_accuracy=0.80)

    sidecar = tmp_path / "xgboost.meta.json"
    assert sidecar.exists()
    import json
    meta = json.loads(sidecar.read_text())
    assert "accuracy" in meta
    assert "n_rows" in meta
