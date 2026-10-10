"""SVI: parameter validity, derivatives, the density formula (checked against
Breeden–Litzenberger), SSVI's no-arbitrage claims, and fit recovery."""

import math

import numpy as np
import pytest

from quant_hub.models.black_scholes import price_array
from quant_hub.models.svi import RawSVI, SSVISurface, fit_raw_svi

SURFACE = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)
EXPIRIES_YEARS = np.array([7, 14, 30, 60, 91, 182, 365, 730]) / 365.0


class TestRawSVI:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"b": -0.1},
            {"rho": 1.0},
            {"sigma": 0.0},
            {"a": -1.0},  # total variance would go negative
            {"b": 1.5, "rho": 0.5},  # b(1+|rho|) = 2.25 > Lee's bound
            {"m": math.nan},
        ],
    )
    def test_invalid_rejected(self, kwargs):
        base = dict(a=0.01, b=0.1, rho=-0.3, m=0.0, sigma=0.1)
        with pytest.raises(ValueError):
            RawSVI(**{**base, **kwargs})

    def test_minimum_and_wings(self):
        p = RawSVI(a=0.01, b=0.2, rho=-0.4, m=0.05, sigma=0.15)
        k_star = p.m - p.rho * p.sigma / math.sqrt(1 - p.rho**2)
        grid = np.linspace(-1, 1, 20001)
        assert grid[np.argmin(p.total_variance(grid))] == pytest.approx(k_star, abs=1e-3)
        assert float(p.total_variance(k_star)) == pytest.approx(p.min_variance)
        right = (p.total_variance(50.0) - p.total_variance(49.0))
        left = (p.total_variance(-50.0) - p.total_variance(-49.0))
        assert right == pytest.approx(p.b * (1 + p.rho), rel=1e-4)
        assert left == pytest.approx(p.b * (1 - p.rho), rel=1e-4)

    def test_derivatives_match_finite_differences(self):
        p = RawSVI(a=0.01, b=0.2, rho=-0.4, m=0.05, sigma=0.15)
        k = np.linspace(-0.6, 0.6, 13)
        h = 1e-5
        w1, w2 = p.derivatives(k)
        fd1 = (p.total_variance(k + h) - p.total_variance(k - h)) / (2 * h)
        fd2 = (p.total_variance(k + h) - 2 * p.total_variance(k) + p.total_variance(k - h)) / h**2
        assert w1 == pytest.approx(fd1, rel=1e-7)
        assert w2 == pytest.approx(fd2, rel=1e-4)

    def test_density_matches_breeden_litzenberger(self):
        # d²C/dK² = e^{-rT} p(k)/K for the density p of ln(S_T/F): an
        # independent check of the g(k) formula using only option prices.
        years, spot, rate = 0.25, 100.0, 0.03
        forward = spot * math.exp(rate * years)
        smile = SURFACE.slice(years)
        strikes = forward * np.exp(np.linspace(-0.3, 0.2, 11))
        h = 0.01

        def call(k_strike):
            vol = smile.implied_vol(np.log(k_strike / forward), years)
            return price_array(spot, k_strike, years * 365.0, rate, vol, "call")

        second = (call(strikes + h) - 2 * call(strikes) + call(strikes - h)) / h**2
        density = math.exp(-rate * years) * smile.risk_neutral_density(np.log(strikes / forward)) / strikes
        assert second == pytest.approx(density, rel=2e-3)

    def test_density_integrates_to_one(self):
        k = np.linspace(-3, 3, 60001)
        density = SURFACE.slice(0.5).risk_neutral_density(k)
        assert float(np.sum(density) * (k[1] - k[0])) == pytest.approx(1.0, abs=1e-4)


