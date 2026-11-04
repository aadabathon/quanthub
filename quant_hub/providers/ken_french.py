r"""Kenneth R. French's Data Library: the daily factor files, stdlib only (D60).

The library publishes the Fama–French factors as zipped CSV files, rebuilt
monthly from the CRSP database. This module is the only code that talks to
it.

* ``FrenchClient`` fetches a dataset's zip and does nothing else: one
  request per file, a plain User-Agent, a timeout, and typed errors.
* ``parse_dataset`` turns the zip's bytes into ``data.series.FetchedSeries``:
  pure, with no network.

**Terms** (checked 2026-11-03). The library states no license, only
"Copyright Eugene F. Fama and Kenneth R. French". The files are used locally
for personal research, never committed or redistributed, and cited wherever
a series is shown (``CITATION``).

**The datasets:**

========  ==============================================  ============
key       file                                            from
========  ==============================================  ============
``ff3``   Fama/French 3 factors, daily                    1926-07-01
``ff5``   Fama/French 5 factors (2x3), daily              1963-07-01
``mom``   Momentum factor, daily                          1926-11-03
========  ==============================================  ============

**The file format** (as of the 202609 vintage): a text preamble, then a
header row that starts with a comma (",Mkt-RF,SMB,HML,RF"), then one row per
trading day, "YYYYMMDD, value, ...", then a blank line and a copyright line.
The parser keys on that header row and on 8-digit date fields, never on line
numbers, so a longer preamble doesn't break it.

**Units.** The file's values are percent per day. They're stored as
fractions (÷ 100), so 0.45 becomes 0.0045. RF is the simple daily T-bill
return that compounds to the month's 1-month T-bill rate (the file says so).
The market's total return is Mkt-RF + RF; it isn't stored, because it's an
identity of two stored series.

**Missing values** are coded −99.99 or −999 (the library's note). They are
left out, never filled; the daily files have none today.
"""

from __future__ import annotations

import io
import re
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from quant_hub.data.series import FetchedSeries, SeriesMeta

BASE_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
SOURCE = "french"
CITATION = ("Kenneth R. French Data Library, Tuck School of Business at Dartmouth. "
            "Copyright Eugene F. Fama and Kenneth R. French.")
USER_AGENT = "QuantHub/1.0 (personal research)"
TIMEOUT_S = 60.0
MISSING = (-99.99, -999.0)


@dataclass(frozen=True)
class Dataset:
    key: str
    file: str
    title: str


DATASETS = {
    "ff3": Dataset("ff3", "F-F_Research_Data_Factors_daily_CSV.zip", "Fama/French 3 factors, daily"),
    "ff5": Dataset("ff5", "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
                   "Fama/French 5 factors (2x3), daily"),
    "mom": Dataset("mom", "F-F_Momentum_Factor_daily_CSV.zip", "Momentum factor, daily"),
}
_VINTAGE = re.compile(r"created by using the (\d{6}) CRSP database", re.IGNORECASE)


class FrenchError(Exception):
    """Fetching or reading a French file failed; the message says what to do."""


class FrenchClient:
    """Fetches the library's zip files (module docstring). ``opener`` is
    injectable for tests."""

    def __init__(self, timeout_s: float = TIMEOUT_S,
                 opener: Callable = urllib.request.urlopen) -> None:
        self._timeout = timeout_s
        self._opener = opener

    def fetch(self, dataset: Dataset) -> bytes:
        request = urllib.request.Request(BASE_URL + dataset.file,
                                         headers={"User-Agent": USER_AGENT})
        try:
            with self._opener(request, timeout=self._timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise FrenchError(f"Ken French's library returned HTTP {exc.code} for "
                              f"{dataset.file}; the file may have moved") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise FrenchError(f"couldn't reach Ken French's library: {reason}") from exc


def column_id(name: str) -> str:
    """'Mkt-RF' -> 'mkt_rf', 'Mom' -> 'mom': lower case, runs of anything else
    become one underscore."""
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def parse_dataset(dataset: Dataset, payload: bytes) -> list[FetchedSeries]:
    """The zip's one CSV as one ``FetchedSeries`` per column (module docstring).
    Raises ``FrenchError`` naming what's wrong with the file."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise FrenchError(f"{dataset.file} isn't a zip file") from exc
    members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
    if len(members) != 1:
        raise FrenchError(f"{dataset.file} should hold one CSV, found {len(members)}")
    lines = archive.read(members[0]).decode("latin-1").splitlines()

    vintage = next((m.group(1) for m in map(_VINTAGE.search, lines[:10]) if m), "")
    header = next((i for i, line in enumerate(lines) if line.strip().startswith(",")), None)
    if header is None:
        raise FrenchError(f"{dataset.file}: no header row (a line starting with a comma)")
    names = [c.strip() for c in lines[header].split(",")[1:]]
    if not names or any(not n for n in names):
        raise FrenchError(f"{dataset.file}: the header row names no columns")

    columns: list[tuple[list[str], list[float]]] = [([], []) for _ in names]
    for line in lines[header + 1:]:
        fields = [f.strip() for f in line.split(",")]
        if not (len(fields[0]) == 8 and fields[0].isdigit()):
            break                         # the daily block ends at its first non-date line
        if len(fields) - 1 != len(names):
            raise FrenchError(f"{dataset.file}: {fields[0]} has {len(fields) - 1} values, "
                              f"the header names {len(names)}")
        try:
            day = datetime.strptime(fields[0], "%Y%m%d").date().isoformat()
            values = [float(f) for f in fields[1:]]
        except ValueError as exc:
            raise FrenchError(f"{dataset.file}: can't read the row for {fields[0]}: {exc}") from exc
        for (dates, kept), value in zip(columns, values):
            if value not in MISSING:
                dates.append(day)
                kept.append(value / 100.0)
    if not any(dates for dates, _ in columns):
        raise FrenchError(f"{dataset.file}: no daily rows after the header")

    note = f"CRSP {vintage}" if vintage else ""
    try:
        return [FetchedSeries(SeriesMeta(SOURCE, f"{dataset.key}.{column_id(name)}",
                                         f"{dataset.title}: {name}", "fraction", "daily",
                                         CITATION, note),
                              tuple(dates), tuple(kept))
                for name, (dates, kept) in zip(names, columns) if dates]
    except ValueError as exc:             # out-of-order dates, from FetchedSeries
        raise FrenchError(f"{dataset.file}: {exc}") from exc
