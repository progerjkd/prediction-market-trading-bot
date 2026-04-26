"""Tests for paper-fill simulator (walk-the-book against synthetic orderbook)."""
from __future__ import annotations

import math

import pytest

from bot.paper.simulator import (
    Fill,
    OrderBook,
    OrderBookLevel,
    Side,
    simulate_fill,
)


def book(asks: list[tuple[float, float]], bids: list[tuple[float, float]]) -> OrderBook:
    """Helper: build an OrderBook from (price, size) tuples."""
    return OrderBook(
        asks=[OrderBookLevel(price=p, size=s) for p, s in asks],
        bids=[OrderBookLevel(price=p, size=s) for p, s in bids],
    )


class TestBuyOrders:
    def test_full_fill_at_best_ask(self):
        # 50 shares wanted, best ask offers 100 @ 0.55
        ob = book(asks=[(0.55, 100)], bids=[(0.50, 100)])
        fill = simulate_fill(ob, side=Side.BUY, size=50, limit_price=0.60)
        assert fill.filled_size == 50
        assert math.isclose(fill.avg_price, 0.55)
        assert fill.unfilled_size == 0

    def test_walks_through_multiple_levels(self):
        # buy 150; book has 100 @ 0.55, 100 @ 0.57
        ob = book(asks=[(0.55, 100), (0.57, 100)], bids=[(0.50, 100)])
        fill = simulate_fill(ob, side=Side.BUY, size=150, limit_price=0.60)
        # 100 @ 0.55 + 50 @ 0.57 = 55 + 28.5 = 83.5 ; avg = 83.5 / 150
        expected_avg = (100 * 0.55 + 50 * 0.57) / 150
        assert fill.filled_size == 150
        assert math.isclose(fill.avg_price, expected_avg)

    def test_partial_fill_when_limit_below_next_level(self):
        # buy 150 with limit 0.56; first level 100 @ 0.55 fills, second 0.57 rejected
        ob = book(asks=[(0.55, 100), (0.57, 100)], bids=[])
        fill = simulate_fill(ob, side=Side.BUY, size=150, limit_price=0.56)
        assert fill.filled_size == 100
        assert math.isclose(fill.avg_price, 0.55)
        assert fill.unfilled_size == 50

    def test_no_fill_when_limit_below_best_ask(self):
        ob = book(asks=[(0.55, 100)], bids=[])
        fill = simulate_fill(ob, side=Side.BUY, size=50, limit_price=0.50)
        assert fill.filled_size == 0
        assert fill.avg_price == 0.0
        assert fill.unfilled_size == 50

    def test_partial_fill_when_book_too_thin(self):
        # buy 200 but only 100 available
        ob = book(asks=[(0.55, 100)], bids=[])
        fill = simulate_fill(ob, side=Side.BUY, size=200, limit_price=0.60)
        assert fill.filled_size == 100
        assert fill.unfilled_size == 100


class TestSellOrders:
    def test_full_fill_at_best_bid(self):
        ob = book(asks=[(0.55, 100)], bids=[(0.50, 100)])
        fill = simulate_fill(ob, side=Side.SELL, size=50, limit_price=0.45)
        assert fill.filled_size == 50
        assert math.isclose(fill.avg_price, 0.50)

    def test_walks_through_bids_descending(self):
        # sell 150 into bids: 100 @ 0.50, 100 @ 0.48
        ob = book(asks=[], bids=[(0.50, 100), (0.48, 100)])
        fill = simulate_fill(ob, side=Side.SELL, size=150, limit_price=0.40)
        expected_avg = (100 * 0.50 + 50 * 0.48) / 150
        assert fill.filled_size == 150
        assert math.isclose(fill.avg_price, expected_avg)

    def test_no_fill_when_limit_above_best_bid(self):
        ob = book(asks=[], bids=[(0.50, 100)])
        fill = simulate_fill(ob, side=Side.SELL, size=50, limit_price=0.55)
        assert fill.filled_size == 0


class TestSlippage:
    def test_slippage_against_mid_for_buy(self):
        # mid = 0.525, fill avg = 0.56, slippage = +0.035
        ob = book(asks=[(0.55, 50), (0.57, 100)], bids=[(0.50, 100)])
        fill = simulate_fill(ob, side=Side.BUY, size=100, limit_price=0.60)
        # 50 @ 0.55 + 50 @ 0.57 = 27.5 + 28.5 = 56 ; avg = 0.56
        # mid = (0.55 + 0.50) / 2 = 0.525
        assert math.isclose(fill.avg_price, 0.56)
        assert math.isclose(fill.slippage, 0.56 - 0.525)


class TestEmptyBook:
    def test_no_fill_on_empty_ask_side(self):
        ob = book(asks=[], bids=[(0.50, 100)])
        fill = simulate_fill(ob, side=Side.BUY, size=50, limit_price=0.60)
        assert fill.filled_size == 0

    def test_no_fill_on_empty_bid_side(self):
        ob = book(asks=[(0.55, 100)], bids=[])
        fill = simulate_fill(ob, side=Side.SELL, size=50, limit_price=0.40)
        assert fill.filled_size == 0


class TestValidation:
    def test_negative_size_raises(self):
        ob = book(asks=[(0.55, 100)], bids=[(0.50, 100)])
        with pytest.raises(ValueError):
            simulate_fill(ob, side=Side.BUY, size=-1, limit_price=0.60)

    def test_invalid_limit_raises(self):
        ob = book(asks=[(0.55, 100)], bids=[(0.50, 100)])
        with pytest.raises(ValueError):
            simulate_fill(ob, side=Side.BUY, size=10, limit_price=1.5)
        with pytest.raises(ValueError):
            simulate_fill(ob, side=Side.BUY, size=10, limit_price=-0.1)
