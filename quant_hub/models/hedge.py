r"""Hedges for the book, and one yardstick to compare them (HEDGE_PLAN U1).

U1 has the linear and rule-based candidates (§4), the rolled option
programs (§2–3), and the yardstick that compares them all (§5).

The book
========

Every candidate is applied to a daily return series :math:`R_{B,t}`: either
today's mix over its lookback, or ALLOCATION A2's β proxy over the longest
history, :math:`R_{B,t} = r_t + b\,(R_{m,t} - r_t)`. Here :math:`R_m` is the
market's daily total return, :math:`r` the T-bill's, and :math:`b` the
book's β (decision 8: the whole book is hedged). Every series is a daily
simple return, a fraction, on one shared calendar.

The candidates (§4)
===================

**Timing.** A rule decides at close :math:`t` and holds from :math:`t` to
:math:`t+1`, as the backtester does. So an exposure :math:`e_t` decided at
:math:`t` applies to day :math:`t+1`'s return. Before a rule has the history
it needs, it doesn't hedge: :math:`e = 1`.

**Scaling the market exposure.** With an exposure :math:`e_t \in [0, 1]`
and a hedge size :math:`h \in [0, 1]`, the book holds
:math:`1 - h\,(1 - e_{t-1})` of its excess return and the rest in bills:

.. math::  R_{H,t} = r_t + \bigl(1 - h\,(1 - e_{t-1})\bigr)\,(R_{B,t} - r_t)

:math:`h = 0` is the book itself. :math:`h = 1` follows the rule fully. A
partial :math:`h` is a mix of the two, rebalanced daily.

* **Cash at weight** :math:`w`: :math:`e \equiv 1 - w`, :math:`h = 1`, which
  gives :math:`R_H = (1 - w)\,R_B + w\,r`.
* **Trend on the market.** :math:`e_t \in \{0, 1\}` from the market's
  total-return level :math:`L_t = \prod_{s \le t} (1 + R_{m,s})`:
    - ``"sma"``: :math:`e_t = 1` when :math:`L_t` is at or above its
      :math:`n`-day average (200 by default), the moving-average rule
    - ``"momentum"``: :math:`e_t = 1` when the market's excess log return
      over the last :math:`n` days (252 by default) is positive, time-series
      momentum (Moskowitz, Ooi and Pedersen, 2012)
* **Vol targeting** (Moreira and Muir, 2017):
  :math:`e_t = \min(1, \sigma^* / \hat\sigma_{t+1|t})`, with
  :math:`\sigma^*` a target annual vol (the book's long-run vol, so it never
  levers) and :math:`\hat\sigma_{t+1|t}` tomorrow's annualized vol, known at
  :math:`t`:
    - ``"realized"``: :math:`\sqrt{252}` times the sample standard
      deviation of the last :math:`n` daily returns (21 by default)
    - ``"garch"``: GARCH(1,1) on the log returns (``models/garch.py``),
      :math:`\hat\sigma^2_{t+1|t} = 252\,h_{t+1}` with
      :math:`h_{t+1} = \omega + \alpha (y_t - \mu)^2 + \beta h_t`. Its
      parameters are fitted on the whole series, so they know the sample.
      That's a stated look-ahead, and ``"realized"`` has none.

**Holding another stream** at weight :math:`w` (Treasuries, a trend fund):
:math:`R_H = (1 - w)\,R_B + w\,R_S`, rebalanced daily.

Rolled option programs (§2–3)
=============================

**A program** holds legs on the index :math:`S`, all struck at a roll and
expiring :math:`T` calendar days later:
* **a put** at :math:`K = m\,S_\tau` (``moneyness`` :math:`m`; 0.9 is 10% out
  of the money)
* **a put spread:** that put, less a put at :math:`K_2 = m_2 S_\tau < K`,
  which caps the protection at :math:`K - K_2` and cuts the premium
* **a collar:** that put, less a call at :math:`K_c` chosen so the call's
  price equals the put's, :math:`C(K_c) = P(K)`, at zero net premium
  before costs. The call's price falls continuously as :math:`K_c` rises
  (it's :math:`-e^{-rT}\Pr(S_T > K_c) < 0` per unit of strike), so
  bisection above the forward finds it.

**Rolls.** At a roll :math:`\tau` the old legs are closed (settled at
intrinsic value if expired, else sold at the mark), and new ones are
bought on

.. math::  q_\tau = \frac{h\,b\,W_\tau}{S_\tau}

units of the index: the hedged notional :math:`h\,b\,W_\tau`, with
:math:`W_\tau` the hedged book's value. The next roll is at expiry, or
after ``roll_every`` trading days if that's sooner.

**Self-financing.** The book's own holdings :math:`A` earn :math:`R_B`
every day. Premiums come out of :math:`A`, and proceeds go back in. With
:math:`u_t` the program's mark per unit (the legs' signed prices),

.. math::  A_t = A_{t-1}(1 + R_{B,t}), \qquad W_t = A_t + q\,u_t, \qquad
           R_{H,t} = \frac{W_t}{W_{t-1}} - 1

and :math:`W_{-1} = 1`. On a roll day, the book's return comes first, then
the trades at the close.

**Costs.** Each trade pays a half-spread :math:`c` as a fraction of each
leg's price (decision 6): bought at :math:`p(1 + c)`, sold at
:math:`p(1 - c)`. Settlement at expiry pays none.

**Marks.** Each open leg is marked at Black–Scholes (European: the
index's options are; ETF puts' early exercise is small out of the money,
and HORIZON X1 is its lattice). At expiry it's worth its intrinsic value.
A position uses the rate and dividend yield of the day it was bought for
its whole life, which over 1 to 12 months moves its marks negligibly.

**Implied vol through history** (§3). Option prices from decades ago aren't
in reach, so the marks are model prices:

.. math::  \sigma_t(k, T) = \mathrm{VIX}_t \cdot \rho \cdot s(k, T), \qquad
           k = \ln\frac{K}{F_t}, \quad F_t = S_t\,e^{(r - q)T}

* :math:`\mathrm{VIX}_t` is the day's 30-day index vol, a fraction.
* :math:`\rho = \sigma_\text{ATM} / \mathrm{VIX}`, measured today on your own
  surface (the ATM 30-day IV over its variance strike).
* :math:`s(k, T)` is today's smile and term shape, normalized to 1 at the
  30-day ATM point. It's held fixed for decades (decision 5), so a flat
  smile, :math:`s \equiv 1`, runs beside it. In a crash the term structure
  inverts, so a fixed shape understates short-dated put marks and
  overstates long-dated ones.

**Granularity.** One contract covers 100 units. The ideal count
:math:`n = h\,b\,V / (100\,S)` rounds to whole contracts, which give the
ratio :math:`h_\text{eff} = 100\,S\,n_\text{whole} / (b\,V)`.

This is the measurement Coval and Shumway (2001) made of expected put
returns, run on a book: what a rolled index put has actually cost and paid.

The yardstick (§5)
==================

**Crash windows, by rule.** A crash is each spell in which the market's
total-return level fell at least 15% from a peak (``models/stress.falls``).
Its window is the days after the peak through the trough. None are
chosen by hand.

**Cost:** what the hedge gives up while nothing happens, the annualized
mean of :math:`R_{B,t} - R_{H,t}` over the days outside every crash window,
:math:`252\,\overline{(R_B - R_H)}`, with its Newey–West error. Positive is
a cost; a hedge that earned more than the book in calm times has a
negative one.

**The crash table:** for each window, the book's return over it
unhedged and hedged, :math:`\prod (1 + R) - 1`, and the difference, which
is what the hedge paid.

**Δ log growth, with its error.** The paired daily difference
:math:`d_t = \ln(1 + R_{H,t}) - \ln(1 + R_{B,t})` gives
:math:`\Delta g = 252\,\bar d`, the hedge's effect on long-run growth. Its
standard error is Newey–West's, from regressing :math:`d` on a constant
(``models/regression.ols``). Pairing removes the market noise both sides
share, so the error is far smaller than either growth rate's own. It's
measured in log growth, the owner's CAGR thesis (decision 7).

**Drawdown:** the deepest peak-to-trough fall and the worst 12 months
(252 trading days, compounded), hedged and not.

**Convexity** (Henriksson and Merton's up and down betas). From monthly
returns, compounded from the days, the hedge's own return
:math:`y = R_H - R_B` is regressed on the market's up and down parts:

.. math::  y = \alpha + \beta_\uparrow \max(R_m, 0) + \beta_\downarrow \min(R_m, 0) + \varepsilon

A hedge earns its name when :math:`\beta_\downarrow < \beta_\uparrow`: it pays
more per point of market fall than it costs per point of rise. Cash is
linear, :math:`\beta_\uparrow = \beta_\downarrow = -w\,b`. A trend rule that's
out in long falls has :math:`\beta_\downarrow` near :math:`-h\,b` and
:math:`\beta_\uparrow` near zero.

What it can't say
-----------------
* **Few crashes.** About a dozen windows in a century, fewer since 1993.
  The table shows each, so a hedge that worked in 2008 and not in 2020 is
  visible as such.
* **Selection.** Many candidates and sizes run through one history, and the
  best-looking one will disappoint. Nothing here optimizes: the lab shows
  them all.
* **No costs** for the rule candidates. A trend rule's switches each pay a
  spread, which is small next to the effects measured here.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.models.black_scholes import DAYS_PER_YEAR, price_array
from quant_hub.models.garch import conditional_variances, fit_garch11
from quant_hub.models.regression import NEWEY_WEST, ols
from quant_hub.models.stress import Fall, falls

TRADING_DAYS = 252
CRASH_DEPTH = 0.15
TREND_RULES = ("sma", "momentum")
VOL_ESTIMATORS = ("realized", "garch")


# ------------------------------------------------------------------ inputs

def _series(name: str, values: pd.Series) -> pd.Series:
    if not isinstance(values, pd.Series) or not isinstance(values.index, pd.DatetimeIndex):
        raise ValueError(f"{name} must be a Series on a DatetimeIndex")
    if values.empty:
        raise ValueError(f"{name} is empty")
    if not values.index.is_monotonic_increasing or not values.index.is_unique:
        raise ValueError(f"{name}'s dates must be unique and ascending")
    array = values.to_numpy(dtype=float)
    if not np.isfinite(array).all():
        raise ValueError(f"{name}'s returns must be finite")
    if (array <= -1.0).any():
        raise ValueError(f"{name} has a return of −100% or worse")
    return values.astype(float)


def _same_days(**named: pd.Series) -> list[pd.Series]:
    """The series, validated, on one shared calendar (it must be the same)."""
    checked = [_series(name, s) for name, s in named.items()]
    first = checked[0].index
    for (name, s) in zip(named, checked):
        if not s.index.equals(first):
            raise ValueError(f"{name} isn't on the same days as {next(iter(named))}")
    return checked


def _size(name: str, value: float) -> float:
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise ValueError(f"{name} must be between 0 and 1, not {value}")
    return float(value)


def _window(value: int, minimum: int = 2) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"a window is a whole number of days ≥ {minimum}, not {value!r}")
    return value


# -------------------------------------------------------------- candidates

def beta_book(market: pd.Series, rf: pd.Series, beta: float) -> pd.Series:
    """A2's β proxy, R_B = r + b (R_m − r)."""
    market, rf = _same_days(market=market, rf=rf)
    if not math.isfinite(beta):
        raise ValueError("β must be finite")
    return rf + beta * (market - rf)


