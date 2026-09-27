"""Tests for the Black-Scholes model: validation, a known textbook value,
put-call parity (which must hold regardless of the specific inputs, so it's
a stronger check than any single hardcoded price), greeks scaling, and the
implied-volatility solver."""

import math

import pytest

from quant_hub.models.black_scholes import (
    IV_MAX_SIGMA,
    BlackScholesInputs,
    greeks,
    implied_volatility,
    no_arbitrage_bounds,
    price,
)


def make_inputs(**overrides):
    params = dict(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20, option_type="call")
    params.update(overrides)
    return BlackScholesInputs(**params)


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            {"spot": 0.0},
            {"spot": -5.0},
            {"strike": 0.0},
            {"dte": 0.0},
            {"dte": -1.0},
            {"sigma": 0.0},
            {"sigma": -0.1},
            {"option_type": "straddle"},
        ],
    )
    def test_invalid_inputs_rejected(self, bad):
        with pytest.raises(ValueError):
            make_inputs(**bad)


class TestPricing:
    def test_known_value_atm_one_year(self):
        # S=K=100, r=5%, sigma=20%, T=1y is a standard textbook example;
        # the call price is ~10.45.
        value = price(make_inputs(option_type="call"))
        assert value == pytest.approx(10.45, abs=0.05)

    def test_put_call_parity(self):
        # C - P = S - K*e^(-rT) holds for any valid inputs -- a property
        # check independent of any single hardcoded price.
        inputs = make_inputs(spot=87.0, strike=95.0, dte=120.0, rate=0.03, sigma=0.35)
        call = price(inputs)
        put = price(BlackScholesInputs(**{**vars(inputs), "option_type": "put"}))
        t = inputs.dte / 365.0
        assert (call - put) == pytest.approx(inputs.spot - inputs.strike * math.exp(-inputs.rate * t))


def _norm_pdf(x: float) -> float:
    return math.exp(-x**2 / 2) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1_d2(inputs: BlackScholesInputs) -> tuple[float, float]:
    t = inputs.dte / 365.0
    d1 = (math.log(inputs.spot / inputs.strike) + (inputs.rate + inputs.sigma**2 / 2) * t) / (
        inputs.sigma * math.sqrt(t)
    )
    return d1, d1 - inputs.sigma * math.sqrt(t)


class TestGreeks:
    # Reference values are the raw analytic Black-Scholes partial derivatives
    # (annual theta, per-100%-move vega/rho), recomputed independently of the
    # model here. `greeks()` is documented (black_scholes.py module
    # docstring) to report trader-convention scaling instead of the raw
    # derivatives, so these tests pin that scaling: theta per calendar day,
    # vega/rho per 1-percentage-point move.
    def test_delta_put_call_relationship(self):
        inputs = make_inputs(spot=87.0, strike=95.0, dte=120.0, rate=0.03, sigma=0.35)
        call = greeks(inputs)
        put = greeks(BlackScholesInputs(**{**vars(inputs), "option_type": "put"}))
        assert call["delta"] - put["delta"] == pytest.approx(1.0)

    def test_gamma_and_vega_identical_for_call_and_put(self):
        # Gamma and vega don't depend on option_type -- same underlying, same T.
        inputs = make_inputs(spot=87.0, strike=95.0, dte=120.0, rate=0.03, sigma=0.35)
        call = greeks(inputs)
        put = greeks(BlackScholesInputs(**{**vars(inputs), "option_type": "put"}))
        assert call["gamma"] == pytest.approx(put["gamma"])
        assert call["vega"] == pytest.approx(put["vega"])

    def test_vega_is_per_one_percent_vol_move(self):
        inputs = make_inputs(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20)
        d1, _ = _d1_d2(inputs)
        t = inputs.dte / 365.0
        raw_vega = inputs.spot * _norm_pdf(d1) * math.sqrt(t)
        assert greeks(inputs)["vega"] == pytest.approx(raw_vega / 100.0)

    def test_rho_is_per_one_percent_rate_move(self):
        inputs = make_inputs(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20, option_type="put")
        d1, d2 = _d1_d2(inputs)
        t = inputs.dte / 365.0
        raw_rho = -inputs.strike * t * math.exp(-inputs.rate * t) * _norm_cdf(-d2)
        assert greeks(inputs)["rho"] == pytest.approx(raw_rho / 100.0)

    def test_theta_is_per_calendar_day(self):
        inputs = make_inputs(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20)
        d1, d2 = _d1_d2(inputs)
        t = inputs.dte / 365.0
        raw_theta = -inputs.spot * _norm_pdf(d1) * inputs.sigma / (2 * math.sqrt(t)) - inputs.rate * inputs.strike * math.exp(
            -inputs.rate * t
        ) * _norm_cdf(d2)
        assert greeks(inputs)["theta"] == pytest.approx(raw_theta / 365.0)


