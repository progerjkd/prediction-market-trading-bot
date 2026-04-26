"""Tests for pm-predict deterministic ensemble helpers."""
from __future__ import annotations

import pytest
from ensemble import PredictionDecision, ensemble_probability, make_prediction_decision


def test_ensemble_probability_uses_configured_weights():
    p = ensemble_probability(xgboost_probability=0.70, claude_probability=0.50, xgboost_weight=0.60)

    assert p == pytest.approx(0.62)


def test_make_prediction_decision_emits_signal_only_above_edge_threshold():
    decision = make_prediction_decision(
        condition_id="cond",
        token_id="yes-token",
        p_market=0.50,
        xgboost_probability=0.70,
        claude_probability=0.60,
        edge_threshold=0.04,
    )

    assert isinstance(decision, PredictionDecision)
    assert decision.should_trade is True
    assert decision.edge == pytest.approx(0.16)
    assert decision.side == "BUY"


def test_make_prediction_decision_rejects_edge_at_threshold_boundary():
    decision = make_prediction_decision(
        condition_id="cond",
        token_id="yes-token",
        p_market=0.50,
        xgboost_probability=0.54,
        claude_probability=0.54,
        edge_threshold=0.04,
    )

    assert decision.should_trade is False
    assert decision.reason == "edge 0.0400 below threshold 0.04"
