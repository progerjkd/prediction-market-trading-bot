"""Tests for resolved-market feature extraction (pure functions, no network)."""
from __future__ import annotations

import json

import pytest
from fetch_resolved_markets import extract_label, parse_resolved_market


def _record(
    outcome_prices: list[str],
    *,
    one_day_change: float = 0.30,
    spread: float = 0.02,
    volume24hr: float | None = 500.0,
    volume: str = "10000",
    end_date: str = "2024-11-01T00:00:00Z",
    created_at: str = "2024-10-25T00:00:00Z",
) -> dict:
    return {
        "conditionId": "cond-test",
        "question": "Will X happen?",
        "outcomePrices": json.dumps(outcome_prices),
        "lastTradePrice": 1,
        "oneDayPriceChange": one_day_change,
        "spread": spread,
        "volume24hr": volume24hr,
        "volume": volume,
        "liquidity": "1000",
        "endDate": end_date,
        "createdAt": created_at,
    }


# ---------------------------------------------------------------------------
# Label extraction
# ---------------------------------------------------------------------------


def test_extract_label_yes_won():
    assert extract_label(["1", "0"]) == 1


def test_extract_label_no_won():
    assert extract_label(["0", "1"]) == 0


def test_extract_label_voided_returns_none():
    assert extract_label(["0", "0"]) is None


def test_extract_label_unresolved_returns_none():
    assert extract_label(["0.5", "0.5"]) is None


def test_extract_label_near_one_yes():
    # Market clearly resolved YES (price very close to 1)
    assert extract_label(["0.9999", "0.0001"]) == 1


def test_extract_label_near_one_no():
    assert extract_label(["0.0001", "0.9999"]) == 0


def test_extract_label_ambiguous_returns_none():
    # Neither outcome clearly dominant
    assert extract_label(["0.6", "0.4"]) is None


# ---------------------------------------------------------------------------
# current_mid feature from price-change fields
# ---------------------------------------------------------------------------


def test_parse_yes_won_current_mid_from_day_change():
    # YES won (outcomePrices[0]=1), dayChg=0.35
    # price 1 day before = 1.0 - 0.35 = 0.65
    rec = _record(["1", "0"], one_day_change=0.35)
    result = parse_resolved_market(rec)
    assert result is not None
    features, label = result
    assert label == 1
    assert features["current_mid"] == pytest.approx(0.65)


def test_parse_no_won_current_mid_from_day_change():
    # NO won (outcomePrices[0]=0), dayChg=-0.25 (YES price fell 0.25 on last day)
    # YES price 1 day before = 0.0 - (-0.25) = 0.25
    rec = _record(["0", "1"], one_day_change=-0.25)
    result = parse_resolved_market(rec)
    assert result is not None
    features, label = result
    assert label == 0
    assert features["current_mid"] == pytest.approx(0.25)


def test_parse_current_mid_is_clipped_to_valid_range():
    # Computed mid could be <0 or >1 due to stale price-change data; clip it
    rec = _record(["1", "0"], one_day_change=1.5)  # 1 - 1.5 = -0.5 → clip
    features, _ = parse_resolved_market(rec)
    assert 0.01 <= features["current_mid"] <= 0.99


# ---------------------------------------------------------------------------
# Other features
# ---------------------------------------------------------------------------


def test_parse_days_to_resolution_is_one():
    rec = _record(["1", "0"])
    features, _ = parse_resolved_market(rec)
    assert features["days_to_resolution"] == pytest.approx(1.0)


def test_parse_spread_passed_through():
    rec = _record(["1", "0"], spread=0.03)
    features, _ = parse_resolved_market(rec)
    assert features["spread"] == pytest.approx(0.03)


def test_parse_volume_24h_defaults_to_zero_when_null():
    rec = _record(["1", "0"], volume24hr=None)
    features, _ = parse_resolved_market(rec)
    assert features["volume_24h"] == 0.0


def test_parse_volume_24h_passed_through_when_present():
    rec = _record(["1", "0"], volume24hr=1234.5)
    features, _ = parse_resolved_market(rec)
    assert features["volume_24h"] == pytest.approx(1234.5)


def test_parse_zero_features_for_unused_fields():
    rec = _record(["1", "0"])
    features, _ = parse_resolved_market(rec)
    assert features["narrative_score"] == 0.0
    assert features["momentum_1h"] == 0.0
    assert features["momentum_24h"] == 0.0


# ---------------------------------------------------------------------------
# Voided / unresolvable records
# ---------------------------------------------------------------------------


def test_parse_voided_market_returns_none():
    rec = _record(["0", "0"])
    assert parse_resolved_market(rec) is None


def test_parse_missing_outcome_prices_returns_none():
    rec = _record(["1", "0"])
    del rec["outcomePrices"]
    assert parse_resolved_market(rec) is None


def test_parse_malformed_outcome_prices_returns_none():
    rec = _record(["1", "0"])
    rec["outcomePrices"] = "not-json"
    assert parse_resolved_market(rec) is None
