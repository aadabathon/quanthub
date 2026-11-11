"""Chain pipeline: synthetic quoting, IV inversion + weights, surface fit vs
the known SSVI truth, arbitrage diagnostics; plus the chain contract and the
GARCH forecast term structure."""

import math
from datetime import datetime

import numpy as np
import pytest

from quant_hub.data.options import OptionChain, OptionQuote
from quant_hub.models.garch import (
    GARCH11Parameters, average_variance_forecast, next_variance,
)
from quant_hub.models.svi import RawSVI, SSVISurface
from quant_hub.models.vol_surface import (
    SyntheticChainSpec, chain_implied_vols, fit_surface, surface_grid, surface_mesh,
    synthetic_chain,
)

SURFACE = SSVISurface(v0=0.25**2, v_inf=0.20**2, kappa=2.0, rho=-0.6, eta=1.2)


class TestChainContract:
    @pytest.mark.parametrize(
        "kwargs",
        [{"option_type": "fwd"}, {"strike": 0.0}, {"dte": 0.0}, {"bid": 2.0, "ask": 1.0},
         {"bid": -0.1}, {"ask": 0.0, "bid": 0.0}],
    )
    def test_quote_validation(self, kwargs):
        base = dict(option_type="call", strike=100.0, dte=30.0, bid=1.0, ask=1.1)
        with pytest.raises(ValueError):
            OptionQuote(**{**base, **kwargs})

    def test_chain_forward_and_expiries(self):
        quotes = (OptionQuote("call", 100.0, 60.0, 1, 2), OptionQuote("put", 90.0, 30.0, 1, 2))
        chain = OptionChain("X", "test", datetime(2026, 1, 1), 100.0, 0.05, 0.01, quotes)
        assert chain.expiries == (30.0, 60.0)
        assert chain.forward(365.0) == pytest.approx(100.0 * math.exp(0.04))
        with pytest.raises(ValueError):
            OptionChain("X", "test", datetime(2026, 1, 1), 100.0, 0.05, 0.0, ())


class TestSyntheticChain:
    def test_quotes_are_otm_and_well_formed(self):
        spec = SyntheticChainSpec(spot=250.0, rate=0.05, dividend_yield=0.01, seed=1)
        chain = synthetic_chain(SURFACE, spec)
        assert len(chain.quotes) == len(spec.expiries) * spec.strikes_per_expiry
        for q in chain.quotes:
            forward = chain.forward(q.dte)
            assert (q.option_type == "call") == (q.strike >= forward)
            assert 0 <= q.bid <= q.ask

    def test_seeded_draws_are_deterministic(self):
        spec = SyntheticChainSpec(seed=9)
        assert synthetic_chain(SURFACE, spec).quotes == synthetic_chain(SURFACE, spec).quotes

    def test_noiseless_mids_invert_to_the_true_smile(self):
        spec = SyntheticChainSpec(noise=0.0, relative_spread=0.02, seed=0)
        ivs = chain_implied_vols(synthetic_chain(SURFACE, spec))
        use = ivs.usable
        true = np.array([
            float(SURFACE.slice(d / 365.0).implied_vol(k, d / 365.0))
            for d, k in zip(ivs.dte[use], ivs.k[use])
        ])
        assert ivs.iv[use] == pytest.approx(true, abs=1e-6)
        assert use.sum() >= 0.9 * len(use)

    def test_no_bid_quotes_are_excluded_and_counted(self):
        # A huge spread pushes wing bids to zero.
        spec = SyntheticChainSpec(relative_spread=0.9, noise=2.0, seed=2)
        ivs = chain_implied_vols(synthetic_chain(SURFACE, spec))
        assert ivs.excluded_no_bid > 0
        assert not np.any(ivs.usable & np.isnan(ivs.iv))
        assert np.all(ivs.variance_uncertainty[ivs.usable] > 0)

    def test_invalid_spec_rejected(self):
        for kwargs in ({"expiries": ()}, {"strikes_per_expiry": 3}, {"noise": -1.0},
                       {"relative_spread": 1.0}):
            with pytest.raises(ValueError):
                SyntheticChainSpec(**kwargs)


