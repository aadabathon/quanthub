r"""GBM simulation experiments: one call, one immutable result.

The dashboard's laboratory workflow needs a single object that captures *an
entire experiment* — the true process parameters, every simulated path, and
the primary path wrapped as a ``PriceHistory`` — so that all downstream
analytics and charts describe exactly the same draw. This module provides
that object; the generator itself lives in ``quant_hub.data.synthetic``.

Mathematics
-----------
``GeometricBrownianMotion`` simulates the exact solution of
:math:`dS_t = \mu S_t\,dt + \sigma S_t\,dW_t`:

.. math:: S_t = S_0 \exp\!\left[(\mu - \tfrac{1}{2}\sigma^2)\,t + \sigma W_t\right]

so over a horizon of :math:`T` years the log terminal price is exactly

.. math:: \ln S_T \sim N\!\big(\ln S_0 + (\mu - \tfrac{1}{2}\sigma^2)T,\; \sigma^2 T\big)

which gives the closed forms used as reference lines in the Monte Carlo view:

* **Mean**: :math:`\mathbb{E}[S_T] = S_0 e^{\mu T}` — the SDE drift
  :math:`\mu` is the growth rate of the *expectation*.
* **Median**: :math:`S_0 e^{(\mu - \sigma^2/2)T}` — the *typical* path grows
  at the log-drift, which is lower by the Ito correction
  :math:`\tfrac{1}{2}\sigma^2`.

Mean > median because :math:`S_T` is lognormal (right-skewed): a minority of
large outcomes pulls the average above the typical outcome, and the gap
widens with :math:`\sigma^2 T`. This is the central lesson the Monte Carlo
panel exists to demonstrate — *known parameters do not imply a known
realized outcome*.

Horizon convention: ``n_days`` steps of size ``dt`` years each, so
:math:`T = n_{\text{days}} \cdot dt` (one year = 252 daily steps at the
default ``dt`` = 1/252). Dates are synthetic business days from a fixed
start; they exist to give charts a calendar axis, not to model any calendar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from quant_hub.data.synthetic import GeometricBrownianMotion
from quant_hub.data.types import PriceHistory

#: Arbitrary fixed first business day of simulated calendars. Fixed (not
#: "today") so identical seeds produce identical objects across sessions.
DEFAULT_START = datetime(2024, 1, 2)

#: Upper bound on paths per experiment: keeps the paths array comfortably in
#: memory (20_000 paths x 253 steps x 8 bytes ~ 40 MB) and the GUI responsive.
MAX_PATHS = 20_000

#: Percentiles reported for the terminal-price distribution.
TERMINAL_PERCENTILES = (5, 25, 50, 75, 95)


@dataclass(frozen=True)
class GBMSimulationResult:
    """One complete GBM experiment.

    ``paths`` has shape ``(n_paths, n_days + 1)``; ``history`` wraps path 0
    on the synthetic calendar, so the price series shown in the lab is itself
    one draw from the same ensemble the Monte Carlo panel summarizes.
    """

    params: GeometricBrownianMotion
    dates: pd.DatetimeIndex
    paths: np.ndarray = field(repr=False)
    history: PriceHistory = field(repr=False)

    @property
    def n_days(self) -> int:
        return self.paths.shape[1] - 1

    @property
    def n_paths(self) -> int:
        return self.paths.shape[0]

    @property
    def horizon_years(self) -> float:
        """:math:`T = n_{\\text{days}} \\cdot dt` in years."""
        return self.n_days * self.params.dt

    @property
    def terminal_prices(self) -> np.ndarray:
        """:math:`S_T` of every path, shape ``(n_paths,)``."""
        return self.paths[:, -1]

    def expected_terminal_price(self) -> float:
        """Closed form :math:`\\mathbb{E}[S_T] = S_0 e^{\\mu T}`."""
        p = self.params
        return p.initial_price * float(np.exp(p.mu * self.horizon_years))

    def median_terminal_price(self) -> float:
        """Closed form median :math:`S_0 e^{(\\mu - \\sigma^2/2)T}`."""
        p = self.params
        return p.initial_price * float(
            np.exp((p.mu - 0.5 * p.sigma**2) * self.horizon_years)
        )

    def expected_price_curve(self) -> np.ndarray:
        """:math:`\\mathbb{E}[S_t] = S_0 e^{\\mu t}` at every step (for the
        Monte Carlo fan chart's reference line)."""
        p = self.params
        t = np.arange(self.n_days + 1) * p.dt
        return p.initial_price * np.exp(p.mu * t)


def run_gbm_simulation(
    initial_price: float,
    mu: float,
    sigma: float,
    n_days: int,
    seed: int | None,
    n_paths: int = 1,
    dt: float = 1 / 252,
    start: datetime = DEFAULT_START,
) -> GBMSimulationResult:
    """Simulate ``n_paths`` GBM paths of ``n_days`` steps and package them.

    ``initial_price``/``mu``/``sigma``/``dt`` are validated by
    ``GeometricBrownianMotion`` itself; this function validates only the
    experiment shape. ``n_days >= 2`` so every downstream estimator (which
    needs at least two returns) is defined.
    """
    if n_days < 2:
        raise ValueError(f"n_days must be >= 2, got {n_days}")
    if not 1 <= n_paths <= MAX_PATHS:
        raise ValueError(f"n_paths must be in [1, {MAX_PATHS}], got {n_paths}")

    params = GeometricBrownianMotion(
        initial_price=initial_price, mu=mu, sigma=sigma, dt=dt, seed=seed
    )
    paths = params.simulate_paths(n_days, n_paths)
    dates = pd.bdate_range(start, periods=n_days + 1)
    history = PriceHistory(
        symbol="GBM",
        source="simulation",
        frame=pd.DataFrame({"close": paths[0]}, index=dates),
    )
    return GBMSimulationResult(params=params, dates=dates, paths=paths, history=history)


@dataclass(frozen=True)
class MonteCarloSummary:
    """Descriptive statistics of the terminal-price distribution.

    ``percentiles`` maps percentile level -> price (levels from
    ``TERMINAL_PERCENTILES``). ``expected``/``theoretical_median`` are the
    closed-form lognormal values for comparison against the sample
    ``mean``/``median`` — with few paths they visibly disagree, which is the
    point.
    """

    n_paths: int
    mean: float
    median: float
    std: float
    percentiles: dict[int, float]
    expected: float
    theoretical_median: float


def monte_carlo_summary(result: GBMSimulationResult) -> MonteCarloSummary:
    """Summarize ``result.terminal_prices`` against the closed forms."""
    terminal = result.terminal_prices
    levels = np.percentile(terminal, TERMINAL_PERCENTILES)
    return MonteCarloSummary(
        n_paths=result.n_paths,
        mean=float(terminal.mean()),
        median=float(np.median(terminal)),
        std=float(terminal.std(ddof=1)) if len(terminal) >= 2 else float("nan"),
        percentiles=dict(zip(TERMINAL_PERCENTILES, (float(x) for x in levels))),
        expected=result.expected_terminal_price(),
        theoretical_median=result.median_terminal_price(),
    )


def path_percentiles(
    result: GBMSimulationResult, levels: tuple[int, ...] = TERMINAL_PERCENTILES
) -> dict[int, np.ndarray]:
    r"""Cross-sectional percentiles of the paths at every step: the Monte
    Carlo fan's bands.

    At step :math:`k` (:math:`t_k = k\,dt`) the :math:`q`-quantile of
    :math:`S_{t_k}` has the lognormal closed form

    .. math:: S_0 \exp\!\big((\mu - \tfrac12\sigma^2)\,t_k + z_q\,\sigma\sqrt{t_k}\big)

    with :math:`z_q = \Phi^{-1}(q)`; the sample bands converge to it as
    ``n_paths`` grows (the 50th is the median curve, below the mean curve
    :math:`S_0 e^{\mu t}` by the Ito correction). Each value has shape
    ``(n_days + 1,)``; step 0 is :math:`S_0` for every level.
    """
    if result.n_paths < 2:
        raise ValueError("path_percentiles needs at least 2 paths")
    if not all(0 <= q <= 100 for q in levels):
        raise ValueError(f"percentile levels must lie in [0, 100], got {levels}")
    values = np.percentile(result.paths, levels, axis=0)
    return dict(zip(levels, values, strict=True))
