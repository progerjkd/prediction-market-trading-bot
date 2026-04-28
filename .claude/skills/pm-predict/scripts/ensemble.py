"""Probability ensemble helpers for pm-predict."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PredictionDecision:
    condition_id: str
    token_id: str
    p_model: float
    p_market: float
    edge: float
    should_trade: bool
    side: str | None
    reason: str
    components: dict[str, Any] = field(default_factory=dict)


def ensemble_probability(
    *,
    xgboost_probability: float,
    claude_probability: float,
    xgboost_weight: float = 0.60,
) -> float:
    if not 0 <= xgboost_probability <= 1:
        raise ValueError(f"xgboost_probability must be in [0, 1], got {xgboost_probability}")
    if not 0 <= claude_probability <= 1:
        raise ValueError(f"claude_probability must be in [0, 1], got {claude_probability}")
    if not 0 <= xgboost_weight <= 1:
        raise ValueError(f"xgboost_weight must be in [0, 1], got {xgboost_weight}")
    claude_weight = 1.0 - xgboost_weight
    return (xgboost_probability * xgboost_weight) + (claude_probability * claude_weight)


def make_prediction_decision(
    *,
    condition_id: str,
    token_id: str,
    p_market: float,
    xgboost_probability: float,
    claude_probability: float,
    edge_threshold: float = 0.04,
    xgboost_weight: float = 0.60,
    edge_shrink_threshold: float = 0.0,
    edge_shrink_factor: float = 1.0,
) -> PredictionDecision:
    p_model = ensemble_probability(
        xgboost_probability=xgboost_probability,
        claude_probability=claude_probability,
        xgboost_weight=xgboost_weight,
    )
    edge = p_model - p_market
    shrinkage_applied = False
    effective_edge = edge
    if edge_shrink_factor < 1.0 and 0.0 < abs(edge) < edge_shrink_threshold:
        effective_edge = edge * edge_shrink_factor
        shrinkage_applied = True
    should_trade = (effective_edge - edge_threshold) > 1e-12
    reason = "edge cleared threshold" if should_trade else f"edge {effective_edge:.4f} below threshold {edge_threshold}"
    return PredictionDecision(
        condition_id=condition_id,
        token_id=token_id,
        p_model=p_model,
        p_market=p_market,
        edge=edge,
        should_trade=should_trade,
        side="BUY" if should_trade else None,
        reason=reason,
        components={
            "xgboost_probability": xgboost_probability,
            "claude_probability": claude_probability,
            "xgboost_weight": xgboost_weight,
            "claude_weight": 1.0 - xgboost_weight,
            "reason": reason,
            "shrinkage_applied": shrinkage_applied,
            "effective_edge": effective_edge,
        },
    )
