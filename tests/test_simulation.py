"""Tests for GBM simulation experiments and Monte Carlo summaries."""

import math

import numpy as np
import pytest

from quant_hub.data.simulation import (
    MAX_PATHS,
    TERMINAL_PERCENTILES,
    monte_carlo_summary,
    path_percentiles,
    run_gbm_simulation,
)


def run(seed=42, n_days=252, n_paths=5, **kwargs):
    return run_gbm_simulation(
        initial_price=100.0, mu=0.08, sigma=0.20, n_days=n_days, seed=seed,
        n_paths=n_paths, **kwargs
    )


class TestRunGBMSimulation:
    def test_shapes(self):
        result = run(n_days=100, n_paths=7)
        assert result.paths.shape == (7, 101)
        assert len(result.dates) == 101
        assert len(result.history) == 101
        assert result.n_days == 100
        assert result.n_paths == 7

    def test_deterministic_with_seed(self):
        a, b = run(seed=9), run(seed=9)
        assert np.array_equal(a.paths, b.paths)
        assert a.history.closes.equals(b.history.closes)

    def test_history_is_path_zero(self):
        result = run(n_paths=3)
        assert np.array_equal(result.history.closes.to_numpy(), result.paths[0])

    def test_dates_are_business_days(self):
        result = run(n_days=10)
        assert (result.dates.dayofweek < 5).all()

    def test_horizon_years(self):
        assert run(n_days=252).horizon_years == pytest.approx(1.0)
        assert run(n_days=126).horizon_years == pytest.approx(0.5)

    def test_terminal_prices_are_last_column(self):
        result = run(n_paths=4)
        assert np.array_equal(result.terminal_prices, result.paths[:, -1])

    def test_closed_form_reference_values(self):
        result = run(n_days=252)  # T = 1 year
        assert result.expected_terminal_price() == pytest.approx(100 * math.exp(0.08))
        assert result.median_terminal_price() == pytest.approx(
            100 * math.exp(0.08 - 0.5 * 0.04)
        )
        curve = result.expected_price_curve()
        assert curve[0] == pytest.approx(100.0)
        assert curve[-1] == pytest.approx(result.expected_terminal_price())

    def test_invalid_inputs_rejected(self):
        with pytest.raises(ValueError):
            run(n_days=1)
        with pytest.raises(ValueError):
            run(n_paths=0)
        with pytest.raises(ValueError):
            run(n_paths=MAX_PATHS + 1)
        with pytest.raises(ValueError):
            run_gbm_simulation(
                initial_price=-5.0, mu=0.08, sigma=0.2, n_days=10, seed=1
            )


class TestMonteCarloSummary:
    def test_descriptives_match_numpy(self):
        result = run(n_paths=500)
        summary = monte_carlo_summary(result)
        terminal = result.terminal_prices
        assert summary.n_paths == 500
        assert summary.mean == pytest.approx(float(terminal.mean()))
        assert summary.median == pytest.approx(float(np.median(terminal)))
        assert summary.std == pytest.approx(float(terminal.std(ddof=1)))
        assert set(summary.percentiles) == set(TERMINAL_PERCENTILES)
        assert summary.percentiles[50] == pytest.approx(summary.median)
        for level, value in summary.percentiles.items():
            assert value == pytest.approx(float(np.percentile(terminal, level)))

    def test_percentiles_are_monotone(self):
        summary = monte_carlo_summary(run(n_paths=200))
        values = [summary.percentiles[p] for p in sorted(summary.percentiles)]
        assert values == sorted(values)

    def test_sample_mean_converges_to_closed_form(self):
        # Statistical validation: with 4000 one-year paths the MC mean of S_T
        # should sit within ~4 standard errors of S0*exp(mu*T).
        # SE = E[S_T]*sqrt(exp(sigma^2*T)-1)/sqrt(n) ~ 108.3*0.202/63.2 ~ 0.35.
        result = run(seed=123, n_days=252, n_paths=4000)
        summary = monte_carlo_summary(result)
        assert summary.expected == pytest.approx(100 * math.exp(0.08))
        assert summary.mean == pytest.approx(summary.expected, abs=1.4)

    def test_lognormal_right_skew_mean_above_median(self):
        # E[S_T] > median[S_T] in theory; check the closed forms and that the
        # large-sample empirical values agree in direction.
        summary = monte_carlo_summary(run(seed=5, n_paths=4000))
        assert summary.expected > summary.theoretical_median
        assert summary.mean > summary.median

    def test_single_path_summary_degenerates_gracefully(self):
        summary = monte_carlo_summary(run(n_paths=1))
        assert summary.mean == summary.median
        assert math.isnan(summary.std)


class TestPathPercentiles:
    def test_shape_and_start(self):
        result = run(n_paths=50, n_days=30)
        bands = path_percentiles(result)
        assert set(bands) == set(TERMINAL_PERCENTILES)
        for values in bands.values():
            assert values.shape == (31,)
            assert values[0] == pytest.approx(100.0)

    def test_bands_are_ordered_at_every_step(self):
        bands = path_percentiles(run(n_paths=400))
        stacked = np.vstack([bands[q] for q in TERMINAL_PERCENTILES])
        assert np.all(np.diff(stacked, axis=0) >= 0)

    def test_terminal_bands_match_lognormal_quantiles(self):
        """q-quantile of S_T = S0 exp((mu - sigma^2/2)T + z_q sigma sqrt(T)).
        The sample-quantile s.e. in log space is about
        sqrt(q(1-q)) / (phi(z_q) sqrt(n)) * sigma sqrt(T), at most 0.3% here
        (the 5%/95% tails), so 1.5% is about 5 s.e."""
        result = run(n_paths=20_000, seed=3)
        bands = path_percentiles(result, levels=(5, 50, 95))
        drift = (0.08 - 0.5 * 0.20**2) * result.horizon_years
        spread = 0.20 * math.sqrt(result.horizon_years)
        for q, z in ((5, -1.6448536), (50, 0.0), (95, 1.6448536)):
            closed = 100.0 * math.exp(drift + z * spread)
            assert bands[q][-1] == pytest.approx(closed, rel=0.015)

    def test_rejects_single_path_and_bad_levels(self):
        with pytest.raises(ValueError, match="at least 2 paths"):
            path_percentiles(run(n_paths=1))
        with pytest.raises(ValueError, match="levels"):
            path_percentiles(run(n_paths=5), levels=(5, 101))
