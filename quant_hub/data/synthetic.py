r"""Synthetic price-path generators for testing models and driving the app offline.

Geometric Brownian Motion (GBM)
===============================

Mathematical description
------------------------
GBM models a price process :math:`S_t` by the stochastic differential equation

.. math::  dS_t = \mu S_t\,dt + \sigma S_t\,dW_t

where :math:`\mu` is the (annualized) drift, :math:`\sigma` the (annualized)
volatility, and :math:`W_t` a standard Brownian motion. Applying Ito's lemma
to :math:`\ln S_t` gives

.. math::  d(\ln S_t) = \left(\mu - \tfrac{1}{2}\sigma^2\right)dt + \sigma\,dW_t

The :math:`-\tfrac{1}{2}\sigma^2` term is the Ito correction: the drift of the
*log* price is lower than the drift of the price because the exponential is
convex. Integrating over a step of length :math:`\Delta t` (over which the
Brownian increment is exactly :math:`N(0, \Delta t)`) yields the **exact**
discrete transition

.. math::  S_{t+\Delta t} = S_t \exp\!\left[
               \left(\mu - \tfrac{1}{2}\sigma^2\right)\Delta t
               + \sigma\sqrt{\Delta t}\, Z\right],
           \qquad Z \sim N(0, 1)

This is what ``simulate`` implements. It is *not* an Euler approximation --
for GBM the log-space step is distributionally exact for any step size, and
because it exponentiates, simulated prices are strictly positive by
construction.

Useful closed-form facts (used by the statistical tests):

.. math::  \mathbb{E}[\ln(S_T/S_0)] = (\mu - \tfrac{1}{2}\sigma^2)T, \qquad
           \mathrm{Var}[\ln(S_T/S_0)] = \sigma^2 T

Assumptions and limitations
---------------------------
Constant drift and volatility, continuous paths, independent Gaussian log
returns. Real asset returns exhibit fat tails, volatility clustering, and
jumps -- GBM is the transparent baseline those richer models (GARCH,
regime-switching) will later be compared against, not a claim about markets.

Units
-----
``mu`` and ``sigma`` are annualized; ``dt`` is the step size in years
(1/252 = one trading day). ``simulate(n_steps)`` therefore covers
``n_steps * dt`` years.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class GeometricBrownianMotion:
    """Exact-discretization GBM path generator.

    Example::

        generator = GeometricBrownianMotion(
            initial_price=100, mu=0.08, sigma=0.20, dt=1 / 252, seed=42
        )
        prices = generator.simulate(252)   # 253 prices spanning one year

    A fixed ``seed`` makes every call to ``simulate``/``simulate_paths``
    deterministic (each call creates a fresh generator from the seed, so
    repeated calls return identical output rather than continuing a stream).
    ``seed=None`` draws fresh OS entropy per call.
    """

    initial_price: float
    mu: float
    sigma: float
    dt: float = 1 / TRADING_DAYS_PER_YEAR
    seed: int | None = None

    def __post_init__(self) -> None:
        if not self.initial_price > 0:
            raise ValueError(f"initial_price must be > 0, got {self.initial_price}")
        if self.sigma < 0:
            raise ValueError(f"sigma must be >= 0, got {self.sigma}")
        if not self.dt > 0:
            raise ValueError(f"dt must be > 0, got {self.dt}")

    def simulate(self, n_steps: int) -> np.ndarray:
        """Simulate one path.

        Returns an array of length ``n_steps + 1``; element 0 is
        ``initial_price`` and element ``k`` is the price after ``k`` steps.
        """
        return self.simulate_paths(n_steps, n_paths=1)[0]

    def simulate_paths(self, n_steps: int, n_paths: int) -> np.ndarray:
        """Simulate many independent paths at once (vectorized).

        Returns shape ``(n_paths, n_steps + 1)``. Used by Monte Carlo-style
        consumers and by the statistical tests that check simulated moments
        against the closed-form ones.
        """
        if n_steps < 1:
            raise ValueError(f"n_steps must be >= 1, got {n_steps}")
        if n_paths < 1:
            raise ValueError(f"n_paths must be >= 1, got {n_paths}")

        rng = np.random.default_rng(self.seed)
        z = rng.standard_normal((n_paths, n_steps))

        # Exact log-space transition: ln S_{k+1} - ln S_k
        #   = (mu - sigma^2/2) dt + sigma sqrt(dt) Z_k
        drift = (self.mu - 0.5 * self.sigma**2) * self.dt
        diffusion = self.sigma * np.sqrt(self.dt)
        log_increments = drift + diffusion * z

        log_paths = np.cumsum(log_increments, axis=1)
        paths = np.empty((n_paths, n_steps + 1))
        paths[:, 0] = self.initial_price
        paths[:, 1:] = self.initial_price * np.exp(log_paths)
        return paths
