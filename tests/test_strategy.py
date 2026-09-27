"""Tests for multi-leg strategies: closed-form payoff facts, exact expiry
analysis vs grid sampling, greek linearity, and horizon valuation."""

import math

import numpy as np
import pytest

from quant_hub.models.black_scholes import BlackScholesInputs, MarketInputs, price
from quant_hub.models.strategy import (
    STRATEGY_TEMPLATES,
    OptionLeg,
    Strategy,
    entry_cost,
    expiry_profile,
    grid_breakevens,
    nearest_listed_strike,
    spot_grid,
    strategy_greeks,
    strategy_pnl,
    strategy_value,
)

MARKET = MarketInputs(spot=100.0, rate=0.04, sigma=0.25, dividend_yield=0.01)


def bs(option_type, strike, dte, spot=100.0):
    return price(BlackScholesInputs(spot, strike, dte, 0.04, 0.25, option_type, 0.01))


class TestEntryAndValue:
    def test_fair_entry_has_zero_pnl_at_entry(self):
        for name, build in STRATEGY_TEMPLATES.items():
            strategy = build(100.0, 30.0, 0.05)
            pnl = strategy_pnl(strategy, MARKET, np.array([100.0]), 0.0)
            assert pnl[0] == pytest.approx(0.0, abs=1e-9), name

    def test_recorded_premium_overrides_fair_value(self):
        strategy = Strategy((OptionLeg("call", 100.0, 30.0, 2, premium=3.0),))
        assert entry_cost(strategy, MARKET) == pytest.approx(2 * 100 * 3.0)

    def test_value_is_signed_sum_of_leg_prices(self):
        strategy = Strategy(
            (OptionLeg("call", 105.0, 45.0, -1), OptionLeg("put", 95.0, 45.0, 3)),
            stock_quantity=50.0,
        )
        got = strategy_value(strategy, MARKET, np.array([100.0]), 0.0)[0]
        expected = 100 * (-bs("call", 105.0, 45.0) + 3 * bs("put", 95.0, 45.0)) + 50 * 100.0
        assert got == pytest.approx(expected)

    def test_expired_legs_settle_at_intrinsic(self):
        strategy = Strategy((OptionLeg("put", 100.0, 10.0, 1),))
        spots = np.array([80.0, 100.0, 120.0])
        assert strategy_value(strategy, MARKET, spots, 10.0) == pytest.approx([2000.0, 0.0, 0.0])
        assert strategy_value(strategy, MARKET, spots, 25.0) == pytest.approx([2000.0, 0.0, 0.0])


class TestExpiryProfile:
    def test_long_straddle(self):
        strategy = Strategy((OptionLeg("call", 100.0, 30.0, 1), OptionLeg("put", 100.0, 30.0, 1)))
        premium = bs("call", 100.0, 30.0) + bs("put", 100.0, 30.0)
        profile = expiry_profile(strategy, MARKET)
        assert profile.max_profit == math.inf
        assert profile.max_loss == pytest.approx(-100 * premium)
        assert profile.breakevens == pytest.approx((100.0 - premium, 100.0 + premium))

    def test_bull_call_spread(self):
        strategy = Strategy((OptionLeg("call", 100.0, 30.0, 1), OptionLeg("call", 110.0, 30.0, -1)))
        debit = bs("call", 100.0, 30.0) - bs("call", 110.0, 30.0)
        profile = expiry_profile(strategy, MARKET)
        assert profile.max_profit == pytest.approx(100 * (10.0 - debit))
        assert profile.max_loss == pytest.approx(-100 * debit)
        assert profile.breakevens == pytest.approx((100.0 + debit,))

    def test_iron_condor_is_bounded_both_ways(self):
        strategy = STRATEGY_TEMPLATES["Iron condor"](100.0, 30.0, 0.05)
        credit = -entry_cost(strategy, MARKET)
        profile = expiry_profile(strategy, MARKET)
        assert credit > 0
        assert profile.max_profit == pytest.approx(credit)
        assert profile.max_loss == pytest.approx(credit - 100 * 5.0)
        assert len(profile.breakevens) == 2

    def test_covered_call_loss_is_finite(self):
        strategy = STRATEGY_TEMPLATES["Covered call"](100.0, 30.0, 0.05)
        call = bs("call", 105.0, 30.0)
        profile = expiry_profile(strategy, MARKET)
        assert profile.max_profit == pytest.approx(100 * (105.0 - 100.0 + call))
        assert profile.max_loss == pytest.approx(-100 * (100.0 - call))
        assert profile.breakevens == pytest.approx((100.0 - call,))

    def test_short_call_loss_is_unbounded(self):
        profile = expiry_profile(Strategy((OptionLeg("call", 100.0, 30.0, -1),)), MARKET)
        assert profile.max_loss == -math.inf

    def test_matches_dense_grid(self):
        # The exact piecewise-linear analysis must agree with brute force.
        for name in ("Long strangle", "Iron condor", "Call butterfly", "Bear put spread"):
            strategy = STRATEGY_TEMPLATES[name](100.0, 30.0, 0.06)
            spots = np.linspace(0.0, 200.0, 200_001)
            pnl = strategy_pnl(strategy, MARKET, spots, 30.0)
            profile = expiry_profile(strategy, MARKET)
            assert grid_breakevens(spots, pnl) == pytest.approx(profile.breakevens, abs=1e-3), name
            if math.isfinite(profile.max_profit):
                assert pnl.max() == pytest.approx(profile.max_profit, abs=1e-6), name
            if math.isfinite(profile.max_loss):
                assert pnl.min() == pytest.approx(profile.max_loss, abs=1e-6), name

    def test_mixed_expiries_rejected(self):
        with pytest.raises(ValueError):
            expiry_profile(STRATEGY_TEMPLATES["Call calendar"](100.0, 30.0, 0.05), MARKET)


