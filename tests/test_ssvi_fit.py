"""Joint SSVI fit (C3), following ``models/ssvi_fit.py``'s validation plan:
exact recovery, the heavy-noise comparison with independent SVI, no static
arbitrage by construction, honest χ², and an optimizer that never scores
worse than the truth."""

import math

import numpy as np
import pytest

from quant_hub.models.ssvi_fit import (
    GAMMA_MAX,
    SSVIParameters,
    fit_joint_ssvi,
    total_variance,
)
from quant_hub.models.svi import SSVISurface, butterfly_g
from quant_hub.models.vol_surface import SyntheticChainSpec, fit_surface, synthetic_chain

TRUTH = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)


def chain(noise=0.5, spread=0.04, seed=3):
    return synthetic_chain(TRUTH, SyntheticChainSpec(noise=noise, relative_spread=spread,
                                                     seed=seed))


def iv_errors(fit) -> tuple[float, float]:
    """Median |IV error| over each slice's quoted range, and the worst ATM error."""
    errors = [np.abs(s.fit.params.implied_vol(k, s.years)
                     - TRUTH.slice(s.years).implied_vol(k, s.years))
              for s in fit.slices for k in [np.linspace(s.k_min, s.k_max, 81)]]
    worst_atm = max(abs(s.atm_vol - TRUTH.atm_vol(s.years)) for s in fit.slices)
    return float(np.median(np.concatenate(errors))), worst_atm


def chi2_at(fit, thetas, rho, eta, gamma) -> float:
    ivs, total = fit.ivs, 0.0
    for theta, dte in zip(thetas, fit.joint.dtes):
        mask = ivs.usable & (ivs.dte == dte)
        observed = ivs.iv[mask] ** 2 * dte / 365.0
        model = total_variance(ivs.k[mask], np.full(mask.sum(), theta), rho, eta, gamma)
        total += float(np.sum(((model - observed) / ivs.variance_uncertainty[mask]) ** 2))
    return total


class TestRecovery:
    def test_noise_free_chain_is_reproduced(self):
        fit = fit_surface(chain(noise=0.0), method="ssvi")
        params = fit.joint.params
        assert fit.method == "ssvi" and len(fit.slices) == 6
        assert params.rho == pytest.approx(TRUTH.rho, abs=1e-3)
        assert params.eta == pytest.approx(TRUTH.eta, abs=1e-3)
        assert params.gamma == pytest.approx(0.5, abs=0.02)
        for theta, s in zip(params.thetas, fit.slices):
            assert theta == pytest.approx(TRUTH.atm_total_variance(s.years), rel=1e-6)
            k = np.linspace(s.k_min, s.k_max, 81)
            assert np.max(np.abs(s.fit.params.implied_vol(k, s.years)
                                 - TRUTH.slice(s.years).implied_vol(k, s.years))) < 1e-4

    def test_gamma_one_half_slices_match_the_synthetic_truth(self):
        params = SSVIParameters((TRUTH.atm_total_variance(0.25),), TRUTH.rho, TRUTH.eta, 0.5)
        mine, theirs = params.slice(0), TRUTH.slice(0.25)
        for name in ("a", "b", "rho", "m", "sigma"):
            assert getattr(mine, name) == pytest.approx(getattr(theirs, name), rel=1e-12)


class TestHeavyNoise:
    @pytest.fixture(scope="class")
    def fits(self):
        # The D30 case: 40% spreads and 2x noise, where independent slices overfit.
        return [(fit_surface(c, method="svi"), fit_surface(c, method="ssvi"))
                for c in (chain(noise=2.0, spread=0.40, seed=s) for s in range(3))]

    def test_joint_fit_tracks_the_truth_better(self, fits):
        # Measured over 8 seeds: median IV error 1.12 -> 0.69 vol points,
        # worst ATM error 4.62 -> 1.55, better on every seed.
        svi = np.array([iv_errors(a) for a, _ in fits])
        ssvi = np.array([iv_errors(b) for _, b in fits])
        assert ssvi[:, 0].mean() < 0.8 * svi[:, 0].mean()
        assert ssvi[:, 1].mean() < 0.6 * svi[:, 1].mean()

    def test_never_worse_than_the_truth(self, fits):
        # The check that caught the stalled N+3-dimensional search.
        for _, fit in fits:
            j = fit.joint
            at_fit = chi2_at(fit, j.params.thetas, j.params.rho, j.params.eta, j.params.gamma)
            at_truth = chi2_at(fit, [TRUTH.atm_total_variance(d / 365.0) for d in j.dtes],
                               TRUTH.rho, TRUTH.eta, 0.5)
            assert at_fit <= at_truth * (1 + 1e-9)
            assert at_fit / j.n_points == pytest.approx(j.chi2_per_point)

    def test_joint_fits_pass_the_arbitrage_checks(self, fits):
        for _, fit in fits:
            assert fit.butterfly_free and fit.calendar_free


