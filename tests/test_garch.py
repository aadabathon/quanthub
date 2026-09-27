"""Tests for stationary GARCH(1,1) simulation and Gaussian QMLE fitting."""

import math

import numpy as np
import pytest

from quant_hub.models.garch import (
    MIN_OBSERVATIONS,
    GARCH11Parameters,
    conditional_variances,
    fit_garch11,
    simulate_garch11,
)


def params() -> GARCH11Parameters:
    return GARCH11Parameters(mean=0.0002, omega=0.000004, alpha=0.08, beta=0.88)


class TestParameters:
    def test_unconditional_variance(self):
        p = params()
        assert p.persistence == pytest.approx(0.96)
        assert p.unconditional_variance == pytest.approx(0.0001)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"omega": 0.0},
            {"alpha": -0.01},
            {"beta": -0.01},
            {"alpha": 0.2, "beta": 0.8},
            {"mean": math.nan},
        ],
    )
    def test_invalid_parameters_rejected(self, kwargs):
        base = {"mean": 0.0, "omega": 0.00001, "alpha": 0.1, "beta": 0.8}
        base.update(kwargs)
        with pytest.raises(ValueError):
            GARCH11Parameters(**base)


class TestSimulation:
    def test_seed_is_deterministic(self):
        a = simulate_garch11(params(), 100, seed=7)
        b = simulate_garch11(params(), 100, seed=7)
        assert np.array_equal(a.returns, b.returns)
        assert np.array_equal(a.conditional_variances, b.conditional_variances)

    def test_recursion_matches_definition(self):
        result = simulate_garch11(params(), 100, seed=9)
        p = result.parameters
        assert result.conditional_variances[0] == pytest.approx(p.unconditional_variance)
        for t in range(1, len(result.returns)):
            expected = p.omega + p.alpha * (result.returns[t - 1] - p.mean) ** 2
            expected += p.beta * result.conditional_variances[t - 1]
            assert result.conditional_variances[t] == pytest.approx(expected)

    def test_long_run_variance_matches_closed_form(self):
        # With 200k stationary draws, E[(r-c)^2] should be close to omega/(1-a-b).
        result = simulate_garch11(params(), 200_000, seed=4)
        observed = float(np.mean((result.returns - params().mean) ** 2))
        assert observed == pytest.approx(params().unconditional_variance, rel=0.06)

    def test_rejects_invalid_length(self):
        with pytest.raises(ValueError):
            simulate_garch11(params(), 0)


class TestConditionalVariances:
    def test_reproduces_simulation_variances(self):
        result = simulate_garch11(params(), 100, seed=2)
        got = conditional_variances(result.returns, result.parameters)
        assert np.array_equal(got, result.conditional_variances)

    @pytest.mark.parametrize("returns", [[], [[0.01]], [0.01, math.nan]])
    def test_rejects_invalid_returns(self, returns):
        with pytest.raises(ValueError):
            conditional_variances(np.array(returns), params())


class TestFit:
    def test_recovery_from_long_synthetic_path(self):
        # QMLE uncertainty remains material for persistence.  This 60-year
        # daily sample makes recovery a stable integration test without
        # asserting unrealistically exact estimates.
        true = params()
        simulated = simulate_garch11(true, 252 * 60, seed=21)
        fit = fit_garch11(simulated.returns)
        got = fit.parameters
        assert fit.converged
        assert got.mean == pytest.approx(true.mean, abs=0.00025)
        assert got.alpha == pytest.approx(true.alpha, abs=0.035)
        assert got.beta == pytest.approx(true.beta, abs=0.04)
        assert got.persistence == pytest.approx(true.persistence, abs=0.025)
        assert got.unconditional_variance == pytest.approx(
            true.unconditional_variance, rel=0.15
        )
        assert np.isfinite(fit.negative_log_likelihood)
        assert np.all(fit.conditional_variances > 0)
        assert len(fit.standardized_residuals) == len(simulated.returns)

    def test_rejects_undefined_input(self):
        with pytest.raises(ValueError):
            fit_garch11(np.zeros(MIN_OBSERVATIONS))
        with pytest.raises(ValueError):
            fit_garch11(np.arange(MIN_OBSERVATIONS - 1, dtype=float))
        with pytest.raises(ValueError):
            fit_garch11(np.arange(MIN_OBSERVATIONS, dtype=float), max_iterations=0)