def scaled(book: pd.Series, rf: pd.Series, exposure: pd.Series, h: float) -> pd.Series:
    """R_H = r + (1 − h (1 − e_{t−1})) (R_B − r): the exposure decided at
    close t − 1 applies to day t; the first day is unhedged."""
    book, rf, exposure = _same_days(book=book, rf=rf, exposure=exposure)
    h = _size("h", h)
    e = exposure.to_numpy()
    if ((e < 0) | (e > 1)).any():
        raise ValueError("an exposure must be between 0 and 1")
    held = 1.0 - h * (1.0 - np.concatenate(([1.0], e[:-1])))
    return pd.Series(rf.to_numpy() + held * (book - rf).to_numpy(), index=book.index)


def cash(book: pd.Series, rf: pd.Series, w: float) -> pd.Series:
    """R_H = (1 − w) R_B + w r."""
    return blend(book, rf, w)


def blend(book: pd.Series, other: pd.Series, w: float) -> pd.Series:
    """R_H = (1 − w) R_B + w R_S: another stream held at weight w."""
    book, other = _same_days(book=book, other=other)
    w = _size("w", w)
    return (1.0 - w) * book + w * other


def trend_exposure(market: pd.Series, rf: pd.Series, rule: str = "sma",
                   window: int | None = None) -> pd.Series:
    """e_t ∈ {0, 1} decided at close t (module docstring); 1 during the
    warm-up, before the rule has its window."""
    market, rf = _same_days(market=market, rf=rf)
    if rule not in TREND_RULES:
        raise ValueError(f"unknown trend rule {rule!r}; one of {TREND_RULES}")
    n = _window(window if window is not None else (200 if rule == "sma" else 252))
    if rule == "sma":
        level = np.cumprod(1.0 + market.to_numpy())
        average = pd.Series(level).rolling(n).mean().to_numpy()
        on = np.where(np.isnan(average), True, level >= average)
    else:
        excess = np.log1p(market.to_numpy()) - np.log1p(rf.to_numpy())
        trailing = pd.Series(excess).rolling(n).sum().to_numpy()
        on = np.where(np.isnan(trailing), True, trailing > 0.0)
    return pd.Series(on.astype(float), index=market.index, name="exposure")


