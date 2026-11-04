"""External time series as fetched: factor returns, rates, indices (D60).

The data batch (D59) brings in free series from outside the broker: Ken
French's factors first, then FRED, VIX and Shiller. Each one is a dated
column of numbers with the same provenance, so one record carries them all
from a provider (``providers/``) to the store (``storage/series_store.py``)
and on to the models.

* ``SeriesMeta`` says what a series is: its source and id, a title, the unit
  of its values, its frequency, the citation its terms ask for, and a note
  on the file's vintage ("CRSP 202609").
* ``FetchedSeries`` is one series as a provider parsed it: ISO dates
  (``YYYY-MM-DD``), strictly increasing, and finite values. Missing values
  are left out, never filled.
* ``StoredSeries`` is one series as the store serves it, a pandas Series on a
  DatetimeIndex, with when it was fetched and the SHA-256 of the file it
  came from.

Units name what a value is:
* ``fraction`` for a return or a rate per period (0.01 is 1%)
* ``percent`` where a source's convention is kept (a yield of 4.25)
* ``index`` for a level (the VIX's 18.4, the S&P's 7722.72)
* ``ratio`` for a dimensionless multiple (a CAPE of 40.7)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

FREQUENCIES = ("daily", "weekly", "monthly", "quarterly", "annual")
UNITS = ("fraction", "percent", "index", "ratio")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_ID = re.compile(r"[a-z0-9][a-z0-9_.\-]*")


@dataclass(frozen=True)
class SeriesMeta:
    source: str        # "french"
    series: str        # "ff3.mkt_rf"
    title: str
    unit: str
    frequency: str
    citation: str      # what the source's terms ask to be shown
    note: str = ""     # the file's vintage, or anything else worth keeping

    def __post_init__(self) -> None:
        for name in ("source", "series"):
            if not _ID.fullmatch(getattr(self, name)):
                raise ValueError(f"{name} {getattr(self, name)!r}: use lower-case letters, "
                                 "digits, '.', '_' or '-'")
        if self.unit not in UNITS:
            raise ValueError(f"unknown unit {self.unit!r}; one of {UNITS}")
        if self.frequency not in FREQUENCIES:
            raise ValueError(f"unknown frequency {self.frequency!r}; one of {FREQUENCIES}")


@dataclass(frozen=True)
class FetchedSeries:
    meta: SeriesMeta
    dates: tuple[str, ...]
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        where = f"{self.meta.source}/{self.meta.series}"
        if not self.dates:
            raise ValueError(f"{where}: no values")
        if len(self.dates) != len(self.values):
            raise ValueError(f"{where}: {len(self.dates)} dates but {len(self.values)} values")
        for day in self.dates:
            if not _ISO.fullmatch(day):
                raise ValueError(f"{where}: {day!r} is not an ISO date")
        if any(b <= a for a, b in zip(self.dates, self.dates[1:])):
            raise ValueError(f"{where}: dates must be strictly increasing")
        if not all(math.isfinite(v) for v in self.values):
            raise ValueError(f"{where}: values must be finite (leave missing ones out)")


@dataclass(frozen=True)
class StoredSeries:
    meta: SeriesMeta
    fetched_at: datetime
    sha256: str              # of the file the values were parsed from
    values: pd.Series        # float, on a DatetimeIndex, ascending

    @property
    def first(self) -> pd.Timestamp:
        return self.values.index[0]

    @property
    def last(self) -> pd.Timestamp:
        return self.values.index[-1]