class TestNoArbitrageBounds:
    @pytest.mark.parametrize("option_type", ["call", "put"])
    def test_bounds_are_the_sigma_limits(self, option_type):
        # The solver's existence argument: price -> lower bound as sigma -> 0
        # and -> upper bound as sigma -> infinity.
        contract = dict(spot=90.0, strike=100.0, dte=180.0, rate=0.04, option_type=option_type)
        lower, upper = no_arbitrage_bounds(**contract)
        assert price(BlackScholesInputs(sigma=1e-6, **contract)) == pytest.approx(lower, abs=1e-9)
        assert price(BlackScholesInputs(sigma=IV_MAX_SIGMA, **contract)) == pytest.approx(upper, rel=1e-3)


def _identified_grid():
    """Round-trip cases whose time value is resolvable in double precision.

    Far out of the money at low vol/short expiry the price underflows to its
    bound: no sigma is identified there, so those points are excluded.
    """
    cases = []
    for option_type in ("call", "put"):
        for moneyness in (0.6, 0.9, 1.0, 1.1, 1.5):
            for dte in (2.0, 30.0, 365.0, 1095.0):
                for sigma in (0.05, 0.25, 0.8, 2.5):
                    inputs = make_inputs(
                        strike=100.0 * moneyness, dte=dte, rate=0.03,
                        sigma=sigma, option_type=option_type,
                    )
                    lower, _ = no_arbitrage_bounds(
                        inputs.spot, inputs.strike, dte, inputs.rate, option_type
                    )
                    if price(inputs) - lower > 1e-9 * inputs.spot:
                        cases.append(inputs)
    return cases


_GRID = _identified_grid()


class TestImpliedVolatility:
    def test_grid_mostly_identified(self):
        # Guard against the filter silently emptying the round-trip test.
        assert len(_GRID) > 120

    @pytest.mark.parametrize("inputs", _GRID)
    def test_round_trip(self, inputs):
        target = price(inputs)
        iv = implied_volatility(
            target, inputs.spot, inputs.strike, inputs.dte, inputs.rate, inputs.option_type
        )
        # The price is always reproduced; sigma is recovered to a precision
        # set by how much the price moves per unit sigma (raw vega).
        assert price(BlackScholesInputs(**{**vars(inputs), "sigma": iv})) == pytest.approx(target, abs=1e-10)
        raw_vega = greeks(inputs)["vega"] * 100.0
        assert iv == pytest.approx(inputs.sigma, abs=max(1e-8, 1e-10 / raw_vega))

    def test_call_and_put_at_parity_share_one_volatility(self):
        spot, strike, dte, rate = 87.0, 95.0, 120.0, 0.03
        call = price(make_inputs(spot=spot, strike=strike, dte=dte, rate=rate, sigma=0.35))
        put = call - spot + strike * math.exp(-rate * dte / 365.0)
        assert implied_volatility(call, spot, strike, dte, rate, "call") == pytest.approx(
            implied_volatility(put, spot, strike, dte, rate, "put")
        )

    @pytest.mark.parametrize("option_type", ["call", "put"])
    def test_prices_on_or_outside_bounds_rejected(self, option_type):
        contract = dict(spot=100.0, strike=100.0, dte=30.0, rate=0.05, option_type=option_type)
        lower, upper = no_arbitrage_bounds(**contract)
        for bad in (lower, lower - 0.01, upper, upper + 0.01, math.nan):
            with pytest.raises(ValueError):
                implied_volatility(bad, **contract)

    @pytest.mark.parametrize(
        "bad",
        [{"spot": 0.0}, {"strike": -1.0}, {"dte": 0.0}, {"rate": math.inf}, {"option_type": "straddle"}],
    )
    def test_invalid_contract_rejected(self, bad):
        contract = dict(spot=100.0, strike=100.0, dte=30.0, rate=0.05, option_type="call")
        contract.update(bad)
        with pytest.raises(ValueError):
            implied_volatility(3.0, **contract)
