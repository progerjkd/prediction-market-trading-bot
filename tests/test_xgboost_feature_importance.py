"""XGBoost feature importance — infer_probability returns top feature contributions.

When a real model is available, infer_probability should return an extended
result with the top-3 feature importances so the prediction pipeline can log
what drove the signal.  The fallback paths (model missing, xgb not installed)
are unchanged and return an empty importance dict.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

FEATURES = {
    "current_mid": 0.55,
    "spread": 0.02,
    "volume_24h": 10_000.0,
    "days_to_resolution": 14.0,
    "narrative_score": 0.3,
    "momentum_1h": 0.01,
    "momentum_24h": 0.05,
}


def test_fallback_returns_empty_importances_when_model_missing(tmp_path):
    """When model file doesn't exist, importance dict is empty."""
    from infer_xgboost import infer_probability
    prob, source, importances = infer_probability(FEATURES, model_path=tmp_path / "nonexistent.json")
    assert source == "xgboost_model_missing"
    assert importances == {}


def test_infer_returns_three_values():
    """infer_probability always returns a 3-tuple (prob, source, importances)."""
    from infer_xgboost import infer_probability
    result = infer_probability(FEATURES, model_path=Path("/nonexistent/model.json"))
    assert len(result) == 3


def test_real_model_returns_importances(tmp_path):
    """When XGBoost model is present, importances is a non-empty dict."""
    from infer_xgboost import infer_probability

    mock_model = MagicMock()
    mock_model.predict_proba.return_value = [[0.3, 0.7]]
    mock_model.feature_importances_ = [0.40, 0.25, 0.15, 0.08, 0.06, 0.04, 0.02]

    fake_path = tmp_path / "model.json"
    fake_path.touch()

    with patch("infer_xgboost.xgb") as mock_xgb:
        mock_xgb.XGBClassifier.return_value = mock_model
        prob, source, importances = infer_probability(FEATURES, model_path=fake_path)

    assert source == "xgboost_model"
    assert isinstance(importances, dict)
    assert len(importances) > 0
    # Top feature by importance should be current_mid (importance=0.40)
    top_feature = max(importances, key=importances.__getitem__)
    assert top_feature == "current_mid"
