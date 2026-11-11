r"""Multi-leg option strategies: value, P&L, greeks, and exact expiry analysis.

Position model
--------------
A strategy is a set of European option legs :math:`i` (type, strike
:math:`K_i`, days to expiry :math:`\tau_i`, signed contract count
:math:`n_i`: positive long, negative short), plus an optional signed stock
position :math:`n_s` shares, with contract multiplier :math:`m` (100 for US
equity options). Its value at a scenario spot :math:`S`, :math:`h` calendar
days after entry, is

.. math::

   V(S, h) = \sum_i n_i\, m\, V_i(S, \tau_i - h) + n_s S,
   \qquad V_i(S, \tau) = \begin{cases}
       V_{BS}(S, K_i, \tau) & \tau > 0 \\
       \text{intrinsic}_i(S) & \tau \le 0
   \end{cases}

and the P&L is :math:`V(S, h) - C_0`. The entry cost :math:`C_0` is
:math:`\sum_i n_i m p_i + n_s S_0^{stock}`, positive for a net debit. Each
leg's entry premium :math:`p_i` is the price actually paid, when known
(e.g., from a broker fill). Otherwise it is the model's fair value at entry,
in which case the P&L at :math:`(S_0, h=0)` is exactly zero. Greeks are
linear in position size, so the strategy's greeks are
:math:`\sum_i n_i m\,\text{greeks}_i` plus :math:`n_s` of delta from the stock.
Delta is therefore in shares-equivalent and the other greeks are in dollars.

Exact expiry analysis
---------------------
When all legs share one expiry :math:`T`, the P&L at expiry

.. math::  P(S) = \sum_i n_i m\,(\pm(S - K_i))^+ + n_s S - C_0

is **piecewise linear** in :math:`S \ge 0`, with kinks only at the strikes.
So on :math:`[0, \infty)` its extremes are attained at :math:`S = 0`, at a
strike, or as :math:`S \to \infty`. Beyond the largest strike every call is
in the money and every put is worthless, so the slope there is
:math:`\sum_{\text{calls}} n_i m + n_s`. A positive slope means unbounded
profit and a negative slope unbounded loss. Breakevens are solved exactly
segment by segment, with no grid. ``expiry_profile`` implements this.
Mixed expiries (calendars) are not piecewise linear at the first expiry,
so their curves are analyzed on a spot grid (``grid_breakevens``).

Per-leg market inputs
---------------------
A leg may carry its own volatility, rate and dividend yield. A market
surface gives each leg its strike's implied vol and its expiry's carry
(``models/market_surface.apply_surface``). A leg without them uses the
shared ``MarketInputs``.

Assumptions
-----------
Scenario valuation: each leg's rate, dividend yield, and volatility stay at
their entry values over the horizon (sticky strike: no vol-surface
dynamics); a leg that expires
before the horizon is settled at its intrinsic value *at the scenario spot*
(as if spot were already there at its expiry); carry on the entry cash and
dividends received by a stock leg over the horizon are ignored. European
exercise throughout. Units follow ``models/black_scholes.py``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

from quant_hub.models.black_scholes import (
    Greeks,
    MarketInputs,
    OptionType,
    greeks_array,
    intrinsic_value,
    price_array,
)

CONTRACT_MULTIPLIER = 100.0
"""Shares per US equity option contract."""


@dataclass(frozen=True)
class OptionLeg:
    """One option position. ``premium`` is per share; None = fair value at entry.

    ``vol``, ``rate`` and ``dividend_yield`` override the shared market
    inputs for this leg (module docstring); None uses the shared ones.
    """

    option_type: OptionType
    strike: float
    dte: float
    quantity: float
    premium: float | None = None
    vol: float | None = None
    rate: float | None = None
    dividend_yield: float | None = None

    def __post_init__(self) -> None:
        if self.option_type not in ("call", "put"):
            raise ValueError(f"unknown option_type {self.option_type!r}")
        if not math.isfinite(self.strike) or self.strike <= 0:
            raise ValueError("strike must be positive")
        if not math.isfinite(self.dte) or self.dte <= 0:
            raise ValueError("days to expiration must be positive")
        if not math.isfinite(self.quantity) or self.quantity == 0:
            raise ValueError("quantity must be a non-zero number of contracts")
        if self.premium is not None and (not math.isfinite(self.premium) or self.premium < 0):
            raise ValueError("premium must be non-negative")
        if self.vol is not None and not (math.isfinite(self.vol) and self.vol > 0):
            raise ValueError("a leg's vol must be positive")
        if any(v is not None and not math.isfinite(v) for v in (self.rate, self.dividend_yield)):
            raise ValueError("a leg's rate and dividend yield must be finite")


@dataclass(frozen=True)
class Strategy:
    """Option legs plus an optional stock position (shares, signed)."""

    legs: tuple[OptionLeg, ...] = field(default_factory=tuple)
    stock_quantity: float = 0.0
    stock_entry: float | None = None
    multiplier: float = CONTRACT_MULTIPLIER

    def __post_init__(self) -> None:
        object.__setattr__(self, "legs", tuple(self.legs))
        if not self.legs and self.stock_quantity == 0:
            raise ValueError("a strategy needs at least one leg or a stock position")
        if not math.isfinite(self.stock_quantity):
            raise ValueError("stock quantity must be finite")
        if self.stock_entry is not None and (
            not math.isfinite(self.stock_entry) or self.stock_entry <= 0
        ):
            raise ValueError("stock entry price must be positive")
        if not math.isfinite(self.multiplier) or self.multiplier <= 0:
            raise ValueError("multiplier must be positive")

    @property
    def expiries(self) -> tuple[float, ...]:
        """Distinct leg expiries (days), ascending."""
        return tuple(sorted({leg.dte for leg in self.legs}))


@dataclass(frozen=True)
class ExpiryProfile:
    """Exact at-expiry extremes and breakevens for a single-expiry strategy.

    ``max_profit`` may be ``inf`` and ``max_loss`` ``-inf`` (unbounded).
    """

    max_profit: float
    max_loss: float
    breakevens: tuple[float, ...]


def entry_cost(strategy: Strategy, market: MarketInputs) -> float:
    """Net entry cost :math:`C_0` in dollars; positive = debit, negative = credit."""
    total = strategy.stock_quantity * (strategy.stock_entry or market.spot)
    for leg in strategy.legs:
        total += leg.quantity * strategy.multiplier * leg_entry_premium(leg, market)
    return total


def leg_inputs(leg: OptionLeg, market: MarketInputs) -> tuple[float, float, float]:
    """The leg's ``(sigma, rate, dividend_yield)``: its own where set, else the market's."""
    return (market.sigma if leg.vol is None else leg.vol,
            market.rate if leg.rate is None else leg.rate,
            market.dividend_yield if leg.dividend_yield is None else leg.dividend_yield)


def leg_entry_premium(leg: OptionLeg, market: MarketInputs) -> float:
    """The premium paid per share: the recorded one, else fair value at entry."""
    if leg.premium is not None:
        return leg.premium
    sigma, rate, dividend_yield = leg_inputs(leg, market)
    return float(price_array(market.spot, leg.strike, leg.dte, rate, sigma, leg.option_type,
                             dividend_yield))


def strategy_value(
    strategy: Strategy, market: MarketInputs, spots: np.ndarray, days_forward: float = 0.0
) -> np.ndarray:
    """Mark-to-model value :math:`V(S, h)` at each scenario spot."""
    spots = _validate_spots(spots)
    _validate_horizon(days_forward)
    value = strategy.stock_quantity * spots
    for leg in strategy.legs:
        remaining = leg.dte - days_forward
        if remaining > 0:
            sigma, rate, dividend_yield = leg_inputs(leg, market)
            leg_value = price_array(spots, leg.strike, remaining, rate, sigma, leg.option_type,
                                    dividend_yield)
        else:
            leg_value = intrinsic_value(spots, leg.strike, leg.option_type)
        value = value + leg.quantity * strategy.multiplier * leg_value
    return value


def strategy_pnl(
    strategy: Strategy, market: MarketInputs, spots: np.ndarray, days_forward: float = 0.0
) -> np.ndarray:
    """P&L :math:`V(S, h) - C_0` at each scenario spot."""
    return strategy_value(strategy, market, spots, days_forward) - entry_cost(strategy, market)


def strategy_greeks(
    strategy: Strategy, market: MarketInputs, spots: np.ndarray, days_forward: float = 0.0
) -> Greeks:
    """Position greeks at each scenario spot; expired legs contribute nothing."""
    spots = _validate_spots(spots)
    _validate_horizon(days_forward)
    zeros = np.zeros_like(spots)
    totals = {"delta": zeros + strategy.stock_quantity, "gamma": zeros, "vega": zeros,
              "theta": zeros, "rho": zeros}
    for leg in strategy.legs:
        remaining = leg.dte - days_forward
        if remaining <= 0:
            continue
        sigma, rate, dividend_yield = leg_inputs(leg, market)
        g = greeks_array(spots, leg.strike, remaining, rate, sigma, leg.option_type,
                         dividend_yield)
        size = leg.quantity * strategy.multiplier
        for name in totals:
            totals[name] = totals[name] + size * getattr(g, name)
    return Greeks(**totals)


def expiry_profile(strategy: Strategy, market: MarketInputs) -> ExpiryProfile:
    """Exact extremes and breakevens at the common expiry (module docstring)."""
    if len(strategy.expiries) > 1:
        raise ValueError("exact expiry analysis needs all legs to share one expiry")
    cost = entry_cost(strategy, market)
    nodes = np.array(sorted({0.0, *(leg.strike for leg in strategy.legs)}))
    pnl = strategy_value(strategy, market, nodes, _expiry_horizon(strategy)) - cost
    tail_slope = strategy.stock_quantity + sum(
        leg.quantity * strategy.multiplier for leg in strategy.legs if leg.option_type == "call"
    )

    max_profit = math.inf if tail_slope > 0 else float(np.max(pnl))
    max_loss = -math.inf if tail_slope < 0 else float(np.min(pnl))

    roots: list[float] = []
    for i in range(len(nodes)):
        if pnl[i] == 0.0:
            roots.append(float(nodes[i]))
        if i + 1 < len(nodes) and pnl[i] * pnl[i + 1] < 0:
            fraction = pnl[i] / (pnl[i] - pnl[i + 1])
            roots.append(float(nodes[i] + fraction * (nodes[i + 1] - nodes[i])))
    if tail_slope != 0 and pnl[-1] * tail_slope < 0:
        roots.append(float(nodes[-1] - pnl[-1] / tail_slope))
    return ExpiryProfile(max_profit, max_loss, tuple(sorted(set(roots))))


def grid_breakevens(spots: np.ndarray, pnl: np.ndarray) -> tuple[float, ...]:
    """Zero crossings of a sampled P&L curve, by linear interpolation."""
    spots = np.asarray(spots, dtype=float)
    pnl = np.asarray(pnl, dtype=float)
    if spots.shape != pnl.shape or spots.ndim != 1:
        raise ValueError("spots and pnl must be matching one-dimensional arrays")
    roots = [float(s) for s, v in zip(spots, pnl) if v == 0.0]
    crossing = np.nonzero(pnl[:-1] * pnl[1:] < 0)[0]
    for i in crossing:
        fraction = pnl[i] / (pnl[i] - pnl[i + 1])
        roots.append(float(spots[i] + fraction * (spots[i + 1] - spots[i])))
    return tuple(sorted(roots))


def spot_grid(strategy: Strategy, market: MarketInputs, points: int = 241) -> np.ndarray:
    r"""Scenario spots covering the strikes and a :math:`\pm 3\sigma\sqrt{T}` move.

    The half-width in log-space is :math:`\max(3\sigma\sqrt{T_{max}}, 0.15)`;
    the 15% floor keeps very short-dated diagrams from collapsing to a
    sliver around spot. Strikes outside that band are included with 10% margin.
    """
    if points < 2:
        raise ValueError("points must be >= 2")
    longest = max(strategy.expiries, default=30.0) / 365.0
    half_width = max(3.0 * market.sigma * math.sqrt(longest), 0.15)
    strikes = [leg.strike for leg in strategy.legs]
    low = min([market.spot * math.exp(-half_width), *(0.9 * k for k in strikes)])
    high = max([market.spot * math.exp(half_width), *(1.1 * k for k in strikes)])
    return np.linspace(low, high, points)


def nearest_listed_strike(value: float, spot: float) -> float:
    """Round to a plausible listed-strike increment: 1/20 of spot's power of ten.

    Gives $5 increments for a $100-$999 underlying and $0.50 for $10-$99,
    close to US listing conventions, so templates show familiar strikes.
    """
    step = 10.0 ** math.floor(math.log10(spot)) / 20.0
    return max(step, round(value / step) * step)


# --------------------------------------------------------------------------
# Templates: standard structures, parameterized by spot, expiry, and width.
# --------------------------------------------------------------------------

TemplateBuilder = Callable[[float, float, float], Strategy]


def _template(build: Callable[[Callable[[float], float], float, float], Strategy]) -> TemplateBuilder:
    """Adapt a builder written in terms of ``k(offset)``, the listed strike near
    spot*(1+offset).

    Strikes are the listed strike nearest spot plus a whole number of listed
    steps, at least one for a nonzero offset. Rounding each strike on its own
    would break symmetric structures: at spot 771.51 a 5% butterfly would be
    735/770/810, wings of 35 and 40, its two tails no longer level.
    """

    def builder(spot: float, dte: float, width: float) -> Strategy:
        if spot <= 0 or dte <= 0 or not 0 < width < 1:
            raise ValueError("templates need spot > 0, dte > 0, and 0 < width < 1")
        step = nearest_listed_strike(0.0, spot)            # the listing increment
        centre = nearest_listed_strike(spot, spot)

        def k(offset: float) -> float:
            steps = round(spot * offset / step)
            if offset != 0.0 and steps == 0:
                steps = 1 if offset > 0 else -1
            return max(step, centre + steps * step)

        return build(k, dte, width)

    return builder


STRATEGY_TEMPLATES: dict[str, TemplateBuilder] = {
    "Long call": _template(lambda k, d, w: Strategy((OptionLeg("call", k(0), d, 1),))),
    "Long put": _template(lambda k, d, w: Strategy((OptionLeg("put", k(0), d, 1),))),
    "Covered call": _template(lambda k, d, w: Strategy(
        (OptionLeg("call", k(w), d, -1),), stock_quantity=CONTRACT_MULTIPLIER)),
    "Protective put": _template(lambda k, d, w: Strategy(
        (OptionLeg("put", k(-w), d, 1),), stock_quantity=CONTRACT_MULTIPLIER)),
    "Bull call spread": _template(lambda k, d, w: Strategy(
        (OptionLeg("call", k(0), d, 1), OptionLeg("call", k(w), d, -1)))),
    "Bear put spread": _template(lambda k, d, w: Strategy(
        (OptionLeg("put", k(0), d, 1), OptionLeg("put", k(-w), d, -1)))),
    "Long straddle": _template(lambda k, d, w: Strategy(
        (OptionLeg("call", k(0), d, 1), OptionLeg("put", k(0), d, 1)))),
    "Long strangle": _template(lambda k, d, w: Strategy(
        (OptionLeg("call", k(w), d, 1), OptionLeg("put", k(-w), d, 1)))),
    "Iron condor": _template(lambda k, d, w: Strategy((
        OptionLeg("put", k(-2 * w), d, 1), OptionLeg("put", k(-w), d, -1),
        OptionLeg("call", k(w), d, -1), OptionLeg("call", k(2 * w), d, 1)))),
    "Call butterfly": _template(lambda k, d, w: Strategy((
        OptionLeg("call", k(-w), d, 1), OptionLeg("call", k(0), d, -2),
        OptionLeg("call", k(w), d, 1)))),
    "Call calendar": _template(lambda k, d, w: Strategy(
        (OptionLeg("call", k(0), d, -1), OptionLeg("call", k(0), 2 * d, 1)))),
}


def _expiry_horizon(strategy: Strategy) -> float:
    return strategy.expiries[0] if strategy.legs else 0.0


def _validate_spots(spots: np.ndarray) -> np.ndarray:
    values = np.asarray(spots, dtype=float)
    if not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError("scenario spots must be finite and non-negative")
    return values


def _validate_horizon(days_forward: float) -> None:
    if not math.isfinite(days_forward) or days_forward < 0:
        raise ValueError("days forward must be non-negative")
