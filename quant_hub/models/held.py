r"""Returns as held: the account's value day by day, and what it earned (TR6).

The derivation below was reviewed and approved before the code
(2026-11-25). The points that were reviewed are marked **Review:**;
each was kept as proposed.

Every page so far replays **today's** weights over the past. This module
uses the history (``data/activity.py``) to rebuild what was actually held
each day, so the return is the one the account really had: its timing,
its cash, its shorts, its margin.

0. The cash, explained first
----------------------------
The replay (``models/ledger.py``, TR2) matched every holding of every
positions export, but its cash check missed by up to about a thousand
dollars over one summer. Its cash was "shown, not judged". Returns need
the cash right, so this came first. On a real history the cause turned out
to be four things, and the rule below reproduces every export's cash lines
to the cent:

1. **An export's cash line is the core money-market fund only.** A broker
   keeps an account's cash in **types**: cash, margin, and short. Each
   activity row says which (Fidelity's wording ends with it: "(Cash)",
   "(Margin)", "(Short)"). A short sale's proceeds sit in the **short**
   type, marked to market daily by journals between short and margin, and
   never appear in the cash line.
2. **Cash moves on the settlement date.** A trade on the export's day (or
   the day before) shows in its shares but not yet in its cash.
3. **A core-fund dividend reinvested in the core fund** doesn't spend
   cash: it buys the cash line itself.
4. **A margin debit has no line.** When an account's cash and margin
   balance is below zero (a loan), the export shows no cash at all.

So, per account, with :math:`B_k(d)` the sum of the settled amounts of
type k through day d, counting a core-fund reinvestment as zero:

.. math::  L(d) = \max\bigl(0,\; B_{\text{cash}}(d) + B_{\text{margin}}(d)\bigr)

**Review:** this becomes the replay's cash check: **pass or fail to the
cent**, not "shown". Rows with no type (the generic layout, or a broker
that doesn't say) are taken as cash type, and such an account is checked
on its total.

**Review:** the type is read from the row's own wording (a trailing
"(Cash)", "(Margin)" or "(Short)"), so nothing stored changes. The
generic layout may give it in a ``type`` column instead.

1. What was held, day by day
----------------------------
:math:`N_s(t)`, the units of each security s at the close of session t, is
the history replayed by **trade date**: buys, sells, shorts, covers,
reinvestments, transfers, splits and option events (``models/ledger.py``'s
replay, from zero; the replay already checks that it reaches every
export).

**Core funds are cash, not holdings** (section 0, point 3): their units
are dollars, and they sit in :math:`C_t`.

2. What it was worth
--------------------
.. math::  V_t = \sum_s N_s(t)\,P_s(t) + C_t

- :math:`P_s(t)` is s's **traded** close.

  **Review:** not the adjusted close. Dividends are already in the
  history as cash, and an adjusted price would count them twice.
- :math:`C_t` is **all** of the account's cash, every type, on a **trade
  date** basis: the sum of every amount through t, from the first day of
  the history.
  - A short's proceeds are in :math:`C_t`, and its negative
    :math:`N_s P_s` offsets them.
  - A margin loan is negative cash.

  **Review:** trade date, not settlement, so the value doesn't jump when
  a trade settles: an unsettled amount is owed either way.
- **The book is every account together.** A transfer between two of your
  accounts cancels.
- **A holding with no close that day** (a name without bars, an option
  without option prices, crypto) is carried at its **last trade price**,
  and that day is marked "partly at cost".

  **Review:** carrying, rather than dropping the day, keeps the chain of
  returns unbroken. The share of value carried is shown, so a day that's
  mostly guesswork says so.

3. Money in and out (external flows)
------------------------------------
:math:`F_t` is what came into the book from outside on day t (positive)
or left it (negative):
- deposits and withdrawals;
- securities transferred in or out, at that day's close times the units.

Dividends, interest, fees and journals are inside the book, so they're
returns, not flows.

4. Your skill: the time-weighted return
---------------------------------------
With a flow counted at the start of its day (money that arrives can be
traded that day):

.. math::  r_t = \frac{V_t}{V_{t-1} + F_t} - 1, \qquad
           1 + R = \prod_t (1 + r_t)

Deposits and withdrawals don't move it; only what the holdings did.
Annualized as :math:`(1 + R)^{252/n} - 1` over n sessions. A day where
:math:`V_{t-1} + F_t \le 0` has no return, and the chain stops there and
says so.

5. Your dollars: the money-weighted return
------------------------------------------
The rate y at which the flows grow into the end value, with τ in years
(days / 365.25) from the start:

.. math::  V_0 + \sum_i F_i\,(1 + y)^{-\tau_i} = V_T\,(1 + y)^{-T}

It's solved by bisection on :math:`y \in (-0.99, 10)`.

**Review:** when the money only ever went in (or only out), Descartes'
rule of signs makes the root unique. When it went both ways the page says
"no unique rate" instead of picking one, unless the function crosses zero
exactly once on the interval.

**The gap between the two** is what the timing of your deposits and
withdrawals did.

6. Against the market, the same dollars
---------------------------------------
The same flows, on the same days, put into SPY instead (its adjusted
closes, so dividends are in) give :math:`V^{\text{SPY}}_T` and that
book's own two returns. The difference :math:`V_T - V^{\text{SPY}}_T` is,
in dollars, what holding what you held earned over holding the market.

7. The window
-------------
The returns run over the sessions the price source covers for what was
held (two years on Massive's free plan) **from the first session where
the book's value is known**. Before that, the history still gives the
units, but not the prices.

**Review:** the start is the first session on which the book is at least
90% priced by closes, not carried. An IBKR backfill extends it back.

8. The page
-----------
TR6b: the Trades page shows the value, the two returns, the gap and the
market beside them. The Portfolio page gets the option to replay its
history on the real :math:`N_s(t)` instead of today's weights.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from quant_hub.data.activity import TRADES, Kind, Transaction

CARRIED_LIMIT = 0.10             # the window starts at 90% priced by closes (section 7)
TRADING_DAYS = 252
YEAR_DAYS = 365.25
RATE_BOUNDS = (-0.99, 10.0)      # the money-weighted rate's search interval
_FLOWS = (Kind.DEPOSIT, Kind.WITHDRAWAL)
_TRANSFERS = (Kind.TRANSFER_IN, Kind.TRANSFER_OUT)


# ------------------------------------------------------------ 1 and 2
@dataclass(frozen=True)
class Book:
    """Section 1 and 2's inputs on the sessions: the units held, all the
    cash, each name's last trade price (for carrying) and multiplier."""

    sessions: pd.DatetimeIndex
    units: pd.DataFrame            # session x symbol, at the close (trade date)
    cash: pd.Series                # all of it, every type, trade date
    last_price: pd.DataFrame       # session x symbol: the latest trade price per unit
    multipliers: Mapping[str, float]