def vol_target_exposure(book: pd.Series, target: float, estimator: str = "realized",
                        window: int = 21) -> pd.Series:
    """e_t = min(1, σ* / σ̂_{t+1|t}), decided at close t (module docstring)."""
    (book,) = _same_days(book=book)
    if not (math.isfinite(target) and target > 0):
        raise ValueError("the target vol must be positive")
    if estimator not in VOL_ESTIMATORS:
        raise ValueError(f"unknown estimator {estimator!r}; one of {VOL_ESTIMATORS}")
    if estimator == "realized":
        n = _window(window)
        sigma = book.rolling(n).std(ddof=1).to_numpy() * math.sqrt(TRADING_DAYS)
    else:
        y = np.log1p(book.to_numpy())
        p = fit_garch11(y).parameters
        h = conditional_variances(y, p)
        h_next = p.omega + p.alpha * (y - p.mean) ** 2 + p.beta * h
        sigma = np.sqrt(TRADING_DAYS * h_next)
    with np.errstate(divide="ignore", invalid="ignore"):
        e = np.where(np.isfinite(sigma) & (sigma > 0), np.minimum(1.0, target / sigma), 1.0)
    return pd.Series(e, index=book.index, name="exposure")


# --------------------------------------------------------- option programs

Smile = Callable[[np.ndarray, np.ndarray], np.ndarray]
"""s(k, T): a vol multiplier for log-moneyness k = ln(K/F) and T in years."""


