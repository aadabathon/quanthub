r"""Black-Scholes European option pricing.

Mathematical description
-------------------------
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
in years. :math:`\Phi` is computed from the stdlib error function via the
identity :math:`\Phi(x) = \tfrac{1}{2}\left(1 + \mathrm{erf}(x/\sqrt{2})\right)`
rather than pulling in scipy for a single call.

The greeks are the analytic partial derivatives of the same closed form,
with :math:`\varphi` the standard normal PDF:

.. math::  \Delta_C = \Phi(d_1), \qquad \Delta_P = \Phi(d_1) - 1

.. math::  \Gamma = \frac{\varphi(d_1)}{S\sigma\sqrt{T}} \quad\text{(call and put)}

.. math::  \nu = S\varphi(d_1)\sqrt{T} \quad\text{(call and put)}

.. math::  \Theta_C = -\frac{S\varphi(d_1)\sigma}{2\sqrt{T}} - rKe^{-rT}\Phi(d_2),
           \qquad \Theta_P = -\frac{S\varphi(d_1)\sigma}{2\sqrt{T}} + rKe^{-rT}\Phi(-d_2)

.. math::  \rho_C = KTe^{-rT}\Phi(d_2), \qquad \rho_P = -KTe^{-rT}\Phi(-d_2)

Assumptions and limitations
----------------------------
European exercise, no dividends, constant ``r``/``sigma``, continuous
trading, no transaction costs -- the textbook baseline. No implied-vol
solver or American-style early exercise yet.

Units
-----
``dte`` is calendar days to expiration; ``rate`` and ``sigma`` are
annualized decimals (0.05 = 5%), not percentages.

``greeks()`` reports trader-convention scaling, not the raw calculus
derivatives: ``vega`` and ``rho`` are per 1-percentage-point move in
``sigma``/``rate`` (raw :math:`\nu`, :math:`\rho` divided by 100), and
``theta`` is per calendar day of decay (raw annual :math:`\Theta` divided
by ``DAYS_PER_YEAR``) rather than per year. ``delta`` and ``gamma`` need no
such scaling and are the raw derivatives.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

OptionType = Literal["call", "put"]

DAYS_PER_YEAR = 365.0


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class BlackScholesInputs:
    spot: float
    strike: float
    dte: float
    rate: float
    sigma: float
    option_type: OptionType

    def __post_init__(self) -> None:
        if self.spot <= 0:
            raise ValueError("spot must be positive")
        if self.strike <= 0:
            raise ValueError("strike must be positive")
        if self.dte <= 0:
            raise ValueError("days to expiration must be positive")
        if self.sigma <= 0:
            raise ValueError("sigma must be positive")
        if self.option_type not in ("call", "put"):
            raise ValueError(f"unknown option_type {self.option_type!r}")


class OptionService:
    """Prices European options under Black-Scholes."""

    def price(self, inputs: BlackScholesInputs) -> float:
        s, k, r, sigma = inputs.spot, inputs.strike, inputs.rate, inputs.sigma
        t = inputs.dte / DAYS_PER_YEAR

        d1 = (math.log(s / k) + (r + sigma**2 / 2) * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)

        if inputs.option_type == "call":
            return s * _norm_cdf(d1) - k * math.exp(-r * t) * _norm_cdf(d2)
        return k * math.exp(-r * t) * _norm_cdf(-d2) - s * _norm_cdf(-d1)

    def greeks(self, inputs: BlackScholesInputs) -> dict[str, float]:
        s, k, r, sigma = inputs.spot, inputs.strike, inputs.rate, inputs.sigma
        t = inputs.dte / DAYS_PER_YEAR

        d1 = (math.log(s / k) + (r + sigma**2 / 2) * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)

        delta = _norm_cdf(d1) if inputs.option_type == "call" else _norm_cdf(d1) - 1
        gamma = math.exp(-d1**2 / 2) / (s * sigma * math.sqrt(2 * math.pi * t))
        vega_annual = s * math.exp(-d1**2 / 2) * math.sqrt(t / (2 * math.pi))
        theta_annual = (
            -s * sigma * math.exp(-d1**2 / 2) / (2 * math.sqrt(2 * math.pi * t))
            - r * k * math.exp(-r * t) * _norm_cdf(d2)
            if inputs.option_type == "call"
            else -s * sigma * math.exp(-d1**2 / 2) / (2 * math.sqrt(2 * math.pi * t))
            + r * k * math.exp(-r * t) * _norm_cdf(-d2)
        )
        rho_annual = (
            k * t * math.exp(-r * t) * _norm_cdf(d2)
            if inputs.option_type == "call"
            else -k * t * math.exp(-r * t) * _norm_cdf(-d2)
        )

        return {
            "delta": delta,
            "gamma": gamma,
            "vega": vega_annual / 100.0,
            "theta": theta_annual / DAYS_PER_YEAR,
            "rho": rho_annual / 100.0,
        }