class TestNoStaticArbitrage:
    def test_every_point_of_the_parameter_space_is_arbitrage_free(self):
        rng = np.random.default_rng(0)
        k = np.linspace(-3.0, 3.0, 4001)
        for _ in range(200):
            rho = math.tanh(rng.normal())
            eta = 2.0 / (1 + abs(rho)) * rng.uniform(0.01, 1.0)
            gamma = GAMMA_MAX * rng.uniform(0.01, 1.0)
            thetas = np.cumsum(rng.uniform(1e-4, 0.8, 5))
            params = SSVIParameters(tuple(thetas), rho, eta, gamma)
            previous = None
            for i in range(len(thetas)):
                smile = params.slice(i)
                assert smile.butterfly_g(k).min() >= 0
                w = smile.total_variance(k)
                if previous is not None:
                    assert np.min(w - previous) >= -1e-12
                previous = w

    def test_slices_agree_with_the_ssvi_formula(self):
        params = SSVIParameters((0.01, 0.03), -0.5, 1.1, 0.35)
        k = np.linspace(-1, 1, 11)
        for i, theta in enumerate(params.thetas):
            direct = total_variance(k, np.full(len(k), theta), -0.5, 1.1, 0.35)
            assert np.allclose(params.slice(i).total_variance(k), direct, rtol=1e-12)
            w1, w2 = params.slice(i).derivatives(k)
            assert np.allclose(params.slice(i).butterfly_g(k),
                               butterfly_g(k, direct, w1, w2), rtol=1e-12)


class TestCalibration:
    def test_chi2_per_point_matches_the_noise(self):
        # Measured over 8 seeds: chi2/pt / (noise^2 (n-p)/n) averages 0.96.
        ratios = []
        for seed in (0, 4):
            j = fit_surface(chain(noise=0.5, seed=seed), method="ssvi").joint
            ratios.append(j.chi2_per_point / 0.25 * j.n_points / (j.n_points - j.n_parameters))
        assert 0.7 < float(np.mean(ratios)) < 1.3


class TestValidation:
    @pytest.mark.parametrize("kwargs", [
        {"thetas": (0.02, 0.01)},                      # decreasing: calendar arbitrage
        {"thetas": ()},
        {"rho": 1.0},
        {"gamma": 0.6},
        {"gamma": 0.0},
        {"eta": 1.5, "rho": -0.6},                     # eta(1+|rho|) > 2
    ])
    def test_invalid_parameters_rejected(self, kwargs):
        base = {"thetas": (0.01, 0.02), "rho": -0.5, "eta": 1.0, "gamma": 0.4}
        with pytest.raises(ValueError):
            SSVIParameters(**{**base, **kwargs})

    def test_invalid_fit_inputs_rejected(self):
        k, w, dw = np.array([-0.1, 0.0, 0.1]), np.array([0.01, 0.009, 0.01]), np.full(3, 1e-3)
        with pytest.raises(ValueError):
            fit_joint_ssvi([30.0], [k], [w], [dw])                 # 3 quotes <= 1 + 3
        with pytest.raises(ValueError):
            fit_joint_ssvi([60.0, 30.0], [k, k], [w, w], [dw, dw])  # not ascending
        with pytest.raises(ValueError):
            fit_joint_ssvi([30.0], [k], [w * np.nan], [dw])
        with pytest.raises(ValueError):
            fit_surface(chain(), method="spline")
