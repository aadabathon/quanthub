"""Tests for the Black-Scholes calculator: validation, a known textbook
value, and put-call parity (which must hold regardless of the specific
inputs, so it's a stronger check than any single hardcoded price)."""

import math

import pytest

from quant_hub.services.Options.options_service import BlackScholesInputs, OptionService


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
        service = OptionService()
        price = service.price(make_inputs(option_type="call"))
        assert price == pytest.approx(10.45, abs=0.05)

    def test_put_call_parity(self):
        # C - P = S - K*e^(-rT) holds for any valid inputs -- a property
        # check independent of any single hardcoded price.
        service = OptionService()
        inputs = make_inputs(spot=87.0, strike=95.0, dte=120.0, rate=0.03, sigma=0.35)
        call = service.price(inputs)
        put = service.price(BlackScholesInputs(**{**vars(inputs), "option_type": "put"}))
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
    # service here. `greeks()` is documented (options_service.py module
    # docstring) to report trader-convention scaling instead of the raw
    # derivatives, so these tests pin that scaling: theta per calendar day,
    # vega/rho per 1-percentage-point move.
    def test_delta_put_call_relationship(self):
        service = OptionService()
        inputs = make_inputs(spot=87.0, strike=95.0, dte=120.0, rate=0.03, sigma=0.35)
        call = service.greeks(inputs)
        put = service.greeks(BlackScholesInputs(**{**vars(inputs), "option_type": "put"}))
        assert call["delta"] - put["delta"] == pytest.approx(1.0)

    def test_gamma_and_vega_identical_for_call_and_put(self):
        # Gamma and vega don't depend on option_type -- same underlying, same T.
        service = OptionService()
        inputs = make_inputs(spot=87.0, strike=95.0, dte=120.0, rate=0.03, sigma=0.35)
        call = service.greeks(inputs)
        put = service.greeks(BlackScholesInputs(**{**vars(inputs), "option_type": "put"}))
        assert call["gamma"] == pytest.approx(put["gamma"])
        assert call["vega"] == pytest.approx(put["vega"])

    def test_vega_is_per_one_percent_vol_move(self):
        service = OptionService()
        inputs = make_inputs(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20)
        d1, _ = _d1_d2(inputs)
        t = inputs.dte / 365.0
        raw_vega = inputs.spot * _norm_pdf(d1) * math.sqrt(t)
        assert service.greeks(inputs)["vega"] == pytest.approx(raw_vega / 100.0)

    def test_rho_is_per_one_percent_rate_move(self):
        service = OptionService()
        inputs = make_inputs(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20, option_type="put")
        d1, d2 = _d1_d2(inputs)
        t = inputs.dte / 365.0
        raw_rho = -inputs.strike * t * math.exp(-inputs.rate * t) * _norm_cdf(-d2)
        assert service.greeks(inputs)["rho"] == pytest.approx(raw_rho / 100.0)

    def test_theta_is_per_calendar_day(self):
        service = OptionService()
        inputs = make_inputs(spot=100.0, strike=100.0, dte=365.0, rate=0.05, sigma=0.20)
        d1, d2 = _d1_d2(inputs)
        t = inputs.dte / 365.0
        raw_theta = -inputs.spot * _norm_pdf(d1) * inputs.sigma / (2 * math.sqrt(t)) - inputs.rate * inputs.strike * math.exp(
            -inputs.rate * t
        ) * _norm_cdf(d2)
        assert service.greeks(inputs)["theta"] == pytest.approx(raw_theta / 365.0)
