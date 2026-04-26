"""Deterministic risk-rule engine for trade signals.

All rules must pass for a trade to be approved. Returns the FIRST rule that fails.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kelly_size import kelly_size


@dataclass(frozen=True)
class RiskLimits:
    edge_threshold: float = 0.04
    kelly_fraction: float = 0.25
    max_position_pct: float = 0.05
    max_exposure_pct: float = 0.50
    max_open_positions: int = 15
    daily_loss_pct: float = 0.15
    max_drawdown_pct: float = 0.08
    daily_api_cost_usd: float = 50.0


@dataclass(frozen=True)
class RiskInputs:
    p_model: float
    p_market: float
    b: float
    size_usd: float
    bankroll_usd: float
    open_positions: int
    total_exposure_usd: float
    daily_loss_usd: float
    drawdown_pct: float
    daily_api_cost_usd: float
    stop_file: Path = field(default_factory=lambda: Path("data/STOP"))


@dataclass(frozen=True)
class RiskCheck:
    ok: bool
    reason: str


def validate_risk(inputs: RiskInputs, limits: RiskLimits) -> RiskCheck:
    """Run every rule. Return first failure, or ok=True if all pass."""
    # Kill switch first — it short-circuits everything else.
    if inputs.stop_file.exists():
        return RiskCheck(False, "kill-switch (STOP file) present")

    edge = inputs.p_model - inputs.p_market
    if edge <= limits.edge_threshold:
        return RiskCheck(False, f"edge {edge:.4f} below threshold {limits.edge_threshold}")

    kelly_cap = kelly_size(
        p=inputs.p_model,
        b=inputs.b,
        bankroll=inputs.bankroll_usd,
        fraction=limits.kelly_fraction,
    )
    if inputs.size_usd > kelly_cap + 1e-9:
        return RiskCheck(False, f"size {inputs.size_usd:.2f} exceeds quarter-Kelly cap {kelly_cap:.2f}")

    pos_cap = limits.max_position_pct * inputs.bankroll_usd
    if inputs.size_usd > pos_cap + 1e-9:
        return RiskCheck(
            False,
            f"position size {inputs.size_usd:.2f} exceeds 5% bankroll cap {pos_cap:.2f}",
        )

    exposure_cap = limits.max_exposure_pct * inputs.bankroll_usd
    if inputs.total_exposure_usd + inputs.size_usd > exposure_cap + 1e-9:
        return RiskCheck(
            False,
            f"total exposure {inputs.total_exposure_usd + inputs.size_usd:.2f} exceeds cap {exposure_cap:.2f}",
        )

    if inputs.open_positions >= limits.max_open_positions:
        return RiskCheck(
            False,
            f"open positions {inputs.open_positions} at/above limit {limits.max_open_positions}",
        )

    daily_loss_cap = limits.daily_loss_pct * inputs.bankroll_usd
    if inputs.daily_loss_usd >= daily_loss_cap:
        return RiskCheck(
            False,
            f"daily loss {inputs.daily_loss_usd:.2f} at/above limit {daily_loss_cap:.2f}",
        )

    if inputs.drawdown_pct >= limits.max_drawdown_pct:
        return RiskCheck(
            False,
            f"drawdown {inputs.drawdown_pct:.4f} at/above limit {limits.max_drawdown_pct}",
        )

    if inputs.daily_api_cost_usd >= limits.daily_api_cost_usd:
        return RiskCheck(
            False,
            f"daily API cost {inputs.daily_api_cost_usd:.2f} at/above limit {limits.daily_api_cost_usd}",
        )

    return RiskCheck(True, "all rules passed")
