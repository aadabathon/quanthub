r"""Joint SSVI fit: one arbitrage-free surface through every expiry at once.

Milestone C3. The derivation below was reviewed before the code.

Why
---
The independent raw-SVI fits (``models/vol_surface.fit_surface``) give
each expiry 5 free parameters, 30 for six expiries. On noisy real chains
they overfit: D30 records several vol points of ATM error at 40% spreads
and 2× noise. Their butterfly and calendar checks *test* the result but
can't prevent a failure. A joint SSVI fit has :math:`N + 3` parameters for
:math:`N` expiries, 9 for six, and is free of static arbitrage *by
construction*. Each expiry contributes one number, its ATM level, and the
shape comes from the whole surface.

The model
---------
SSVI (Gatheral & Jacquier, 2014), with the same notation as ``models/svi``.
At expiry :math:`T_i` with ATM total variance :math:`\theta_i`,

.. math::

    w(k, \theta) = \frac{\theta}{2}\left(1 + \rho\varphi(\theta)k
        + \sqrt{(\varphi(\theta)k + \rho)^2 + 1 - \rho^2}\right),
    \qquad
    \varphi(\theta) = \frac{\eta}{\theta^{\gamma}(1+\theta)^{1-\gamma}}.

The parameters are :math:`\theta_1, \dots, \theta_N` (one per expiry) and
the global :math:`\rho, \eta, \gamma`. The synthetic truth (``SSVISurface``)
is this family with :math:`\gamma = 1/2` and a mean-reverting
:math:`\theta(T)`, so the fit has a known answer there. Each fitted slice
converts to raw SVI exactly (``svi`` module docstring), so every existing
view, check and mesh works unchanged.

What the parameters mean
------------------------
At the money, :math:`\partial_k w|_{k=0} = \rho\,\theta\varphi(\theta)`
(differentiate under the root: its derivative at 0 is
:math:`\varphi\rho`). In implied vol, with :math:`\theta = \sigma^2 T`,

.. math::  \partial_k\sigma|_{k=0} = \frac{\rho\,\theta\varphi(\theta)}{2\sigma T}
           \;\approx\; \frac{\rho\,\eta}{2}\,\sigma^{1-2\gamma}\,T^{-\gamma}
           \quad (\theta \ll 1,\ \varphi \approx \eta\theta^{-\gamma}).

* :math:`\rho` is the sign and size of the skew.
* :math:`\eta` scales the curvature, together with the skew.
* :math:`\gamma` sets how the ATM skew decays with maturity, roughly as
  :math:`T^{-\gamma}`. Equity skew is usually quoted decaying between
  :math:`T^{-0.4}` and :math:`T^{-0.5}`, which is why :math:`\gamma` is
  fitted rather than fixed at 1/2.

No static arbitrage, derived
----------------------------
Gatheral–Jacquier give sufficient conditions per slice. Write
:math:`u = \theta/(1+\theta) \in (0, 1)`. Then

.. math::  \theta\varphi = \eta\,u^{1-\gamma}, \qquad
           \theta\varphi^2 = \eta^2\,\frac{u^{1-2\gamma}}{1+\theta}.

*Butterfly* (sufficient): :math:`\theta\varphi(1+|\rho|) < 4` and
:math:`\theta\varphi^2(1+|\rho|) \le 4`.

* For :math:`0 < \gamma \le 1/2` both exponents are non-negative and
  :math:`u < 1`, so :math:`\theta\varphi < \eta` and
  :math:`\theta\varphi^2 < \eta^2`.
* Hence :math:`\eta(1+|\rho|) \le 2` gives :math:`\theta\varphi(1+|\rho|) < 2 < 4`
  and :math:`\theta\varphi^2(1+|\rho|) < \eta^2(1+|\rho|)^2 \le 4`.
* So that single condition holds **for every** :math:`\theta`: no cap on
  maturity or vol level. That's the purpose of the :math:`(1+\theta)`
  factor.
* For :math:`\gamma > 1/2`, :math:`\theta\varphi^2 \to \infty` as
  :math:`\theta \to 0`, so short expiries would break it. That's why
  :math:`\gamma \le 1/2`.

*Calendar*: :math:`\theta_i` must be nondecreasing in :math:`T`, and
:math:`0 \le \partial_\theta(\theta\varphi) \le \rho^{-2}(1+\sqrt{1-\rho^2})\varphi`.

* :math:`\partial_\theta(\theta\varphi) = \eta(1-\gamma)u^{-\gamma}/(1+\theta)^2 \ge 0`.
* Its ratio to :math:`\varphi` is :math:`(1-\gamma)/(1+\theta) < 1`.
* :math:`\rho^{-2}(1+\sqrt{1-\rho^2}) \ge 1` for :math:`|\rho| < 1`, so the
  upper bound always holds.
* So the surface is calendar-free **iff** the :math:`\theta_i` are
  nondecreasing.

The tests re-check all of this numerically on grids (``g(k) \ge 0`` and
:math:`w` nondecreasing in :math:`\theta` at fixed :math:`k`) rather than
trusting the algebra.

Coordinates (constraints by construction)
-----------------------------------------
The shared Nelder–Mead searches unconstrained coordinates. Here :math:`s`
is the median observed ATM total variance, used only for scale:

* :math:`\theta_1 = s\,e^{x_1}` and :math:`\theta_i = \theta_{i-1} + s\,e^{x_i}`:
  increasing, so calendar-free;
* :math:`\rho = \tanh y`;
* :math:`\eta = \frac{2}{1+|\rho|}\,\mathrm{sigmoid}(z)`: butterfly-free;
* :math:`\gamma = \tfrac12\,\mathrm{sigmoid}(v)`.

**Every point the optimizer can visit is an arbitrage-free surface.**

The fit
-------
The objective is the same weighted least squares as the slice fits, in
total variance, over every usable (out-of-the-money, two-sided) quote:

.. math::  \chi^2 = \sum_{i}\sum_{j} \left(\frac{w(k_{ij}, \theta_i) - w^{obs}_{ij}}
                                     {\delta w_{ij}}\right)^2,

with :math:`\delta w` the half-spread propagated to total variance
(``vol_surface``). :math:`\chi^2` per point is directly comparable with the
independent fits. Being lower there isn't the goal: the independent fits
spend 30 parameters on noise.

The search, profiled. It minimizes the same :math:`\chi^2` over the same
constrained set, but not by one Nelder–Mead run in :math:`N + 3`
dimensions. That version stalled in testing: under heavy noise an expiry's
θ increment shrank toward zero, where :math:`e^{x}` has almost no gradient,
and the fit ended at :math:`\chi^2` = 430 when the truth scored 331. So:

* **Each θ given the shape.** For a fixed shape :math:`(\rho, \eta, \gamma)`,
  :math:`\chi^2` separates by expiry, so each :math:`\theta_i` is a
  one-dimensional problem. :math:`w` rises with :math:`\theta` at every
  :math:`k` (that's the calendar condition), so each expiry's loss has one
  valley. A golden-section search on :math:`\ln\theta` finds it, for all
  expiries in lockstep, within ×/÷ 30 of the observed ATM level.
* **Calendar order.** Where the per-expiry optima decrease, adjacent
  violators are pooled and given one common θ that minimizes their summed
  loss, repeating until the sequence is nondecreasing (pool adjacent
  violators). That's the exact constrained answer for convex per-expiry
  losses, and a close one for these single-valley losses.
* **The shape.** Nelder–Mead then searches only :math:`(\rho, \eta, \gamma)`,
  in the coordinates below, from :math:`\rho \in \{-0.7, -0.4, 0\}`,
  :math:`\gamma = 0.4` and :math:`\eta` at half its bound, plus a restart
  from the best point. In 3 dimensions it converges reliably.

The coordinates above remain the parameter space. Profiling only changes
how the minimum is found.

The fit reports overall and per-expiry :math:`\chi^2/\text{pt}`,
:math:`\rho`, :math:`\eta`, :math:`\gamma`, and :math:`\theta_i`. It needs
more quotes than :math:`N + 3`.

Limits
------
* With :math:`\gamma` fitted, the true :math:`\gamma = 1/2` is the edge of
  its range. Nelder–Mead approaches it without reaching it, so the
  synthetic recovery checks the surface tightly and :math:`\gamma` loosely.
* One :math:`\rho` for the whole surface. Real skew can steepen or flatten
  across maturities in ways three global parameters can't follow.
* **Measured live (2026-10-15): real SPY/SPX smiles lie outside the SSVI
  slice family itself.**
  - Joint χ²/pt: SPY 1,015 against independent SVI's 7; SPX 28 against
    0.6.
  - SSVI fitted to each expiry *alone*, with its own ρ and η, is still far
    off: SPY 42–1,732, SPX 2–21.
  - η sits at its no-arbitrage bound on most SPY expiries.
  - So the rigidity isn't the shared shape. An SSVI slice ties the SVI
    vertex position and curvature together, and real smiles need them
    apart. Extended SSVI (per-expiry ρ) wouldn't help, since its slices
    are the same family.
  - Hence D35: independent SVI is the default, and this fit is the option
    for noisy or thin chains, where it wins clearly (synthetic heavy noise
    over 8 seeds: worst ATM error 4.6 → 1.6 vol points, arbitrage-free 8
    of 8 against SVI's 3).

Validation plan (tests)
-----------------------
1. A noise-free synthetic SSVI chain is reproduced: every fitted slice
   within 1e-4 vol of the true smile over its quoted range; :math:`\theta_i`
   to 1e-6 relative; :math:`\rho, \eta` to 1e-3; :math:`\gamma` within
   0.02 of 1/2.
2. Heavy noise (40% spreads, 2× noise, the D30 case): the joint fit's
   median IV error against the truth, and its ATM errors, are well below
   the independent fits'.
3. No static arbitrage by construction: random coordinates map to surfaces
   with :math:`g \ge 0` on wide grids and :math:`w` nondecreasing across
   expiries at fixed :math:`k`. Every fitted surface passes the existing
   checks.
4. On a correct model, :math:`\chi^2/\text{pt} \approx \text{noise}^2\,(n-p)/n`,
   and the fit never scores worse than the true parameters (the check
   that caught the stalled search).
5. Invalid inputs are rejected: too few quotes, non-finite values.

Live check: SPY and SPX, joint vs independent χ²/pt per expiry, the fitted
:math:`\rho, \eta, \gamma`, and refit stability between two fetches.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from quant_hub.models.optimize import nelder_mead
from quant_hub.models.svi import RawSVI

GAMMA_MAX = 0.5
"""Largest power-law exponent that keeps every θ butterfly-free."""

RHO_STARTS = (-0.7, -0.4, 0.0)
GAMMA_START = 0.4
MAX_ITERATIONS = 2000
"""Nelder–Mead iterations per start (the shape search has 3 dimensions)."""


@dataclass(frozen=True)
class SSVIParameters:
    """One surface: nondecreasing ATM total variances and the global shape.

    Validated against the no-arbitrage region of the module docstring.
    """

    thetas: tuple[float, ...]
    rho: float
    eta: float
    gamma: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "thetas", tuple(float(t) for t in self.thetas))
        values = (*self.thetas, self.rho, self.eta, self.gamma)
        if not self.thetas or not all(math.isfinite(v) for v in values):
            raise ValueError("SSVI parameters must be finite, with at least one theta")
        if any(t <= 0 for t in self.thetas) or any(
                b < a for a, b in zip(self.thetas, self.thetas[1:])):
            raise ValueError("thetas must be positive and nondecreasing (calendar)")
        if not -1 < self.rho < 1:
            raise ValueError("rho must lie strictly between -1 and 1")
        if not 0 < self.gamma <= GAMMA_MAX:
            raise ValueError("gamma must lie in (0, 1/2]")
        if self.eta <= 0 or self.eta * (1 + abs(self.rho)) > 2 + 1e-12:
            raise ValueError("need 0 < eta and eta(1+|rho|) <= 2 (butterfly)")

    def phi(self, theta: float | np.ndarray) -> float | np.ndarray:
        r""":math:`\varphi(\theta) = \eta/(\theta^\gamma(1+\theta)^{1-\gamma})`."""
        return self.eta / (theta**self.gamma * (1 + theta) ** (1 - self.gamma))

    def slice(self, index: int) -> RawSVI:
        """Expiry ``index`` as exact raw SVI (``svi`` module docstring)."""
        theta = self.thetas[index]
        phi = float(self.phi(theta))
        return RawSVI(a=0.5 * theta * (1 - self.rho**2), b=0.5 * theta * phi, rho=self.rho,
                      m=-self.rho / phi, sigma=math.sqrt(1 - self.rho**2) / phi)


@dataclass(frozen=True)
class JointSSVIFit:
    """The fitted surface and its quality, overall and per expiry."""

    dtes: tuple[float, ...]
    params: SSVIParameters
    chi2_per_point: float
    slice_chi2_per_point: tuple[float, ...]
    slice_rmse_vol: tuple[float, ...]
    slice_points: tuple[int, ...]
    converged: bool
    iterations: int

    @property
    def n_points(self) -> int:
        return sum(self.slice_points)

    @property
    def n_parameters(self) -> int:
        return len(self.dtes) + 3

    def slice(self, dte: float) -> RawSVI:
        return self.params.slice(self.dtes.index(dte))


def total_variance(k: np.ndarray, theta: np.ndarray, rho: float, eta: float,
                   gamma: float) -> np.ndarray:
    r"""SSVI :math:`w(k, \theta)`, vectorized over matching ``k`` and ``theta``."""
    phi = eta / (theta**gamma * (1 + theta) ** (1 - gamma))
    return 0.5 * theta * (1 + rho * phi * k + np.sqrt((phi * k + rho) ** 2 + 1 - rho**2))


def fit_joint_ssvi(dtes: Sequence[float], k: Sequence[np.ndarray], total_variance_obs:
                   Sequence[np.ndarray], uncertainty: Sequence[np.ndarray],
                   max_iterations: int = MAX_ITERATIONS) -> JointSSVIFit:
    r"""Weighted least squares over every expiry at once (module docstring).

    Per expiry (ascending ``dtes``): log-moneyness, observed total variance,
    and its uncertainty :math:`\delta w`. ``max_iterations`` bounds each
    Nelder–Mead run over the shape."""
    dtes = tuple(float(d) for d in dtes)
    n = len(dtes)
    if n == 0 or not (len(k) == len(total_variance_obs) == len(uncertainty) == n):
        raise ValueError("need matching per-expiry arrays for at least one expiry")
    if any(d <= 0 or not math.isfinite(d) for d in dtes) or list(dtes) != sorted(set(dtes)):
        raise ValueError("expiries must be positive, distinct and ascending")
    arrays = [tuple(np.asarray(a, dtype=float) for a in triple)
              for triple in zip(k, total_variance_obs, uncertainty)]
    for ks, ws, dws in arrays:
        if not (ks.ndim == ws.ndim == dws.ndim == 1 and len(ks) == len(ws) == len(dws) > 0):
            raise ValueError("each expiry needs matching, non-empty 1-D arrays")
        if not (np.isfinite(ks).all() and np.isfinite(ws).all() and np.isfinite(dws).all()):
            raise ValueError("fit inputs must be finite")
        if np.any(ws <= 0) or np.any(dws <= 0):
            raise ValueError("total variances and uncertainties must be positive")
    counts = [len(a[0]) for a in arrays]
    if sum(counts) <= n + 3:
        raise ValueError(f"need more than {n + 3} quotes for {n} expiries")
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    data = _Points(arrays)
    observed_atm = np.array([_atm_total_variance(ks, ws) for ks, ws, _ in arrays])

    def shape(x: np.ndarray) -> tuple[float, float, float]:
        rho = math.tanh(float(x[0]))
        eta = 2.0 / (1.0 + abs(rho)) * _sigmoid(float(x[1]))
        gamma = GAMMA_MAX * _sigmoid(float(x[2]))
        return rho, eta, gamma

    def objective(x: np.ndarray) -> float:
        rho, eta, gamma = shape(x)
        if not (eta > 0 and gamma > 0):
            return math.inf
        thetas = _profile_thetas(data, observed_atm, rho, eta, gamma)
        value = float(data.losses(thetas[data.index], rho, eta, gamma).sum())
        return value if math.isfinite(value) else math.inf

    best = None
    iterations = 0
    for rho in RHO_STARTS:
        result = nelder_mead(objective, np.array([math.atanh(rho), 0.0,
                                                  _logit(GAMMA_START / GAMMA_MAX)]),
                             max_iterations)
        iterations += result.iterations
        if best is None or result.value < best.value:
            best = result
    assert best is not None
    restart = nelder_mead(objective, best.x, max_iterations)
    iterations += restart.iterations
    if restart.value <= best.value:
        best = restart

    rho, eta, gamma = shape(best.x)
    thetas = _profile_thetas(data, observed_atm, rho, eta, gamma)
    params = SSVIParameters(tuple(thetas), rho, eta, gamma)
    fitted = total_variance(data.k, thetas[data.index], rho, eta, gamma)
    residuals = ((fitted - data.w) / data.dw) ** 2
    years = np.asarray(dtes)[data.index] / 365.0
    vol_errors = (np.sqrt(fitted / years) - np.sqrt(data.w / years)) ** 2
    slice_chi2 = tuple(float(residuals[data.index == i].mean()) for i in range(n))
    slice_rmse = tuple(float(math.sqrt(vol_errors[data.index == i].mean())) for i in range(n))
    return JointSSVIFit(
        dtes=dtes, params=params, chi2_per_point=float(residuals.mean()),
        slice_chi2_per_point=slice_chi2, slice_rmse_vol=slice_rmse,
        slice_points=tuple(counts), converged=best.converged, iterations=iterations)


_GOLDEN = (math.sqrt(5.0) - 1.0) / 2.0
_GOLDEN_STEPS = 40          # shrinks a ×/÷ 30 bracket in ln θ to about 3e-8
_LOG_BRACKET = math.log(30.0)


class _Points:
    """Every quote of every expiry as flat arrays, with each quote's expiry."""

    def __init__(self, arrays) -> None:
        self.n = len(arrays)
        self.index = np.repeat(np.arange(self.n), [len(a[0]) for a in arrays])
        self.k = np.concatenate([a[0] for a in arrays])
        self.w = np.concatenate([a[1] for a in arrays])
        self.dw = np.concatenate([a[2] for a in arrays])

    def losses(self, theta_per_point: np.ndarray, rho: float, eta: float,
               gamma: float) -> np.ndarray:
        """Each expiry's chi-squared, for a θ given per quote."""
        with np.errstate(over="ignore", invalid="ignore"):
            fitted = total_variance(self.k, theta_per_point, rho, eta, gamma)
            squared = ((fitted - self.w) / self.dw) ** 2
        return np.bincount(self.index, weights=squared, minlength=self.n)


