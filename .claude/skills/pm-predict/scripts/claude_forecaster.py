"""Claude forecast adapter entrypoint."""
from __future__ import annotations


def mock_claude_probability(p_market: float, narrative_score: float = 0.0) -> float:
    return min(0.95, max(0.05, p_market + 0.12 + (0.03 * narrative_score)))
