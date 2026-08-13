"""Tests for the GBM generator: determinism, shape, positivity, validation,
and simulated moments against the closed-form ones."""

import math

import numpy as np
import pytest

from quant_hub.data.synthetic import GeometricBrownianMotion


def make_gbm(**overrides):
    params = dict(initial_price=100.0, mu=0.08, sigma=0.20, dt=1 / 252, seed=42)
    params.update(overrides)
    return GeometricBrownianMotion(**params)


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            {"initial_price": 0.0},
            {"initial_price": -5.0},
            {"sigma": -0.1},
            {"dt": 0.0},
            {"dt": -1 / 252},
        ],
    )
    def test_invalid_parameters_rejected(self, bad):
        with pytest.raises(ValueError):
            make_gbm(**bad)

    @pytest.mark.parametrize("n_steps", [0, -1])
    def test_invalid_step_counts_rejected(self, n_steps):
        with pytest.raises(ValueError):
            make_gbm().simulate(n_steps)

    def test_invalid_path_count_rejected(self):
        with pytest.raises(ValueError):
            make_gbm().simulate_paths(10, n_paths=0)


class TestPaths:
    def test_shape_and_initial_price(self):
        prices = make_gbm().simulate(252)
        assert prices.shape == (253,)
        assert prices[0] == 100.0

        paths = make_gbm().simulate_paths(50, n_paths=7)
        assert paths.shape == (7, 51)
        assert np.all(paths[:, 0] == 100.0)

    def test_deterministic_with_fixed_seed(self):
        a = make_gbm(seed=7).simulate(100)
        b = make_gbm(seed=7).simulate(100)
        np.testing.assert_array_equal(a, b)

    def test_repeated_calls_restart_the_stream(self):
        gen = make_gbm(seed=7)
        np.testing.assert_array_equal(gen.simulate(100), gen.simulate(100))

    def test_different_seeds_differ(self):
        a = make_gbm(seed=1).simulate(100)
        b = make_gbm(seed=2).simulate(100)
        assert not np.array_equal(a, b)

    def test_prices_strictly_positive(self):
        # High volatility, long horizon: positivity must still hold because
        # the discretization exponentiates.
        paths = make_gbm(sigma=0.8, seed=3).simulate_paths(2000, n_paths=50)
        assert np.all(paths > 0)

    def test_zero_volatility_is_deterministic_growth(self):
        gbm = make_gbm(sigma=0.0, mu=0.10)
        prices = gbm.simulate(252)
        expected = 100.0 * np.exp(0.10 * np.arange(253) / 252)
        np.testing.assert_allclose(prices, expected, rtol=1e-12)


class TestMoments:
    """Check simulated moments of ln(S_T/S_0) against the closed forms
    E = (mu - sigma^2/2) T and Var = sigma^2 T, within a few standard errors."""

    def test_terminal_log_return_moments(self):
        mu, sigma, dt, n_steps, n_paths = 0.08, 0.20, 1 / 252, 63, 200_000
        horizon = n_steps * dt
        gbm = make_gbm(mu=mu, sigma=sigma, dt=dt, seed=11)
        paths = gbm.simulate_paths(n_steps, n_paths)
        log_total = np.log(paths[:, -1] / paths[:, 0])

        expected_mean = (mu - 0.5 * sigma**2) * horizon
        expected_var = sigma**2 * horizon

        # Standard error of the sample mean is sqrt(var/n); allow 4 SEs.
        se_mean = math.sqrt(expected_var / n_paths)
        assert abs(log_total.mean() - expected_mean) < 4 * se_mean

        # Var of the sample variance of a normal is 2 var^2 / (n-1); allow 4 SEs.
        se_var = math.sqrt(2 * expected_var**2 / (n_paths - 1))
        assert abs(log_total.var(ddof=1) - expected_var) < 4 * se_var

    def test_log_increments_are_uncorrelated_across_steps(self):
        gbm = make_gbm(seed=5)
        paths = gbm.simulate_paths(2, n_paths=100_000)
        inc1 = np.log(paths[:, 1] / paths[:, 0])
        inc2 = np.log(paths[:, 2] / paths[:, 1])
        corr = np.corrcoef(inc1, inc2)[0, 1]
        assert abs(corr) < 0.02
