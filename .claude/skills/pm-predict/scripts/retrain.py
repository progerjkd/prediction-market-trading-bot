"""Retrain automation with guardrails.

Usage:
    python retrain.py --data data/training_data.csv --model-out data/models/xgboost.json

Guardrails (all must pass before the model is deployed):
  - Row count >= min_rows (default 200)
  - Minority label class >= min_minority_ratio * total (default 0.20)
  - Holdout accuracy >= min_accuracy (default 0.80)

On success: writes model to model_path and a sidecar <model>.meta.json.
On failure: existing model is not overwritten.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from train_xgboost import LABEL_COL, train_from_dataframe


def check_guardrails(
    df,
    *,
    min_rows: int = 200,
    min_minority_ratio: float = 0.20,
) -> tuple[bool, str]:
    """Pre-train data quality checks. Returns (ok, reason)."""
    n = len(df)
    if n < min_rows:
        return False, f"only {n} rows; need >= {min_rows}"

    counts = df[LABEL_COL].value_counts()
    minority = int(counts.min())
    if minority / n < min_minority_ratio:
        ratio = minority / n
        return False, f"label imbalance: minority class is {ratio:.1%} (need >= {min_minority_ratio:.0%})"

    return True, ""


def retrain(
    df,
    *,
    model_path: Path | str,
    min_rows: int = 200,
    min_minority_ratio: float = 0.20,
    min_accuracy: float = 0.80,
    test_size: float = 0.20,
    random_state: int = 42,
) -> dict:
    """Train with guardrails. Returns {"ok": bool, "reason": str, "metrics": dict}."""
    model_path = Path(model_path)

    ok, reason = check_guardrails(df, min_rows=min_rows, min_minority_ratio=min_minority_ratio)
    if not ok:
        return {"ok": False, "reason": reason, "metrics": {}}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_model = Path(tmpdir) / "xgboost.json"
        metrics = train_from_dataframe(
            df,
            model_path=tmp_model,
            test_size=test_size,
            random_state=random_state,
        )

        if metrics["accuracy"] < min_accuracy:
            return {
                "ok": False,
                "reason": f"accuracy {metrics['accuracy']:.2%} below threshold {min_accuracy:.0%}",
                "metrics": metrics,
            }

        model_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_model, model_path)

    meta = {
        "accuracy": metrics["accuracy"],
        "n_train": metrics["n_train"],
        "n_test": metrics["n_test"],
        "n_rows": len(df),
        "model_path": str(model_path),
    }
    sidecar = model_path.with_suffix(".meta.json")
    sidecar.write_text(json.dumps(meta, indent=2))

    return {"ok": True, "reason": "", "metrics": metrics}


def retrain_needed(
    *,
    csv_path: Path | str,
    meta_path: Path | str,
    min_new_rows: int = 500,
) -> bool:
    """Return True if the CSV has grown by at least min_new_rows since the last training run."""
    csv_path = Path(csv_path)
    meta_path = Path(meta_path)

    if not csv_path.exists():
        return False

    import pandas as pd
    try:
        current_rows = len(pd.read_csv(csv_path))
    except Exception:
        return False

    if not meta_path.exists():
        return current_rows >= min_new_rows

    try:
        meta = json.loads(meta_path.read_text())
        prev_rows = int(meta.get("n_rows", 0))
    except Exception:
        return current_rows >= min_new_rows

    return (current_rows - prev_rows) >= min_new_rows


def main() -> None:
    import argparse

    import pandas as pd

    parser = argparse.ArgumentParser(description="Retrain XGBoost with deployment guardrails")
    parser.add_argument("--data", default="data/training_data.csv")
    parser.add_argument("--model-out", default="data/models/xgboost.json")
    parser.add_argument("--min-rows", type=int, default=200)
    parser.add_argument("--min-accuracy", type=float, default=0.80)
    parser.add_argument("--min-minority-ratio", type=float, default=0.20)
    args = parser.parse_args()

    df = pd.read_csv(args.data)
    print(f"loaded {len(df)} rows from {args.data}")

    result = retrain(
        df,
        model_path=args.model_out,
        min_rows=args.min_rows,
        min_accuracy=args.min_accuracy,
        min_minority_ratio=args.min_minority_ratio,
    )

    if result["ok"]:
        m = result["metrics"]
        print(f"[OK] model deployed → {args.model_out}")
        print(f"     accuracy={m['accuracy']:.3f}  n_train={m['n_train']}  n_test={m['n_test']}")
    else:
        print(f"[SKIP] model NOT deployed: {result['reason']}")
        if result["metrics"]:
            print(f"       metrics: {result['metrics']}")


if __name__ == "__main__":
    main()
