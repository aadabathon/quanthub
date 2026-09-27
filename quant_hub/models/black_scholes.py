r"""Black-Scholes European option pricing, greeks, and implied volatility.

Pricing
-------
Assuming the underlying follows GBM (see ``data/synthetic.py``) and that the
option can be replicated by a continuously-rebalanced position in the
underlying plus a risk-free bond (no-arbitrage), the price solves the
Black-Scholes PDE in closed form:

.. math::  d_1 = \frac{\ln(S/K) + (r + \sigma^2/2)\,T}{\sigma\sqrt{T}},
           \qquad d_2 = d_1 - \sigma\sqrt{T}

.. math::  C = S\,\Phi(d_1) - K e^{-rT}\Phi(d_2), \qquad
           P = K e^{-rT}\Phi(-d_2) - S\,\Phi(-d_1)

where :math:`\Phi` is the standard normal CDF, :math:`S` the spot price,
:math:`K` the strike, :math:`r` the continuously-compounded risk-free rate,
:math:`\sigma` the annualized volatility, and :math:`T` time to expiration
in years. :math:`\Phi` is computed from the stdlib error function via
:math:`\Phi(x) = \tfrac{1}{2}\left(1 + \mathrm{erf}(x/\sqrt{2})\right)`
rather than pulling in scipy for a single call.

Greeks
------
The analytic partial derivatives of the same closed form, with
:math:`\varphi` the standard normal PDF:

.. math::  \Delta_C = \Phi(d_1), \qquad \Delta_P = \Phi(d_1) - 1

.. math::  \Gamma = \frac{\varphi(d_1)}{S\sigma\sqrt{T}} \quad\text{(call and put)}

.. math::  \nu = S\varphi(d_1)\sqrt{T} \quad\text{(call and put)}

.. math::  \Theta_C = -\frac{S\varphi(d_1)\sigma}{2\sqrt{T}} - rKe^{-rT}\Phi(d_2),
           \qquad \Theta_P = -\frac{S\varphi(d_1)\sigma}{2\sqrt{T}} + rKe^{-rT}\Phi(-d_2)

.. math::  \rho_C = KTe^{-rT}\Phi(d_2), \qquad \rho_P = -KTe^{-rT}\Phi(-d_2)

``greeks()`` reports trader-convention scaling (D25), not the raw
derivatives: ``vega`` and ``rho`` are per 1-percentage-point move in
``sigma``/``rate`` (raw :math:`\nu`, :math:`\rho` divided by 100), and
``theta`` is per calendar day (raw annual :math:`\Theta` divided by
``DAYS_PER_YEAR``). ``delta`` and ``gamma`` are the raw derivatives.

Implied volatility
------------------
Given an observed price :math:`V^{mkt}`, the implied volatility is the root
of :math:`f(\sigma) = V_{BS}(\sigma) - V^{mkt}`.

*Existence and uniqueness.* :math:`\partial V/\partial\sigma = \nu > 0` for
every :math:`\sigma > 0`, so the price is strictly increasing in volatility.
Its limits are

.. math::  \sigma \to 0^+:\; C \to (S - Ke^{-rT})^+, \quad P \to (Ke^{-rT} - S)^+

.. math::  \sigma \to \infty:\; C \to S, \quad P \to Ke^{-rT}

(as :math:`\sigma \to \infty`, :math:`d_1 \to +\infty` and
:math:`d_2 \to -\infty`). These are exactly the model-free no-arbitrage
bounds on a European option, so by continuity and monotonicity a unique
:math:`\sigma > 0` exists **iff** the price lies strictly inside them.
Prices on or outside the bounds are rejected rather than clamped: no
volatility explains them.

*Algorithm: bracketed Newton.* Newton's iteration
:math:`\sigma_{n+1} = \sigma_n - f(\sigma_n)/\nu(\sigma_n)` converges
quadratically near the root, but :math:`\nu` vanishes for deep in- and
out-of-the-money options, where a raw Newton step can overshoot badly.  So
the solver keeps a bracket :math:`[\sigma_{lo}, \sigma_{hi}]` known to contain
the root (monotonicity: :math:`f > 0` means the root is below, :math:`f < 0`
above).  Each iteration shrinks the bracket using the sign of :math:`f`, then
takes the Newton step if it lands strictly inside the bracket, else bisects.
Every iteration therefore keeps the root in a bracket that never grows, and
the bisection fallback guarantees termination.

The upper end starts at :math:`\sigma = 1` (100%) and doubles until the price
exceeds the target, up to ``IV_MAX_SIGMA``.  The first iterate is the
Brenner--Subrahmanyam at-the-money approximation
:math:`\sigma_0 \approx \sqrt{2\pi/T}\,V^{mkt}/S` (from
:math:`C \approx S\sigma\sqrt{T/2\pi}` when :math:`S = Ke^{-rT}`), moved to the
bracket midpoint if it falls outside.  Iteration stops when the step size
drops below ``IV_SIGMA_TOLERANCE``.

*Conditioning.* When the time value :math:`V^{mkt} - \text{lower bound}` is
tiny relative to the price (deep ITM, very short-dated), the price carries
almost no information about :math:`\sigma`; the returned volatility is then
as uncertain as the price's last few digits, whatever the solver tolerance.

Assumptions and units
---------------------
European exercise, no dividends, constant ``r``/``sigma``, continuous
trading, no transaction costs: the textbook baseline. ``dte`` is calendar
days to expiration (:math:`T = \text{dte}/365`); ``rate`` and ``sigma`` are
annualized decimals (0.05 = 5%), not percentages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["call", "put"]

DAYS_PER_YEAR = 365.0
"""Calendar-day convention for ``dte`` -> years and for per-day theta."""

IV_SIGMA_TOLERANCE = 1e-10
"""Stop the implied-volatility search once a step moves sigma less than this."""

IV_MAX_SIGMA = 100.0
"""Search ceiling (10,000% vol); a price needing more carries no vol information."""

IV_MAX_ITERATIONS = 100
"""Bisection alone reaches the tolerance in ~40 steps from the widest bracket."""


@dataclass(frozen=True)
class BlackScholesInputs:
    spot: float
    strike: float
    dte: float
    rate: float
    sigma: float
    option_type: OptionType

    def __post_init__(self) -> None:
        _validate_contract(self.spot, self.strike, self.dte, self.rate, self.option_type)
        if not math.isfinite(self.sigma) or self.sigma <= 0:
            raise ValueError("sigma must be positive")


def price(inputs: BlackScholesInputs) -> float:
    """Black-Scholes price of a European option."""
    return _price(
        inputs.spot, inputs.strike, _years(inputs.dte), inputs.rate,
        inputs.sigma, inputs.option_type,
    )


def greeks(inputs: BlackScholesInputs) -> dict[str, float]:
    """Greeks in trader-convention scaling (see module docstring, D25)."""
    s, k, r, sigma = inputs.spot, inputs.strike, inputs.rate, inputs.sigma
    t = _years(inputs.dte)
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    pdf_d1 = _norm_pdf(d1)
    discount = math.exp(-r * t)
    call = inputs.option_type == "call"

    delta = _norm_cdf(d1) if call else _norm_cdf(d1) - 1.0
    gamma = pdf_d1 / (s * sigma * math.sqrt(t))
    vega_annual = s * pdf_d1 * math.sqrt(t)
    decay = -s * pdf_d1 * sigma / (2.0 * math.sqrt(t))
    theta_annual = (
        decay - r * k * discount * _norm_cdf(d2)
        if call
        else decay + r * k * discount * _norm_cdf(-d2)
    )
    rho_annual = (
        k * t * discount * _norm_cdf(d2) if call else -k * t * discount * _norm_cdf(-d2)
    )
    return {
        "delta": delta,
        "gamma": gamma,
        "vega": vega_annual / 100.0,
        "theta": theta_annual / DAYS_PER_YEAR,
        "rho": rho_annual / 100.0,
    }


def no_arbitrage_bounds(
    spot: float, strike: float, dte: float, rate: float, option_type: OptionType
) -> tuple[float, float]:
    """``(lower, upper)`` European price bounds, i.e. the sigma -> 0 / inf limits."""
    _validate_contract(spot, strike, dte, rate, option_type)
    discounted_strike = strike * math.exp(-rate * _years(dte))
    if option_type == "call":
        return max(spot - discounted_strike, 0.0), spot
    return max(discounted_strike - spot, 0.0), discounted_strike


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    dte: float,
    rate: float,
    option_type: OptionType,
) -> float:
    """Annualized volatility at which the Black-Scholes price equals ``market_price``.

    Raises ``ValueError`` when the price is not strictly inside the
    no-arbitrage bounds (no volatility reproduces it) or needs more than
    ``IV_MAX_SIGMA``.
    """
    lower, upper = no_arbitrage_bounds(spot, strike, dte, rate, option_type)
    if not math.isfinite(market_price):
        raise ValueError("market price must be finite")
    if not lower < market_price < upper:
        raise ValueError(
            f"price {market_price:.6g} is outside the no-arbitrage bounds "
            f"({lower:.6g}, {upper:.6g}); no volatility reproduces it"
        )

    t = _years(dte)

    def residual(sigma: float) -> float:
        return _price(spot, strike, t, rate, sigma, option_type) - market_price

    lo, hi = 0.0, 1.0
    while residual(hi) <= 0.0:
        lo, hi = hi, 2.0 * hi
        if hi > IV_MAX_SIGMA:
            raise ValueError(f"implied volatility exceeds {IV_MAX_SIGMA:.0%}")

    sigma = math.sqrt(2.0 * math.pi / t) * market_price / spot
    if not lo < sigma < hi:
        sigma = 0.5 * (lo + hi)
    for _ in range(IV_MAX_ITERATIONS):
        diff = residual(sigma)
        if diff == 0.0:
            return sigma
        if diff > 0.0:
            hi = sigma
        else:
            lo = sigma
        vega = spot * _norm_pdf(_d1_d2(spot, strike, t, rate, sigma)[0]) * math.sqrt(t)
        newton_ok = vega > 0.0 and lo < sigma - diff / vega < hi
        candidate = sigma - diff / vega if newton_ok else 0.5 * (lo + hi)
        if abs(candidate - sigma) < IV_SIGMA_TOLERANCE:
            return candidate
        sigma = candidate
    raise RuntimeError("implied-volatility search did not converge")


def _validate_contract(
    spot: float, strike: float, dte: float, rate: float, option_type: str
) -> None:
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("spot must be positive")
    if not math.isfinite(strike) or strike <= 0:
        raise ValueError("strike must be positive")
    if not math.isfinite(dte) or dte <= 0:
        raise ValueError("days to expiration must be positive")
    if not math.isfinite(rate):
        raise ValueError("rate must be finite")
    if option_type not in ("call", "put"):
        raise ValueError(f"unknown option_type {option_type!r}")


def _years(dte: float) -> float:
    return dte / DAYS_PER_YEAR


def _d1_d2(s: float, k: float, t: float, r: float, sigma: float) -> tuple[float, float]:
    vol_root_t = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + sigma**2 / 2.0) * t) / vol_root_t
    return d1, d1 - vol_root_t


def _price(s: float, k: float, t: float, r: float, sigma: float, option_type: str) -> float:
    d1, d2 = _d1_d2(s, k, t, r, sigma)
    if option_type == "call":
        return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
    return k * math.exp(-r * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
