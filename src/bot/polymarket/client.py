"""Polymarket CLOB client.

Read-only methods (markets, orderbook, trades) hit the public CLOB endpoints
without authentication. Order placement requires CLOB L2 credentials and is
gated behind LIVE_TRADING — v1 ships with that flag forced off.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_HOST = "https://clob.polymarket.com"
DEFAULT_GAMMA = "https://gamma-api.polymarket.com"
_RESOLUTION_THRESHOLD = 0.95

log = logging.getLogger(__name__)


@dataclass
class MarketResolution:
    resolved: bool
    final_yes_price: float | None  # 1.0 = YES won, 0.0 = NO won, None = pending


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
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ):
        self.host = (host or os.environ.get("CLOB_HOST", DEFAULT_HOST)).rstrip("/")
        self.gamma = (gamma_host or os.environ.get("GAMMA_HOST", DEFAULT_GAMMA)).rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> PolymarketClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def _get_with_retry(self, url: str, params: dict | None = None) -> httpx.Response:
        """GET with exponential-backoff retry on transport errors and 5xx responses."""
        for attempt in range(self._max_retries):
            is_last = attempt == self._max_retries - 1
            try:
                resp = await self._http.get(url, params=params)
                if resp.status_code < 500:
                    return resp
                # 5xx: raise on last attempt, otherwise back off and retry
                if is_last:
                    resp.raise_for_status()
                delay = self._retry_base_delay * (2**attempt)
                log.warning(
                    "server error %d (attempt %d/%d), retrying in %.1fs",
                    resp.status_code,
                    attempt + 1,
                    self._max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if is_last:
                    raise
                delay = self._retry_base_delay * (2**attempt)
                log.warning(
                    "transport error (attempt %d/%d): %s, retrying in %.1fs",
                    attempt + 1,
                    self._max_retries,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        raise RuntimeError("retry loop exhausted without returning")  # unreachable

    async def list_markets(
        self,
        limit: int = 100,
        active_only: bool = True,
        max_pages: int = 5,
    ) -> list[Market]:
        """Fetch markets from Gamma with offset pagination and retry.

        active_only=True sends closed=false&active=true.
        active_only=False fetches all markets (no closed filter).
        """
        params: dict[str, str | int] = {}
        if active_only:
            params["closed"] = "false"
            params["active"] = "true"
        # active_only=False: omit closed/active filters to see all markets

        all_markets: list[Market] = []
        for page in range(max_pages):
            params["limit"] = limit
            params["offset"] = page * limit
            resp = await self._get_with_retry(f"{self.gamma}/markets", params=params)
            resp.raise_for_status()
            data = resp.json()

            # Gamma may return a plain list or a paginated dict
            if isinstance(data, list):
                items = data
            else:
                items = data.get("data") or data.get("markets") or []

            for m in items:
                market = self._parse_market(m)
                if market is not None:
                    all_markets.append(market)

            log.debug("page %d: fetched %d items (total so far: %d)", page, len(items), len(all_markets))
            if len(items) < limit:
                break

        return all_markets

    def _parse_market(self, m: dict[str, Any]) -> Market | None:
        tokens = _parse_clob_token_ids(m)
        if not tokens:
            log.debug("skipping market without valid token ids: %s", m.get("conditionId", "?"))
            return None
        yes_token, no_token = tokens
        return Market(
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

    async def get_orderbook(self, token_id: str) -> OrderBookSnapshot:
        resp = await self._get_with_retry(f"{self.host}/book", params={"token_id": token_id})
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

    async def get_market_resolution(self, condition_id: str) -> MarketResolution:
        """Query Gamma for a single market's resolution status."""
        resp = await self._get_with_retry(
            f"{self.gamma}/markets",
            params={"conditionId": condition_id, "limit": 1},
        )
        resp.raise_for_status()
        data = resp.json()
        markets = data if isinstance(data, list) else data.get("data") or []
        if not markets:
            return MarketResolution(resolved=False, final_yes_price=None)
        record = markets[0]
        raw_prices = record.get("outcomePrices")
        if not raw_prices:
            return MarketResolution(resolved=False, final_yes_price=None)
        try:
            import json as _json
            prices = _json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
            yes_price = float(prices[0])
            no_price = float(prices[1])
        except (ValueError, TypeError, IndexError):
            return MarketResolution(resolved=False, final_yes_price=None)
        if yes_price >= _RESOLUTION_THRESHOLD and no_price < (1 - _RESOLUTION_THRESHOLD):
            return MarketResolution(resolved=True, final_yes_price=yes_price)
        if no_price >= _RESOLUTION_THRESHOLD and yes_price < (1 - _RESOLUTION_THRESHOLD):
            return MarketResolution(resolved=True, final_yes_price=yes_price)
        return MarketResolution(resolved=False, final_yes_price=None)

    async def get_midpoint(self, token_id: str) -> float | None:
        resp = await self._get_with_retry(f"{self.host}/midpoint", params={"token_id": token_id})
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
