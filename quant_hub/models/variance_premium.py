r"""The variance risk premium: implied variance against the variance delivered.

VARIANCE I2 (VARIANCE_PLAN.md sections 7 and 8). The derivation below was
in the plan for review before the code. The implied side is I1's
``models/implied_variance.py``; this module is the realized side, GARCH's
expectation, and the three premiums.

1. Windows, in trading days (decision 6)
----------------------------------------
A snapshot taken on day :math:`d_0` prices variance over the next :math:`T`
calendar days, with :math:`T` in years as days/365, as the chain's ``dte``
is. Its **window** is the NYSE trading days :math:`d` with
:math:`d_0 < d \le d_0 + T` (``data/trading_calendar.py``). Their returns
run from :math:`d_0`'s close to the last one. The snapshot is taken during
the day, so the window drops the rest of :math:`d_0` and takes all of its
last day: the same length, shifted. A window over a holiday has one return
fewer, and the count is exact rather than :math:`T \times 252/365`, which
can be a day off in a month: about 5% of the variance.

The **trailing window** mirrors it. It covers the trading days in the
:math:`T` calendar days ending at the last close before :math:`d_0`. So at
the snapshot it is all past, and nothing in it looks ahead.

2. Realized total variance
--------------------------
.. math::  RV = \sum_{d \in W} r_d^2, \qquad r_d = \ln(C_d / C_{d^-})

where :math:`d^-` is the trading day before :math:`d`. These are adjusted
closes (D38), close to close, with zero mean, as variance swaps settle. The
returns in the window are counted, not estimated: every trading day in it,
and the one before, needs a bar, or the window is left out and named. Bars
on days that aren't trading days are ignored.

3. GARCH's expectation over the window
--------------------------------------
For GARCH(1,1) (``models/garch.py``), with persistence :math:`p =
\alpha + \beta`, long-run variance :math:`\bar\sigma^2 = \omega/(1-p)` and
:math:`h_{t+1}` the next day's variance after the last bar,

.. math::  E_t[h_{t+j}] = \bar\sigma^2 + p^{\,j-1}(h_{t+1} - \bar\sigma^2).

The window starts after :math:`s` trading days that aren't in it: those
after the last bar, through :math:`d_0`. The model's return is
:math:`r = \mu + \varepsilon` with :math:`h` the variance of
:math:`\varepsilon`, and RV is zero-mean, so :math:`E[r^2] = \mu^2 + h`.
Summing :math:`j = s+1 \dots s+n`,

.. math::  E_t[RV] = n(\mu^2 + \bar\sigma^2)
           + (h_{t+1} - \bar\sigma^2)\,p^{\,s}\,\frac{1 - p^{\,n}}{1 - p}.

At :math:`s = 0` and :math:`\mu = 0` this is :math:`n` times
``average_variance_forecast(n)``. The :math:`\mu^2` term is tiny (a daily
mean of 0.04% is about 0.2% of a day's variance at 16% vol), but exact.

4. The premium, three ways
--------------------------
Each compares totals over the same window, then annualizes both by the
same :math:`1/T`: never 252 against 365.

=====================================  ==========================================  ==============
Premium                                Definition, in annualized variance          Known at
=====================================  ==========================================  ==============
Against GARCH                          :math:`(K_{var}T - E_t^{G}[RV])/T`          :math:`t`
Against trailing realized              :math:`(K_{var}T - RV_{trailing})/T`        :math:`t`, no model
Ex post                                :math:`(K_{var}T - RV(t, t+T))/T`           :math:`t + T`
=====================================  ==========================================  ==============

The trailing one is Bollerslev, Tauchen & Zhou's (2009) measure. The ex
post one is what a short variance position earned (Carr & Wu 2009). Each is
also shown in vol points, :math:`\sqrt{K_{var}} - \sqrt{RV/T}`, for
reading. Averages are taken in variance, which adds.

5. The ex-post series, and no look-ahead
----------------------------------------
A snapshot's ex-post premium exists once the bar for its window's last day
does. Until then it is *pending*, and it never enters an average. A value
depends only on the closes in its own window, so appending bars never
changes a past value.

**D57.** A window that holds a split-sized gap (``data/splits.py``) is left
out of every average and named, never repaired. So is a window missing a
bar.

6. The mean premium, and its error
----------------------------------
Daily snapshots of 30-day windows overlap: each shares returns with about
20 later ones. So the premiums are serially correlated, and the mean's
error needs Newey–West (``models/regression.ols`` on a constant, Bartlett
weights).

**The lag.** Let the *overlap* :math:`h` be the most later windows that
share a return with any one window. Overlapping sums have
autocorrelations falling linearly to zero at lag :math:`h`, and Bartlett
weights at lag :math:`h` would underweight them. So :math:`L = 2h`.
Simulated on Gaussian returns with daily 30-day windows, the ±1.96 SE
interval covered the true mean:

==============  ===========  ===========
History         :math:`L=h`  :math:`L=2h`
==============  ===========  ===========
1 year          85%          85%
2 years         86%          89%
5 years         89%          93%
==============  ===========  ===========

**The interval is too narrow on a short history.** Each 21-day realized
variance has a relative error of about :math:`\sqrt{2/21} \approx 31\%`,
even with Gaussian returns. A year holds only about a dozen independent
months, and fat tails make it worse. So the result also says how many
non-overlapping windows the history holds (``independent``). One month
says almost nothing; the mean over many does.

7. Today's percentile
---------------------
Today's premium against trailing realized is placed among the same
premium on every earlier snapshot: the share below it, ties counted half.
Both sides are known at their own :math:`t`, so the ranking looks ahead
nowhere. It needs ``MIN_PERCENTILE_HISTORY`` earlier points.

8. What it claims, and doesn't
------------------------------
For the S&P 500 the premium is large and reliably positive in variance
(implied above realized; Carr & Wu 2009). For single stocks it is smaller
and mixed. Driessen, Maenhout & Vilkov (2009) trace the index premium
largely to correlation risk. So a single name's number is reported and
nothing more is claimed for it. The implied side slightly understates a
premium when the price can jump (I1, section 5).

Validation plan (tests)
-----------------------
1. **GBM world**, the chain priced at the paths' own variance: the mean
   premium is zero within three of its errors.
2. **A planted premium** (priced at 1.15σ) is recovered within three
   errors.
3. **Coverage** across seeds is near the table above, so the error isn't
   broken.
4. **No look-ahead:** a value appears only once its window's last bar
   does, and appending bars never changes one.
5. **Trading days:** windows over holidays and weekends count right; the
   GARCH sum matches its closed form, with and without skipped days.
6. **D57:** a window with a split-sized gap is left out and named, and so
   is one missing a bar.
7. **The percentile** on known values; inputs validated.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from quant_hub.data.trading_calendar import is_trading_day, trading_days
from quant_hub.models.garch import GARCH11Parameters
from quant_hub.models.regression import NEWEY_WEST, ols

DAYS_PER_YEAR = 365.0
"""Calendar days per year, as the chain's ``dte`` counts them."""