class TestSurfaceFit:
    def test_recovers_truth_and_is_arbitrage_free(self):
        # Mid noise has sd noise × half-spread and the fit's uncertainty is
        # the half-spread, so E[chi2/pt] ≈ noise² (n-5)/n ≈ 0.76 here
        # (sd ≈ 0.27 with 16 dof over 21 points).
        spec = SyntheticChainSpec(seed=11, noise=1.0, relative_spread=0.04)
        fit = fit_surface(synthetic_chain(SURFACE, spec))
        assert len(fit.slices) == len(spec.expiries) and not fit.skipped_expiries
        for s in fit.slices:
            assert s.atm_vol == pytest.approx(SURFACE.atm_vol(s.years), abs=0.005)
            assert 0.2 < s.fit.chi2_per_point < 1.8
        assert fit.butterfly_free
        assert fit.calendar_free

    def test_detects_calendar_arbitrage(self):
        # Swap two slices' levels: the back month cheaper than the front.
        spec = SyntheticChainSpec(expiries=(30, 60), noise=0.0, seed=0)
        inverted = SSVISurface(v0=0.60**2, v_inf=0.05**2, kappa=40.0, rho=-0.3, eta=1.0)
        chain = synthetic_chain(inverted, spec)
        bumped = tuple(
            q if q.dte == 60 else OptionQuote(q.option_type, q.strike, q.dte, q.bid * 3, q.ask * 3)
            for q in chain.quotes
        )
        fit = fit_surface(OptionChain(chain.symbol, chain.source, chain.as_of, chain.spot,
                                      chain.rate, chain.dividend_yield, bumped))
        assert not fit.calendar_free

    def test_surface_grid_spans_common_standardized_range(self):
        fit = fit_surface(synthetic_chain(SURFACE, SyntheticChainSpec(seed=4)))
        z, dte, iv = surface_grid(fit, points=31)
        assert iv.shape == (len(dte), 31)
        assert z[0] < 0 < z[-1]
        assert np.all(np.isfinite(iv)) and np.all(iv > 0)


class TestSurfaceMesh:
    @pytest.fixture(scope="class")
    def clean(self):
        spec = SyntheticChainSpec(noise=0.0, relative_spread=0.01, seed=3)
        fit = fit_surface(synthetic_chain(SURFACE, spec))
        return fit, surface_mesh(fit, SURFACE)

    def test_fitted_expiries_are_rows_and_reproduce_their_slices(self, clean):
        fit, mesh = clean
        assert mesh.dte[mesh.node_rows].tolist() == [s.dte for s in fit.slices]
        assert mesh.dte[0] == fit.slices[0].dte and mesh.dte[-1] == fit.slices[-1].dte
        for row, s in zip(mesh.node_rows, fit.slices):
            k = mesh.z * math.sqrt(float(s.fit.params.total_variance(0.0)))
            np.testing.assert_allclose(mesh.iv[row], s.fit.params.implied_vol(k, s.years),
                                       rtol=1e-12)

    def test_total_variance_rises_with_expiry_at_fixed_k(self, clean):
        # Read each row's w(k) = iv^2 T at common k by interpolating along the
        # row: with calendar-free slices it must rise row by row.
        fit, mesh = clean
        assert fit.calendar_free and mesh.min_calendar_gap > 0
        w = mesh.iv**2 * (mesh.dte[:, None] / 365.0)
        theta = np.array([np.interp(0.0, mesh.z, row) for row in w])
        for k0 in (-0.05, 0.0, 0.03):
            k_rows = mesh.z[None, :] * np.sqrt(theta)[:, None]
            w_at_k0 = np.array([np.interp(k0, k, row) for k, row in zip(k_rows, w)])
            assert np.all(np.diff(w_at_k0) > 0)

    def test_atm_forward_variance_is_constant_between_expiries(self, clean):
        # At k = 0 the interpolation is linear in T: equal steps in T give
        # equal steps in total variance within each interval.
        fit, mesh = clean
        w = mesh.iv**2 * (mesh.dte[:, None] / 365.0)
        theta = np.array([np.interp(0.0, mesh.z, row) for row in w])
        for start, end in zip(mesh.node_rows, mesh.node_rows[1:]):
            steps = np.diff(theta[start:end + 1])
            np.testing.assert_allclose(steps, steps.mean(), rtol=1e-3)

    def test_butterfly_minimum_matches_finite_differences(self, clean):
        # Independent of the mesh's analytic mix of derivatives: rebuild each
        # interpolated smile and difference it numerically.
        from quant_hub.models.svi import butterfly_g
        fit, mesh = clean
        slices = fit.slices
        theta = [float(s.fit.params.total_variance(0.0)) for s in slices]
        expected = math.inf
        for i, (a, b) in enumerate(zip(slices, slices[1:])):
            for d in np.linspace(a.dte, b.dte, 7):
                lam = (b.years - d / 365.0) / (b.years - a.years)
                root = math.sqrt(lam * theta[i] + (1 - lam) * theta[i + 1])
                k = np.linspace(mesh.z[0] * root, mesh.z[-1] * root, 4001)
                w = lam * a.fit.params.total_variance(k) + (1 - lam) * b.fit.params.total_variance(k)
                w1, w2 = np.gradient(w, k), np.gradient(np.gradient(w, k), k)
                expected = min(expected, float(butterfly_g(k, w, w1, w2)[2:-2].min()))
        assert mesh.min_butterfly_g > 0
        assert mesh.min_butterfly_g == pytest.approx(expected, abs=2e-3)

    def test_truth_matches_at_nodes_and_within_interpolation_error_between(self, clean):
        # SSVI slices are SVI, so a clean chain fits the nodes exactly; between
        # them the truth isn't linear in T, which is the residual error.
        _, mesh = clean
        error = np.abs(mesh.iv - mesh.true_iv)
        assert error[mesh.node_rows].max() < 1e-6
        assert error.max() < 0.015

    def test_extrapolated_points_lie_between_expiries_in_the_wings(self, clean):
        _, mesh = clean
        assert not mesh.extrapolated[mesh.node_rows].any()
        assert mesh.extrapolated.any()
        center = np.abs(mesh.z) < 0.5
        assert not mesh.extrapolated[:, center].any()

    def test_quotes_sit_on_fitted_expiries(self, clean):
        fit, mesh = clean
        assert len(mesh.quote_iv) == int(fit.ivs.usable.sum())
        assert set(mesh.quote_dte.tolist()) <= set(mesh.dte[mesh.node_rows].tolist())
        assert mesh.quote_z.min() == pytest.approx(-2.5, abs=0.1)
        assert mesh.quote_z.max() == pytest.approx(1.5, abs=0.1)

    def test_crossing_slices_show_a_negative_gap(self):
        spec = SyntheticChainSpec(expiries=(30, 60), noise=0.0, seed=0)
        inverted = SSVISurface(v0=0.60**2, v_inf=0.05**2, kappa=40.0, rho=-0.3, eta=1.0)
        chain = synthetic_chain(inverted, spec)
        bumped = tuple(
            q if q.dte == 60 else OptionQuote(q.option_type, q.strike, q.dte, q.bid * 3, q.ask * 3)
            for q in chain.quotes
        )
        fit = fit_surface(OptionChain(chain.symbol, chain.source, chain.as_of, chain.spot,
                                      chain.rate, chain.dividend_yield, bumped))
        assert surface_mesh(fit).min_calendar_gap < 0

    def test_invalid_requests_rejected(self):
        one = fit_surface(synthetic_chain(SURFACE, SyntheticChainSpec(expiries=(30,), seed=1)))
        with pytest.raises(ValueError):
            surface_mesh(one)
        fit = fit_surface(synthetic_chain(SURFACE, SyntheticChainSpec(seed=1)))
        with pytest.raises(ValueError):
            surface_mesh(fit, points=1)
        with pytest.raises(ValueError):
            surface_mesh(fit, rows_between=-1)


