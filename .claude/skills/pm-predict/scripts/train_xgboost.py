"""Train XGBClassifier on resolved Polymarket market data.

Reads data/training_data.csv (produced by fetch_resolved_markets.py) and saves
a trained model to data/models/xgboost.json.  The feature order must match the
ordered list in infer_xgboost.py.
"""
from __future__ import annotations

import json
from pathlib import Path

FEATURE_COLS = [
    "current_mid",
    "spread",
    "volume_24h",
    "days_to_resolution",
    "narrative_score",
    "momentum_1h",
    "momentum_24h",
]
LABEL_COL = "label"


def train_from_dataframe(
    df,
    *,
    model_path: Path | str = "data/models/xgboost.json",
    test_size: float = 0.20,
    random_state: int = 42,
) -> dict:
    """Train an XGBClassifier and save to model_path.  Returns basic metrics."""
    import numpy as np
    import xgboost as xgb
    from sklearn.metrics import accuracy_score
    from sklearn.model_selection import train_test_split

    df = df.dropna(subset=FEATURE_COLS + [LABEL_COL])
    X = df[FEATURE_COLS].values.astype(np.float32)
    y = df[LABEL_COL].values.astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=y
    )

    model = xgb.XGBClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="logloss",
        random_state=random_state,
        verbosity=0,
    )
    model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

    y_pred = model.predict(X_test)
    accuracy = float(accuracy_score(y_test, y_pred))

    out = Path(model_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(str(out))

    return {
        "n_train": len(X_train),
        "n_test": len(X_test),
        "accuracy": accuracy,
        "model_path": str(out),
    }


def main() -> None:
    import argparse

    import pandas as pd

    parser = argparse.ArgumentParser(description="Train XGBoost on resolved Polymarket data")
    parser.add_argument("--data", default="data/training_data.csv")
    parser.add_argument("--model-out", default="data/models/xgboost.json")
    parser.add_argument("--test-size", type=float, default=0.20)
    args = parser.parse_args()

    df = pd.read_csv(args.data)
    print(f"loaded {len(df)} rows from {args.data}")
    print(f"label distribution:\n{df[LABEL_COL].value_counts()}")

    metrics = train_from_dataframe(df, model_path=args.model_out, test_size=args.test_size)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