LAG_PER_OVERLAP = 2
"""Newey–West lags per window of overlap (section 6)."""

MIN_PERCENTILE_HISTORY = 20
"""Fewest earlier points for today's percentile."""

MIN_INDEPENDENT_WINDOWS = 2
"""Fewest non-overlapping windows for a mean with an error."""


# ------------------------------------------------------------------ windows

def previous_trading_day(day: date) -> date:
    """The last trading day before ``day``."""
    day -= timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def window_after(day0: date, calendar_days: float) -> tuple[date, ...]:
    """The trading days :math:`d_0 < d \\le d_0 + T` (section 1)."""
    _check_days(calendar_days)
    return tuple(trading_days(day0 + timedelta(days=1),
                              day0 + timedelta(days=math.floor(calendar_days))))


def window_before(day0: date, calendar_days: float) -> tuple[date, ...]:
    """The trading days in the :math:`T` calendar days ending at the last close
    before ``day0`` (section 1)."""
    _check_days(calendar_days)
    last = previous_trading_day(day0)
    return tuple(trading_days(last - timedelta(days=math.floor(calendar_days) - 1), last))


def _check_days(calendar_days: float) -> None:
    if not (math.isfinite(calendar_days) and calendar_days >= 1):
        raise ValueError("a window needs at least one calendar day")


class MissingBar(LookupError):
    """A trading day in a window, or the one before it, has no bar."""

    def __init__(self, day: date) -> None:
        super().__init__(f"no bar on {day:%Y-%m-%d}")
        self.day = day


