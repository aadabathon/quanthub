r"""Crash stress: the book's β replayed through a century of crashes (ALLOCATION A2).

The drawdown check reads the worst fall of today's mix over the lookback,
which can be two calm years. ALLOCATION_PLAN found a book that passed its
tolerance comfortably and lost about half in a constant-β replay of
2007–09. This replays the book's β through the longest stored market
history instead.

Mathematical description
------------------------
On day :math:`s` let :math:`R_s` be the market's return and :math:`r_s` the
T-bill's, and let :math:`x_s = R_s - r_s` be the market's excess return,
which is what Ken French publishes (``Mkt-RF``, beside ``RF``). A book with
β :math:`b` to the market holds :math:`b` in the market and :math:`1 - b` in
bills, borrowing at the bill rate when :math:`b > 1`. Rebalanced daily, it
grows

.. math::  V_t = \prod_{s \le t} \bigl(1 + b R_s + (1 - b) r_s\bigr)
               = \prod_{s \le t} \bigl(1 + r_s + b\,x_s\bigr), \qquad V_0 = 1

The measure is :math:`V`'s largest peak-to-trough fall,

.. math::  D(b) = \max_t \Bigl(1 - \frac{V_t}{\max_{u \le t} V_u}\Bigr)

reported with its peak, its trough and the day :math:`V` regained the peak,
if it has. The five worst falls are reported too, each a separate spell
under water: one ends on the day :math:`V` makes a new high.

If a day's factor :math:`1 + r_s + b\,x_s` is zero or less, the book is
wiped out (with :math:`b \ge 6`, 19 October 1987 does it). :math:`V` stays
at zero from then, and the fall is 100%.

**D grows with |b|.** In logs, a spell's change is
:math:`g_W(b) = \sum_{s \in W} \log(1 + r_s + b\,x_s)`, which is concave in
:math:`b`. The worst fall is :math:`D(b) = 1 - e^{h(b)}` with
:math:`h(b) = \min_W g_W(b)`, the minimum over spells including the empty
one. As a minimum of concave functions, :math:`h` is concave. With
:math:`r_s \ge 0`, :math:`h(0) = 0` and :math:`h \le 0`. For
:math:`0 \le b_1 < b_2`, concavity gives
:math:`h(b_1) \ge (1 - b_1/b_2)\,h(0) + (b_1/b_2)\,h(b_2) \ge h(b_2)`. So
:math:`h` never rises and :math:`D` never falls as :math:`b` grows from
zero, and the same holds for negative :math:`b` towards :math:`-\infty`.
An error band :math:`[\hat b - z\,\mathrm{SE}, \hat b + z\,\mathrm{SE}]` on
one side of zero therefore maps to the falls at its two ends.

Assumptions and limitations
---------------------------
* **A floor, not an estimate.** The replay holds β fixed and carries no
  stock-specific risk. Correlations rise in bear markets (Longin and
  Solnik, 2001), so a stock book's β tends to rise in a crash, and a book
  of single stocks loses more than its β replay.
* **The market is CRSP's value-weighted index** (French's), not the
  benchmark itself. β is measured against the benchmark (SPY by default),
  which tracks that index closely. The replay treats them as one.
* **Rebalanced daily, without costs.** A book left to drift would hold
  less market after a fall.
* **Measured, never graded** (ALLOCATION decision 4, D59). It sits beside
  the drawdown tolerance, as the long-run view of what that number can't
  see. In Roy's (1952) terms, it's the disaster a safety-first investor
  would size against.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

Z95 = 1.959963984540054
"""The two-sided 95% normal quantile, for the β error band."""


@dataclass(frozen=True)
class Fall:
    """One spell under water: from its peak to the day it's regained."""

    depth: float                 # 1 − trough / peak, a fraction
    peak: date                   # the last day at the peak (the first day, from the start)
    trough: date
    recovered: date | None       # the first day back at the peak; None if not yet


@dataclass(frozen=True)
class CrashStress:
    """The replay at the book's β, its worst falls, and the β band's."""

    beta: float
    error: float | None
    path: pd.Series              # V_t
    drawdown: pd.Series          # V_t / running peak − 1, ≤ 0
    falls: tuple[Fall, ...]      # the worst first, separate spells
    band: tuple[tuple[float, float], tuple[float, float]] | None   # ((β, D) at −z SE, at +z SE)

    @property
    def worst(self) -> Fall:
        return self.falls[0]

    @property
    def first(self) -> date:
        return self.path.index[0].date()

    @property
    def last(self) -> date:
        return self.path.index[-1].date()


def replay(excess: pd.Series, rf: pd.Series, beta: float) -> pd.Series:
    """V_t (module docstring) on the dates both series share, ascending.
    Raises ``ValueError`` for no shared dates, unsorted or non-finite
    values, or a non-finite β."""
    if not math.isfinite(beta):
        raise ValueError("β must be finite")
    frame = pd.concat({"x": excess, "r": rf}, axis=1, join="inner")
    if frame.empty:
        raise ValueError("the market and bill series share no dates")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("the dates must be unique and ascending")
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("the returns must be finite")
    factors = 1.0 + values[:, 1] + beta * values[:, 0]
    ruin = np.flatnonzero(factors <= 0.0)
    if ruin.size:
        factors[ruin[0]:] = 0.0
    return pd.Series(np.cumprod(factors), index=frame.index, name="V")


def falls(path: pd.Series, top: int = 5) -> tuple[pd.Series, tuple[Fall, ...]]:
    """The drawdown series and the ``top`` worst spells under water, the
    worst first. ``path`` starts from V₀ = 1, so a fall from the first day
    counts."""
    values = path.to_numpy(dtype=float)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], values)))[1:]
    drawdown = values / peaks - 1.0
    under = drawdown < 0.0
    dates = path.index
    spells: list[Fall] = []
    i, n = 0, len(values)
    while i < n:
        if not under[i]:
            i += 1
            continue
        start = i
        while i < n and under[i]:
            i += 1
        trough = start + int(np.argmin(drawdown[start:i]))
        spells.append(Fall(float(-drawdown[trough]),
                           dates[start - 1 if start > 0 else 0].date(),
                           dates[trough].date(), dates[i].date() if i < n else None))
    spells.sort(key=lambda f: f.depth, reverse=True)
    return pd.Series(drawdown, index=dates, name="drawdown"), tuple(spells[:top])


def worst_fall(excess: pd.Series, rf: pd.Series, beta: float) -> float:
    """D(b), the worst fall alone."""
    path = replay(excess, rf, beta).to_numpy()
    peaks = np.maximum.accumulate(np.concatenate(([1.0], path)))[1:]
    return float(-(path / peaks - 1.0).min()) if len(path) else 0.0


def crash_stress(excess: pd.Series, rf: pd.Series, beta: float, error: float | None = None,
                 z: float = Z95, top: int = 5) -> CrashStress:
    """The replay at ``beta``, its ``top`` worst falls, and, with ``error``
    (β's standard error), the worst falls at β ∓ z·SE."""
    if error is not None and not (math.isfinite(error) and error >= 0):
        raise ValueError("a standard error must be finite and not negative")
    path = replay(excess, rf, beta)
    drawdown, worst = falls(path, top)
    if not worst:                       # never under water (β = 0, bills only)
        day = path.index[0].date()
        worst = (Fall(0.0, day, day, day),)
    band = None
    if error is not None:
        low, high = beta - z * error, beta + z * error
        band = ((low, worst_fall(excess, rf, low)), (high, worst_fall(excess, rf, high)))
    return CrashStress(beta, error, path, drawdown, worst, band)
