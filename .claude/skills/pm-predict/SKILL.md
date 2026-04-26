---
name: pm-predict
description: Estimate Polymarket probabilities using XGBoost plus Claude and emit trade signals only when edge clears threshold.
---

# PM Predict

Use this skill to turn market metadata and research briefs into calibrated probabilities.

Defaults:
- XGBoost weight: 0.60
- Claude weight: 0.40
- Edge threshold: 0.04
- Persist every prediction, including those below threshold.

Rules:
- `edge = p_model - p_market`.
- Emit a BUY signal only when `edge > edge_threshold`.
- If the XGBoost model is missing, record that component as unavailable and use a neutral fallback rather than blocking paper-mode smoke tests.
- Track Brier score after resolution.

Scripts:
- `scripts/ensemble.py`
- `scripts/infer_xgboost.py`
- `scripts/claude_forecaster.py`
- `scripts/train_xgboost.py`

References:
- `references/formulas.md`
