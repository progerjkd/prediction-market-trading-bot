"""XGBoost inference adapter with a safe missing-model fallback."""
from __future__ import annotations

from pathlib import Path

try:
    import xgboost as xgb
    _XGB_AVAILABLE = True
except ImportError:
    xgb = None  # type: ignore[assignment]
    _XGB_AVAILABLE = False

FEATURE_NAMES = [
    "current_mid", "spread", "volume_24h", "days_to_resolution",
    "narrative_score", "momentum_1h", "momentum_24h",
]


def infer_probability(
    features: dict[str, float],
    model_path: Path | str = "data/models/xgboost.json",
) -> tuple[float, str, dict[str, float]]:
    """Return (probability, source_tag, feature_importances).

    feature_importances maps feature name → gain importance; empty dict when
    the model is unavailable.
    """
    path = Path(model_path)
    if not path.exists():
        current_mid = float(features.get("current_mid", 0.5))
        narrative_score = float(features.get("narrative_score", 0.0))
        fallback = min(0.95, max(0.05, current_mid + (0.05 * narrative_score)))
        return fallback, "xgboost_model_missing", {}

    if not _XGB_AVAILABLE:
        current_mid = float(features.get("current_mid", 0.5))
        return current_mid, "xgboost_not_installed", {}

    model = xgb.XGBClassifier()
    model.load_model(path)
    ordered = [features.get(name, 0.0) for name in FEATURE_NAMES]
    proba = model.predict_proba([ordered])[0][1]
    importances = dict(zip(FEATURE_NAMES, model.feature_importances_, strict=False))
    return float(proba), "xgboost_model", importances
