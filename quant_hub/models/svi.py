r"""SVI volatility smiles: the raw parameterization, the SSVI surface, and fitting.

Coordinates
-----------
Smiles live in log-forward-moneyness :math:`k = \ln(K/F)`, with
:math:`F = Se^{(r-q)T}`, and in *total implied variance*
:math:`w(k) = \sigma_{BS}^2(k)\,T`. Working in :math:`w` rather than
:math:`\sigma` makes the no-arbitrage conditions below simple, and it makes
slices at different expiries comparable: :math:`w` must not decrease in
:math:`T`.

Raw SVI (Gatheral, 2004)
------------------------
.. math::  w(k) = a + b\left(\rho(k - m) + \sqrt{(k-m)^2 + \sigma^2}\right)

It is a hyperbola in :math:`k`. :math:`a` sets the level, :math:`b` the
wing angle, :math:`\rho` the skew (rotation), :math:`m` the horizontal
shift, and :math:`\sigma` the ATM curvature (smoothness of the vertex).
Its extremes and asymptotes:

* the minimum is at :math:`k^* = m - \rho\sigma/\sqrt{1-\rho^2}`, where
  :math:`w_{min} = a + b\sigma\sqrt{1-\rho^2}`. Validity needs
  :math:`w_{min} \ge 0`;
* the wings are linear with slopes :math:`b(1+\rho)` (right) and
  :math:`-b(1-\rho)` (left). Roger Lee's moment formula bounds any
  arbitrage-free smile's wing slopes by 2, so we require
  :math:`b(1+|\rho|) \le 2`.

Derivatives (used by the density check and tested against finite differences):

.. math::  w'(k) = b\left(\rho + \frac{k-m}{\sqrt{(k-m)^2+\sigma^2}}\right), \qquad
           w''(k) = \frac{b\,\sigma^2}{\left((k-m)^2+\sigma^2\right)^{3/2}}

Butterfly (density) arbitrage
-----------------------------
A smile is free of butterfly arbitrage iff the implied risk-neutral density
is non-negative. In total-variance coordinates (Gatheral & Jacquier, 2014)
that density is

.. math::  p(k) = \frac{g(k)}{\sqrt{2\pi w(k)}}\exp\!\left(-\frac{d_-(k)^2}{2}\right),
           \qquad d_-(k) = -\frac{k}{\sqrt{w}} - \frac{\sqrt{w}}{2},

.. math::  g(k) = \left(1 - \frac{k\,w'}{2w}\right)^2
                  - \frac{w'^2}{4}\left(\frac{1}{w} + \frac{1}{4}\right) + \frac{w''}{2},

so the condition is :math:`g(k) \ge 0` everywhere. (:math:`p` is the density
of :math:`\ln(S_T/F)`. The tests confirm it against Breeden–Litzenberger,
:math:`\partial^2 C/\partial K^2 = e^{-rT}p(k)/K`.)

SSVI surface (Gatheral & Jacquier, 2014)
----------------------------------------
The synthetic "true" world. Given the ATM total variance :math:`\theta(T)`,

.. math::  w(k, \theta) = \frac{\theta}{2}\left(1 + \rho\varphi(\theta)k
           + \sqrt{(\varphi(\theta)k + \rho)^2 + 1 - \rho^2}\right),
           \qquad \varphi(\theta) = \frac{\eta}{\sqrt{\theta(1+\theta)}},

(their power law with :math:`\gamma = 1/2`). At :math:`k = 0` this gives
:math:`w = \theta`, so :math:`\theta` is exactly the ATM total variance.
Each slice is a raw SVI with

.. math::  a = \tfrac{\theta}{2}(1-\rho^2),\; b = \tfrac{\theta\varphi}{2},\;
           \rho = \rho,\; m = -\frac{\rho}{\varphi},\; \sigma = \frac{\sqrt{1-\rho^2}}{\varphi}

(substitute and expand: the :math:`\theta\rho^2/2` terms recombine). So
fitting raw SVI per expiry has a known answer. With
:math:`\eta(1+|\rho|) \le 2` and :math:`\theta(T)` increasing, the surface is
free of static arbitrage; the tests check both conditions numerically on a
grid rather than trusting the citation.

ATM term structure. We use mean-reverting variance: the expected *average*
variance over :math:`[0, T]` when the instantaneous variance relaxes from
:math:`v_0` to :math:`v_\infty` at rate :math:`\kappa`,

.. math::  \theta(T) = v_\infty T + (v_0 - v_\infty)\frac{1 - e^{-\kappa T}}{\kappa},

Its slope :math:`\theta'(T) = v_\infty + (v_0 - v_\infty)e^{-\kappa T} > 0`, so
it is increasing (no calendar arbitrage at the money). This is the
continuous-time analogue of GARCH's average-variance forecast
(``models/garch.average_variance_forecast``), which is why the lab can put
the two side by side.

Fitting
-------
``fit_raw_svi`` minimizes :math:`\frac{1}{n}\sum_i \left((w(k_i) - w_i)/\delta w_i\right)^2`,
where :math:`\delta w_i` is each observation's uncertainty in total-variance
units, so a good fit scores about 1. The search runs over unconstrained
coordinates that satisfy the validity constraints by construction:
:math:`w_{min} = e^{x_0}\cdot s`, :math:`\rho = \tanh x_2`,
:math:`b = \frac{2}{1+|\rho|}\,\text{sigmoid}(x_1)` (Lee's bound),
:math:`m = x_3\sqrt{s}`, :math:`\sigma = e^{x_4}\sqrt{s}`, and
:math:`a = w_{min} - b\sigma\sqrt{1-\rho^2}`. Here :math:`s` is the
observed ATM-level variance, used only for scaling. The shared Nelder–Mead
(``models/optimize.py``) runs from three skew starts, then restarts once
from the best point, a standard remedy for Nelder–Mead stalling in 5-D.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from quant_hub.models.optimize import nelder_mead

LEE_WING_BOUND = 2.0
"""Maximum wing slope of total variance in |k| (Lee's moment formula)."""

MIN_SLICE_QUOTES = 6
"""Fewest quotes accepted for a five-parameter slice fit (one spare dof)."""


def butterfly_g(k: np.ndarray, w: np.ndarray, w1: np.ndarray, w2: np.ndarray) -> np.ndarray:
    """:math:`g(k)` (module docstring) from any smile's total variance and its
    first two k-derivatives, so smiles that aren't one SVI (an interpolated
    surface row) are checked by the same formula."""
    return (1 - k * w1 / (2 * w)) ** 2 - (w1**2 / 4) * (1 / w + 0.25) + w2 / 2


@dataclass(frozen=True)
class RawSVI:
    """One raw-SVI smile in total variance vs log-forward-moneyness."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.a, self.b, self.rho, self.m, self.sigma)):
            raise ValueError("SVI parameters must be finite")
        if self.b < 0:
            raise ValueError("b must be non-negative")
        if not -1 < self.rho < 1:
            raise ValueError("rho must lie strictly between -1 and 1")
        if self.sigma <= 0:
            raise ValueError("sigma must be positive")
        # Tolerances absorb round-off from the SSVI conversion / decoding.
        if self.min_variance < -1e-12:
            raise ValueError("SVI total variance must be non-negative (a + b sigma sqrt(1-rho^2) >= 0)")
        if self.b * (1 + abs(self.rho)) > LEE_WING_BOUND + 1e-12:
            raise ValueError("wing slope b(1+|rho|) exceeds Lee's bound of 2")

    @property
    def min_variance(self) -> float:
        return self.a + self.b * self.sigma * math.sqrt(1 - self.rho**2)

    def total_variance(self, k: np.ndarray) -> np.ndarray:
        x = np.asarray(k, dtype=float) - self.m
        return self.a + self.b * (self.rho * x + np.sqrt(x * x + self.sigma**2))

    def implied_vol(self, k: np.ndarray, years: float) -> np.ndarray:
        """Black-Scholes implied vol :math:`\\sqrt{w(k)/T}`."""
        if years <= 0:
            raise ValueError("years must be positive")
        return np.sqrt(np.maximum(self.total_variance(k), 0.0) / years)

    def derivatives(self, k: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """:math:`(w'(k), w''(k))`."""
        x = np.asarray(k, dtype=float) - self.m
        root = np.sqrt(x * x + self.sigma**2)
        return self.b * (self.rho + x / root), self.b * self.sigma**2 / root**3

    def butterfly_g(self, k: np.ndarray) -> np.ndarray:
        """Gatheral–Jacquier :math:`g(k)`; negative anywhere = butterfly arbitrage."""
        k = np.asarray(k, dtype=float)
        return butterfly_g(k, self.total_variance(k), *self.derivatives(k))

    def risk_neutral_density(self, k: np.ndarray) -> np.ndarray:
        """Density of :math:`\\ln(S_T/F)` implied by the smile (module docstring)."""
        k = np.asarray(k, dtype=float)
        w = self.total_variance(k)
        d_minus = -k / np.sqrt(w) - np.sqrt(w) / 2
        return self.butterfly_g(k) / np.sqrt(2 * math.pi * w) * np.exp(-0.5 * d_minus**2)


@dataclass(frozen=True)
class SSVISurface:
    """Arbitrage-free synthetic surface: mean-reverting ATM variance + SSVI smile.

    ``v0``/``v_inf`` are annualized variances (vol²), ``kappa`` per year.
    """

    v0: float
    v_inf: float
    kappa: float
    rho: float
    eta: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.v0, self.v_inf, self.kappa, self.rho, self.eta)):
            raise ValueError("SSVI parameters must be finite")
        if self.v0 <= 0 or self.v_inf <= 0:
            raise ValueError("ATM variances must be positive")
        if self.kappa <= 0:
            raise ValueError("kappa must be positive")
        if not -1 < self.rho < 1:
            raise ValueError("rho must lie strictly between -1 and 1")
        if self.eta <= 0 or self.eta * (1 + abs(self.rho)) > 2:
            raise ValueError("need 0 < eta and eta(1+|rho|) <= 2 for no static arbitrage")

    def atm_total_variance(self, years: float) -> float:
        """:math:`\\theta(T)`."""
        if years <= 0:
            raise ValueError("years must be positive")
        decay = -math.expm1(-self.kappa * years) / self.kappa
        return self.v_inf * years + (self.v0 - self.v_inf) * decay

    def atm_vol(self, years: float) -> float:
        return math.sqrt(self.atm_total_variance(years) / years)

    def slice(self, years: float) -> RawSVI:
        """The expiry-``years`` smile as exact raw SVI parameters."""
        theta = self.atm_total_variance(years)
        phi = self.eta / math.sqrt(theta * (1 + theta))
        return RawSVI(
            a=0.5 * theta * (1 - self.rho**2),
            b=0.5 * theta * phi,
            rho=self.rho,
            m=-self.rho / phi,
            sigma=math.sqrt(1 - self.rho**2) / phi,
        )


@dataclass(frozen=True)
class SVIFit:
    """A fitted slice: parameters, fit quality, and optimizer bookkeeping.

    ``chi2_per_point`` is the mean squared normalized residual (≈1 when the
    residuals match the stated uncertainties); ``rmse_vol`` is the RMS
    implied-vol error in decimal vol.
    """

    params: RawSVI
    chi2_per_point: float
    rmse_vol: float
    n_points: int
    converged: bool
    iterations: int


def fit_raw_svi(
    k: np.ndarray,
    total_variance: np.ndarray,
    uncertainty: np.ndarray,
    years: float,
    max_iterations: int = 4000,
) -> SVIFit:
    """Weighted least-squares raw-SVI fit to one expiry (module docstring)."""
    k = np.asarray(k, dtype=float)
    w = np.asarray(total_variance, dtype=float)
    dw = np.asarray(uncertainty, dtype=float)
    if not (k.ndim == w.ndim == dw.ndim == 1 and len(k) == len(w) == len(dw)):
        raise ValueError("k, total_variance, and uncertainty must be matching 1-D arrays")
    if len(k) < MIN_SLICE_QUOTES:
        raise ValueError(f"need at least {MIN_SLICE_QUOTES} points to fit a slice, got {len(k)}")
    if not (np.isfinite(k).all() and np.isfinite(w).all() and np.isfinite(dw).all()):
        raise ValueError("fit inputs must be finite")
    if np.any(w <= 0) or np.any(dw <= 0):
        raise ValueError("total variances and uncertainties must be positive")
    if years <= 0:
        raise ValueError("years must be positive")

    scale = float(w[np.argmin(np.abs(k))])

    def objective(x: np.ndarray) -> float:
        try:
            params = _decode(x, scale)
        except (ValueError, OverflowError):
            return math.inf
        resid = (params.total_variance(k) - w) / dw
        return float(np.mean(resid * resid))

    best = None
    total_iterations = 0
    for rho0 in (-0.6, 0.0, 0.4):
        result = nelder_mead(objective, _encode_start(scale, rho0, float(np.min(w))), max_iterations)
        total_iterations += result.iterations
        if best is None or result.value < best.value:
            best = result
    polish = nelder_mead(objective, best.x, max_iterations, initial_step=0.05)
    total_iterations += polish.iterations
    if polish.value <= best.value:
        best = polish

    params = _decode(best.x, scale)
    fitted_vol = params.implied_vol(k, years)
    rmse = float(np.sqrt(np.mean((fitted_vol - np.sqrt(w / years)) ** 2)))
    return SVIFit(params, best.value, rmse, len(k), polish.converged, total_iterations)


def _decode(x: np.ndarray, scale: float) -> RawSVI:
    rho = math.tanh(float(x[2]))
    b = LEE_WING_BOUND / (1 + abs(rho)) * _sigmoid(float(x[1]))
    sigma = math.exp(float(x[4])) * math.sqrt(scale)
    w_min = math.exp(float(x[0])) * scale
    a = w_min - b * sigma * math.sqrt(1 - rho**2)
    return RawSVI(a=a, b=b, rho=rho, m=float(x[3]) * math.sqrt(scale), sigma=sigma)


def _encode_start(scale: float, rho: float, w_floor: float) -> np.ndarray:
    """Starting point shaped like an SSVI slice at ATM variance ``scale``:
    b ≈ √θ/2, σ ≈ √θ, vertex at the money, minimum just below the data."""
    b = min(0.5 * math.sqrt(scale), 0.9 * LEE_WING_BOUND / (1 + abs(rho)))
    share = b * (1 + abs(rho)) / LEE_WING_BOUND
    w_min = max(0.8 * min(w_floor, scale), 1e-12)
    return np.array([
        math.log(w_min / scale),
        math.log(share / (1 - share)),
        math.atanh(rho),
        0.0,
        0.0,
    ])


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)
