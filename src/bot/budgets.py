"""Runtime guardrails for paper-trading loops."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BudgetLimits:
    stop_file: Path = Path("data/STOP")
    bankroll_usdc: float = 10_000.0
    daily_loss_pct: float = 0.15
    max_drawdown_pct: float = 0.08
    daily_api_cost_limit: float = 50.0
    daily_gain_pct: float = 1.0  # default: no cap (100% gain would halt)


@dataclass(frozen=True)
class RuntimeBudgetSnapshot:
    daily_loss_usd: float = 0.0
    drawdown_pct: float = 0.0
    daily_api_cost_usd: float = 0.0
    daily_gain_usd: float = 0.0


def halt_reason(snapshot: RuntimeBudgetSnapshot, limits: BudgetLimits) -> str | None:
    if limits.stop_file.exists():
        return "kill-switch (STOP file) present"

    daily_loss_cap = limits.daily_loss_pct * limits.bankroll_usdc
    if snapshot.daily_loss_usd >= daily_loss_cap:
        return f"daily loss {snapshot.daily_loss_usd:.2f} at/above limit {daily_loss_cap:.2f}"

    if snapshot.drawdown_pct >= limits.max_drawdown_pct:
        return f"drawdown {snapshot.drawdown_pct:.4f} at/above limit {limits.max_drawdown_pct}"

    if snapshot.daily_api_cost_usd >= limits.daily_api_cost_limit:
        return (
            f"daily API cost {snapshot.daily_api_cost_usd:.2f} "
            f"at/above limit {limits.daily_api_cost_limit:.2f}"
        )

    daily_gain_cap = limits.daily_gain_pct * limits.bankroll_usdc
    if snapshot.daily_gain_usd >= daily_gain_cap:
        return f"daily gain cap reached: {snapshot.daily_gain_usd:.2f} >= {daily_gain_cap:.2f}"

    return None
