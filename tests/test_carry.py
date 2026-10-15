"""Parity-implied carry (C1b), following ``models/carry.py``'s validation
plan: exact recovery without noise, honest standard errors with it, the
per-expiry rule's branches, and the downstream fix to implied vols."""

import math
from dataclasses import replace

import numpy as np
import pytest

from quant_hub.data.options import ExpiryCarry, OptionChain, OptionQuote
from quant_hub.models.carry import (
    FIXED_RATE,
    NONE,
    REGRESSION,
    fit_expiry_carry,
    implied_carry,
    parity_pairs,
)
from quant_hub.models.svi import SSVISurface
from quant_hub.models.vol_surface import SyntheticChainSpec, chain_implied_vols, synthetic_chain

TRUTH = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
# Two-sided 97.5% Student-t quantiles: the scale is estimated from the
# residuals, so small samples need t, not 1.96.
T975 = {4: 2.776, 13: 2.160}


def true_carry(spec: SyntheticChainSpec, dte: float) -> tuple[float, float]:
    years = dte / 365.0
    return (spec.spot * math.exp((spec.rate - spec.dividend_yield) * years),
            math.exp(-spec.rate * years))


def simulated_pairs(rng, forward, discount, strikes, half, noise):
    """y = D(F - K) plus mid noise of sd noise × half on each side."""
    y = discount * (forward - strikes)
    y = y + noise * half * (rng.standard_normal(len(strikes)) - rng.standard_normal(len(strikes)))
    return y, np.full(len(strikes), 1.0 / (2 * half**2))


class TestRecovery:
    def test_noise_free_chain_recovers_forward_and_discount(self):
        spec = SyntheticChainSpec(rate=0.045, dividend_yield=0.018, noise=0.0, parity_pairs=5)
        fit = implied_carry(synthetic_chain(TRUTH, spec))
        assert len(fit.expiries) == len(spec.expiries)
        for e in fit.expiries:
            forward, discount = true_carry(spec, e.dte)
            assert e.method == REGRESSION and e.pairs == 5
            assert e.forward == pytest.approx(forward, abs=1e-9)
            assert e.discount == pytest.approx(discount, abs=1e-11)
            assert fit.chain.forward(e.dte) == pytest.approx(forward, abs=1e-9)

    def test_regression_intervals_cover_at_the_stated_rate(self):
        rng = np.random.default_rng(11)
        forward, discount, years = 101.0, math.exp(-0.04), 1.0
        strikes = np.linspace(91.0, 111.0, 15)
        hits_f = hits_d = 0
        draws = 2000
        for _ in range(draws):
            y, w = simulated_pairs(rng, forward, discount, strikes, half=0.02, noise=1.0)
            fit = fit_expiry_carry(365.0 * years, strikes, y, w, flat_rate=0.03)
            assert fit.method == REGRESSION and fit.dof == 13
            hits_f += abs(fit.forward - forward) <= T975[13] * fit.forward_se
            hits_d += abs(fit.discount - discount) <= T975[13] * fit.discount_se
        # Binomial sd at 95% over 2000 draws is about 0.005.
        assert hits_f / draws == pytest.approx(0.95, abs=0.02)
        assert hits_d / draws == pytest.approx(0.95, abs=0.02)

    def test_fixed_rate_intervals_cover_at_the_stated_rate(self):
        rng = np.random.default_rng(12)
        forward, discount = 100.4, math.exp(-0.04 * 30 / 365)
        strikes = np.array([99.0, 100.0, 101.0, 102.0, 103.0])
        hits = 0
        draws = 2000
        for _ in range(draws):
            y, w = simulated_pairs(rng, forward, discount, strikes, half=0.05, noise=1.0)
            fit = fit_expiry_carry(30.0, strikes, y, w, flat_rate=0.04)
            assert fit.method == FIXED_RATE          # five close strikes can't pin a rate
            assert fit.dof == 4
            hits += abs(fit.forward - forward) <= T975[4] * fit.forward_se
        assert hits / draws == pytest.approx(0.95, abs=0.02)

    def test_short_expiry_takes_the_fixed_rate_branch_and_still_finds_the_forward(self):
        spec = SyntheticChainSpec(rate=0.045, dividend_yield=0.018, noise=0.5, parity_pairs=5,
                                  seed=3, expiries=(7,))
        (e,) = implied_carry(synthetic_chain(TRUTH, spec)).expiries
        forward, discount = true_carry(spec, 7)
        assert e.method == FIXED_RATE and e.rate == spec.rate
        assert e.discount == pytest.approx(discount)
        assert abs(e.forward - forward) < 4 * e.forward_se


