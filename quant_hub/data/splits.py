r"""Overnight gaps the size of a stock split: a guard for unadjusted history.

Why this exists
---------------
A k-for-1 split divides every price from its first session on by k. A
split-adjusted series divides the history before it by k as well, so the
series runs on without a step. Some of IBKR's older daily history doesn't.
On a copy of the store (2026-11-01), both its TRADES and its ADJUSTED_LAST
bars kept every 2:1 split of MSFT, CSCO, INTC, ORCL, NVDA and two other
large caps, from 1999 through May 2005, as a one-day fall of about 50%.
NVDA's splits of 2006 and 2007 were adjusted. Both bases carry the same step, so the cache's
split guard, which compares the two (``services/cached_source.py``), can't
see it. One fake −50% day wrecks everything built on the history:
volatility, drawdown, GARCH, regimes and backtests.

The signature
-------------
Let a split take effect at the open of session :math:`t`. In unadjusted
bars the observed open is the true open over :math:`k`, while the previous
close is unscaled, so the overnight log gap is

.. math::  x_t = \ln\frac{O_t}{C_{t-1}} = x_t^{*} - \ln k

where :math:`x_t^{*}` is the true overnight move. A forward split
(:math:`k > 1`) shows as a gap near :math:`-\ln k`, and a reverse split
(1-for-:math:`k`) as one near :math:`+\ln k`. Day :math:`t` is
*split-sized* when

.. math::  |x_t + \ln k| \le \tau,\ k \in \{3/2, 2, 3, 4\}, \qquad
           |x_t - \ln k| \le \tau,\ k \in \{2, 3, 4\}, \qquad
           \text{or}\ |x_t| \ge \ln 5 - \tau.

The last clause covers 5:1 and up (7:1, 10:1, 20:1, large reverse splits)
without listing every ratio: nothing ordinary moves a price fivefold
overnight. There is no reverse 3:2, since reverse splits are 1-for-k with a
whole k. The bands don't overlap: neighbouring ratios are at least
:math:`\ln(4/3) \approx 0.29` apart in log terms, more than :math:`2\tau`.

The open is used, not the close. A split is exact at the open, and the
day's own trading only adds noise: NVDA's 2:1 of 2000-06-27 opened at
×0.496 of the previous close and closed at ×0.43.

The open has to agree with the close, though. A bad open print looks like
a huge gap: XLK's bar of 2004-11-16 opened at $0.255 and closed at $10.51.
So a day counts only when its own move, :math:`|\ln(C_t/O_t)|`, is at most
``DAY_MOVE_LIMIT`` = 0.25. Real splits stay well inside that (the widest
found was −0.14, that NVDA day), and ordinary days pass it 99.99% of the
time. A day that fails it is skipped, not tested on its close: open and
close that far apart mean a bad print or a news day, and a news day's
close-to-close move would only add false alarms.

A day without a usable open (a close-only file, the synthetic source) is
tested on its close, so the test sees that day's move on top of the gap.

Tolerance
---------
τ = 0.07 in log terms. It has to hold the true overnight move, which is
usually 1–2% but can be larger when a split coincides with news. The 16
unadjusted 2:1 splits found had :math:`|x_t + \ln 2| \le 0.064`. The widest
was NVDA's of 2001-09-17, the market's reopening after 9/11.

False alarms, and why this labels instead of repairing
------------------------------------------------------
A genuine overnight move inside a band can't be told from a split by
open, high, low, close and volume alone. A split multiplies volume by k,
but news lifts volume too. The bands are falls of about 28–38% (3:2),
47–53% (2:1), 64–69% (3:1) and 73–77% (4:1), the matching rises for
reverse splits, and any move of five times or more.

On the same store copy, they were checked against 41 symbols and 162,666
overnight gaps. They caught all 16 unadjusted 2:1 splits and ASML's 3:1 of
2000-04-17. They also caught two days that aren't known splits:
- NVDA's −31% of 2004-08-06, a genuine fall (NVDA didn't split that year)
- TSM's −30% of 2001-06-26, perhaps one of its large stock dividends

Testing news days on their close instead would have added six more,
mostly a small biotech's doublings and collapses.

A live check on 2026-11-02 added AAPL. Its 2:1 splits of 2000-06-21 and
2005-02-28 are unadjusted and were found, while its splits of 2014 and 2020
are adjusted. So was its genuine fall of 2000-09-29 on a profit warning,
which opened at ×0.527: the textbook false alarm.

So the guard only names the day. Pages show it, and nothing is rescaled:
a wrong repair would erase a real crash, the worst failure a risk tool can
have. Real adjustment needs a source with a split calendar (D57).

Units
-----
``SplitGap.gap`` is the price factor :math:`e^{x_t}`: 0.5 for a 2:1 split.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

FORWARD_RATIOS = (1.5, 2.0, 3.0, 4.0)
"""k-for-1 splits matched one by one; 3:2 is 1.5."""

REVERSE_RATIOS = (2.0, 3.0, 4.0)
"""1-for-k reverse splits matched one by one."""

EXTREME_RATIO = 5.0
"""Any overnight move of this factor or more, either way, is split-sized."""

GAP_TOLERANCE = 0.07
"""τ: the true overnight move a split's gap may carry (module docstring)."""

