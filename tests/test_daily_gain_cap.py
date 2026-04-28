"""Daily gain cap — halt new trades once day's PnL exceeds daily_gain_pct * bankroll.

This is the profit-taking mirror of daily_loss_pct: once we've made enough
for the day, stop taking new positions to lock in gains.  The halt_reason
returned is 'daily_gain_cap' and the daemon skips the prediction loop.
"""
from __future__ import annotations

from bot.budgets import BudgetLimits, RuntimeBudgetSnapshot, halt_reason


def _snapshot(daily_pnl_usd: float) -> RuntimeBudgetSnapshot:
    return RuntimeBudgetSnapshot(
        daily_loss_usd=-daily_pnl_usd,  # pnl stored as negative loss
        drawdown_pct=0.0,
        daily_api_cost_usd=0.0,
        daily_gain_usd=daily_pnl_usd,
    )


def _limits(gain_pct: float = 0.20, bankroll: float = 10_000.0) -> BudgetLimits:
    from pathlib import Path
    return BudgetLimits(
        stop_file=Path("/nonexistent/STOP"),
        bankroll_usdc=bankroll,
        daily_loss_pct=0.15,
        max_drawdown_pct=0.08,
        daily_api_cost_limit=50.0,
        daily_gain_pct=gain_pct,
    )


def test_halt_when_gain_exceeds_cap():
    """halt_reason returns 'daily_gain_cap' when daily PnL >= daily_gain_pct * bankroll."""
    snapshot = _snapshot(daily_pnl_usd=2_100.0)  # 21% of 10k — over 20% cap
    reason = halt_reason(snapshot, _limits(gain_pct=0.20))
    assert reason is not None
    assert "gain" in reason.lower()


def test_no_halt_when_gain_below_cap():
    """halt_reason returns None when daily PnL is below the gain cap."""
    snapshot = _snapshot(daily_pnl_usd=1_500.0)  # 15% of 10k — under 20% cap
    reason = halt_reason(snapshot, _limits(gain_pct=0.20))
    assert reason is None


def test_halt_at_exact_cap():
    """halt_reason triggers when gain equals the cap exactly."""
    snapshot = _snapshot(daily_pnl_usd=2_000.0)  # exactly 20% of 10k
    reason = halt_reason(snapshot, _limits(gain_pct=0.20))
    assert reason is not None


def test_gain_cap_env_override(monkeypatch):
    """DAILY_GAIN_PCT env var is wired into load_settings."""
    from bot.config import load_settings
    monkeypatch.setenv("DAILY_GAIN_PCT", "0.25")
    s = load_settings()
    assert s.daily_gain_pct == 0.25
