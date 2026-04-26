"""Deterministic fallback sentiment scoring."""
from __future__ import annotations

POSITIVE_WORDS = {
    "approved",
    "beat",
    "bullish",
    "gained",
    "improved",
    "likely",
    "passed",
    "support",
    "win",
}
NEGATIVE_WORDS = {
    "bearish",
    "blocked",
    "declined",
    "delay",
    "failed",
    "loss",
    "opposition",
    "rejected",
    "unlikely",
}


def lexical_sentiment_score(text: str) -> float:
    words = {token.strip(".,:;!?()[]{}\"'").lower() for token in text.split()}
    positive = len(words & POSITIVE_WORDS)
    negative = len(words & NEGATIVE_WORDS)
    total = positive + negative
    if total == 0:
        return 0.0
    return (positive - negative) / total
