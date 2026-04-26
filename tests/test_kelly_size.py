"""Tests for kelly_size — quarter-Kelly position sizing."""
import math

import pytest
from kelly_size import kelly_fraction, kelly_size


class TestKellyFraction:
    def test_full_kelly_for_70pct_at_2to1_odds(self):
        # f* = (p*b - q) / b = (0.7*2 - 0.3) / 2 = 0.55
        assert math.isclose(kelly_fraction(p=0.7, b=2.0), 0.55, abs_tol=1e-9)

    def test_full_kelly_for_60pct_at_evens(self):
        # b=1, p=0.6, q=0.4 -> f* = (0.6 - 0.4) / 1 = 0.2
        assert math.isclose(kelly_fraction(p=0.6, b=1.0), 0.2, abs_tol=1e-9)

    def test_zero_when_no_edge(self):
        # b=1, p=0.5 -> f* = 0
        assert math.isclose(kelly_fraction(p=0.5, b=1.0), 0.0, abs_tol=1e-9)

    def test_clipped_to_zero_when_negative_edge(self):
        # b=1, p=0.4 -> f* = -0.2 -> clipped to 0
        assert kelly_fraction(p=0.4, b=1.0) == 0.0

    def test_full_kelly_at_certainty(self):
        # p=1 -> f* = (b + 0)/b = 1, the cap
        assert kelly_fraction(p=1.0, b=2.0) == 1.0


class TestKellySize:
    def test_pdf_example_quarter_kelly(self):
        # PDF: $10k bankroll, 70% confidence, 2:1 payoff, quarter-Kelly -> $1375
        # Full Kelly = 0.55, quarter = 0.1375 -> $1375 of bankroll
        # Plan claims $300; that figure assumes a different b. Verify the math:
        # If b=2.0, full Kelly = 0.55, quarter = 0.1375, dollar = $1375.
        size = kelly_size(p=0.7, b=2.0, bankroll=10_000, fraction=0.25)
        assert math.isclose(size, 1375.0, abs_tol=0.01)

    def test_quarter_kelly_default(self):
        size = kelly_size(p=0.6, b=1.0, bankroll=1000)
        # full Kelly = 0.2 -> quarter = 0.05 -> $50
        assert math.isclose(size, 50.0, abs_tol=1e-9)

    def test_no_position_when_no_edge(self):
        assert kelly_size(p=0.5, b=1.0, bankroll=1000) == 0.0

    def test_no_position_on_negative_edge(self):
        assert kelly_size(p=0.4, b=1.0, bankroll=1000) == 0.0

    def test_custom_fraction(self):
        # half-Kelly: f* = 0.2, half = 0.1, $100
        size = kelly_size(p=0.6, b=1.0, bankroll=1000, fraction=0.5)
        assert math.isclose(size, 100.0, abs_tol=1e-9)

    def test_polymarket_style_b_from_price(self):
        # If you buy YES at price 0.40 (decimal odds), payoff if win = (1-0.40)/0.40 = 1.5
        # With p_model=0.55, full Kelly = (0.55*1.5 - 0.45)/1.5 = 0.25
        # Quarter Kelly = 0.0625 -> $62.50 on $1000
        b = (1 - 0.40) / 0.40
        size = kelly_size(p=0.55, b=b, bankroll=1000, fraction=0.25)
        assert math.isclose(size, 62.5, abs_tol=0.01)

    def test_invalid_p_raises(self):
        with pytest.raises(ValueError):
            kelly_size(p=-0.1, b=1.0, bankroll=1000)
        with pytest.raises(ValueError):
            kelly_size(p=1.5, b=1.0, bankroll=1000)

    def test_invalid_b_raises(self):
        with pytest.raises(ValueError):
            kelly_size(p=0.6, b=0.0, bankroll=1000)
        with pytest.raises(ValueError):
            kelly_size(p=0.6, b=-1.0, bankroll=1000)

    def test_negative_bankroll_raises(self):
        with pytest.raises(ValueError):
            kelly_size(p=0.6, b=1.0, bankroll=-100)