class TestButterflyDetection:
    def test_negative_g_for_an_arbitrageable_smile(self):
        # Very sharp vertex with steep wings on tiny variance: g < 0 near ATM.
        smile = RawSVI(a=0.0001, b=0.9, rho=0.0, m=0.0, sigma=0.001)
        assert smile.butterfly_g(np.linspace(-0.05, 0.05, 101)).min() < 0


class TestGarchForecast:
    P = GARCH11Parameters(mean=0.0, omega=0.000002, alpha=0.08, beta=0.9)

    def test_matches_iterated_expectation(self):
        h1 = 3 * self.P.unconditional_variance
        expected, h, total = [], h1, 0.0
        for n in range(1, 60):
            total += h
            expected.append(total / n)
            h = self.P.omega + (self.P.alpha + self.P.beta) * h  # E[eps^2] = E[h]
        got = average_variance_forecast(self.P, h1, np.arange(1, 60))
        assert got == pytest.approx(expected, rel=1e-12)

    def test_limits(self):
        v_l = self.P.unconditional_variance
        assert average_variance_forecast(self.P, v_l, np.array([1.0, 500.0])) == pytest.approx([v_l, v_l])
        assert float(average_variance_forecast(self.P, 2 * v_l, np.array([1e7]))[0]) == pytest.approx(v_l, rel=1e-4)

    def test_next_variance_is_the_recursion(self):
        got = next_variance(self.P, 0.02, 0.0001)
        assert got == pytest.approx(0.000002 + 0.08 * 0.02**2 + 0.9 * 0.0001)
        with pytest.raises(ValueError):
            next_variance(self.P, 0.01, 0.0)


def test_smile_curves_cover_each_expiry():
    from quant_hub.models.vol_surface import smile_curves

    fit = fit_surface(synthetic_chain(SURFACE, SyntheticChainSpec(seed=5)))
    curves = smile_curves(fit, SURFACE, points=50)
    assert [c.dte for c in curves] == list(fit.chain.expiries)
    for c in curves:
        assert c.k_grid[0] < c.quote_k.min() and c.k_grid[-1] > c.quote_k.max()
        assert np.max(np.abs(c.fitted_iv - c.true_iv)) < 0.01
    assert smile_curves(fit)[0].true_iv is None
