"""Tests for XGBoost training pipeline and trained-model inference."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from infer_xgboost import infer_probability
from train_xgboost import train_from_dataframe

FEATURE_COLS = [
    "current_mid",
    "spread",
    "volume_24h",
    "days_to_resolution",
    "narrative_score",
    "momentum_1h",
    "momentum_24h",
]


def _synthetic_df(n: int = 300, seed: int = 42) -> pd.DataFrame:
    """Random dataset where label is probabilistically correlated with current_mid."""
    rng = np.random.default_rng(seed)
    mid = rng.uniform(0.05, 0.95, n)
    label = (rng.uniform(0, 1, n) < mid).astype(int)
    return pd.DataFrame(
        {
            "current_mid": mid,
            "spread": rng.uniform(0.001, 0.05, n),
            "volume_24h": rng.uniform(0, 10_000, n),
            "days_to_resolution": np.ones(n),
            "narrative_score": np.zeros(n),
            "momentum_1h": np.zeros(n),
            "momentum_24h": np.zeros(n),
            "label": label,
        }
    )


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------


def test_train_from_dataframe_creates_model_file(tmp_path):
    df = _synthetic_df()
    model_path = tmp_path / "xgboost.json"
    train_from_dataframe(df, model_path=model_path)
    assert model_path.exists(), "model file should be written after training"


def test_train_from_dataframe_returns_metrics(tmp_path):
    df = _synthetic_df()
    metrics = train_from_dataframe(df, model_path=tmp_path / "xgboost.json")
    assert "n_train" in metrics
    assert "n_test" in metrics
    assert "accuracy" in metrics
    assert 0.0 <= metrics["accuracy"] <= 1.0


def test_train_from_dataframe_splits_data(tmp_path):
    df = _synthetic_df(n=200)
    metrics = train_from_dataframe(df, model_path=tmp_path / "xgboost.json", test_size=0.2)
    assert metrics["n_train"] == pytest.approx(160, abs=5)
    assert metrics["n_test"] == pytest.approx(40, abs=5)


# ---------------------------------------------------------------------------
# Inference with trained model
# ---------------------------------------------------------------------------


def test_trained_model_returns_valid_probability(tmp_path):
    df = _synthetic_df()
    model_path = tmp_path / "xgboost.json"
    train_from_dataframe(df, model_path=model_path)

    prob, source, _ = infer_probability(
        {
            "current_mid": 0.7,
            "spread": 0.02,
            "volume_24h": 500,
            "days_to_resolution": 1,
            "narrative_score": 0,
            "momentum_1h": 0,
            "momentum_24h": 0,
        },
        model_path=model_path,
    )
    assert 0.05 <= prob <= 0.95
    assert source == "xgboost_model"


def test_trained_model_is_monotone_in_current_mid(tmp_path):
    """Higher current_mid should produce higher predicted probability."""
    df = _synthetic_df(n=500)
    model_path = tmp_path / "xgboost.json"
    train_from_dataframe(df, model_path=model_path)

    base = {
        "spread": 0.02,
        "volume_24h": 1000,
        "days_to_resolution": 1,
        "narrative_score": 0,
        "momentum_1h": 0,
        "momentum_24h": 0,
    }
    prob_low, *_ = infer_probability({**base, "current_mid": 0.2}, model_path=model_path)
    prob_mid, *_ = infer_probability({**base, "current_mid": 0.5}, model_path=model_path)
    prob_high, *_ = infer_probability({**base, "current_mid": 0.8}, model_path=model_path)

    assert prob_low < prob_mid < prob_high


# ---------------------------------------------------------------------------
# Fallback when model is missing
# ---------------------------------------------------------------------------


def test_infer_probability_falls_back_when_model_missing(tmp_path):
    prob, source, _ = infer_probability(
        {"current_mid": 0.6, "spread": 0.02, "volume_24h": 0,
         "days_to_resolution": 1, "narrative_score": 0,
         "momentum_1h": 0, "momentum_24h": 0},
        model_path=tmp_path / "nonexistent.json",
    )
    assert 0.05 <= prob <= 0.95
    assert source == "xgboost_model_missing"
