"""Tests for metrics — Brier, win rate, Sharpe, drawdown, profit factor."""
from __future__ import annotations

import math

import pytest

from bot.metrics import (
    brier_score,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)


class TestBrierScore:
    def test_perfect_predictions_zero(self):
        # Predicted exactly the outcome each time
        assert brier_score(predicted=[1.0, 0.0, 1.0], actual=[1, 0, 1]) == 0.0

    def test_worst_predictions_one(self):
        assert brier_score(predicted=[0.0, 1.0, 0.0], actual=[1, 0, 1]) == 1.0

    def test_uniform_50pct_gives_quarter(self):
        # (0.5 - x)^2 = 0.25 for each, mean = 0.25
        assert math.isclose(brier_score(predicted=[0.5, 0.5, 0.5], actual=[1, 0, 1]), 0.25)

    def test_known_example(self):
        # one-trade BS = (0.7 - 1)^2 = 0.09
        assert math.isclose(brier_score(predicted=[0.7], actual=[1]), 0.09)

    def test_empty_returns_zero(self):
        assert brier_score(predicted=[], actual=[]) == 0.0

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            brier_score(predicted=[0.5, 0.5], actual=[1])


class TestWinRate:
    def test_all_wins(self):
        assert win_rate(pnls=[10, 20, 30]) == 1.0

    def test_all_losses(self):
        assert win_rate(pnls=[-10, -20, -30]) == 0.0

    def test_mixed(self):
        # 3 wins, 2 losses, 0 ignored (ties don't count as wins)
        assert math.isclose(win_rate(pnls=[1, 2, 3, -1, -2]), 3 / 5)

    def test_zero_pnls_excluded(self):
        # 1 win, 1 loss, 1 tie -> 1/2
        assert math.isclose(win_rate(pnls=[1, -1, 0]), 0.5)

    def test_empty_returns_zero(self):
        assert win_rate(pnls=[]) == 0.0


class TestSharpe:
    def test_zero_when_constant_returns(self):
        # std = 0 -> Sharpe undefined; we return 0
        assert sharpe_ratio(returns=[0.01, 0.01, 0.01]) == 0.0

    def test_positive_for_positive_mean(self):
        # mean > 0, some variance -> positive
        s = sharpe_ratio(returns=[0.02, 0.01, 0.03, -0.01, 0.02])
        assert s > 0

    def test_negative_for_negative_mean(self):
        s = sharpe_ratio(returns=[-0.02, -0.01, -0.03, 0.01, -0.02])
        assert s < 0

    def test_annualized_with_periods(self):
        # daily returns, 252 trading days
        daily = [0.001] * 252
        # std=0 special case -> 0
        assert sharpe_ratio(returns=daily) == 0.0

    def test_too_few_returns_zero(self):
        assert sharpe_ratio(returns=[0.01]) == 0.0
        assert sharpe_ratio(returns=[]) == 0.0


class TestMaxDrawdown:
    def test_no_drawdown_on_monotonic(self):
        # equity always rising
        assert max_drawdown(equity=[100, 110, 120, 130]) == 0.0

    def test_simple_drawdown(self):
        # peak 200, trough 100 -> 50% drawdown
        assert math.isclose(max_drawdown(equity=[100, 200, 100]), 0.5)

    def test_picks_worst_drawdown(self):
        # peak 200 then 100 = 50%; later peak 150 then 75 = 50%; same
        assert math.isclose(max_drawdown(equity=[100, 200, 100, 150, 75]), 0.625)
        # actually: peak so far = 200, trough = 75 -> (200-75)/200 = 0.625

    def test_empty_zero(self):
        assert max_drawdown(equity=[]) == 0.0


class TestProfitFactor:
    def test_only_wins_returns_inf(self):
        # gross_loss == 0 -> infinity
        assert profit_factor(pnls=[1, 2, 3]) == float("inf")

    def test_balanced(self):
        # gross_win = 6, gross_loss = 3, pf = 2
        assert math.isclose(profit_factor(pnls=[1, 2, 3, -1, -2]), 2.0)

    def test_only_losses(self):
        assert profit_factor(pnls=[-1, -2]) == 0.0

    def test_empty_zero(self):
        assert profit_factor(pnls=[]) == 0.0
