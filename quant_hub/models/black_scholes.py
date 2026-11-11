r"""Black-Scholes-Merton European option pricing, greeks, and implied volatility.

Pricing
-------
Assume the underlying follows GBM (see ``data/synthetic.py``) and pays a
continuous dividend yield :math:`q`. The option can be replicated by a
continuously-rebalanced position in the underlying plus a risk-free bond
(no-arbitrage). Under the risk-neutral measure the underlying then drifts at
:math:`r - q`, and the price solves the Black-Scholes PDE in closed form
(Merton's extension):

.. math::  d_1 = \frac{\ln(S/K) + (r - q + \sigma^2/2)\,T}{\sigma\sqrt{T}},
           \qquad d_2 = d_1 - \sigma\sqrt{T}

.. math::  C = S e^{-qT}\Phi(d_1) - K e^{-rT}\Phi(d_2), \qquad
           P = K e^{-rT}\Phi(-d_2) - S e^{-qT}\Phi(-d_1)

where :math:`\Phi` is the standard normal CDF, :math:`S` the spot price,
:math:`K` the strike, :math:`r` the continuously-compounded risk-free rate,
:math:`q` the continuous dividend yield, :math:`\sigma` the annualized
volatility, and :math:`T` time to expiration in years. With :math:`q = 0`
this is the textbook Black-Scholes formula. Intuition: holding the stock
forgoes the dividends, so the stock's "deliverable value" at expiry is the
prepaid forward :math:`Se^{-qT}` rather than :math:`S`. Put-call parity
becomes :math:`C - P = Se^{-qT} - Ke^{-rT}`.

:math:`\Phi(x) = \tfrac{1}{2}(1 + \mathrm{erf}(x/\sqrt{2}))` uses the stdlib
``math.erf`` (exact to double precision; NumPy has no ``erf`` and SciPy is
not a dependency), vectorized with ``np.vectorize``. That is a Python-level
loop, adequate for the few-thousand-point grids the labs draw. Every
function here accepts NumPy arrays that broadcast together, so one call
prices a whole strike/expiry/spot grid. The scalar ``price``/``greeks``
entry points go through the same code.

Greeks
------
The analytic partial derivatives of the same closed form, with
:math:`\varphi` the standard normal PDF:

.. math::  \Delta_C = e^{-qT}\Phi(d_1), \qquad \Delta_P = -e^{-qT}\Phi(-d_1)

.. math::  \Gamma = \frac{e^{-qT}\varphi(d_1)}{S\sigma\sqrt{T}}, \qquad
           \nu = S e^{-qT}\varphi(d_1)\sqrt{T} \quad\text{(call and put)}

.. math::  \Theta_C = -\frac{S e^{-qT}\varphi(d_1)\sigma}{2\sqrt{T}}
                      - rKe^{-rT}\Phi(d_2) + qSe^{-qT}\Phi(d_1)

.. math::  \Theta_P = -\frac{S e^{-qT}\varphi(d_1)\sigma}{2\sqrt{T}}
                      + rKe^{-rT}\Phi(-d_2) - qSe^{-qT}\Phi(-d_1)

.. math::  \rho_C = KTe^{-rT}\Phi(d_2), \qquad \rho_P = -KTe^{-rT}\Phi(-d_2)

:math:`\Theta` is the derivative with respect to calendar time passing,
:math:`\partial V/\partial t = -\partial V/\partial T`.

``Greeks`` reports trader-convention scaling (D25), not the raw
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

.. math::  \sigma \to 0^+:\; C \to (Se^{-qT} - Ke^{-rT})^+,
           \quad P \to (Ke^{-rT} - Se^{-qT})^+

.. math::  \sigma \to \infty:\; C \to Se^{-qT}, \quad P \to Ke^{-rT}

(as :math:`\sigma \to \infty`, :math:`d_1 \to +\infty` and
:math:`d_2 \to -\infty`). These are exactly the model-free no-arbitrage
bounds on a European option, so by continuity and monotonicity a unique
:math:`\sigma > 0` exists **iff** the price lies strictly inside them.
Prices on or outside the bounds are rejected rather than clamped: no
volatility explains them.

*Algorithm: bracketed Newton.* Newton's iteration
:math:`\sigma_{n+1} = \sigma_n - f(\sigma_n)/\nu(\sigma_n)` converges
quadratically near the root. But :math:`\nu` vanishes for deep in- and
out-of-the-money options, where a raw Newton step can overshoot badly. So
the solver keeps a bracket :math:`[\sigma_{lo}, \sigma_{hi}]` known to contain
the root. By monotonicity, :math:`f > 0` means the root is below and
:math:`f < 0` means it is above. Each iteration shrinks the bracket using
the sign of :math:`f`, then takes the Newton step if it lands strictly
inside the bracket and bisects otherwise. So the root always stays inside a
bracket that never grows, and the bisection fallback guarantees termination.

The upper end starts at :math:`\sigma = 1` (100%) and doubles until the price
exceeds the target, up to ``IV_MAX_SIGMA``. The first iterate is the
Brenner--Subrahmanyam at-the-money approximation
:math:`\sigma_0 \approx \sqrt{2\pi/T}\,V^{mkt}/(Se^{-qT})`. It comes from
:math:`C \approx Se^{-qT}\sigma\sqrt{T/2\pi}` at the forward, and is moved
to the bracket midpoint if it falls outside. Iteration stops when the step
size drops below ``IV_SIGMA_TOLERANCE``.

*Conditioning.* When the time value :math:`V^{mkt} - \text{lower bound}` is
tiny relative to the price (deep ITM, very short-dated), the price carries
almost no information about :math:`\sigma`. The returned volatility is then
only as certain as the price's last few digits, whatever the solver
tolerance.

Assumptions and units
---------------------
European exercise, continuous dividend yield, constant ``r``/``q``/``sigma``,
continuous trading, no transaction costs: the textbook baseline. ``dte`` is
calendar days to expiration (:math:`T = \text{dte}/365`). ``rate``,
``dividend_yield``, and ``sigma`` are annualized decimals (0.05 = 5%), not
percentages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

OptionType = Literal["call", "put"]
ArrayLike = float | np.ndarray

DAYS_PER_YEAR = 365.0
"""Calendar-day convention for ``dte`` -> years and for per-day theta."""

IV_SIGMA_TOLERANCE = 1e-10
"""Stop the implied-volatility search once a step moves sigma less than this."""

IV_MAX_SIGMA = 100.0
"""Search ceiling (10,000% vol); a price needing more carries no vol information."""

IV_MAX_ITERATIONS = 100
"""Bisection alone reaches the tolerance in ~40 steps from the widest bracket."""

_erf = np.vectorize(math.erf, otypes=[float])


@dataclass(frozen=True)
class MarketInputs:
    """The market state every pricing workflow consumes.

    Deliberately source-agnostic: the Options Lab fills it by hand, from a
    synthetic source, or from IBKR/SQLite history (``services/underlying.py``);
    the models never know which.
    """

    spot: float
    rate: float
    sigma: float
    dividend_yield: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.spot) or self.spot <= 0:
            raise ValueError("spot must be positive")
        if not math.isfinite(self.sigma) or self.sigma <= 0:
            raise ValueError("sigma must be positive")
        if not math.isfinite(self.rate):
            raise ValueError("rate must be finite")
        if not math.isfinite(self.dividend_yield):
            raise ValueError("dividend yield must be finite")


@dataclass(frozen=True)
class BlackScholesInputs:
    spot: float
    strike: float
    dte: float
    rate: float
    sigma: float
    option_type: OptionType
    dividend_yield: float = 0.0

    def __post_init__(self) -> None:
        _validate_contract(
            self.spot, self.strike, self.dte, self.rate, self.dividend_yield, self.option_type
        )
        if not math.isfinite(self.sigma) or self.sigma <= 0:
            raise ValueError("sigma must be positive")


@dataclass(frozen=True)
class Greeks:
    """Greeks in trader-convention scaling (D25).

    Each field is a float for scalar inputs or an array matching the
    broadcast input shape. Greeks are linear in position size, so strategy
    greeks are signed, multiplied sums of these.
    """

    delta: ArrayLike
    gamma: ArrayLike
    vega: ArrayLike
    theta: ArrayLike
    rho: ArrayLike


def price(inputs: BlackScholesInputs) -> float:
    """Black-Scholes-Merton price of one European option."""
    return float(
        price_array(
            inputs.spot, inputs.strike, inputs.dte, inputs.rate, inputs.sigma,
            inputs.option_type, inputs.dividend_yield,
        )
    )


def greeks(inputs: BlackScholesInputs) -> Greeks:
    """Greeks of one European option (trader-convention scaling)."""
    g = greeks_array(
        inputs.spot, inputs.strike, inputs.dte, inputs.rate, inputs.sigma,
        inputs.option_type, inputs.dividend_yield,
    )
    return Greeks(*(float(value) for value in (g.delta, g.gamma, g.vega, g.theta, g.rho)))


def price_array(
    spot: ArrayLike,
    strike: ArrayLike,
    dte: ArrayLike,
    rate: float,
    sigma: ArrayLike,
    option_type: OptionType,
    dividend_yield: float = 0.0,
) -> np.ndarray:
    """Vectorized price; array arguments broadcast together."""
    s, k, t, sig = _validated_arrays(spot, strike, dte, sigma, rate, dividend_yield, option_type)
    return _price_core(s, k, t, rate, dividend_yield, sig, option_type == "call")


def greeks_array(
    spot: ArrayLike,
    strike: ArrayLike,
    dte: ArrayLike,
    rate: float,
    sigma: ArrayLike,
    option_type: OptionType,
    dividend_yield: float = 0.0,
) -> Greeks:
    """Vectorized greeks (trader-convention scaling); arguments broadcast."""
    s, k, t, sig = _validated_arrays(spot, strike, dte, sigma, rate, dividend_yield, option_type)
    r, q = rate, dividend_yield
    call = option_type == "call"
    d1, d2 = _d1_d2(s, k, t, r, q, sig)
    root_t = np.sqrt(t)
    carry = np.exp(-q * t)
    discount = np.exp(-r * t)
    density = carry * _norm_pdf(d1)

    delta = carry * _cdf(d1) if call else -carry * _cdf(-d1)
    gamma = density / (s * sig * root_t)
    vega_annual = s * density * root_t
    decay = -s * density * sig / (2.0 * root_t)
    if call:
        theta_annual = decay - r * k * discount * _cdf(d2) + q * s * carry * _cdf(d1)
        rho_annual = k * t * discount * _cdf(d2)
    else:
        theta_annual = decay + r * k * discount * _cdf(-d2) - q * s * carry * _cdf(-d1)
        rho_annual = -k * t * discount * _cdf(-d2)
    return Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega_annual / 100.0,
        theta=theta_annual / DAYS_PER_YEAR,
        rho=rho_annual / 100.0,
    )


def intrinsic_value(spot: ArrayLike, strike: float, option_type: OptionType) -> np.ndarray:
    """Exercise value at expiry: :math:`(S-K)^+` or :math:`(K-S)^+`."""
    s = np.asarray(spot, dtype=float)
    return np.maximum(s - strike, 0.0) if option_type == "call" else np.maximum(strike - s, 0.0)


def no_arbitrage_bounds(
    spot: float,
    strike: float,
    dte: float,
    rate: float,
    option_type: OptionType,
    dividend_yield: float = 0.0,
) -> tuple[float, float]:
    """``(lower, upper)`` European price bounds, i.e. the sigma -> 0 / inf limits."""
    _validate_contract(spot, strike, dte, rate, dividend_yield, option_type)
    t = _years(dte)
    prepaid_forward = spot * math.exp(-dividend_yield * t)
    discounted_strike = strike * math.exp(-rate * t)
    if option_type == "call":
        return max(prepaid_forward - discounted_strike, 0.0), prepaid_forward
    return max(discounted_strike - prepaid_forward, 0.0), discounted_strike


def implied_volatility(
    market_price: float,
    spot: float,
    strike: float,
    dte: float,
    rate: float,
    option_type: OptionType,
    dividend_yield: float = 0.0,
) -> float:
    """Annualized volatility at which the model price equals ``market_price``.

    Raises ``ValueError`` when the price is not strictly inside the
    no-arbitrage bounds (no volatility reproduces it) or needs more than
    ``IV_MAX_SIGMA``.
    """
    lower, upper = no_arbitrage_bounds(spot, strike, dte, rate, option_type, dividend_yield)
    if not math.isfinite(market_price):
        raise ValueError("market price must be finite")
    if not lower < market_price < upper:
        raise ValueError(
            f"price {market_price:.6g} is outside the no-arbitrage bounds "
            f"({lower:.6g}, {upper:.6g}); no volatility reproduces it"
        )

    t = _years(dte)
    q = dividend_yield
    call = option_type == "call"

    def residual(sigma: float) -> float:
        return float(_price_core(spot, strike, t, rate, q, sigma, call)) - market_price

    def raw_vega(sigma: float) -> float:
        d1, _ = _d1_d2(spot, strike, t, rate, q, sigma)
        return spot * math.exp(-q * t) * float(_norm_pdf(d1)) * math.sqrt(t)

    lo, hi = 0.0, 1.0
    while residual(hi) <= 0.0:
        lo, hi = hi, 2.0 * hi
        if hi > IV_MAX_SIGMA:
            raise ValueError(f"implied volatility exceeds {IV_MAX_SIGMA:.0%}")

    sigma = math.sqrt(2.0 * math.pi / t) * market_price / (spot * math.exp(-q * t))
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
        vega = raw_vega(sigma)
        newton_ok = vega > 0.0 and lo < sigma - diff / vega < hi
        candidate = sigma - diff / vega if newton_ok else 0.5 * (lo + hi)
        if abs(candidate - sigma) < IV_SIGMA_TOLERANCE:
            return candidate
        sigma = candidate
    raise RuntimeError("implied-volatility search did not converge")


def _validate_contract(
    spot: float, strike: float, dte: float, rate: float, dividend_yield: float, option_type: str
) -> None:
    if not math.isfinite(spot) or spot <= 0:
        raise ValueError("spot must be positive")
    if not math.isfinite(strike) or strike <= 0:
        raise ValueError("strike must be positive")
    if not math.isfinite(dte) or dte <= 0:
        raise ValueError("days to expiration must be positive")
    if not math.isfinite(rate):
        raise ValueError("rate must be finite")
    if not math.isfinite(dividend_yield):
        raise ValueError("dividend yield must be finite")
    if option_type not in ("call", "put"):
        raise ValueError(f"unknown option_type {option_type!r}")


def _validated_arrays(spot, strike, dte, sigma, rate, dividend_yield, option_type):
    if option_type not in ("call", "put"):
        raise ValueError(f"unknown option_type {option_type!r}")
    if not math.isfinite(rate):
        raise ValueError("rate must be finite")
    if not math.isfinite(dividend_yield):
        raise ValueError("dividend yield must be finite")
    arrays = np.broadcast_arrays(
        *(np.asarray(value, dtype=float) for value in (spot, strike, dte, sigma))
    )
    for name, values in zip(("spot", "strike", "days to expiration", "sigma"), arrays):
        if not np.isfinite(values).all() or np.any(values <= 0):
            raise ValueError(f"{name} must be positive")
    s, k, dte_values, sig = arrays
    return s, k, _years(dte_values), sig


def _years(dte):
    return dte / DAYS_PER_YEAR


def _d1_d2(s, k, t, r, q, sigma):
    vol_root_t = sigma * np.sqrt(t)
    d1 = (np.log(s / k) + (r - q + 0.5 * sigma**2) * t) / vol_root_t
    return d1, d1 - vol_root_t


def _price_core(s, k, t, r, q, sigma, call: bool):
    d1, d2 = _d1_d2(s, k, t, r, q, sigma)
    prepaid_forward = s * np.exp(-q * t)
    discounted_strike = k * np.exp(-r * t)
    if call:
        return prepaid_forward * _cdf(d1) - discounted_strike * _cdf(d2)
    return discounted_strike * _cdf(-d2) - prepaid_forward * _cdf(-d1)


def _cdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x) / math.sqrt(2.0)))


def _norm_pdf(x):
    return np.exp(-0.5 * np.square(x)) / math.sqrt(2.0 * math.pi)