def _session(sessions: pd.DatetimeIndex, day: date) -> int:
    """The first session on or after ``day`` (a weekend dividend counts from
    the next session)."""
    return int(sessions.searchsorted(pd.Timestamp(day)))


def book(transactions: Iterable[Transaction], cash_funds: set[str],
         sessions: pd.DatetimeIndex) -> Book:
    """Sections 1 and 2: units and cash at each session's close, by trade
    date, from the first row of the history. Cash funds are cash (their
    share moves are cash into cash); a row the importer couldn't read
    (``other``) moves its cash, as the broker did, but no shares."""
    n = len(sessions)
    units: dict[str, np.ndarray] = {}
    prices: dict[str, list[tuple[int, float]]] = {}
    multipliers: dict[str, float] = {}
    cash = np.zeros(n)
    for t in transactions:
        i = _session(sessions, t.trade_date)
        if i >= n:
            continue
        in_fund = t.quantity != 0 and t.symbol in cash_funds
        if not in_fund:
            cash[i] += t.amount
        if t.quantity != 0 and not in_fund and t.kind is not Kind.OTHER:
            units.setdefault(t.symbol, np.zeros(n))[i] += t.quantity
            multipliers[t.symbol] = t.multiplier
            if t.kind in TRADES and t.price is not None:
                prices.setdefault(t.symbol, []).append((i, t.price * t.multiplier))
    held = pd.DataFrame({s: np.cumsum(q) for s, q in units.items()}, index=sessions)
    last = pd.DataFrame(index=sessions, columns=list(held.columns), dtype=float)
    for symbol, points in prices.items():
        for i, p in points:
            last.iloc[i, last.columns.get_loc(symbol)] = p
    return Book(sessions, held, pd.Series(np.cumsum(cash), index=sessions), last.ffill(),
                multipliers)