class TestSSVI:
    def test_slice_is_exact_raw_svi_with_atm_theta(self):
        k = np.linspace(-1, 1, 41)
        for years in EXPIRIES_YEARS:
            theta = SURFACE.atm_total_variance(years)
            phi = SURFACE.eta / math.sqrt(theta * (1 + theta))
            ssvi = 0.5 * theta * (1 + SURFACE.rho * phi * k
                                  + np.sqrt((phi * k + SURFACE.rho) ** 2 + 1 - SURFACE.rho**2))
            assert SURFACE.slice(years).total_variance(k) == pytest.approx(ssvi, rel=1e-12)
            assert float(SURFACE.slice(years).total_variance(0.0)) == pytest.approx(theta)

    def test_term_structure_limits(self):
        # ATM variance starts at v0 and relaxes to v_inf.
        assert SURFACE.atm_vol(1e-6) == pytest.approx(0.25, rel=1e-4)
        assert SURFACE.atm_vol(50.0) == pytest.approx(0.20, rel=5e-3)

    @pytest.mark.parametrize("rho,eta", [(-0.6, 1.2), (-0.9, 1.05), (0.3, 1.5), (0.0, 2.0)])
    def test_no_static_arbitrage_on_grid(self, rho, eta):
        surface = SSVISurface(v0=0.4**2, v_inf=0.18**2, kappa=4.0, rho=rho, eta=eta)
        k = np.linspace(-2.0, 2.0, 801)
        slices = [surface.slice(t) for t in EXPIRIES_YEARS]
        assert min(float(s.butterfly_g(k).min()) for s in slices) >= 0
        for front, back in zip(slices, slices[1:]):
            assert np.all(back.total_variance(k) >= front.total_variance(k))

    def test_invalid_rejected(self):
        with pytest.raises(ValueError):
            SSVISurface(v0=0.04, v_inf=0.04, kappa=1.0, rho=-0.5, eta=1.5)  # eta(1+|rho|) > 2
        with pytest.raises(ValueError):
            SSVISurface(v0=0.04, v_inf=0.04, kappa=0.0, rho=0.0, eta=1.0)


class TestFit:
    @pytest.mark.parametrize("dte", [7, 30, 365])
    def test_noiseless_recovery(self, dte):
        years = dte / 365.0
        true = SURFACE.slice(years)
        sd = math.sqrt(SURFACE.atm_total_variance(years))
        k = np.linspace(-2.5 * sd, 1.5 * sd, 21)
        w = true.total_variance(k)
        fit = fit_raw_svi(k, w, np.full_like(w, 1e-4 * w.mean()), years)
        p = fit.params
        assert fit.converged
        assert fit.rmse_vol < 1e-6
        assert (p.a, p.b, p.rho, p.m, p.sigma) == pytest.approx(
            (true.a, true.b, true.rho, true.m, true.sigma), rel=1e-3, abs=1e-6)

    def test_noisy_fit_is_close_in_vol_and_chi2_near_one(self):
        years = 60 / 365.0
        true = SURFACE.slice(years)
        sd = math.sqrt(SURFACE.atm_total_variance(years))
        k = np.linspace(-2.5 * sd, 1.5 * sd, 31)
        w_true = true.total_variance(k)
        dw = 0.01 * w_true
        rng = np.random.default_rng(3)
        fit = fit_raw_svi(k, w_true + dw * rng.standard_normal(len(k)), dw, years)
        # Chi-square per point with 5 fitted params on 31 points: mean ≈ 26/31.
        assert 0.3 < fit.chi2_per_point < 2.0
        grid_vol_error = fit.params.implied_vol(k, years) - true.implied_vol(k, years)
        assert np.max(np.abs(grid_vol_error)) < 0.005

    def test_rejects_bad_inputs(self):
        k = np.linspace(-0.1, 0.1, 8)
        w = np.full(8, 0.01)
        with pytest.raises(ValueError):
            fit_raw_svi(k[:5], w[:5], w[:5], 0.1)
        with pytest.raises(ValueError):
            fit_raw_svi(k, -w, w, 0.1)
        with pytest.raises(ValueError):
            fit_raw_svi(k, w, w, 0.0)
