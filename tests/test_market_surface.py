"""Market surface (C4): a fitted chain as (sigma, r, q) at any strike and
expiry, consistent with the chain; and strategy legs priced from it."""

import math

import numpy as np
import pytest

from quant_hub.models.black_scholes import MarketInputs, price_array
from quant_hub.models.carry import implied_carry
from quant_hub.models.market_surface import MarketSurface, apply_surface
from quant_hub.models.strategy import OptionLeg, Strategy, leg_inputs, strategy_value
from quant_hub.models.svi import SSVISurface
from quant_hub.models.vol_surface import SyntheticChainSpec, fit_surface, synthetic_chain

TRUTH = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
SPEC = SyntheticChainSpec(rate=0.045, dividend_yield=0.015, noise=0.0, parity_pairs=5)


@pytest.fixture(scope="module")
def surface() -> MarketSurface:
    return MarketSurface(fit_surface(implied_carry(synthetic_chain(TRUTH, SPEC)).chain))


class TestOnTheFittedExpiries:
    def test_points_reproduce_the_truth_and_the_carry(self, surface):
        for s in surface.fit.slices:
            forward = SPEC.spot * math.exp((SPEC.rate - SPEC.dividend_yield) * s.years)
            for strike in (0.9 * forward, forward, 1.05 * forward):
                p = surface.point(strike, s.dte)
                true_iv = float(TRUTH.slice(s.years).implied_vol(math.log(strike / forward),
                                                                 s.years))
                assert p.forward == pytest.approx(forward, rel=1e-9)
                assert p.rate == pytest.approx(SPEC.rate, abs=1e-9)
                assert p.dividend_yield == pytest.approx(SPEC.dividend_yield, abs=1e-9)
                assert p.vol == pytest.approx(true_iv, abs=2e-4)

    def test_pricing_from_the_surface_reproduces_the_quotes(self, surface):
        chain, ivs = surface.fit.chain, surface.fit.ivs
        for i, q in enumerate(chain.quotes):
            if not ivs.usable[i]:
                continue
            p = surface.point(q.strike, q.dte)
            model = float(price_array(surface.spot, q.strike, q.dte, p.rate, p.vol,
                                      q.option_type, p.dividend_yield))
            assert model == pytest.approx(q.mid, abs=max(0.01 * q.half_spread, 2e-4))


class TestBetweenAndBeyond:
    def test_total_variance_is_linear_in_time_at_fixed_moneyness(self, surface):
        front, back = surface.fit.slices[2], surface.fit.slices[3]
        years = 0.3 * front.years + 0.7 * back.years
        lam = (back.years - years) / (back.years - front.years)
        for k in (-0.1, 0.0, 0.05):
            forward = surface.point(100.0, years * 365).forward
            p = surface.point(forward * math.exp(k), years * 365)
            expected = (lam * float(front.fit.params.total_variance(k))
                        + (1 - lam) * float(back.fit.params.total_variance(k)))
            assert p.vol**2 * years == pytest.approx(expected, rel=1e-10)
            assert not p.extrapolated

    def test_carry_interpolates_linearly_in_time(self, surface):
        front, back = surface.fit.slices[0], surface.fit.slices[1]
        years = 0.5 * (front.years + back.years)
        p = surface.point(100.0, years * 365)
        carries = [math.log(s.forward / surface.spot) / s.years for s in (front, back)]
        assert math.log(p.forward / surface.spot) / years == pytest.approx(np.mean(carries))

    @pytest.mark.parametrize("which, factor", [(0, 0.5), (-1, 1.5)])
    def test_vol_is_held_flat_outside_the_fitted_expiries(self, surface, which, factor):
        edge = surface.fit.slices[which]
        dte = edge.dte * factor
        for k in (-0.05, 0.0):
            at_edge = surface.point(edge.forward * math.exp(k), edge.dte)
            beyond = surface.point(surface.point(100.0, dte).forward * math.exp(k), dte)
            assert beyond.vol == pytest.approx(at_edge.vol, rel=1e-10)
            assert beyond.extrapolated

    def test_far_wing_strikes_are_flagged(self, surface):
        s = surface.fit.slices[2]
        assert not surface.point(s.forward, s.dte).extrapolated
        assert surface.point(s.forward * math.exp(s.k_min - 0.5), s.dte).extrapolated

    def test_invalid_requests_rejected(self, surface):
        with pytest.raises(ValueError):
            surface.point(0.0, 30.0)
        with pytest.raises(ValueError):
            surface.point(100.0, -1.0)


class TestStrategiesOnTheSurface:
    def test_legs_take_the_surface_inputs(self, surface):
        strategy = Strategy((OptionLeg("put", 95.0, 60.0, -1), OptionLeg("call", 105.0, 91.0, 1)))
        priced = apply_surface(strategy, surface)
        market = MarketInputs(surface.spot, 0.0, 0.5, 0.0)    # deliberately wrong flat inputs
        value = 0.0
        for leg in priced.legs:
            p = surface.point(leg.strike, leg.dte)
            assert leg_inputs(leg, market) == (p.vol, p.rate, p.dividend_yield)
            value += leg.quantity * priced.multiplier * float(price_array(
                surface.spot, leg.strike, leg.dte, p.rate, p.vol, leg.option_type,
                p.dividend_yield))
        got = strategy_value(priced, market, np.array([surface.spot]))[0]
        assert got == pytest.approx(value, rel=1e-12)

    def test_legs_without_overrides_use_the_market(self):
        leg = OptionLeg("call", 100.0, 30.0, 1)
        market = MarketInputs(100.0, 0.03, 0.2, 0.01)
        assert leg_inputs(leg, market) == (0.2, 0.03, 0.01)

    @pytest.mark.parametrize("kwargs", [{"vol": 0.0}, {"vol": math.nan}, {"rate": math.inf}])
    def test_invalid_leg_inputs_rejected(self, kwargs):
        with pytest.raises(ValueError):
            OptionLeg("call", 100.0, 30.0, 1, **kwargs)

    def test_a_surface_needs_a_fitted_expiry(self, surface):
        from dataclasses import replace
        with pytest.raises(ValueError):
            MarketSurface(replace(surface.fit, slices=(), calendar_gaps=()))
