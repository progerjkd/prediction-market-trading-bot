"""Fetch resolved Polymarket markets and extract XGBoost training features.

Feature vector (matches infer_xgboost.py column order):
  current_mid        - YES probability 1 day before resolution
  spread             - bid-ask spread at close
  volume_24h         - 24-hour volume (0 when unavailable)
  days_to_resolution - always 1.0 (using 1-day lookback)
  narrative_score    - 0.0 (not in historical data)
  momentum_1h        - 0.0 (not in historical data)
  momentum_24h       - 0.0 (set to 0 to avoid leaking final-day signal)

Label: 1 = YES resolved, 0 = NO resolved.

The key insight: for a resolved market,
  final_yes_price - oneDayPriceChange  =  YES price 1 day before resolution.
This is the same signal `current_mid` represents at inference time.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

RESOLUTION_THRESHOLD = 0.95  # outcomePrices above this = "clearly resolved"


def extract_label(outcome_prices: list[str]) -> int | None:
    """Return 1 (YES won), 0 (NO won), or None (voided/ambiguous)."""
    if len(outcome_prices) < 2:
        return None
    try:
        yes_price = float(outcome_prices[0])
        no_price = float(outcome_prices[1])
    except (ValueError, TypeError):
        return None
    if yes_price >= RESOLUTION_THRESHOLD and no_price < (1 - RESOLUTION_THRESHOLD):
        return 1
    if no_price >= RESOLUTION_THRESHOLD and yes_price < (1 - RESOLUTION_THRESHOLD):
        return 0
    return None


def parse_resolved_market(record: dict) -> tuple[dict[str, float], int] | None:
    """Extract (features_dict, label) from a Gamma market record, or None to skip."""
    raw_prices = record.get("outcomePrices")
    if raw_prices is None:
        return None
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
    except (ValueError, TypeError):
        return None

    label = extract_label(prices)
    if label is None:
        return None

    final_yes_price = float(prices[0])
    day_change = float(record.get("oneDayPriceChange") or 0.0)

    # YES probability 1 day before resolution: final_yes = yesterday + dayChange
    raw_mid = final_yes_price - day_change
    current_mid = max(0.01, min(0.99, raw_mid))

    volume_24h_raw = record.get("volume24hr")
    volume_24h = float(volume_24h_raw) if volume_24h_raw is not None else 0.0

    spread_raw = record.get("spread")
    spread = float(spread_raw) if spread_raw is not None else 0.05

    features = {
        "current_mid": current_mid,
        "spread": spread,
        "volume_24h": volume_24h,
        "days_to_resolution": 1.0,
        "narrative_score": 0.0,
        "momentum_1h": 0.0,
        "momentum_24h": 0.0,
    }
    return features, label


async def fetch_all_resolved(
    gamma_host: str = "https://gamma-api.polymarket.com",
    page_size: int = 100,
    max_pages: int = 100,
    start_offset: int = 14_000,
) -> list[tuple[dict[str, float], int]]:
    """Page through Gamma closed markets and return parsed (features, label) pairs."""
    import httpx

    rows: list[tuple[dict[str, float], int]] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for page in range(max_pages):
            offset = start_offset + page * page_size
            try:
                resp = await client.get(
                    f"{gamma_host}/markets",
                    params={"closed": "true", "limit": page_size, "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                log.warning("page %d (offset %d) failed: %s", page, offset, exc)
                break

            if not data:
                log.info("no more markets at offset %d", offset)
                break

            for record in data:
                parsed = parse_resolved_market(record)
                if parsed is not None:
                    rows.append(parsed)

            log.info("offset=%d fetched=%d clean_total=%d", offset, len(data), len(rows))

            if len(data) < page_size:
                break

    return rows


def to_dataframe(rows: list[tuple[dict[str, float], int]]):
    """Convert (features, label) pairs to a pandas DataFrame."""
    import pandas as pd

    records = [{**features, "label": label} for features, label in rows]
    return pd.DataFrame(records)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Fetch resolved Polymarket markets for XGBoost training")
    parser.add_argument("--output", default="data/training_data.csv")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--start-offset", type=int, default=14_000)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()

    rows = asyncio.run(
        fetch_all_resolved(
            max_pages=args.max_pages,
            start_offset=args.start_offset,
            page_size=args.page_size,
        )
    )
    df = to_dataframe(rows)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"saved {len(df)} rows → {out}")
    print(f"label distribution:\n{df['label'].value_counts()}")


if __name__ == "__main__":
    main()
