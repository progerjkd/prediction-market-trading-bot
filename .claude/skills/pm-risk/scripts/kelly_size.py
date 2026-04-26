"""Quarter-Kelly position sizing for binary outcomes.

Kelly criterion for a binary bet:
    f* = (p*b - q) / b
where:
    p = probability of winning
    q = 1 - p
    b = net odds received on win (payoff per $1 staked, i.e. profit ratio)
"""
from __future__ import annotations


def kelly_fraction(p: float, b: float) -> float:
    """Full Kelly fraction of bankroll, clipped to [0, 1].

    Returns 0 when edge is zero or negative — never bet against yourself.
    """
    if not 0 <= p <= 1:
        raise ValueError(f"p must be in [0, 1], got {p}")
    if b <= 0:
        raise ValueError(f"b must be > 0, got {b}")
    q = 1 - p
    f_star = (p * b - q) / b
    if f_star <= 0:
        return 0.0
    return min(f_star, 1.0)


def kelly_size(
    p: float,
    b: float,
    bankroll: float,
    fraction: float = 0.25,
) -> float:
    """Dollar size for a Kelly-fraction-of-Kelly bet.

    fraction defaults to 0.25 (quarter-Kelly) per the project's risk policy.
    """
    if bankroll < 0:
        raise ValueError(f"bankroll must be >= 0, got {bankroll}")
    if not 0 < fraction <= 1:
        raise ValueError(f"fraction must be in (0, 1], got {fraction}")
    f = kelly_fraction(p, b)
    return f * fraction * bankroll