def flat_smile(k: np.ndarray, years: np.ndarray) -> np.ndarray:
    """s ≡ 1: every strike and tenor at the day's ATM vol."""
    return np.ones(np.broadcast(np.asarray(k), np.asarray(years)).shape)


@dataclass(frozen=True)
class OptionProgram:
    """A rolled option hedge (module docstring)."""

    moneyness: float = 0.9             # the long put's K / S at the roll
    tenor: int = 91                    # calendar days to expiry
    roll_every: int | None = None      # trading days between rolls; None rolls at expiry
    short_put: float | None = None     # a put spread's short put, K₂ / S
    collar: bool = False               # sell the zero-cost call
    half_spread: float = 0.0           # per trade, a fraction of each leg's price

    def __post_init__(self) -> None:
        if not (math.isfinite(self.moneyness) and 0.2 <= self.moneyness <= 1.5):
            raise ValueError("a put's moneyness K/S is between 0.2 and 1.5")
        if not isinstance(self.tenor, int) or isinstance(self.tenor, bool) or self.tenor < 1:
            raise ValueError("the tenor is a whole number of calendar days ≥ 1")
        if self.roll_every is not None:
            _window(self.roll_every, minimum=1)
        if self.short_put is not None and not 0.0 < self.short_put < self.moneyness:
            raise ValueError("a put spread's short put is below the long put")
        if self.collar and self.short_put is not None:
            raise ValueError("a program is a put, a put spread or a collar, not both")
        if not (math.isfinite(self.half_spread) and 0.0 <= self.half_spread < 1.0):
            raise ValueError("the half-spread is a fraction of the price, in [0, 1)")


