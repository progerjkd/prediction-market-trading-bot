"""XGBoost inference adapter with a safe missing-model fallback."""
from __future__ import annotations

from pathlib import Path


def infer_probability(features: dict[str, float], model_path: Path | str = "data/models/xgboost.json") -> tuple[float, str]:
    path = Path(model_path)
    if not path.exists():
        current_mid = float(features.get("current_mid", 0.5))
        narrative_score = float(features.get("narrative_score", 0.0))
        fallback = min(0.95, max(0.05, current_mid + (0.05 * narrative_score)))
        return fallback, "xgboost_model_missing"

    try:
        import xgboost as xgb
    except ImportError:
        current_mid = float(features.get("current_mid", 0.5))
        return current_mid, "xgboost_not_installed"

    model = xgb.XGBClassifier()
    model.load_model(path)
    ordered = [
        features.get("current_mid", 0.5),
        features.get("spread", 0.0),
        features.get("volume_24h", 0.0),
        features.get("days_to_resolution", 0.0),
        features.get("narrative_score", 0.0),
        features.get("momentum_1h", 0.0),
        features.get("momentum_24h", 0.0),
    ]
    proba = model.predict_proba([ordered])[0][1]
    return float(proba), "xgboost_model"
