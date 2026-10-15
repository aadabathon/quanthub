r"""A fitted chain as market inputs at any strike and expiry (milestone C4).

The Options Lab prices legs that need not sit on a quoted strike or expiry.
``MarketSurface`` turns a ``vol_surface.SurfaceFit`` into the three inputs
Black–Scholes–Merton needs, :math:`(\sigma, r, q)`, at any :math:`(K, T)`,
consistent with the chain it was fitted to.

Carry
-----
Each fitted expiry :math:`T_i` has a forward :math:`F_i` and a discount
factor :math:`D_i` (put–call parity, ``models/carry``). Write each as an
annualized rate,

.. math::  c_i = \frac{\ln(F_i/S)}{T_i}, \qquad r_i = -\frac{\ln D_i}{T_i}.

Interpolate both linearly in :math:`T` between expiries, and hold them flat
outside :math:`[T_1, T_N]`. Then

.. math::  F(T) = S e^{c(T)T}, \qquad D(T) = e^{-r(T)T}, \qquad q(T) = r(T) - c(T),

and :math:`(r, q)` reproduce :math:`F` and :math:`D` exactly in BSM
(``data/options`` module docstring). A leg on a fitted expiry gets exactly
that expiry's carry. The spot :math:`S` is the chain's own, the one its
forwards were measured against. Pricing with a different spot would move
every forward with it.

Volatility
----------
At fixed log-moneyness :math:`k = \ln(K/F(T))`:

* **Between expiries:** total variance is linear in :math:`T`, the
  ``vol_surface`` mesh rule. It adds no calendar arbitrage of its own,
  wherever the fitted slices don't cross.
* **Before the first and after the last fitted expiry:** the implied vol at
  fixed :math:`k` is held flat, so :math:`w(k, T) = w_1(k)\,T/T_1` or
  :math:`w_N(k)\,T/T_N`. Both join the surface continuously and keep
  :math:`w` rising with :math:`T`.

Then :math:`\sigma = \sqrt{w/T}`.

**Extrapolated** means :math:`T` lies outside :math:`[T_1, T_N]`, or
:math:`k` lies outside the quoted range of a slice the point draws on. The
fitted SVI wing or the flat-vol rule is then doing the work, not data. Every
point says so, and the page shows it.

Scenarios: sticky strike
------------------------
``apply_surface`` gives each strategy leg the surface's
:math:`(\sigma, r, q)` at its strike and expiry, and the leg keeps them as
spot moves and days pass. That's the simplest smile assumption, stated
rather than hidden. Under "sticky moneyness" a leg's vol would instead
follow its moneyness as spot moves, and scenario P&L away from today's spot
would differ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime

import numpy as np

from quant_hub.models.strategy import Strategy
from quant_hub.models.vol_surface import SurfaceFit


@dataclass(frozen=True)
class SurfacePoint:
    """BSM inputs at one strike and expiry, and whether data supports them."""

    vol: float
    rate: float
    dividend_yield: float
    forward: float
    extrapolated: bool


class MarketSurface:
    """A fitted chain as a function of strike and expiry (module docstring)."""

    def __init__(self, fit: SurfaceFit) -> None:
        if not fit.slices:
            raise ValueError("a market surface needs at least one fitted expiry")
        chain = fit.chain
        self.fit = fit
        self.symbol, self.source, self.as_of = chain.symbol, chain.source, chain.as_of
        self.spot = chain.spot
        self._slices = sorted(fit.slices, key=lambda s: s.dte)
        self._years = np.array([s.years for s in self._slices])
        self._carry = np.array([math.log(s.forward / chain.spot) / s.years
                                for s in self._slices])
        self._rates = np.array([chain.rate_at(s.dte) for s in self._slices])

    @property
    def as_of_text(self) -> str:
        return self.as_of.strftime("%Y-%m-%d %H:%M") if isinstance(self.as_of, datetime) else ""

    def point(self, strike: float, dte: float) -> SurfacePoint:
        if not (math.isfinite(strike) and strike > 0 and math.isfinite(dte) and dte > 0):
            raise ValueError("strike and days to expiry must be positive")
        years = dte / 365.0
        carry = float(np.interp(years, self._years, self._carry))
        rate = float(np.interp(years, self._years, self._rates))
        forward = self.spot * math.exp(carry * years)
        k = math.log(strike / forward)
        first, last = self._slices[0], self._slices[-1]

        def outside(s) -> bool:
            return not s.k_min <= k <= s.k_max

        if years <= first.years:
            total = float(first.fit.params.total_variance(k)) * years / first.years
            extrapolated = years < first.years or outside(first)
        elif years >= last.years:
            total = float(last.fit.params.total_variance(k)) * years / last.years
            extrapolated = years > last.years or outside(last)
        else:
            i = int(np.searchsorted(self._years, years, side="right")) - 1
            front, back = self._slices[i], self._slices[i + 1]
            lam = (back.years - years) / (back.years - front.years)
            total = (lam * float(front.fit.params.total_variance(k))
                     + (1 - lam) * float(back.fit.params.total_variance(k)))
            extrapolated = (lam > 0 and outside(front)) or (lam < 1 and outside(back))
        return SurfacePoint(vol=math.sqrt(total / years), rate=rate,
                            dividend_yield=rate - carry, forward=forward,
                            extrapolated=extrapolated)


def apply_surface(strategy: Strategy, surface: MarketSurface) -> Strategy:
    """Each leg priced from the surface at its strike and expiry (sticky strike)."""
    legs = []
    for leg in strategy.legs:
        p = surface.point(leg.strike, leg.dte)
        legs.append(replace(leg, vol=p.vol, rate=p.rate, dividend_yield=p.dividend_yield))
    return replace(strategy, legs=tuple(legs))