@dataclass(frozen=True)
class ProgramRun:
    """A program's hedged book, and what it traded."""

    returns: pd.Series          # R_H
    rolls: int
    premium: float              # premiums paid at the rolls, net of legs sold, a year
    payoff: float               # what closing and settling brought back, a year
    spread: float               # half-spreads paid, a year
    # each a fraction of the hedged book's value when it happened

    @property
    def net_cost(self) -> float:
        """Premiums less payoffs, a year: the program's cost after what it paid."""
        return self.premium - self.payoff


@dataclass(frozen=True)
class Contracts:
    """Whole contracts against the ideal count (module docstring)."""

    ideal: float
    whole: int
    h_effective: float


def contracts(h: float, beta: float, value: float, spot: float,
              multiplier: int = 100) -> Contracts:
    """n = h b V / (100 S), rounded to the nearest whole contract, and the
    hedge ratio that whole number gives."""
    h = _size("h", h)
    for name, x in (("β", beta), ("the book's value", value), ("the spot", spot)):
        if not (math.isfinite(x) and x > 0):
            raise ValueError(f"{name} must be positive")
    if multiplier < 1:
        raise ValueError("a contract covers at least one unit")
    ideal = h * beta * value / (multiplier * spot)
    whole = int(math.floor(ideal + 0.5))
    return Contracts(ideal, whole, multiplier * spot * whole / (beta * value))


def collar_strike(spot: float, put_strike: float, days: float, rate: float, dividend: float,
                  vol: Callable[[float], float]) -> float | None:
    """K_c with C(K_c) = P(K), by bisection above the forward. ``vol(K)``
    is the implied vol at strike K. None when the put is worth too little
    to pay for any call."""
    def call(k: float) -> float:
        return float(price_array(spot, k, days, rate, vol(k), "call", dividend))
    put = float(price_array(spot, put_strike, days, rate, vol(put_strike), "put", dividend))
    low = spot * math.exp((rate - dividend) * days / DAYS_PER_YEAR)
    high = 2.0 * low
    while call(high) > put:
        high *= 2.0
        if high > 1e3 * low:
            return None
    if call(low) < put:                       # an in-the-money put: the call's below the forward
        low = 1e-3 * low
    for _ in range(80):
        middle = 0.5 * (low + high)
        low, high = (middle, high) if call(middle) > put else (low, middle)
    return 0.5 * (low + high)


