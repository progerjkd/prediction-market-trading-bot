"""Performance metrics for trade history and predictions.

All metrics are pure functions over plain Python lists — no SQL, no I/O.
"""
from __future__ import annotations

import math
from collections.abc import Sequence


def brier_score(predicted: Sequence[float], actual: Sequence[int]) -> float:
    """Mean squared error of probabilistic predictions vs binary outcomes.

    BS = (1/n) Σ (predicted_i - actual_i)^2

    Lower is better; 0 = perfect, 1 = worst possible. Reference 0.25 = always-50%.
    """
    if len(predicted) != len(actual):
        raise ValueError(f"length mismatch: {len(predicted)} vs {len(actual)}")
    if not predicted:
        return 0.0
    return sum((p - a) ** 2 for p, a in zip(predicted, actual, strict=True)) / len(predicted)


def win_rate(pnls: Sequence[float]) -> float:
    """Fraction of pnls > 0, excluding ties (pnl == 0)."""
    decided = [pnl for pnl in pnls if pnl != 0]
    if not decided:
        return 0.0
    wins = sum(1 for pnl in decided if pnl > 0)
    return wins / len(decided)


def sharpe_ratio(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio (assumes 0% risk-free rate).

    Returns 0 when fewer than 2 samples or zero variance.
    """
    n = len(returns)
    if n < 2:
        return 0.0
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def max_drawdown(equity: Sequence[float]) -> float:
    """Maximum peak-to-trough drop, expressed as a fraction of the running peak."""
    if not equity:
        return 0.0
    peak = equity[0]
    worst = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (peak - v) / peak
            if dd > worst:
                worst = dd
    return worst


def profit_factor(pnls: Sequence[float]) -> float:
    """gross_win / gross_loss. Inf if no losses; 0 if no wins."""
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0 and gross_win == 0:
        return 0.0
    if gross_loss == 0:
        return float("inf")
    return gross_win / gross_loss