def _profile_thetas(data: _Points, observed_atm: np.ndarray, rho: float, eta: float,
                    gamma: float) -> np.ndarray:
    """The best nondecreasing θ for this shape (module docstring, "The search")."""
    def loss(log_thetas: np.ndarray) -> np.ndarray:
        return data.losses(np.exp(log_thetas)[data.index], rho, eta, gamma)

    # Golden section, every expiry in lockstep. Each step keeps one interior
    # point and its loss, so it costs one new evaluation, not two.
    low = np.log(observed_atm) - _LOG_BRACKET
    high = np.log(observed_atm) + _LOG_BRACKET
    inner_low = high - _GOLDEN * (high - low)
    inner_high = low + _GOLDEN * (high - low)
    loss_low, loss_high = loss(inner_low), loss(inner_high)
    for _ in range(_GOLDEN_STEPS):
        lower_wins = loss_low < loss_high
        # Lower wins: keep [low, inner_high]; the old inner_low becomes the
        # new inner_high. Otherwise keep [inner_low, high], mirrored.
        high = np.where(lower_wins, inner_high, high)
        low = np.where(lower_wins, low, inner_low)
        moved = np.where(lower_wins, inner_low, inner_high)
        moved_loss = np.where(lower_wins, loss_low, loss_high)
        fresh = np.where(lower_wins, high - _GOLDEN * (high - low), low + _GOLDEN * (high - low))
        fresh_loss = loss(fresh)
        inner_low = np.where(lower_wins, fresh, moved)
        inner_high = np.where(lower_wins, moved, fresh)
        loss_low = np.where(lower_wins, fresh_loss, moved_loss)
        loss_high = np.where(lower_wins, moved_loss, fresh_loss)
    log_thetas = list(0.5 * (low + high))

    # Pool adjacent violators: merge decreasing neighbours into one block
    # with a common θ minimizing their summed loss, until nondecreasing.
    blocks = [[i] for i in range(data.n)]
    values = log_thetas
    i = 0
    while i < len(blocks) - 1:
        if values[i] <= values[i + 1]:
            i += 1
            continue
        merged = blocks[i] + blocks[i + 1]
        blocks[i:i + 2] = [merged]
        values[i:i + 2] = [_pooled_log_theta(data, merged, observed_atm, rho, eta, gamma)]
        i = max(i - 1, 0)
    thetas = np.empty(data.n)
    for block, value in zip(blocks, values):
        thetas[block] = math.exp(value)
    return thetas