class TestDownstream:
    def test_implied_carry_repairs_a_wrong_dividend_yield(self):
        # Quotes come from q = 3%, but the chain claims q = 0: its flat
        # forward is off, which skews every implied vol. Parity reads the
        # right forward back off the quotes.
        spec = SyntheticChainSpec(rate=0.04, dividend_yield=0.03, noise=0.2, parity_pairs=5,
                                  seed=5)
        wrong = replace(synthetic_chain(TRUTH, spec), dividend_yield=0.0)
        fixed = implied_carry(wrong).chain

        def iv_errors(chain: OptionChain) -> np.ndarray:
            ivs = chain_implied_vols(chain)
            errors = []
            for i, q in enumerate(chain.quotes):
                if not ivs.usable[i]:
                    continue
                forward, _ = true_carry(spec, q.dte)
                years = q.dte / 365.0
                true_iv = float(TRUTH.slice(years).implied_vol(math.log(q.strike / forward), years))
                errors.append(abs(ivs.iv[i] - true_iv))
            return np.array(errors)

        flat, implied = np.median(iv_errors(wrong)), np.median(iv_errors(fixed))
        assert flat > 0.005
        assert implied < flat / 5


def quote(kind, strike, bid, ask, dte=30.0):
    return OptionQuote(kind, strike, dte, bid, ask)


def small_chain(quotes, **kwargs):
    return OptionChain("X", "test", __import__("datetime").datetime(2026, 10, 15), 100.0, 0.04,
                       0.0, tuple(quotes), **kwargs)


class TestRuleBranches:
    def test_no_pairs_keeps_the_flat_carry(self):
        chain = small_chain([quote("call", 105, 1.0, 1.1), quote("put", 95, 0.9, 1.0)])
        fit = implied_carry(chain)
        assert fit.expiries[0].method == NONE and fit.expiries[0].pairs == 0
        assert 30.0 not in fit.chain.carry
        assert fit.chain.forward(30.0) == chain.forward(30.0)

    def test_one_pair_uses_the_half_spreads_as_uncertainty(self):
        chain = small_chain([quote("call", 100, 2.0, 2.2), quote("put", 100, 1.6, 1.8)])
        (e,) = implied_carry(chain).expiries
        discount = math.exp(-0.04 * 30 / 365)
        assert e.method == FIXED_RATE and e.pairs == 1
        assert e.forward == pytest.approx(100 + 0.4 / discount)
        assert e.forward_se == pytest.approx(math.sqrt(0.1**2 + 0.1**2) / discount)

    def test_only_two_sided_pairs_count_and_the_nearest_are_used(self):
        quotes = [quote("call", k, 1.0, 1.2) for k in (90, 95, 100, 105, 110, 115)]
        quotes += [quote("put", k, 1.0, 1.2) for k in (90, 95, 100, 105, 110)]
        quotes += [quote("put", 115, 0.0, 0.1)]                 # no bid: not a pair
        quotes += [quote("call", 120, 1.0, 1.2)]                 # no put: not a pair
        strikes, _, _ = parity_pairs(small_chain(quotes), 30.0, max_pairs=3)
        assert strikes.tolist() == [95.0, 100.0, 105.0]
        strikes, _, _ = parity_pairs(small_chain(quotes), 30.0, max_pairs=10)
        assert strikes.tolist() == [90.0, 95.0, 100.0, 105.0, 110.0]

    def test_implausible_rate_or_negative_discount_falls_back(self):
        strikes = np.array([95.0, 100.0, 105.0])
        weights = np.ones(3)
        rising = fit_expiry_carry(30.0, strikes, np.array([1.0, 2.0, 3.0]), weights, 0.04)
        assert rising.method == FIXED_RATE                      # slope > 0: D < 0
        steep = fit_expiry_carry(365.0, strikes, 0.5 * (100.0 - strikes), weights, 0.04)
        assert steep.method == FIXED_RATE                       # D = 0.5: r = 69%
        assert steep.discount == pytest.approx(math.exp(-0.04))

    def test_existing_carry_is_kept_for_unfitted_expiries(self):
        chain = small_chain([quote("call", 105, 1.0, 1.1, dte=60.0)],
                            carry={60.0: ExpiryCarry(101.0, 0.99)})
        assert implied_carry(chain).chain.carry[60.0] == ExpiryCarry(101.0, 0.99)

    @pytest.mark.parametrize("args", [
        (30.0, [100.0, 101.0], [1.0], [1.0, 1.0], 0.04),
        (30.0, [100.0], [math.nan], [1.0], 0.04),
        (30.0, [100.0], [1.0], [0.0], 0.04),
        (0.0, [100.0], [1.0], [1.0], 0.04),
    ])
    def test_invalid_inputs_rejected(self, args):
        with pytest.raises(ValueError):
            fit_expiry_carry(*args)

    def test_max_pairs_must_be_positive(self):
        with pytest.raises(ValueError):
            implied_carry(small_chain([quote("call", 100, 1.0, 1.1)]), max_pairs=0)
