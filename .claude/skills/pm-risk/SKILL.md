---
name: pm-risk
description: Validate risk, size positions with quarter-Kelly, and allow paper execution only after every rule passes.
---

# PM Risk

Use this skill before any paper execution.

All rules must pass:
- Edge must be greater than 0.04.
- Size must be at or below quarter-Kelly.
- Position size must be at or below 5% of bankroll.
- Total exposure after the trade must be within the configured cap.
- Open positions must be below 15.
- Daily loss must be below 15% of bankroll.
- Drawdown must be below 8%.
- Daily API cost must be below $50.
- `data/STOP` must not exist.

Scripts:
- `scripts/validate_risk.py`
- `scripts/kelly_size.py`

References:
- `references/limits.md`