def closes_by_day(closes: pd.Series) -> dict[date, float]:
    """A close per calendar day from a daily series with a DatetimeIndex."""
    if not isinstance(closes.index, pd.DatetimeIndex):
        raise ValueError("closes need a DatetimeIndex")
    values = closes.to_numpy(dtype=float)
    if not (np.isfinite(values).all() and (values > 0).all()):
        raise ValueError("closes must be positive and finite")
    return {ts.date(): float(v) for ts, v in zip(closes.index, values)}


def realized_total(closes: Mapping[date, float], days: Sequence[date]) -> float:
    """:math:`\\sum r_d^2` over the window's trading days (section 2). Raises
    ``MissingBar`` when a day, or the one before the first, has no close."""
    if not days:
        raise ValueError("an empty window has no realized variance")
    total, before = 0.0, previous_trading_day(days[0])
    if before not in closes:
        raise MissingBar(before)
    for day in days:
        if day not in closes:
            raise MissingBar(day)
        total += math.log(closes[day] / closes[before]) ** 2
        before = day
    return total


# --------------------------------------------------------------------- GARCH

def garch_expected_total(parameters: GARCH11Parameters, next_variance: float, n: int,
                         skip: int = 0) -> float:
    """:math:`E_t[RV]` over ``n`` trading days after ``skip`` that aren't in
    the window (section 3)."""
    if n < 1 or skip < 0:
        raise ValueError("need n >= 1 trading days and skip >= 0")
    if not (math.isfinite(next_variance) and next_variance > 0):
        raise ValueError("next variance must be positive")
    p, long_run = parameters.persistence, parameters.unconditional_variance
    return (n * (parameters.mean**2 + long_run)
            + (next_variance - long_run) * p**skip * (1 - p**n) / (1 - p))


# ------------------------------------------------------------------ premiums

@dataclass(frozen=True)
class Premium:
    """Implied total variance against another total over the same window."""

    implied: float
    against: float
    years: float

    @property
    def variance(self) -> float:
        """The premium in annualized variance (section 4)."""
        return (self.implied - self.against) / self.years

    @property
    def implied_vol(self) -> float:
        return math.sqrt(max(self.implied, 0.0) / self.years)

    @property
    def against_vol(self) -> float:
        return math.sqrt(max(self.against, 0.0) / self.years)

    @property
    def vol_points(self) -> float:
        """:math:`\\sqrt{K_{var}} - \\sqrt{RV/T}`, for reading."""
        return self.implied_vol - self.against_vol


@dataclass(frozen=True)
class ImpliedPoint:
    """One snapshot's implied total variance :math:`K_{var}T` at a tenor."""

    as_of: datetime
    days: float
    total: float

    def __post_init__(self) -> None:
        _check_days(self.days)
        if not (math.isfinite(self.total) and self.total > 0):
            raise ValueError("implied total variance must be positive")

    @property
    def years(self) -> float:
        return self.days / DAYS_PER_YEAR


def against_garch(point: ImpliedPoint, parameters: GARCH11Parameters, next_variance: float,
                  last_bar: date) -> Premium:
    """The premium against GARCH's expectation (sections 3 and 4). ``last_bar``
    is the day of the last close GARCH saw, on or before the snapshot's day."""
    day0 = point.as_of.date()
    if last_bar > day0:
        raise ValueError("GARCH saw bars after the snapshot: that would look ahead")
    days = window_after(day0, point.days)
    if not days:
        raise ValueError("the window holds no trading days")
    skip = len(trading_days(last_bar + timedelta(days=1), day0))
    expected = garch_expected_total(parameters, next_variance, len(days), skip)
    return Premium(point.total, expected, point.years)


@dataclass(frozen=True)
class PremiumPoint:
    as_of: datetime
    window: tuple[date, ...]
    premium: Premium


@dataclass(frozen=True)
class Excluded:
    """A snapshot left out of the series, and why (section 5)."""

    as_of: datetime
    reason: str


@dataclass(frozen=True)
class PremiumMean:
    """The mean premium over a series, with its Newey–West error (section 6)."""

    mean: float
    error: float
    lags: int
    windows: int
    independent: int
    mean_implied: float
    mean_against: float

    @property
    def vol_points(self) -> float:
        """The mean's root of implied minus the root of realized, for reading."""
        return math.sqrt(max(self.mean_implied, 0.0)) - math.sqrt(max(self.mean_against, 0.0))


