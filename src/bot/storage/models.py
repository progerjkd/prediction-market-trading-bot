"""Domain models for persisted records."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlaggedMarket:
    condition_id: str
    yes_token: str
    no_token: str
    mid_price: float
    spread: float
    volume_24h: float
    flagged_at: int = field(default_factory=lambda: int(time.time()))
    question: str = ""
    end_date_iso: str | None = None
    liquidity: float = 0.0
    edge_proxy: float = 0.0
    raw_json: str = "{}"


@dataclass
class ResearchBrief:
    condition_id: str
    bullish_signals: list[str]
    bearish_signals: list[str]
    narrative_score: float  # range [-1, 1]; positive = bullish for YES
    bullish_score: float = 0.0
    bearish_score: float = 0.0
    sources: list[str] = field(default_factory=list)
    created_at: int = field(default_factory=lambda: int(time.time()))

    def to_json(self) -> str:
        return json.dumps(
            {
                "bullish_signals": self.bullish_signals,
                "bearish_signals": self.bearish_signals,
                "narrative_score": self.narrative_score,
                "sources": self.sources,
            }
        )


@dataclass
class Prediction:
    condition_id: str
    token_id: str
    p_model: float
    p_market: float
    edge: float
    components: dict[str, Any] = field(default_factory=dict)
    created_at: int = field(default_factory=lambda: int(time.time()))
    id: int | None = None

    def components_json(self) -> str:
        return json.dumps(self.components)


@dataclass
class Trade:
    condition_id: str
    token_id: str
    side: str  # "BUY" | "SELL"
    size: float
    limit_price: float
    is_paper: bool = True
    prediction_id: int | None = None
    fill_price: float | None = None
    slippage: float | None = None
    intended_size: float | None = None
    opened_at: int = field(default_factory=lambda: int(time.time()))
    closed_at: int | None = None
    pnl: float | None = None
    outcome: str | None = None
    source: str = "paper_live"
    id: int | None = None


@dataclass
class PaperExecution:
    condition_id: str
    token_id: str
    side: str  # "BUY" | "SELL"
    requested_size: float
    filled_size: float
    unfilled_size: float
    limit_price: float
    status: str  # "FULL_FILL" | "PARTIAL_FILL" | "NO_FILL"
    is_paper: bool = True
    prediction_id: int | None = None
    trade_id: int | None = None
    fill_price: float | None = None
    slippage: float | None = None
    created_at: int = field(default_factory=lambda: int(time.time()))
    id: int | None = None


@dataclass
class SkipEvent:
    stage: str
    reason: str
    condition_id: str | None = None
    token_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    source: str = "paper_live"
    created_at: int = field(default_factory=lambda: int(time.time()))
    id: int | None = None

    def detail_json(self) -> str:
        return json.dumps(self.detail)


@dataclass
class Lesson:
    trade_id: int
    cause: str  # bad-prediction | bad-timing | bad-execution | external-shock
    rule_proposed: str
    notes: str = ""
    created_at: int = field(default_factory=lambda: int(time.time()))
    id: int | None = None


@dataclass
class OpenTradeRecord:
    trade_id: int
    condition_id: str
    token_id: str
    fill_price: float | None
    size: float
    slippage: float | None
    end_date_iso: str | None
    question: str | None = None
    side: str = "BUY"


@dataclass
class ApiSpend:
    provider: str
    cost_usd: float
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    created_at: int = field(default_factory=lambda: int(time.time()))
