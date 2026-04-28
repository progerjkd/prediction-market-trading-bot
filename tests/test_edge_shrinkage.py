"""Edge shrinkage — penalise low-confidence signals by dampening small edges.

When |edge| < edge_shrink_threshold, the effective edge used for trading is
multiplied by edge_shrink_factor (0..1), making it less likely to exceed the
edge_threshold required to actually trade.  This reduces noise-trading on
borderline signals without changing the threshold itself.

The shrinkage is applied in make_prediction_decision; ensemble.py is
responsible for wiring the new parameter through.
"""
from __future__ import annotations


def test_small_edge_is_shrunk():
    """An edge below the shrink threshold is reduced by the shrink factor."""
    from ensemble import make_prediction_decision

    decision = make_prediction_decision(
        condition_id="c1",
        token_id="t1",
        p_market=0.50,
        xgboost_probability=0.55,  # raw edge = 0.05 (just above typical 0.04 threshold)
        claude_probability=0.55,
        edge_threshold=0.04,
        edge_shrink_threshold=0.08,  # 0.05 < 0.08 → shrink
        edge_shrink_factor=0.5,      # effective edge = 0.05 * 0.5 = 0.025 < 0.04
    )
    # After shrinkage, 0.025 < edge_threshold(0.04) → should_trade=False
    assert not decision.should_trade


def test_large_edge_not_shrunk():
    """An edge above the shrink threshold is passed through unchanged."""
    from ensemble import make_prediction_decision

    decision = make_prediction_decision(
        condition_id="c2",
        token_id="t2",
        p_market=0.50,
        xgboost_probability=0.65,  # raw edge = 0.15 — well above shrink_threshold
        claude_probability=0.65,
        edge_threshold=0.04,
        edge_shrink_threshold=0.08,  # 0.15 > 0.08 → no shrink
        edge_shrink_factor=0.5,
    )
    assert decision.should_trade


def test_no_shrink_when_factor_is_one():
    """edge_shrink_factor=1.0 disables shrinkage (identity operation)."""
    from ensemble import make_prediction_decision

    decision = make_prediction_decision(
        condition_id="c3",
        token_id="t3",
        p_market=0.50,
        xgboost_probability=0.55,  # edge = 0.05
        claude_probability=0.55,
        edge_threshold=0.04,
        edge_shrink_threshold=0.08,
        edge_shrink_factor=1.0,  # factor=1 → no shrinkage
    )
    assert decision.should_trade  # 0.05 > 0.04 → should trade


def test_shrinkage_recorded_in_components():
    """When shrinkage fires, components includes 'shrinkage_applied'=True."""
    from ensemble import make_prediction_decision

    decision = make_prediction_decision(
        condition_id="c4",
        token_id="t4",
        p_market=0.50,
        xgboost_probability=0.55,
        claude_probability=0.55,
        edge_threshold=0.04,
        edge_shrink_threshold=0.08,
        edge_shrink_factor=0.5,
    )
    assert decision.components.get("shrinkage_applied") is True
