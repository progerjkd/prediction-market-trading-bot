"""RuntimeSettings startup validation — TDD RED phase."""
from __future__ import annotations

import pytest

from bot.config import RuntimeSettings


def _ok(**kwargs) -> RuntimeSettings:
    """Return valid settings with overrides."""
    return RuntimeSettings(**kwargs)


# ---------------------------------------------------------------------------
# Valid defaults should never raise
# ---------------------------------------------------------------------------


def test_default_settings_are_valid():
    s = RuntimeSettings()
    assert s.bankroll_usdc > 0


# ---------------------------------------------------------------------------
# edge_threshold
# ---------------------------------------------------------------------------


def test_edge_threshold_zero_raises():
    with pytest.raises(ValueError, match="edge_threshold"):
        RuntimeSettings(edge_threshold=0.0)


def test_edge_threshold_negative_raises():
    with pytest.raises(ValueError, match="edge_threshold"):
        RuntimeSettings(edge_threshold=-0.1)


def test_edge_threshold_one_raises():
    with pytest.raises(ValueError, match="edge_threshold"):
        RuntimeSettings(edge_threshold=1.0)


def test_edge_threshold_above_one_raises():
    with pytest.raises(ValueError, match="edge_threshold"):
        RuntimeSettings(edge_threshold=2.0)


def test_edge_threshold_valid_small_value():
    s = RuntimeSettings(edge_threshold=0.01)
    assert s.edge_threshold == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# kelly_fraction
# ---------------------------------------------------------------------------


def test_kelly_fraction_zero_raises():
    with pytest.raises(ValueError, match="kelly_fraction"):
        RuntimeSettings(kelly_fraction=0.0)


def test_kelly_fraction_negative_raises():
    with pytest.raises(ValueError, match="kelly_fraction"):
        RuntimeSettings(kelly_fraction=-0.25)


def test_kelly_fraction_above_one_raises():
    with pytest.raises(ValueError, match="kelly_fraction"):
        RuntimeSettings(kelly_fraction=1.5)


def test_kelly_fraction_exactly_one_is_valid():
    s = RuntimeSettings(kelly_fraction=1.0)
    assert s.kelly_fraction == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# bankroll_usdc
# ---------------------------------------------------------------------------


def test_bankroll_zero_raises():
    with pytest.raises(ValueError, match="bankroll"):
        RuntimeSettings(bankroll_usdc=0.0)


def test_bankroll_negative_raises():
    with pytest.raises(ValueError, match="bankroll"):
        RuntimeSettings(bankroll_usdc=-1000.0)


# ---------------------------------------------------------------------------
# position / exposure limits
# ---------------------------------------------------------------------------


def test_max_position_pct_zero_raises():
    with pytest.raises(ValueError, match="max_position_pct"):
        RuntimeSettings(max_position_pct=0.0)


def test_max_position_pct_above_one_raises():
    with pytest.raises(ValueError, match="max_position_pct"):
        RuntimeSettings(max_position_pct=1.5)


def test_max_exposure_pct_zero_raises():
    with pytest.raises(ValueError, match="max_exposure_pct"):
        RuntimeSettings(max_exposure_pct=0.0)


def test_max_exposure_pct_above_one_raises():
    with pytest.raises(ValueError, match="max_exposure_pct"):
        RuntimeSettings(max_exposure_pct=1.5)


# ---------------------------------------------------------------------------
# loss limits
# ---------------------------------------------------------------------------


def test_daily_loss_pct_zero_raises():
    with pytest.raises(ValueError, match="daily_loss_pct"):
        RuntimeSettings(daily_loss_pct=0.0)


def test_daily_loss_pct_above_one_raises():
    with pytest.raises(ValueError, match="daily_loss_pct"):
        RuntimeSettings(daily_loss_pct=1.5)


def test_max_drawdown_pct_zero_raises():
    with pytest.raises(ValueError, match="max_drawdown_pct"):
        RuntimeSettings(max_drawdown_pct=0.0)


def test_max_drawdown_pct_above_one_raises():
    with pytest.raises(ValueError, match="max_drawdown_pct"):
        RuntimeSettings(max_drawdown_pct=1.5)


# ---------------------------------------------------------------------------
# scan params
# ---------------------------------------------------------------------------


def test_scan_max_spread_zero_raises():
    with pytest.raises(ValueError, match="scan_max_spread"):
        RuntimeSettings(scan_max_spread=0.0)


def test_scan_max_days_zero_raises():
    with pytest.raises(ValueError, match="scan_max_days"):
        RuntimeSettings(scan_max_days=0)


def test_scan_interval_seconds_negative_raises():
    with pytest.raises(ValueError, match="scan_interval_seconds"):
        RuntimeSettings(scan_interval_seconds=-1)
