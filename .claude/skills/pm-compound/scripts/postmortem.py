"""Deterministic fallback postmortem classification."""
from __future__ import annotations

from pathlib import Path


def classify_trade(pnl: float | None, slippage: float | None = None) -> tuple[str, str]:
    if pnl is not None and pnl >= 0:
        return "bad-prediction", "No rule change; winning trade."
    if slippage is not None and slippage > 0.03:
        return "bad-execution", "Reject paper trades with simulated slippage above 3 cents."
    return "bad-prediction", "Require a stronger cross-source narrative before similar trades."


def append_to_failure_log(
    *,
    log_path: Path,
    condition_id: str,
    trade_id: int,
    outcome: str,
    pnl: float,
    cause: str,
    rule_proposed: str,
) -> None:
    """Append one postmortem entry to the failure log markdown file."""
    entry = (
        f"\n## trade_id={trade_id} | {condition_id}\n"
        f"- outcome: {outcome}\n"
        f"- pnl: {pnl:.2f}\n"
        f"- cause: {cause}\n"
        f"- rule: {rule_proposed}\n"
    )
    with open(log_path, "a") as f:
        f.write(entry)