DAY_MOVE_LIMIT = 0.25
"""Largest |ln(close/open)| at which a day's open is trusted (module docstring)."""


@dataclass(frozen=True)
class SplitGap:
    """One split-sized overnight gap: the session it opened, the price
    factor across it, and the split it's the size of ("2:1", "1:4")."""

    day: datetime
    gap: float
    ratio: str


def split_gaps(frame: pd.DataFrame) -> tuple[SplitGap, ...]:
    """The split-sized gaps in a daily bar frame (module docstring), oldest
    first. Needs ``close``. Uses ``open`` where it's positive and finite,
    and skips a day whose close is more than ``DAY_MOVE_LIMIT`` from it."""
    if "close" not in frame.columns:
        raise ValueError("split_gaps needs a 'close' column")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("split_gaps needs a DatetimeIndex")
    frame = frame.sort_index()
    close = frame["close"].to_numpy(dtype=float)
    if len(close) < 2:
        return ()
    after = close[1:]
    with np.errstate(divide="ignore", invalid="ignore"):
        x = np.log(after / close[:-1])
        if "open" in frame.columns:
            opens = frame["open"].to_numpy(dtype=float)[1:]
            usable = np.isfinite(opens) & (opens > 0)
            x = np.where(usable, np.log(opens / close[:-1]), x)
            x[usable & ~(np.abs(np.log(after / opens)) <= DAY_MOVE_LIMIT)] = np.nan
    nearest = math.log(min(FORWARD_RATIOS + REVERSE_RATIOS)) - GAP_TOLERANCE
    found = []
    for i in np.flatnonzero(np.isfinite(x) & (np.abs(x) >= nearest)):
        ratio = _ratio(float(x[i]))
        if ratio is not None:
            found.append(SplitGap(frame.index[i + 1].to_pydatetime(), float(math.exp(x[i])),
                                  ratio))
    return tuple(found)


def _ratio(x: float) -> str | None:
    """The split a log gap ``x`` is the size of, or None."""
    if abs(x) >= math.log(EXTREME_RATIO) - GAP_TOLERANCE:
        k = math.exp(abs(x))
        return f"{k:.0f}:1" if x < 0 else f"1:{k:.0f}"
    for k in FORWARD_RATIOS:
        if abs(x + math.log(k)) <= GAP_TOLERANCE:
            return "3:2" if k == 1.5 else f"{k:g}:1"
    for k in REVERSE_RATIOS:
        if abs(x - math.log(k)) <= GAP_TOLERANCE:
            return f"1:{k:g}"
    return None