class TestGreeks:
    def test_position_delta_matches_finite_difference(self):
        strategy = Strategy(
            (OptionLeg("call", 100.0, 30.0, -2), OptionLeg("put", 90.0, 60.0, 1)),
            stock_quantity=150.0,
        )
        spots = np.array([85.0, 100.0, 115.0])
        h = 1e-3
        fd = (
            strategy_value(strategy, MARKET, spots + h, 5.0)
            - strategy_value(strategy, MARKET, spots - h, 5.0)
        ) / (2 * h)
        assert strategy_greeks(strategy, MARKET, spots, 5.0).delta == pytest.approx(fd, rel=1e-6)

    def test_stock_only_delta_is_share_count(self):
        g = strategy_greeks(Strategy(stock_quantity=-40.0), MARKET, np.array([90.0, 110.0]))
        assert g.delta == pytest.approx([-40.0, -40.0])
        assert g.gamma == pytest.approx([0.0, 0.0])

    def test_calendar_after_front_expiry_keeps_back_leg_greeks(self):
        strategy = STRATEGY_TEMPLATES["Call calendar"](100.0, 30.0, 0.05)
        g = strategy_greeks(strategy, MARKET, np.array([100.0]), 30.0)
        assert g.vega[0] > 0 and g.theta[0] < 0


class TestHelpers:
    def test_spot_grid_covers_spot_and_strikes(self):
        strategy = Strategy((OptionLeg("put", 40.0, 7.0, 1),))
        grid = spot_grid(strategy, MARKET)
        assert grid[0] <= 36.0 and grid[-1] >= 100.0

    @pytest.mark.parametrize(
        "value,spot,expected",
        [(103.2, 100.0, 105.0), (51.3, 50.0, 51.5), (447.0, 450.0, 445.0), (0.01, 100.0, 5.0)],
    )
    def test_nearest_listed_strike(self, value, spot, expected):
        assert nearest_listed_strike(value, spot) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "bad",
        [
            lambda: OptionLeg("straddle", 100.0, 30.0, 1),
            lambda: OptionLeg("call", 0.0, 30.0, 1),
            lambda: OptionLeg("call", 100.0, 0.0, 1),
            lambda: OptionLeg("call", 100.0, 30.0, 0),
            lambda: OptionLeg("call", 100.0, 30.0, 1, premium=-1.0),
            lambda: Strategy(),
            lambda: Strategy(stock_quantity=10.0, multiplier=0.0),
            lambda: STRATEGY_TEMPLATES["Long call"](100.0, 30.0, 1.5),
            lambda: strategy_value(Strategy(stock_quantity=1.0), MARKET, np.array([-1.0])),
            lambda: strategy_value(Strategy(stock_quantity=1.0), MARKET, np.array([1.0]), -2.0),
        ],
    )
    def test_invalid_rejected(self, bad):
        with pytest.raises(ValueError):
            bad()
