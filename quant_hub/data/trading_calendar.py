r"""NYSE trading days, by rule (VARIANCE decision 6, D63).

A trading day is a weekday that isn't an NYSE holiday. The holidays follow
fixed rules, applied from 1998, the first year Martin Luther King Jr. Day
closed the exchange. Earlier dates raise, since older rules (election
days, Lincoln's Birthday) aren't modeled.

**The rules:**

=======================  =============================================
New Year's Day           Jan 1; on a Sunday, Monday Jan 2. On a
                         Saturday it isn't observed: the Friday before
                         ends the year, and stays open.
Martin Luther King Day   third Monday of January
Washington's Birthday    third Monday of February
Good Friday              two days before Easter Sunday
Memorial Day             last Monday of May
Juneteenth               June 19, observed, from 2022
Independence Day         July 4, observed
Labor Day                first Monday of September
Thanksgiving             fourth Thursday of November
Christmas                December 25, observed
=======================  =============================================

*Observed:* a holiday on a Saturday closes the Friday before, and one on a
Sunday the Monday after, except New Year's Day on a Saturday (above).

*Easter* is the anonymous Gregorian computation. With :math:`Y` the year:

.. math::

   a = Y \bmod 19,\; b = \lfloor Y/100 \rfloor,\; c = Y \bmod 100,\;
   d = \lfloor b/4 \rfloor,\; e = b \bmod 4,\; f = \lfloor (b+8)/25 \rfloor,\;
   g = \lfloor (b-f+1)/3 \rfloor,\; h = (19a+b-d-g+15) \bmod 30,

   i = \lfloor c/4 \rfloor,\; k = c \bmod 4,\; l = (32+2e+2i-h-k) \bmod 7,\;
   m = \lfloor (a+11h+22l)/451 \rfloor,

and Easter is month :math:`\lfloor (h+l-7m+114)/31 \rfloor`, day
:math:`((h+l-7m+114) \bmod 31) + 1`.

**Unscheduled closures** (``SPECIAL_CLOSURES``): September 11–14, 2001; the
national days of mourning for Presidents Reagan (2004-06-11), Ford
(2007-01-02), G. H. W. Bush (2018-12-05) and Carter (2025-01-09); and
Hurricane Sandy (2012-10-29, 30). A future one must be added here when it
happens.

**Not modeled:** early closes (13:00 New York on some days around July 4,
Thanksgiving and Christmas). A day that closes early is still a trading
day.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

FIRST_YEAR = 1998
SPECIAL_CLOSURES = frozenset({
    date(2001, 9, 11), date(2001, 9, 12), date(2001, 9, 13), date(2001, 9, 14),
    date(2004, 6, 11), date(2007, 1, 2), date(2012, 10, 29), date(2012, 10, 30),
    date(2018, 12, 5), date(2025, 1, 9),
})


def easter(year: int) -> date:
    """Easter Sunday, Gregorian (module docstring)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    n = h + l - 7 * m + 114
    return date(year, n // 31, n % 31 + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th (1-based) given weekday (Monday = 0) of a month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


@lru_cache(maxsize=None)
def holidays(year: int) -> frozenset[date]:
    """NYSE full-day closures in ``year``: the rules, plus special closures."""
    if year < FIRST_YEAR:
        raise ValueError(f"NYSE holidays are modeled from {FIRST_YEAR}; {year} uses older rules")
    days = {
        _nth_weekday(year, 1, 0, 3),          # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),          # Washington's Birthday
        easter(year) - timedelta(days=2),     # Good Friday
        _last_weekday(year, 5, 0),            # Memorial Day
        _observed(date(year, 7, 4)),          # Independence Day
        _nth_weekday(year, 9, 0, 1),          # Labor Day
        _nth_weekday(year, 11, 3, 4),         # Thanksgiving
        _observed(date(year, 12, 25)),        # Christmas
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:               # a Saturday New Year isn't observed
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days |= {d for d in SPECIAL_CLOSURES if d.year == year}
    return frozenset(d for d in days if d.year == year)


def is_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays(day.year)


def trading_days(start: date, end: date) -> list[date]:
    """Every trading day from ``start`` to ``end``, both included."""
    out, day = [], start
    while day <= end:
        if is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out
