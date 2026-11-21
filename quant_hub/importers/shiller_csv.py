r"""Robert Shiller's monthly market data, from a CSV you save (TARGET T1, D61).

Shiller publishes ``ie_data.xls``, the S&P Composite's monthly price,
dividend and earnings, CPI and the 10-year yield, from 1871. No allowed
package reads a legacy ``.xls``, so the step is manual (D59, TARGET
decision 2a). Open the file in Excel, and Save As CSV with the Data sheet
showing. Then Ctrl+K: Import Shiller CSV.

**Terms.** Neither the page nor the workbook states a license. The data is
used locally only: never committed, tests on a made-up fixture, and cited
where shown (D59, TARGET decision 3).

**Reading the file:**
* **Columns by header label, not position.** The header is the row whose
  first cell is ``Date``. It must name ``P``, ``D``, ``E``, ``CPI`` and
  ``Rate GS10``, once each; ``CAPE`` is read when present. Shiller adds
  columns over the years, and a missing label is an error that names it.
* **Dates.** Shiller writes October as ``1871.1`` and January as
  ``1871.01``, so the month is round(100·(x − ⌊x⌋)). Months must be
  consecutive and unique; a gap or a repeat names its line. A month is
  dated the 1st (FRED's convention for monthly series).
* **Blank and NA cells** are missing values, left out, never filled.
  Earnings and dividends end months before prices do; the note says
  "earnings through …".
* **The last month is provisional, and dropped.** The file's own footnote
  says so ("Oct price is Oct 2nd close", "Sept/Oct CPI estimated"), the same
  treatment V0 gives a same-day bar. The footnotes are kept in the note.
* Excel may write thousands separators in a quoted number; they're removed.
  Percent-formatted and real-terms columns are never read.

**Units:** P, D and E are index points (D and E at annual rates, trailing
twelve months), CPI is the index level, GS10 a percent yield, CAPE a ratio.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass

from quant_hub.data.series import FetchedSeries, SeriesMeta

SOURCE = "shiller"
CITATION = ("Robert J. Shiller, U.S. stock market data (ie_data.xls), from "
            "'Irrational Exuberance', Princeton University Press. Used locally.")

# header label -> (series id, title, unit); CAPE is optional (module docstring).
COLUMNS = {
    "P": ("sp.price", "S&P Composite price (monthly average of daily closes)", "index"),
    "D": ("sp.dividend", "S&P Composite dividends, trailing 12 months", "index"),
    "E": ("sp.earnings", "S&P Composite earnings, trailing 12 months", "index"),
    "CPI": ("cpi", "Consumer Price Index", "index"),
    "Rate GS10": ("gs10", "10-year Treasury yield (GS10)", "percent"),
    "CAPE": ("cape", "Cyclically adjusted P/E (CAPE), as Shiller computes it", "ratio"),
}
REQUIRED = ("P", "D", "E", "CPI", "Rate GS10")


class ShillerError(ValueError):
    """The CSV can't be read as Shiller's data sheet; the message says where."""


@dataclass(frozen=True)
class ShillerImport:
    series: list[FetchedSeries]
    first: str                 # first month kept, ISO
    last: str                  # last month kept, ISO
    dropped: str               # the provisional month left out, ISO
    earnings_through: str      # the last month with earnings, ISO
    footnotes: str


def _decode(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("cp1252")


def shiller_month(text: str) -> tuple[int, int] | None:
    """'1871.1' -> (1871, 10), '1871.01' -> (1871, 1); None if not a date."""
    try:
        x = float(text)
    except ValueError:
        return None
    if not math.isfinite(x) or x < 1000:
        return None
    year = math.floor(x)
    month = round(100 * (x - year))
    return (year, month) if 1 <= month <= 12 else None


def _number(cell: str) -> float | None:
    cell = cell.strip().replace(",", "")
    if not cell or cell.upper() == "NA":
        return None
    try:
        value = float(cell)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_shiller_csv(payload: bytes) -> ShillerImport:
    """The data sheet as series (module docstring). Raises ``ShillerError``."""
    rows = list(csv.reader(io.StringIO(_decode(payload))))
    header_at = next((i for i, r in enumerate(rows) if r and r[0].strip() == "Date"), None)
    if header_at is None:
        raise ShillerError("no header row: Shiller's data sheet has a row starting with 'Date'")
    labels = [c.strip() for c in rows[header_at]]
    index = {}
    for label in COLUMNS:
        found = [i for i, c in enumerate(labels) if c == label]
        if len(found) > 1:
            raise ShillerError(f"the header names {label!r} {len(found)} times")
        if found:
            index[label] = found[0]
        elif label in REQUIRED:
            raise ShillerError(f"the header has no {label!r} column")

    months: list[tuple[int, int]] = []
    cells: dict[str, list[float | None]] = {label: [] for label in index}
    footnotes = ""
    for line, row in enumerate(rows[header_at + 1:], start=header_at + 2):
        first = row[0].strip() if row else ""
        if not first:
            notes = [c.strip() for c in row if c.strip()]
            if notes and months:
                footnotes = "; ".join(notes)
                break
            continue
        month = shiller_month(first)
        if month is None:
            break
        if months:
            y, m = months[-1]
            expected = (y + (m == 12), m % 12 + 1)
            if month != expected:
                what = "repeats" if month == months[-1] else "skips to"
                raise ShillerError(f"line {line}: {what} {month[0]}-{month[1]:02d} after "
                                   f"{y}-{m:02d}; months must be consecutive")
        months.append(month)
        for label, col in index.items():
            cells[label].append(_number(row[col]) if col < len(row) else None)
    if len(months) < 2:
        raise ShillerError("fewer than two months of data after the header")

    dropped = months.pop()                  # provisional (module docstring)
    for values in cells.values():
        values.pop()
    iso = [f"{y:04d}-{m:02d}-01" for y, m in months]
    earnings = [d for d, v in zip(iso, cells["E"]) if v is not None]
    note = (f"earnings through {earnings[-1][:7]}" if earnings else "no earnings") + (
        f"; {footnotes}" if footnotes else "")
    series = []
    for label, values in cells.items():
        kept = [(d, v) for d, v in zip(iso, values) if v is not None]
        if not kept:
            continue
        sid, title, unit = COLUMNS[label]
        series.append(FetchedSeries(SeriesMeta(SOURCE, sid, title, unit, "monthly", CITATION, note),
                                    tuple(d for d, _ in kept), tuple(v for _, v in kept)))
    return ShillerImport(series, iso[0], iso[-1], f"{dropped[0]:04d}-{dropped[1]:02d}-01",
                         earnings[-1] if earnings else "", footnotes)
