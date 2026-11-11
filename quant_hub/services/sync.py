"""Synchronization planning: what part of a request must actually be fetched.

Pure decision logic over ``data/ranges.py`` interval arithmetic — no I/O, no
broker, no database — so every cache decision the application makes is a
deterministic, unit-testable function of (requested range, covered ranges).

The three outcomes, which the ingestion log always names explicitly:

* ``FULL_HIT``    — the store covers the whole request; zero network.
* ``PARTIAL_HIT`` — the store covers part; fetch only the missing ranges.
* ``MISS``        — nothing relevant is covered; fetch the whole request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from quant_hub.data.ranges import DateRange, subtract_ranges


class SyncStatus(Enum):
    FULL_HIT = "full-hit"
    PARTIAL_HIT = "partial-hit"
    MISS = "miss"


@dataclass(frozen=True)
class SyncPlan:
    """The decision for one history request."""

    status: SyncStatus
    requested: DateRange
    missing: tuple[DateRange, ...]  # ranges to fetch, chronological; () on FULL_HIT

    def describe(self) -> str:
        """Log-friendly one-liner, e.g. ``partial-hit (missing 2025-07-01..2026-01-01)``."""
        if not self.missing:
            return self.status.value
        gaps = ", ".join(str(r) for r in self.missing)
        return f"{self.status.value} (missing {gaps})"


def plan_sync(requested: DateRange, covered: list[DateRange]) -> SyncPlan:
    """Classify a request against existing coverage and list what to fetch."""
    missing = subtract_ranges(requested, covered)
    if not missing:
        status = SyncStatus.FULL_HIT
    elif missing == [requested]:
        status = SyncStatus.MISS
    else:
        status = SyncStatus.PARTIAL_HIT
    return SyncPlan(status=status, requested=requested, missing=tuple(missing))