def option_program(book: pd.Series, spot: pd.Series, rate: pd.Series, vix: pd.Series,
                   program: OptionProgram, h: float, beta: float, dividend: float = 0.0,
                   rho: float = 1.0, smile: Smile = flat_smile) -> ProgramRun:
    """The book with ``program`` rolled on it (module docstring). ``spot`` is
    the index's price level, ``rate`` the annual continuously compounded
    rate, ``vix`` the 30-day index vol (a fraction), all on the book's days;
    ``dividend`` the index's annual yield."""
    book, spot, rate, vix = _same_days(book=book, spot=spot, rate=rate, vix=vix)
    h = _size("h", h)
    if not (math.isfinite(beta) and math.isfinite(dividend) and math.isfinite(rho) and rho > 0):
        raise ValueError("β and the dividend yield must be finite, and ρ positive")
    S, r, v, rb = (s.to_numpy() for s in (spot, rate, vix, book))
    if (S <= 0).any() or (v <= 0).any():
        raise ValueError("the spot and the VIX must be positive")
    days = (book.index - book.index[0]).days.to_numpy()
    n = len(book)

    def sigma(t: np.ndarray, strike: float, left: np.ndarray, bought: int) -> np.ndarray:
        """σ_t(k, T) = VIX_t ρ s(k, T) on days t, with ``left`` calendar days to go."""
        years = left / DAYS_PER_YEAR
        forward = S[t] * np.exp((r[bought] - dividend) * years)
        s = np.asarray(smile(np.log(strike / forward), years), dtype=float)
        if not (np.isfinite(s).all() and (s > 0).all()):
            raise ValueError("the smile must be positive and finite")
        return v[t] * rho * s

    def legs_at(i: int) -> list[tuple[str, float, float]]:
        """The (type, strike, sign) bought at the roll on day i."""
        put = program.moneyness * S[i]
        legs = [("put", put, 1.0)]
        if program.short_put is not None:
            legs.append(("put", program.short_put * S[i], -1.0))
        if program.collar:
            today, tenor = np.array([i]), np.array([float(program.tenor)])
            strike = collar_strike(S[i], put, program.tenor, r[i], dividend,
                                   lambda k: float(sigma(today, k, tenor, i)[0]))
            if strike is not None:
                legs.append(("call", strike, -1.0))
        return legs

    def marks(i: int, end: int, legs, expiry: int) -> np.ndarray:
        """Each leg's price on days i..end (rows) at the day's vol, or its
        intrinsic value once expired."""
        t = np.arange(i, end + 1)
        left = (expiry - days[t]).astype(float)
        live = left > 0
        out = np.empty((len(t), len(legs)))
        for c, (kind, strike, _) in enumerate(legs):
            out[:, c] = (np.maximum(strike - S[t], 0.0) if kind == "put"
                         else np.maximum(S[t] - strike, 0.0))
            if live.any():
                on = t[live]
                out[live, c] = price_array(S[on], strike, left[live], r[i],
                                           sigma(on, strike, left[live], i), kind, dividend)
        return out

    wealth = np.empty(n)
    holdings = 1.0 + rb[0]          # A: day 0's return comes before the first purchase
    premium = payoff = spread = 0.0  # fractions of the book when each happened
    rolls, i = 0, 0
    while True:
        expiry = days[i] + program.tenor
        j = int(np.searchsorted(days, expiry, side="left"))       # first day at or past expiry
        if program.roll_every is not None:
            j = min(j, i + program.roll_every)
        end = min(j, n - 1)
        legs = legs_at(i) if h > 0 else []
        if legs:
            prices = marks(i, end, legs, expiry)
            unit = prices @ np.array([sign for _, _, sign in legs])
            q = h * beta * holdings / S[i]                         # W_i before trading = A_i
            fee = program.half_spread * q * float(prices[0].sum())
            premium += q * unit[0] / holdings
            spread += fee / holdings
            holdings -= q * unit[0] + fee
        else:
            prices, unit, q = np.zeros((end - i + 1, 0)), np.zeros(end - i + 1), 0.0
        wealth[i] = holdings + q * unit[0]
        rolls += 1
        for t in range(i + 1, end + 1):
            holdings *= 1.0 + rb[t]
            wealth[t] = holdings + q * unit[t - i]
        if j > n - 1:                                              # held to the data's end
            break
        # Close at j: settle at intrinsic if expired, else sell at the mark.
        value = q * unit[-1]
        fee = 0.0 if days[j] >= expiry else program.half_spread * q * float(prices[-1].sum())
        payoff += value / wealth[j]
        spread += fee / wealth[j]
        holdings += value - fee
        if j == n - 1:                                             # no new program on the last day
            wealth[j] = holdings
            break
        i = j                                                      # and roll again at close j
    returns = np.concatenate(([wealth[0] - 1.0], wealth[1:] / wealth[:-1] - 1.0))
    years = n / TRADING_DAYS
    return ProgramRun(pd.Series(returns, index=book.index), rolls, premium / years,
                      payoff / years, spread / years)

# --------------------------------------------------------------- yardstick

@dataclass(frozen=True)
class CrashRow:
    """One crash window: the book's return over it, unhedged and hedged."""

    window: Fall
    unhedged: float
    hedged: float

    @property
    def paid(self) -> float:
        """What the hedge added over the window, in return points."""
        return self.hedged - self.unhedged


@dataclass(frozen=True)
class Assessment:
    """The yardstick of §5 for one hedged book against the book."""

    cost: float                    # a year, outside the crash windows; positive costs
    cost_error: float
    crashes: tuple[CrashRow, ...]
    growth: float                  # Δg, a year
    growth_error: float            # Newey–West
    drawdown: tuple[float, float]  # deepest fall: unhedged, hedged
    worst_year: tuple[float | None, float | None]   # worst 252 days: unhedged, hedged
    beta_up: float
    beta_down: float
    beta_up_error: float
    beta_down_error: float

    @property
    def saved(self) -> float | None:
        """The mean of what the hedge paid over the crash windows."""
        return float(np.mean([c.paid for c in self.crashes])) if self.crashes else None


