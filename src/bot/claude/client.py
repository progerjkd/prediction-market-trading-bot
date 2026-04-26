"""Anthropic client wrapper for forecast calls."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ForecastResult:
    probability: float
    reasoning: str
    cost_usd: float = 0.0
    usage: dict[str, Any] | None = None


class ClaudeForecastClient:
    """Small wrapper around Anthropic Messages with prompt caching.

    The daemon can run without an API key in mock/fallback mode. That keeps
    smoke tests deterministic and prevents API spend surprises.
    """

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-7")

    async def forecast_probability(
        self,
        *,
        market_question: str,
        p_market: float,
        research_brief: str,
    ) -> ForecastResult:
        if not self.api_key:
            return ForecastResult(
                probability=min(0.95, max(0.05, p_market + 0.08)),
                reasoning="fallback_no_anthropic_api_key",
            )

        try:
            import anthropic
        except ImportError:
            return ForecastResult(
                probability=min(0.95, max(0.05, p_market + 0.05)),
                reasoning="fallback_anthropic_not_installed",
            )

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        response = await client.messages.create(
            model=self.model,
            max_tokens=256,
            cache_control={"type": "ephemeral"},
            system=(
                "You are a calibrated prediction-market forecaster. Return only JSON "
                "with probability_yes in [0,1] and reasoning. Be conservative."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Market: {market_question}\n"
                        f"Market-implied probability: {p_market:.4f}\n"
                        f"Research brief:\n{research_brief}"
                    ),
                }
            ],
        )
        text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        probability = _extract_probability(text, default=p_market)
        usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else None
        return ForecastResult(probability=probability, reasoning=text[:1_000], usage=usage)


def _extract_probability(text: str, *, default: float) -> float:
    import json
    import re

    try:
        parsed = json.loads(text)
        value = float(parsed["probability_yes"])
        return min(0.99, max(0.01, value))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        match = re.search(r"0?\.\d+", text)
        if match:
            return min(0.99, max(0.01, float(match.group(0))))
        return default
