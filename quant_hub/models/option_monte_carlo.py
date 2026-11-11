r"""Monte Carlo pricing of European options, validated against the closed form.

Model
-----
Under the risk-neutral measure the underlying is GBM with drift
:math:`r - q`, so the exact terminal law (``data/synthetic.py``'s transition
with :math:`\mu = r - q` and one step of length :math:`T`) is

.. math::  S_T = S_0 \exp\!\left[(r - q - \tfrac{1}{2}\sigma^2)T + \sigma\sqrt{T}\,Z\right],
           \qquad Z \sim N(0, 1),

and the option price is the discounted expected payoff
:math:`V = e^{-rT}\,\mathbb{E}[f(S_T)]`. Because the transition is exact, the
only error is sampling error. That is the point of this module: it is an
independent check on ``black_scholes.price``, which computes the same
expectation in closed form.

Estimator and standard error
----------------------------
With :math:`n` iid draws :math:`Y_j = e^{-rT} f(S_T^{(j)})`,
:math:`\hat V = \bar Y` is unbiased and, by the CLT,
:math:`(\hat V - V)/\widehat{SE} \approx N(0, 1)` with
:math:`\widehat{SE} = s_Y/\sqrt{n}` (sample std, ``ddof=1``). The reported
``z_score`` is that statistic against the closed form. Values beyond about
±3 would indicate a bug rather than bad luck.

Antithetic variates
-------------------
Draw :math:`Z_1..Z_{n/2}` and use each twice, as :math:`Z` and :math:`-Z`. The
pair averages :math:`\tilde Y_j = \tfrac{1}{2}(Y(Z_j) + Y(-Z_j))` are iid,
so the same CLT applies with :math:`n/2` samples:

.. math::  \mathrm{Var}(\tilde Y) = \tfrac{1}{2}\left(\mathrm{Var}\,Y
           + \mathrm{Cov}(Y(Z), Y(-Z))\right).

A call or put payoff is monotone in :math:`Z`, so :math:`Y(Z)` and
:math:`Y(-Z)` are negatively correlated (Chebyshev's association
inequality). The covariance term is therefore :math:`\le 0` and antithetic
sampling never does worse than plain sampling for the same number of payoff
evaluations.

Convergence diagnostics
-----------------------
``running_estimate``/``running_standard_error`` give the estimate and its SE
after the first :math:`k` samples, at geometrically spaced checkpoints. The
error band narrows like :math:`1/\sqrt{k}`, which is the picture the lab
draws.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from quant_hub.models.black_scholes import BlackScholesInputs, intrinsic_value, price

RUNNING_CHECKPOINTS = 80
"""Number of geometrically spaced points in the convergence diagnostics."""


@dataclass(frozen=True)
class MonteCarloPricing:
    """One Monte Carlo pricing run and its convergence path.

    ``checkpoints`` count *payoff evaluations* (paths), antithetic or not,
    so plain and antithetic runs are comparable at equal cost.
    """

    inputs: BlackScholesInputs
    estimate: float
    standard_error: float
    closed_form: float
    n_paths: int
    antithetic: bool
    checkpoints: np.ndarray
    running_estimate: np.ndarray
    running_standard_error: np.ndarray
    terminal_prices: np.ndarray

    @property
    def z_score(self) -> float:
        """(estimate - closed form) / standard error; approximately N(0, 1).

        NaN when every sampled payoff was identical (e.g., all out of the
        money), since the sample then carries no error estimate.
        """
        if self.standard_error == 0:
            return math.nan
        return (self.estimate - self.closed_form) / self.standard_error


def monte_carlo_price(
    inputs: BlackScholesInputs,
    n_paths: int,
    seed: int | None = None,
    antithetic: bool = True,
) -> MonteCarloPricing:
    """Price a European option by simulating the exact risk-neutral terminal law."""
    if n_paths < 4:
        raise ValueError(f"n_paths must be >= 4, got {n_paths}")
    if antithetic and n_paths % 2:
        raise ValueError("antithetic sampling needs an even number of paths")

    t = inputs.dte / 365.0
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n_paths // 2 if antithetic else n_paths)
    if antithetic:
        z = np.concatenate([z, -z])
    terminal = risk_neutral_terminal_prices(inputs, z)

    discounted = math.exp(-inputs.rate * t) * intrinsic_value(
        terminal, inputs.strike, inputs.option_type
    )
    if antithetic:
        half = n_paths // 2
        samples = 0.5 * (discounted[:half] + discounted[half:])
        paths_per_sample = 2
    else:
        samples = discounted
        paths_per_sample = 1

    counts = _checkpoints(len(samples))
    running_mean, running_se = _running_mean_and_se(samples, counts)
    return MonteCarloPricing(
        inputs=inputs,
        estimate=float(running_mean[-1]),
        standard_error=float(running_se[-1]),
        closed_form=price(inputs),
        n_paths=n_paths,
        antithetic=antithetic,
        checkpoints=counts * paths_per_sample,
        running_estimate=running_mean,
        running_standard_error=running_se,
        terminal_prices=terminal,
    )


def risk_neutral_terminal_prices(inputs: BlackScholesInputs, z: np.ndarray) -> np.ndarray:
    """Map standard normal draws to :math:`S_T` under the risk-neutral measure."""
    t = inputs.dte / 365.0
    drift = (inputs.rate - inputs.dividend_yield - 0.5 * inputs.sigma**2) * t
    return inputs.spot * np.exp(drift + inputs.sigma * math.sqrt(t) * np.asarray(z, dtype=float))


def _checkpoints(n_samples: int) -> np.ndarray:
    points = np.geomspace(2, n_samples, RUNNING_CHECKPOINTS)
    return np.unique(np.round(points).astype(int))


def _running_mean_and_se(samples: np.ndarray, counts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and SE of the first ``k`` samples for each ``k`` in ``counts``.

    Uses shifted cumulative sums (shift = first sample) so the variance
    ``(sum x^2 - k mean^2)/(k-1)`` does not lose precision to cancellation.
    """
    shifted = samples - samples[0]
    sums = np.cumsum(shifted)[counts - 1]
    squares = np.cumsum(shifted**2)[counts - 1]
    mean_shifted = sums / counts
    variance = np.maximum(squares - counts * mean_shifted**2, 0.0) / (counts - 1)
    return mean_shifted + samples[0], np.sqrt(variance / counts)