def crash_windows(market: pd.Series, depth: float = CRASH_DEPTH) -> tuple[Fall, ...]:
    """Every spell in which the market's level fell at least ``depth`` from
    a peak, by date."""
    (market,) = _same_days(market=market)
    if not 0.0 < depth < 1.0:
        raise ValueError("the crash depth is a fraction between 0 and 1")
    level = pd.Series(np.cumprod(1.0 + market.to_numpy()), index=market.index)
    _, spells = falls(level, top=len(level))
    return tuple(sorted((f for f in spells if f.depth >= depth), key=lambda f: f.peak))


def assess(book: pd.Series, hedged: pd.Series, market: pd.Series,
           windows: tuple[Fall, ...] | None = None) -> Assessment:
    """The yardstick (module docstring) for ``hedged`` against ``book``.
    ``windows`` defaults to the market's own crash windows."""
    book, hedged, market = _same_days(book=book, hedged=hedged, market=market)
    if len(book) < 3:
        raise ValueError("the yardstick needs at least three days")
    windows = crash_windows(market) if windows is None else windows
    dates = book.index.date
    inside = np.zeros(len(book), dtype=bool)
    rows = []
    for w in windows:
        days = (dates > w.peak) & (dates <= w.trough)
        inside |= days
        if days.any():
            rows.append(CrashRow(w, _compound(book[days]), _compound(hedged[days])))
    spread = (hedged - book).to_numpy()
    calm = spread[~inside]
    cost, cost_error = _annual_mean(-calm) if len(calm) >= 2 else (math.nan, math.nan)
    d = np.log1p(hedged.to_numpy()) - np.log1p(book.to_numpy())
    growth, growth_error = _annual_mean(d)
    up, down = _convexity(book, hedged, market)
    return Assessment(
        cost=cost, cost_error=cost_error, crashes=tuple(rows), growth=growth,
        growth_error=growth_error, drawdown=(_deepest(book), _deepest(hedged)),
        worst_year=(_worst_year(book), _worst_year(hedged)), beta_up=up[0], beta_down=down[0],
        beta_up_error=up[1], beta_down_error=down[1])


def _compound(returns: pd.Series) -> float:
    return float(np.expm1(np.log1p(returns.to_numpy()).sum()))


def _annual_mean(values: np.ndarray) -> tuple[float, float]:
    """252 × the mean, and 252 × its Newey–West standard error."""
    fit = ols(values, np.ones((len(values), 1)), ("mean",))
    return TRADING_DAYS * fit.value("mean"), TRADING_DAYS * fit.error("mean", NEWEY_WEST)


def _deepest(returns: pd.Series) -> float:
    level = pd.Series(np.cumprod(1.0 + returns.to_numpy()), index=returns.index)
    _, spells = falls(level, top=1)
    return spells[0].depth if spells else 0.0


def _worst_year(returns: pd.Series) -> float | None:
    if len(returns) < TRADING_DAYS:
        return None
    rolled = np.log1p(returns).rolling(TRADING_DAYS).sum()
    return float(np.expm1(rolled.min()))


def _convexity(book: pd.Series, hedged: pd.Series, market: pd.Series
               ) -> tuple[tuple[float, float], tuple[float, float]]:
    """(β_up, its SE), (β_down, its SE) of the hedge's monthly return on the
    market's up and down months. NaN when the months don't support a fit."""
    def monthly(s: pd.Series) -> pd.Series:
        return np.expm1(np.log1p(s).resample("ME").sum())
    m = monthly(market)
    y = monthly(hedged) - monthly(book)
    X = np.column_stack([np.ones(len(m)), np.maximum(m, 0.0), np.minimum(m, 0.0)])
    if len(m) < 6 or (m > 0).sum() < 2 or (m < 0).sum() < 2:
        return (math.nan, math.nan), (math.nan, math.nan)
    fit = ols(y.to_numpy(), X, ("alpha", "up", "down"))
    return ((fit.value("up"), fit.error("up", NEWEY_WEST)),
            (fit.value("down"), fit.error("down", NEWEY_WEST)))
