"""Tests for the shared Nelder--Mead minimizer."""

import numpy as np
import pytest

from quant_hub.models.optimize import nelder_mead


def test_recovers_anisotropic_quadratic_minimum():
    # The minimum is off the start + t*(1, 1, 1) diagonal. A degenerate
    # starting simplex (every non-start vertex equal) can only search that
    # line, so this pins the axis-aligned construction.
    center = np.array([1.0, -2.0, 0.5])
    weights = np.array([1.0, 10.0, 0.1])

    def objective(x):
        return float(np.sum(weights * (x - center) ** 2))

    result = nelder_mead(objective, np.zeros(3), max_iterations=2000)
    assert result.converged
    assert result.x == pytest.approx(center, abs=1e-4)
    assert result.value == pytest.approx(0.0, abs=1e-8)


def test_rosenbrock_from_classic_start():
    def rosenbrock(x):
        return float(100.0 * (x[1] - x[0] ** 2) ** 2 + (1.0 - x[0]) ** 2)

    result = nelder_mead(rosenbrock, np.array([-1.2, 1.0]), max_iterations=2000)
    assert result.converged
    assert result.x == pytest.approx([1.0, 1.0], abs=1e-3)


def test_infeasible_region_as_infinity_is_avoided():
    # Minimize (x - 2)^2 subject to x <= 1, encoded as inf outside the set.
    def objective(x):
        return float("inf") if x[0] > 1.0 else float((x[0] - 2.0) ** 2)

    result = nelder_mead(objective, np.array([0.0]), max_iterations=500)
    assert result.x[0] <= 1.0
    assert result.x[0] == pytest.approx(1.0, abs=1e-5)


def test_reports_non_convergence_when_iterations_run_out():
    result = nelder_mead(lambda x: float(np.sum(x**2)), np.full(4, 5.0), max_iterations=3)
    assert not result.converged
    assert result.iterations == 3


def test_deterministic():
    def f(x):
        return float((x[0] - 0.3) ** 2 + 3.0 * (x[1] + 0.7) ** 2)

    a = nelder_mead(f, np.array([2.0, 2.0]), max_iterations=500)
    b = nelder_mead(f, np.array([2.0, 2.0]), max_iterations=500)
    assert np.array_equal(a.x, b.x) and a.value == b.value


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": np.array([])},
        {"start": np.zeros((2, 2))},
        {"start": np.array([np.nan])},
        {"max_iterations": 0},
        {"initial_step": 0.0},
        {"x_tolerance": -1.0},
    ],
)
def test_rejects_invalid_arguments(kwargs):
    args = {"start": np.zeros(2), "max_iterations": 10, **kwargs}
    with pytest.raises(ValueError):
        nelder_mead(lambda x: float(np.sum(x**2)), **args)
