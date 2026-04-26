"""Deterministic fallback postmortem classification."""
from __future__ import annotations


def classify_trade(pnl: float | None, slippage: float | None = None) -> tuple[str, str]:
    if pnl is not None and pnl >= 0:
        return "bad-prediction", "No rule change; winning trade."
    if slippage is not None and slippage > 0.03:
        return "bad-execution", "Reject paper trades with simulated slippage above 3 cents."
    return "bad-prediction", "Require a stronger cross-source narrative before similar trades."