@dataclass(frozen=True)
class Valued:
    """Section 2 on the sessions."""

    value: pd.Series               # V_t
    carried: pd.Series             # the share of gross value at a carried price
    price: pd.DataFrame            # session x symbol: the price used, per unit
    carried_names: tuple[str, ...]


def value(b: Book, closes: Mapping[str, pd.Series]) -> Valued:
    """Section 2: units at each session's traded close, a name without one
    that day at its last trade price (carried), plus all the cash."""
    used = pd.DataFrame(index=b.sessions, columns=b.units.columns, dtype=float)
    is_close = pd.DataFrame(False, index=b.sessions, columns=b.units.columns)
    for symbol in b.units.columns:
        close = closes.get(symbol)
        on_day = (close.reindex(b.sessions) * b.multipliers.get(symbol, 1.0)
                  if close is not None else pd.Series(np.nan, index=b.sessions))
        is_close[symbol] = on_day.notna()
        used[symbol] = on_day.fillna(b.last_price[symbol])
    exposure = (b.units * used).fillna(0.0)
    held_value = exposure.sum(axis=1)
    gross = exposure.abs().sum(axis=1)
    carried_value = exposure.where(~is_close, 0.0).abs().sum(axis=1)
    carried = (carried_value / gross).where(gross > 0, 0.0)
    held_any = (b.units != 0)
    names = tuple(sorted(s for s in b.units.columns if (held_any[s] & ~is_close[s]).any()))
    return Valued(held_value + b.cash, carried, used, names)


# ------------------------------------------------------------ 3
def flows(transactions: Iterable[Transaction], b: Book, price: pd.DataFrame) -> pd.Series:
    """Section 3: deposits and withdrawals, and securities moved in or out
    at that session's price, on the session they count from."""
    out = np.zeros(len(b.sessions))
    for t in transactions:
        i = _session(b.sessions, t.trade_date)
        if i >= len(b.sessions):
            continue
        if t.kind in _FLOWS:
            out[i] += t.amount
        elif t.kind in _TRANSFERS and t.symbol in price.columns:
            p = price.iloc[i][t.symbol]
            if pd.notna(p):
                out[i] += t.quantity * p
    return pd.Series(out, index=b.sessions)


# ------------------------------------------------------------ 4 and 5
@dataclass(frozen=True)
class Returns:
    twr: float | None
    twr_annual: float | None
    daily: pd.Series               # r_t from the day after the start
    mwr: float | None
    mwr_note: str                  # why there's no money-weighted rate, else ""
    stopped: str                   # why the time-weighted chain stopped early, else ""


def time_weighted(value_: pd.Series, flow: pd.Series) -> tuple[pd.Series, str]:
    """Section 4's daily returns, flows at the start of their day; the chain
    stops at a day with nothing invested."""
    returns, stopped = [], ""
    for k in range(1, len(value_)):
        base = value_.iloc[k - 1] + flow.iloc[k]
        if not base > 0:
            stopped = f"nothing invested at the start of {value_.index[k].date()}"
            break
        returns.append(value_.iloc[k] / base - 1.0)
    return pd.Series(returns, index=value_.index[1:1 + len(returns)]), stopped


def money_weighted(v0: float, cash_flows: Sequence[tuple[float, float]], v_end: float,
                   years: float) -> tuple[float | None, str]:
    """Section 5: the rate y with V_0 (1+y)^T + Σ F_i (1+y)^(T−τ_i) = V_T, by
    bisection; None, and why, when there's no single answer."""
    if not years > 0:
        return None, "no time has passed"
    terms = [(years, v0), *((years - tau, f) for tau, f in cash_flows), (0.0, -v_end)]

    def f(y: float) -> float:
        return sum(c * (1.0 + y) ** e for e, c in terms)

    signs = [c for _, c in sorted(terms, key=lambda ec: -ec[0]) if c != 0]
    changes = sum(1 for a, b in zip(signs, signs[1:]) if (a > 0) != (b > 0))
    lo, hi = RATE_BOUNDS
    if changes != 1:
        grid = np.linspace(lo, hi, 4001)
        values = [f(y) for y in grid]
        brackets = [(grid[k], grid[k + 1]) for k in range(len(grid) - 1)
                    if (values[k] > 0) != (values[k + 1] > 0)]
        if len(brackets) != 1:
            return None, "no unique rate: the money went in and out"
        lo, hi = brackets[0]
    f_lo, f_hi = f(lo), f(hi)
    if (f_lo > 0) == (f_hi > 0):
        return None, "the rate is outside −99% to +1,000% a year"
    for _ in range(200):
        mid = (lo + hi) / 2
        if (f(mid) > 0) == (f_lo > 0):
            lo, f_lo = mid, f(mid)
        else:
            hi = mid
        if hi - lo < 1e-12:
            break
    return (lo + hi) / 2, ""


