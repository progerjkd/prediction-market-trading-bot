"""Polymarket CLOB client.

Read-only methods (markets, orderbook, trades) hit the public CLOB endpoints
without authentication. Order placement requires CLOB L2 credentials and is
gated behind LIVE_TRADING — v1 ships with that flag forced off.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_GAMMA = "https://gamma-api.polymarket.com"


@dataclass
class Market:
    condition_id: str
    question: str
    yes_token: str
    no_token: str
    end_date_iso: str | None
    volume_24h: float
    liquidity: float
    closed: bool
    raw: dict[str, Any]


@dataclass
class OrderBookSnapshot:
    token_id: str
    asks: list[tuple[float, float]]  # (price, size), ascending price
    bids: list[tuple[float, float]]  # (price, size), descending price
    timestamp: int

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


class PolymarketClient:
    """Async HTTP client for Polymarket CLOB + Gamma APIs."""

    def __init__(
        self,
        host: str | None = None,
        gamma_host: str | None = None,
        timeout: float = 30.0,
    ):
        self.host = (host or os.environ.get("CLOB_HOST", DEFAULT_HOST)).rstrip("/")
        self.gamma = (gamma_host or os.environ.get("GAMMA_HOST", DEFAULT_GAMMA)).rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PolymarketClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def list_markets(self, limit: int = 100, active_only: bool = True) -> list[Market]:
        """Fetch active markets from Gamma (the queryable index)."""
        params = {"limit": limit, "closed": "false" if active_only else "true"}
        if active_only:
            params["active"] = "true"
        resp = await self._http.get(f"{self.gamma}/markets", params=params)
        resp.raise_for_status()
        data = resp.json()
        markets: list[Market] = []
        for m in data:
            tokens = _parse_clob_token_ids(m)
            if not tokens:
                continue
            yes_token, no_token = tokens
            markets.append(
                Market(
                    condition_id=m.get("conditionId") or m.get("id", ""),
                    question=m.get("question", ""),
                    yes_token=yes_token,
                    no_token=no_token,
                    end_date_iso=m.get("endDate") or m.get("endDateIso"),
                    volume_24h=float(m.get("volume24hr") or 0),
                    liquidity=float(m.get("liquidity") or 0),
                    closed=bool(m.get("closed", False)),
                    raw=m,
                )
            )
        return markets

    async def get_orderbook(self, token_id: str) -> OrderBookSnapshot:
        resp = await self._http.get(f"{self.host}/book", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        asks = sorted(
            ((float(o["price"]), float(o["size"])) for o in data.get("asks", [])),
            key=lambda x: x[0],
        )
        bids = sorted(
            ((float(o["price"]), float(o["size"])) for o in data.get("bids", [])),
            key=lambda x: -x[0],
        )
        return OrderBookSnapshot(
            token_id=token_id,
            asks=asks,
            bids=bids,
            timestamp=int(data.get("timestamp") or 0),
        )

    async def get_midpoint(self, token_id: str) -> float | None:
        resp = await self._http.get(f"{self.host}/midpoint", params={"token_id": token_id})
        resp.raise_for_status()
        data = resp.json()
        mid = data.get("mid")
        return float(mid) if mid is not None else None


def _parse_clob_token_ids(market: dict[str, Any]) -> tuple[str, str] | None:
    """Extract (yes_token, no_token) from a Gamma market record.

    Gamma returns clobTokenIds as a JSON-encoded string of 2 token IDs;
    outcomes is a JSON string like '["Yes", "No"]'.
    """
    raw = market.get("clobTokenIds")
    if not raw:
        return None
    try:
        import json

        ids = json.loads(raw) if isinstance(raw, str) else raw
        outcomes_raw = market.get("outcomes")
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
    except (ValueError, TypeError):
        return None
    if len(ids) < 2:
        return None
    # Map by outcome label so we know which is YES vs NO.
    if len(outcomes) >= 2:
        labelled = dict(zip(outcomes, ids, strict=False))
        yes = labelled.get("Yes") or labelled.get("YES") or ids[0]
        no = labelled.get("No") or labelled.get("NO") or ids[1]
    else:
        yes, no = ids[0], ids[1]
    return str(yes), str(no)