@dataclass(frozen=True)
class PremiumSeries:
    """Premiums over the snapshots, oldest first, with what was left out.

    ``pending`` counts ex-post windows whose last bar doesn't exist yet."""

    points: tuple[PremiumPoint, ...]
    excluded: tuple[Excluded, ...]
    pending: int = 0

    @property
    def values(self) -> np.ndarray:
        return np.array([p.premium.variance for p in self.points])

    def summary(self) -> PremiumMean | None:
        return mean_premium(self.points)


def ex_post_series(implied: Iterable[ImpliedPoint], closes: pd.Series,
                   gap_days: Iterable[date] = ()) -> PremiumSeries:
    """Each snapshot's implied variance against the variance that followed
    (section 5). ``closes`` are daily adjusted closes of completed sessions;
    ``gap_days`` the sessions of split-sized gaps."""
    return _series(implied, closes, gap_days, window_after, ahead=True)


def trailing_series(implied: Iterable[ImpliedPoint], closes: pd.Series,
                    gap_days: Iterable[date] = ()) -> PremiumSeries:
    """Each snapshot's implied variance against the trailing realized variance
    (section 1): known at the snapshot, with no model."""
    return _series(implied, closes, gap_days, window_before, ahead=False)


def _series(implied, closes, gap_days, window, ahead: bool) -> PremiumSeries:
    by_day = closes_by_day(closes)
    last = max(by_day) if by_day else None
    gaps = set(gap_days)
    points: list[PremiumPoint] = []
    excluded: list[Excluded] = []
    pending = 0
    for point in sorted(implied, key=lambda p: p.as_of):
        days = window(point.as_of.date(), point.days)
        if not days:
            excluded.append(Excluded(point.as_of, "no trading days in the window"))
            continue
        if ahead and (last is None or days[-1] > last):
            pending += 1                                    # not known yet
            continue
        gap = next((d for d in days if d in gaps), None)
        if gap is not None:
            excluded.append(Excluded(point.as_of, f"a split-sized gap on {gap:%Y-%m-%d} (D57)"))
            continue
        try:
            realized = realized_total(by_day, days)
        except MissingBar as exc:
            excluded.append(Excluded(point.as_of, str(exc)))
            continue
        points.append(PremiumPoint(point.as_of, days, Premium(point.total, realized,
                                                              point.years)))
    return PremiumSeries(tuple(points), tuple(excluded), pending)


def overlap(windows: Sequence[Sequence[date]]) -> int:
    """The most later windows sharing a return with any one (section 6)."""
    ordered = sorted(windows, key=lambda w: w[0])
    most = 0
    for i, w in enumerate(ordered):
        j = i + 1
        while j < len(ordered) and ordered[j][0] <= w[-1]:
            j += 1
        most = max(most, j - i - 1)
    return most


def independent_windows(windows: Sequence[Sequence[date]]) -> int:
    """Non-overlapping windows, taken greedily oldest first."""
    count, end = 0, None
    for w in sorted(windows, key=lambda w: w[0]):
        if end is None or w[0] > end:
            count, end = count + 1, w[-1]
    return count


def mean_premium(points: Sequence[PremiumPoint]) -> PremiumMean | None:
    """The mean premium with its Newey–West error (section 6), or ``None``
    with fewer than ``MIN_INDEPENDENT_WINDOWS`` non-overlapping windows."""
    windows = [p.window for p in points]
    independent = independent_windows(windows)
    if independent < MIN_INDEPENDENT_WINDOWS:
        return None
    values = np.array([p.premium.variance for p in points])
    lags = min(LAG_PER_OVERLAP * overlap(windows), len(values) - 1)
    fit = ols(values, np.ones((len(values), 1)), ("mean",), lags=lags)
    return PremiumMean(
        mean=fit.value("mean"), error=fit.error("mean", NEWEY_WEST), lags=lags,
        windows=len(values), independent=independent,
        mean_implied=float(np.mean([p.premium.implied / p.premium.years for p in points])),
        mean_against=float(np.mean([p.premium.against / p.premium.years for p in points])))


def percentile(value: float, history: Sequence[float]) -> float | None:
    """The share of ``history`` below ``value``, ties counted half (section 7);
    ``None`` with fewer than ``MIN_PERCENTILE_HISTORY`` points."""
    if not math.isfinite(value):
        raise ValueError("the value must be finite")
    if len(history) < MIN_PERCENTILE_HISTORY:
        return None
    past = np.asarray(history, dtype=float)
    return float((np.sum(past < value) + 0.5 * np.sum(past == value)) / len(past))