def returns(value_: pd.Series, flow: pd.Series) -> Returns:
    """Sections 4 and 5 over the window ``value_`` covers (its first session
    is the start: what's held then is V_0)."""
    daily, stopped = time_weighted(value_, flow)
    twr = float(np.prod(1.0 + daily.to_numpy()) - 1.0) if len(daily) else None
    annual = ((1.0 + twr) ** (TRADING_DAYS / len(daily)) - 1.0
              if twr is not None and len(daily) and twr > -1 else None)
    start = value_.index[0]
    years = (value_.index[-1] - start).days / YEAR_DAYS
    cash_flows = [((day - start).days / YEAR_DAYS, float(f))
                  for day, f in flow.iloc[1:].items() if f != 0]
    mwr, note = money_weighted(float(value_.iloc[0]), cash_flows, float(value_.iloc[-1]), years)
    return Returns(twr, annual, daily, mwr, note, stopped)


# ------------------------------------------------------------ 6 and 7
@dataclass(frozen=True)
class MarketBook:
    """Section 6: the same dollars, on the same days, in the market."""

    symbol: str
    value_end: float
    twr: float
    mwr: float | None
    mwr_note: str


def market_book(symbol: str, value_: pd.Series, flow: pd.Series,
                adjusted: pd.Series) -> MarketBook | None:
    """V_0 and every later flow into ``symbol`` at its adjusted close."""
    p = adjusted.reindex(value_.index).ffill()
    if p.isna().any():
        return None
    units = value_.iloc[0] / p.iloc[0] + float((flow.iloc[1:] / p.iloc[1:]).sum())
    end = float(units * p.iloc[-1])
    start = value_.index[0]
    years = (value_.index[-1] - start).days / YEAR_DAYS
    cash_flows = [((day - start).days / YEAR_DAYS, float(f))
                  for day, f in flow.iloc[1:].items() if f != 0]
    mwr, note = money_weighted(float(value_.iloc[0]), cash_flows, end, years)
    return MarketBook(symbol, end, float(p.iloc[-1] / p.iloc[0] - 1.0), mwr, note)


def window_start(valued: Valued, limit: float = CARRIED_LIMIT) -> int | None:
    """Section 7: the first session with something held, at most ``limit``
    of it carried, from which every later session is too."""
    ok = ((valued.carried <= limit) & (valued.value.abs() > 0)).to_numpy()
    for k in range(len(ok)):
        if ok[k:].all():
            return k
    return None


@dataclass(frozen=True)
class HeldReport:
    """Everything TR6 measures, over the window."""

    start: date | None
    end: date | None
    value: pd.Series               # V_t over the window
    flow: pd.Series                # F_t over the window
    carried: pd.Series
    returns: Returns | None
    market: MarketBook | None
    carried_names: tuple[str, ...]
    deposits: float
    withdrawals: float
    reason: str                    # why there's no window, else ""


def held(transactions: Sequence[Transaction], cash_funds: set[str],
         sessions: pd.DatetimeIndex, closes: Mapping[str, pd.Series],
         market: tuple[str, pd.Series] | None = None) -> HeldReport:
    """The whole of TR6 (module docstring) on ``sessions``."""
    b = book(transactions, cash_funds, sessions)
    valued = value(b, closes)
    flow = flows(transactions, b, valued.price)
    k = window_start(valued)
    if k is None or k >= len(sessions) - 1:
        return HeldReport(None, None, valued.value.iloc[:0], flow.iloc[:0], valued.carried,
                          None, None, valued.carried_names, 0.0, 0.0,
                          "no stretch of sessions is 90% priced by closes")
    v, f = valued.value.iloc[k:], flow.iloc[k:]
    r = returns(v, f)
    later = f.iloc[1:]
    bench = market_book(market[0], v, f, market[1]) if market is not None else None
    return HeldReport(v.index[0].date(), v.index[-1].date(), v, f, valued.carried.iloc[k:], r,
                      bench, valued.carried_names, float(later[later > 0].sum()),
                      float(later[later < 0].sum()), "")