def _pooled_log_theta(data: _Points, block: list[int], observed_atm: np.ndarray, rho: float,
                      eta: float, gamma: float) -> float:
    """One common ln θ for a block of expiries: golden section on their summed loss."""
    mask = np.isin(data.index, block)
    k, w, dw = data.k[mask], data.w[mask], data.dw[mask]

    def loss(log_theta: float) -> float:
        with np.errstate(over="ignore", invalid="ignore"):
            fitted = total_variance(k, np.full(len(k), math.exp(log_theta)), rho, eta, gamma)
            return float(np.sum(((fitted - w) / dw) ** 2))

    centre = float(np.mean(np.log(observed_atm[block])))
    low, high = centre - _LOG_BRACKET, centre + _LOG_BRACKET
    for _ in range(_GOLDEN_STEPS):
        inner_low = high - _GOLDEN * (high - low)
        inner_high = low + _GOLDEN * (high - low)
        if loss(inner_low) < loss(inner_high):
            high = inner_high
        else:
            low = inner_low
    return 0.5 * (low + high)


def _atm_total_variance(k: np.ndarray, w: np.ndarray) -> float:
    """Observed total variance at k = 0: linear between the nearest quotes on
    either side, else the nearest quote (the starting θ)."""
    below, above = k < 0, k >= 0
    if below.any() and above.any():
        i, j = np.where(below)[0][np.argmax(k[below])], np.where(above)[0][np.argmin(k[above])]
        if k[j] == k[i]:
            return float(w[j])
        return float(w[i] + (w[j] - w[i]) * (0.0 - k[i]) / (k[j] - k[i]))
    return float(w[np.argmin(np.abs(k))])


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))
