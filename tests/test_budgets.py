"""Tests for budget and kill-switch guardrails."""
from __future__ import annotations

from bot.budgets import BudgetLimits, RuntimeBudgetSnapshot, halt_reason


def test_halt_reason_detects_stop_file(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("halt")
    limits = BudgetLimits(stop_file=stop)
    snapshot = RuntimeBudgetSnapshot()

    assert halt_reason(snapshot, limits) == "kill-switch (STOP file) present"


def test_halt_reason_detects_daily_loss_limit(tmp_path):
    limits = BudgetLimits(stop_file=tmp_path / "missing", bankroll_usdc=1000, daily_loss_pct=0.15)
    snapshot = RuntimeBudgetSnapshot(daily_loss_usd=150)

    assert halt_reason(snapshot, limits) == "daily loss 150.00 at/above limit 150.00"


def test_halt_reason_detects_api_cost_limit(tmp_path):
    limits = BudgetLimits(stop_file=tmp_path / "missing", daily_api_cost_limit=50)
    snapshot = RuntimeBudgetSnapshot(daily_api_cost_usd=50)

    assert halt_reason(snapshot, limits) == "daily API cost 50.00 at/above limit 50.00"


def test_halt_reason_returns_none_when_limits_clear(tmp_path):
    limits = BudgetLimits(stop_file=tmp_path / "missing")
    snapshot = RuntimeBudgetSnapshot(daily_loss_usd=0, drawdown_pct=0.01, daily_api_cost_usd=0)

    assert halt_reason(snapshot, limits) is None
