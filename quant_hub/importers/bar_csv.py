"""Daily bars from CSV files: one generic layout, and vendor presets (F2, D34).

A file is the one provider every user has: brokers and data vendors all
export CSV. This module only reads. It turns a file into the raw OHLCV frame
any provider hands over (``data.sources.BarSource``): a naive session-date
index and the columns open, high, low, close and volume, not yet validated.
``services/file_import.py`` then validates and stores it exactly as it does
IBKR's bars (``data/bars.py``).

The generic layout
------------------
::

    date,open,high,low,close,volume
    2024-01-02,472.16,473.67,470.49,472.65,123623700

* A header row, names in any case and order; other columns are ignored.
* ``date`` as YYYY-MM-DD, one row per session.
* The columns declare the price basis (D38). ``close`` is the price as
  traded; ``adj_close`` the close adjusted for dividends too. A file needs
  at least one. ``open``, ``high``, ``low`` and ``volume`` are optional: a
  close-only file is fine.
* An optional ``symbol`` column lets one file hold several symbols. Without
  it, the importer is told the symbol.
* Prices are plain numbers. A ``$`` and thousands separators are tolerated,
  and an empty or ``null`` cell is a missing value.

Stooq's daily downloads use exactly this header. Rows that fail validation
(a missing close, ``high < low``, …) are dropped and reported, never
silently. An unreadable date stops the import, naming the row: a bar can't
be placed without its date.

Presets
-------
Vendors whose files differ, recognized by their header:

* **Nasdaq.com** historical data: ``Date, Close/Last, Volume, Open, High,
  Low``, dates as MM/DD/YYYY, prices with ``$``, newest first.
* **Yahoo Finance** downloads: the generic names plus ``Adj Close``. Yahoo
  adjusts ``Close`` for splits and ``Adj Close`` for dividends too, so
  they are the traded and adjusted bases, like IBKR's.

Prices are stored as the vendor gives them, on the basis their columns
declare.

Detection takes the first layout whose identifying columns are all present,
most specific first, so a Yahoo file isn't read as merely generic.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import numpy as np
import pandas as pd

FIELDS = ("open", "high", "low", "close", "volume", "adj_close")
_MISSING = {"", "null", "nan", "n/a", "na", "-"}


@dataclass(frozen=True)
class CsvLayout:
    """How one kind of file names its columns and writes its dates.

    ``columns`` maps our field names (``FIELDS``, ``date``, ``symbol``) to
    the file's header, lower case; ``identifying`` are the headers that must
    all be present for detection to pick this layout.
    """

    name: str
    columns: Mapping[str, str]
    identifying: tuple[str, ...]
    date_format: str
    date_hint: str


_GENERIC_COLUMNS = {"date": "date", "open": "open", "high": "high", "low": "low",
                    "close": "close", "volume": "volume", "symbol": "symbol",
                    "adj_close": "adj_close"}

GENERIC = CsvLayout("Generic", _GENERIC_COLUMNS, ("date",), "%Y-%m-%d", "YYYY-MM-DD")
YAHOO = CsvLayout("Yahoo Finance", {**_GENERIC_COLUMNS, "adj_close": "adj close"},
                  ("date", "close", "adj close"), "%Y-%m-%d", "YYYY-MM-DD")
NASDAQ = CsvLayout("Nasdaq.com", {"date": "date", "open": "open", "high": "high",
                                  "low": "low", "close": "close/last", "volume": "volume"},
                   ("date", "close/last"), "%m/%d/%Y", "MM/DD/YYYY")

LAYOUTS = (NASDAQ, YAHOO, GENERIC)   # most specific first


class BarFileError(ValueError):
    """The file can't be read as daily bars; the message says why."""


@dataclass(frozen=True)
class BarFile:
    """What a file held: its layout, and raw bars by symbol (``None`` when
    the file names no symbol and the caller must)."""

    layout: CsvLayout
    bars: dict[str | None, pd.DataFrame]


def detect_layout(header: Iterable[str]) -> CsvLayout | None:
    names = {str(name).strip().lower() for name in header}
    return next((layout for layout in LAYOUTS if set(layout.identifying) <= names), None)


def read_bar_csv(source: str | Path | IO[str], layout: CsvLayout | None = None) -> BarFile:
    """Read a CSV of daily bars (module docstring). Raises ``BarFileError``."""
    try:
        table = pd.read_csv(source, dtype=str, keep_default_na=False, skipinitialspace=True)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError,
            pd.errors.EmptyDataError) as exc:
        raise BarFileError(f"couldn't read the file as CSV: {exc}") from exc
    table.columns = [str(name).strip().lower() for name in table.columns]
    layout = layout or detect_layout(table.columns)
    if layout is None:
        raise BarFileError(
            f"unrecognized columns {list(table.columns)}: a file needs at least date and "
            "close (the generic layout is date,open,high,low,close,volume)")
    missing = [name for name in layout.identifying if name not in table.columns]
    if missing:
        raise BarFileError(f"a {layout.name} file needs the columns {missing}")
    prices = [layout.columns.get(field) for field in ("close", "adj_close")]
    if not any(name in table.columns for name in prices if name):
        raise BarFileError(f"columns {list(table.columns)} hold no close: a file needs close "
                           "(as traded) or adj_close (adjusted for dividends), or both")
    if table.empty:
        raise BarFileError("the file has a header but no rows")

    text = table[layout.columns["date"]].str.strip()
    dates = pd.to_datetime(text, format=layout.date_format, errors="coerce")
    unreadable = dates.isna().to_numpy()
    if unreadable.any():
        row = int(np.argmax(unreadable))
        raise BarFileError(f"row {row + 2}: date {text.iloc[row]!r} isn't {layout.date_hint}")

    frame = pd.DataFrame(index=pd.DatetimeIndex(dates.to_numpy()))
    for field in FIELDS:
        header = layout.columns.get(field)
        if header in table.columns:
            frame[field] = _numbers(table[header]).to_numpy()

    symbols = layout.columns.get("symbol")
    if symbols not in table.columns:
        return BarFile(layout, {None: frame})
    names = table[symbols].str.strip().str.upper().to_numpy()
    if (names == "").any():
        row = int(np.argmax(names == ""))
        raise BarFileError(f"row {row + 2}: the symbol is empty")
    return BarFile(layout, {name: frame[names == name] for name in dict.fromkeys(names)})


def _numbers(column: pd.Series) -> pd.Series:
    """Text to floats: ``$`` and thousands separators dropped, missing-value
    spellings to NaN. Anything else unreadable also becomes NaN, so a bad
    close is dropped and reported by validation."""
    text = column.str.strip().str.replace(r"[$,]", "", regex=True)
    text = text.where(~text.str.lower().isin(_MISSING))
    return pd.to_numeric(text, errors="coerce")
