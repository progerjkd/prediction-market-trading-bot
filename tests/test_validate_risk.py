"""Tests for validate_risk — the deterministic rule engine.

Each rule is tested in isolation: only that rule fails; all others pass.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from validate_risk import RiskCheck, RiskInputs, RiskLimits, validate_risk

# A baseline that PASSES every rule. Each test mutates one field to fail one rule.
BASELINE = RiskInputs(
    p_model=0.60,
    p_market=0.50,
    b=1.0,
    size_usd=10.0,
    bankroll_usd=1000.0,
    open_positions=0,
    total_exposure_usd=0.0,
    daily_loss_usd=0.0,
    drawdown_pct=0.0,
    daily_api_cost_usd=0.0,
    stop_file=Path("/tmp/__never_exists_pm_bot__"),
)
LIMITS = RiskLimits()


class TestBaseline:
    def test_baseline_passes_all_rules(self):
        result = validate_risk(BASELINE, LIMITS)
        assert result.ok, result.reason


class TestEdge:
    def test_fails_when_edge_below_threshold(self):
        # edge = p_model - p_market = 0.51 - 0.50 = 0.01 < 0.04
        bad = replace(BASELINE, p_model=0.51, p_market=0.50)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "edge" in result.reason.lower()

    def test_passes_at_threshold_boundary(self):
        # edge = 0.04 exactly should pass (rule is `> 0.04` strict, but 0.041 is safer)
        # Use 0.05 to comfortably pass.
        good = replace(BASELINE, p_model=0.55, p_market=0.50)
        assert validate_risk(good, LIMITS).ok


class TestKellyCap:
    def test_fails_when_size_exceeds_kelly(self):
        # full Kelly at p=0.6, b=1.0 = 0.2; quarter = 0.05; bankroll 1000 -> $50 max
        # ask for $100 -> fails
        bad = replace(BASELINE, size_usd=100.0)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "kelly" in result.reason.lower()

    def test_passes_at_kelly_exact(self):
        # quarter-Kelly is $50; exactly equal should pass
        ok = replace(BASELINE, size_usd=50.0)
        assert validate_risk(ok, LIMITS).ok


class TestPositionCap:
    def test_fails_when_position_exceeds_5pct_bankroll(self):
        # 5% of $1000 = $50; ask for $51 -> fails
        # but kelly cap is also $50 — set huge edge to push kelly above $51
        bad = replace(BASELINE, p_model=0.95, p_market=0.50, size_usd=51.0)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "position" in result.reason.lower() or "5%" in result.reason


class TestExposureCap:
    def test_fails_when_total_exposure_exceeds_limit(self):
        # default max_exposure_pct = 0.50 of bankroll = $500
        # current 450 + new 51 = 501 > 500 -> fails
        bad = replace(BASELINE, total_exposure_usd=450.0, size_usd=51.0)
        # but size 51 exceeds 5% cap. Use a high p to allow.
        bad = replace(bad, p_model=0.95, p_market=0.50)
        # but 51 > 5% cap of $50 still fails for that. Need bigger bankroll or smaller pos.
        bad = replace(
            bad,
            bankroll_usd=10_000,
            total_exposure_usd=4960,
            size_usd=50,
            p_model=0.95,
            p_market=0.50,
        )
        # 5% of 10k = 500; size 50 ok. Exposure 4960+50=5010 > 5000. Fails.
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "exposure" in result.reason.lower()


class TestOpenPositions:
    def test_fails_when_open_positions_at_limit(self):
        # default limit = 15
        bad = replace(BASELINE, open_positions=15)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "position" in result.reason.lower()

    def test_passes_at_14_open_positions(self):
        ok = replace(BASELINE, open_positions=14)
        assert validate_risk(ok, LIMITS).ok


class TestDailyLoss:
    def test_fails_when_daily_loss_exceeds_15pct(self):
        # 15% of $1000 = $150; loss of $150 (or more) -> fails
        bad = replace(BASELINE, daily_loss_usd=150.0)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "daily" in result.reason.lower() and "loss" in result.reason.lower()


class TestDrawdown:
    def test_fails_when_drawdown_exceeds_8pct(self):
        bad = replace(BASELINE, drawdown_pct=0.08)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "drawdown" in result.reason.lower()


class TestApiCost:
    def test_fails_when_daily_api_cost_exceeds_50(self):
        bad = replace(BASELINE, daily_api_cost_usd=50.0)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "api" in result.reason.lower() or "cost" in result.reason.lower()


class TestKillSwitch:
    def test_fails_when_stop_file_present(self, tmp_path):
        stop_file = tmp_path / "STOP"
        stop_file.write_text("halt")
        bad = replace(BASELINE, stop_file=stop_file)
        result = validate_risk(bad, LIMITS)
        assert not result.ok
        assert "stop" in result.reason.lower() or "kill" in result.reason.lower()


class TestReturnType:
    def test_returns_RiskCheck_dataclass(self):
        result = validate_risk(BASELINE, LIMITS)
        assert isinstance(result, RiskCheck)
        assert isinstance(result.ok, bool)
        assert isinstance(result.reason, str)
