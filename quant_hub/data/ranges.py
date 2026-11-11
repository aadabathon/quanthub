"""Closed date-interval arithmetic for coverage tracking.

Why this exists
---------------
"Which part of the requested period do we already have?" cannot be answered
by looking at stored bars: weekends, holidays, and legitimate no-data days
mean *absence of a bar proves nothing*. Coverage is therefore tracked as
explicit date intervals ("we successfully synced 2020-01-01..2025-06-30"),
and deciding what to fetch becomes pure interval arithmetic — which lives
here, in the data layer, with no I/O, so both the storage layer and the
sync service share one implementation and the logic is trivially testable.

Semantics
---------
A :class:`DateRange` is a **closed** interval of session dates: both
endpoints included, timestamps floored to midnight (daily bars are
timezone-naive session dates; see ``data/bars.py``). Day granularity makes
adjacency well-defined: ``[a, b]`` and ``[b+1day, c]`` merge into
``[a, c]`` because no session date lies between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

import numpy as np

ONE_DAY = timedelta(days=1)


def floor_day(moment: datetime) -> datetime:
    return datetime(moment.year, moment.month, moment.day)


@dataclass(frozen=True)
class DateRange:
    """Closed interval of session dates; endpoints floored to midnight."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start, end = floor_day(self.start), floor_day(self.end)
        if start > end:
            raise ValueError(f"DateRange start {start:%Y-%m-%d} is after end {end:%Y-%m-%d}")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def overlaps(self, other: "DateRange") -> bool:
        return self.start <= other.end and other.start <= self.end

    def contains(self, other: "DateRange") -> bool:
        return self.start <= other.start and other.end <= self.end

    def intersection(self, other: "DateRange") -> "DateRange | None":
        if not self.overlaps(other):
            return None
        return DateRange(max(self.start, other.start), min(self.end, other.end))

    def __str__(self) -> str:
        return f"{self.start:%Y-%m-%d}..{self.end:%Y-%m-%d}"


def merge_ranges(ranges: Iterable[DateRange]) -> list[DateRange]:
    """Collapse overlapping *or day-adjacent* ranges into maximal disjoint ones."""
    ordered = sorted(ranges, key=lambda r: r.start)
    merged: list[DateRange] = []
    for current in ordered:
        if merged and current.start <= merged[-1].end + ONE_DAY:
            if current.end > merged[-1].end:
                merged[-1] = DateRange(merged[-1].start, current.end)
        else:
            merged.append(current)
    return merged


def subtract_ranges(target: DateRange, covered: Iterable[DateRange]) -> list[DateRange]:
    """The parts of ``target`` not covered by any of ``covered``, in order.

    An empty result means ``target`` is fully covered.
    """
    relevant = merge_ranges(r for r in covered if r.overlaps(target))
    missing: list[DateRange] = []
    cursor = target.start
    for block in relevant:
        if block.start > cursor:
            missing.append(DateRange(cursor, block.start - ONE_DAY))
        cursor = block.end + ONE_DAY
        if cursor > target.end:
            return missing
    missing.append(DateRange(cursor, target.end))
    return missing


def weekdays_missed(last: datetime, now: datetime) -> int:
    """Weekday sessions completed since ``last`` that a series ending on
    ``last`` lacks: the weekdays strictly after it and before today (today's
    session is still provisional, as the cache treats it).

    Weekends never count, so Friday's bar read on Saturday has missed
    nothing. Exchange holidays aren't known here, so one counts as a missed
    session: the error is on the side of saying "stale".
    """
    first, today = floor_day(last) + ONE_DAY, floor_day(now)
    if first >= today:
        return 0
    return int(np.busday_count(first.date(), today.date()))
