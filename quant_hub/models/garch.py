r"""GARCH(1,1) volatility simulation and Gaussian quasi-maximum likelihood.

Model
-----
For a return series :math:`r_t`, GARCH(1,1) separates the conditional mean
from its innovation and makes the innovation variance time-varying:

.. math::

   r_t = c + \varepsilon_t, \qquad \varepsilon_t = \sqrt{h_t}z_t,
   \qquad z_t \overset{\mathrm{iid}}{\sim} N(0, 1),

   h_t = \omega + \alpha\varepsilon_{t-1}^2 + \beta h_{t-1}.

Here ``c`` is the per-period conditional mean, ``omega`` has units of return
squared per period, and ``alpha``/``beta`` are dimensionless.  The model is
covariance-stationary when ``alpha + beta < 1``; then its unconditional
variance is :math:`\omega/(1-\alpha-\beta)`.  This module initializes both
simulation and likelihood recursions at that unconditional variance, so the
first observation has the stationary marginal distribution rather than an
arbitrary zero-volatility start.

``fit_garch11`` minimizes the Gaussian negative log likelihood.  Financial
returns need not be conditionally normal, so this is more precisely a
Gaussian *quasi*-maximum-likelihood estimate (QMLE): it targets the
conditional-variance dynamics while using the normal density as a practical
criterion.  The fit runs the shared deterministic Nelder--Mead minimizer
(``models/optimize.py``) from several starting points.  Its transformed
coordinates enforce ``omega > 0``, ``alpha >= 0``, ``beta >= 0``, and
``alpha + beta < 1`` for every likelihood evaluation.

Assumptions and units
---------------------
Returns are equally spaced decimal returns (for example, daily log returns),
not prices or percentages.  Inputs must be finite and contain at least 20
observations.  The fitted parameters are per observation period; annualizing
a conditional volatility for daily data is a presentation concern:
``sqrt(h_t * 252)``.  GARCH captures volatility clustering, but not leverage
asymmetry, jumps, heavy-tailed innovations, or changing parameters.

Forecasting
-----------
Write :math:`V_L = \omega/(1-p)` with :math:`p = \alpha + \beta`. Taking
expectations of the recursion, and using
:math:`\mathbb{E}_t[\varepsilon_{t+j}^2] = \mathbb{E}_t[h_{t+j}]`, gives
:math:`\mathbb{E}_t[h_{t+j+1}] - V_L = p\,(\mathbb{E}_t[h_{t+j}] - V_L)`, so

.. math::  \mathbb{E}_t[h_{t+j}] = V_L + p^{\,j-1}(h_{t+1} - V_L), \qquad j \ge 1,

where :math:`h_{t+1} = \omega + \alpha\varepsilon_t^2 + \beta h_t` is known at
:math:`t`. The expected *average* variance over the next :math:`n` periods
is a geometric sum:

.. math::  \bar h_n = \frac{1}{n}\sum_{j=1}^{n}\mathbb{E}_t[h_{t+j}]
           = V_L + (h_{t+1} - V_L)\,\frac{1 - p^{\,n}}{n(1-p)}.

:math:`\sqrt{252\,\bar h_n}` is the model's forecast of annualized volatility
over a horizon of :math:`n` trading days. That is the quantity an
at-the-money implied vol of the same maturity prices, so plotting the two
together shows the variance risk premium. It matches the continuous form
used by the synthetic surface (``models/svi.py``) with
:math:`\kappa = -252\ln p`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from quant_hub.models.optimize import nelder_mead


MIN_OBSERVATIONS = 20
"""Smallest return sample accepted by the four-parameter estimator."""

_LOG_2PI = math.log(2.0 * math.pi)


@dataclass(frozen=True)
class GARCH11Parameters:
    """Per-period parameters of a stationary GARCH(1,1) process."""

    mean: float
    omega: float
    alpha: float
    beta: float

    def __post_init__(self) -> None:
        values = (self.mean, self.omega, self.alpha, self.beta)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("GARCH parameters must be finite")
        if self.omega <= 0:
            raise ValueError("omega must be positive")
        if self.alpha < 0 or self.beta < 0:
            raise ValueError("alpha and beta must be non-negative")
        if self.persistence >= 1:
            raise ValueError("alpha + beta must be < 1 for stationarity")

    @property
    def persistence(self) -> float:
        """:math:`\alpha + \beta`, the fraction of variance that persists."""
        return self.alpha + self.beta

    @property
    def unconditional_variance(self) -> float:
        r""":math:`\omega/(1-\alpha-\beta)`, in return-squared units."""
        return self.omega / (1.0 - self.persistence)


@dataclass(frozen=True)
class GARCH11Simulation:
    """One simulated stationary return path and its pre-return variances."""

    parameters: GARCH11Parameters
    returns: np.ndarray
    conditional_variances: np.ndarray


@dataclass(frozen=True)
class GARCH11Fit:
    """Gaussian-QMLE result for one return sample.

    ``conditional_variances[t]`` is :math:`h_t`, the variance known before
    observing ``returns[t]``.  ``standardized_residuals`` are therefore
    ``(returns - mean) / sqrt(conditional_variances)``.
    """

    parameters: GARCH11Parameters
    negative_log_likelihood: float
    conditional_variances: np.ndarray
    standardized_residuals: np.ndarray
    converged: bool
    iterations: int


def simulate_garch11(
    parameters: GARCH11Parameters, n_observations: int, seed: int | None = None
) -> GARCH11Simulation:
    """Simulate ``n_observations`` stationary Gaussian GARCH returns."""
    if n_observations < 1:
        raise ValueError(f"n_observations must be >= 1, got {n_observations}")

    rng = np.random.default_rng(seed)
    variances = np.empty(n_observations, dtype=float)
    returns = np.empty(n_observations, dtype=float)
    variances[0] = parameters.unconditional_variance
    for t in range(n_observations):
        if t:
            innovation = returns[t - 1] - parameters.mean
            variances[t] = (
                parameters.omega
                + parameters.alpha * innovation**2
                + parameters.beta * variances[t - 1]
            )
        returns[t] = parameters.mean + math.sqrt(variances[t]) * rng.standard_normal()
    return GARCH11Simulation(parameters, returns, variances)


def conditional_variances(
    returns: np.ndarray, parameters: GARCH11Parameters
) -> np.ndarray:
    """Evaluate the stationary GARCH variance recursion for ``returns``."""
    values = _validate_returns(returns, min_observations=1)
    variances = np.empty(len(values), dtype=float)
    variances[0] = parameters.unconditional_variance
    for t in range(1, len(values)):
        innovation = values[t - 1] - parameters.mean
        variances[t] = (
            parameters.omega
            + parameters.alpha * innovation**2
            + parameters.beta * variances[t - 1]
        )
    return variances


def next_variance(parameters: GARCH11Parameters, last_return: float, last_variance: float) -> float:
    """One-step-ahead variance :math:`h_{t+1}` from the last return and its variance."""
    if not (math.isfinite(last_return) and math.isfinite(last_variance)) or last_variance <= 0:
        raise ValueError("last return must be finite and last variance positive")
    innovation = last_return - parameters.mean
    return parameters.omega + parameters.alpha * innovation**2 + parameters.beta * last_variance


def average_variance_forecast(
    parameters: GARCH11Parameters, next_var: float, horizons: np.ndarray
) -> np.ndarray:
    """Expected average per-period variance over each horizon (module docstring).

    ``horizons`` are period counts (> 0, may be fractional for plotting).
    """
    n = np.asarray(horizons, dtype=float)
    if not np.isfinite(n).all() or np.any(n <= 0):
        raise ValueError("horizons must be positive")
    if not math.isfinite(next_var) or next_var <= 0:
        raise ValueError("next variance must be positive")
    p = parameters.persistence
    long_run = parameters.unconditional_variance
    return long_run + (next_var - long_run) * (1 - p**n) / (n * (1 - p))


def fit_garch11(returns: np.ndarray, max_iterations: int = 300) -> GARCH11Fit:
    """Fit stationary GARCH(1,1) parameters by Gaussian QMLE.

    ``max_iterations`` applies to each deterministic starting simplex.  The
    function raises rather than silently fitting an ill-defined series; a
    constant return series has no positive variance for GARCH to explain.
    """
    values = _validate_returns(returns, min_observations=MIN_OBSERVATIONS)
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")
    scale = float(np.std(values, ddof=0))
    if scale <= 0:
        raise ValueError("GARCH fitting requires returns with positive variance")

    objective = _objective_factory(values, scale)
    starts = _starting_points(values, scale)
    best_x: np.ndarray | None = None
    best_value = float("inf")
    best_converged = False
    total_iterations = 0
    for start in starts:
        result = nelder_mead(objective, start, max_iterations)
        total_iterations += result.iterations
        if result.value < best_value:
            best_x, best_value, best_converged = result.x, result.value, result.converged

    assert best_x is not None  # starts is fixed and non-empty.
    parameters = _decode_parameters(best_x, scale)
    variances = conditional_variances(values, parameters)
    residuals = (values - parameters.mean) / np.sqrt(variances)
    return GARCH11Fit(
        parameters=parameters,
        negative_log_likelihood=best_value,
        conditional_variances=variances,
        standardized_residuals=residuals,
        converged=best_converged,
        iterations=total_iterations,
    )


def _validate_returns(returns: np.ndarray, min_observations: int) -> np.ndarray:
    values = np.asarray(returns, dtype=float)
    if values.ndim != 1:
        raise ValueError("returns must be one-dimensional")
    if len(values) < min_observations:
        raise ValueError(f"returns must contain at least {min_observations} observations")
    if not np.isfinite(values).all():
        raise ValueError("returns must contain only finite values")
    return values


def _objective_factory(values: np.ndarray, scale: float):
    def objective(x: np.ndarray) -> float:
        try:
            parameters = _decode_parameters(x, scale)
        except (OverflowError, ValueError):
            return float("inf")
        variances = conditional_variances(values, parameters)
        if not np.isfinite(variances).all() or np.any(variances <= 0):
            return float("inf")
        innovations = values - parameters.mean
        return 0.5 * float(np.sum(_LOG_2PI + np.log(variances) + innovations**2 / variances))

    return objective


def _decode_parameters(x: np.ndarray, scale: float) -> GARCH11Parameters:
    mean = float(x[0] * scale)
    omega = float(math.exp(float(x[1])) * scale**2)
    persistence = _sigmoid(float(x[2]))
    alpha_share = _sigmoid(float(x[3]))
    return GARCH11Parameters(
        mean=mean,
        omega=omega,
        alpha=persistence * alpha_share,
        beta=persistence * (1.0 - alpha_share),
    )


def _encode_parameters(parameters: GARCH11Parameters, scale: float) -> np.ndarray:
    persistence = parameters.persistence
    return np.array([
        parameters.mean / scale,
        math.log(parameters.omega / scale**2),
        _logit(persistence),
        _logit(parameters.alpha / persistence),
    ])


def _starting_points(values: np.ndarray, scale: float) -> tuple[np.ndarray, ...]:
    variance = float(np.var(values, ddof=0))
    mean = float(np.mean(values))
    candidates = (
        (0.08, 0.88),
        (0.15, 0.75),
        (0.04, 0.94),
    )
    starts = []
    for alpha, beta in candidates:
        params = GARCH11Parameters(
            mean=mean,
            omega=variance * (1.0 - alpha - beta),
            alpha=alpha,
            beta=beta,
        )
        starts.append(_encode_parameters(params, scale))
    return tuple(starts)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    exp_x = math.exp(x)
    return exp_x / (1.0 + exp_x)


def _logit(p: float) -> float:
    if not 0 < p < 1:
        raise ValueError("logit input must be strictly between zero and one")
    return math.log(p / (1.0 - p))
