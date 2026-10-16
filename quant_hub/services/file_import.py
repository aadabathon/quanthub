"""File import: CSV bars into the store, served back as the ``file`` source
(F2, D34).

Two halves:

* ``import_bar_file`` reads a file (``importers/bar_csv.py``) and validates
  each symbol's bars with the same ``validate_ohlcv_frame`` as IBKR's. Only
  if every symbol has usable bars does it upsert them into the ``BarStore``,
  under the provider name ``file``, so a bad file stores nothing. Importing
  again replaces bars on the same dates (a corrected file wins), and every
  dropped or repaired row is logged.
* The file's columns declare its bases (D38): ``close`` is traded,
  ``adj_close`` adjusted. With both, the adjusted open, high and low are the
  traded ones times that day's ``adj_close / close``, the factor IBKR and
  Yahoo apply to all four prices. A file stores only the bases it holds,
  and asking for another says so.
* ``FileSource`` is the ``DataSource`` that pages name ``file``. It serves
  what was imported, from the store. A file isn't fetched by date range, so
  unlike ``CachedSource`` it keeps no coverage: a window past a file's last
  date just holds fewer bars, and a window with none says to import first.
  A file has no live quote either, so quotes fall back to the last close
  (``services/underlying.py``).

Runs on the task-worker thread (file and database I/O).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quant_hub.broker.types import NoMarketDataError
from quant_hub.data.bars import ValidationReport, validate_ohlcv_frame
from quant_hub.data.sources import BasisUnavailableError
from quant_hub.data.types import PriceBasis, PriceHistory, Quote
from quant_hub.importers.bar_csv import BarFileError, read_bar_csv
from quant_hub.services.cached_source import DAILY
from quant_hub.storage.bar_store import BarStore, InstrumentKey

_logger = logging.getLogger(__name__)

FILE_SOURCE = "file"

# A plausible ticker as a file name: SPY, BRK.B, BRK-B, ^GSPC, ES=F.
_TICKER = re.compile(r"^\^?[A-Za-z0-9][A-Za-z0-9.=\-]{0,11}$")


class NoImportedBarsError(LookupError):
    """The store holds no imported bars for that symbol and window."""


class ImportedBasisMissingError(NoImportedBarsError, BasisUnavailableError):
    """Imported, but not on the requested basis."""


@dataclass(frozen=True)
class ImportedSeries:
    """One symbol's import: what the file held and what the store took."""

    symbol: str
    layout: str
    inserted: int
    replaced: int
    report: ValidationReport
    first: datetime
    last: datetime
    bases: tuple[PriceBasis, ...] = (PriceBasis.TRADED,)

    @property
    def stored(self) -> int:
        return self.inserted + self.replaced

    def describe(self) -> str:
        dropped = len(self.report.dropped)
        bases = " and ".join(b.value for b in self.bases)
        return (f"{self.symbol}: {self.stored:,} bars, {self.first:%Y-%m-%d} to "
                f"{self.last:%Y-%m-%d}, {bases} ({self.layout} layout"
                + (f"; {dropped} row{'s' if dropped != 1 else ''} dropped" if dropped else "")
                + ")")


def symbol_from_filename(path: str | Path) -> str | None:
    """The file's name as a symbol (``spy.csv`` -> ``SPY``), when it looks
    like one; otherwise the user must say."""
    stem = Path(path).stem.strip()
    return stem.upper() if _TICKER.match(stem) else None


def import_bar_file(store: BarStore, path: str | Path,
                    symbol: str | None = None) -> list[ImportedSeries]:
    """Read, validate and store a CSV of daily bars (module docstring).

    ``symbol`` names the bars when the file has no ``symbol`` column; a file
    that names its own symbols ignores it. Raises ``BarFileError``.
    """
    path = Path(path)
    parsed = read_bar_csv(path)
    validated = []
    for named, raw in parsed.bars.items():
        name = named or _clean_symbol(symbol, path)
        series = {}
        for basis, frame in _by_basis(raw).items():
            clean, report = validate_ohlcv_frame(frame)
            log = _logger.info if report.clean else _logger.warning
            log("read %s %s from %s: %s", basis.value, name, path.name, report.summary())
            if clean.empty:
                raise BarFileError(f"{name}: no usable {basis.value} bars in {path.name} "
                                   f"({report.summary()})")
            series[basis] = (clean, report)
        validated.append((name, series))

    imported = []
    for name, series in validated:
        results = {basis: store.insert_bars(InstrumentKey(name), FILE_SOURCE, DAILY, clean,
                                            basis)
                   for basis, (clean, _report) in series.items()}
        # Report the first basis the file holds: traded where there is one.
        basis = next(iter(series))
        clean, report = series[basis]
        result = results[basis]
        _logger.info("imported %s from %s: %d inserted, %d replaced (%s)", name, path.name,
                     result.inserted, result.replaced,
                     " and ".join(b.value for b in series))
        imported.append(ImportedSeries(name, parsed.layout.name, result.inserted,
                                       result.replaced, report, clean.index[0].to_pydatetime(),
                                       clean.index[-1].to_pydatetime(), tuple(series)))
    return imported


def _by_basis(raw):
    """A file's frame as one frame per basis it holds (module docstring)."""
    prices = [col for col in ("open", "high", "low") if col in raw.columns]
    bases = {}
    if "close" in raw.columns:
        bases[PriceBasis.TRADED] = raw.drop(columns=["adj_close"], errors="ignore")
    if "adj_close" in raw.columns:
        adjusted = raw.drop(columns=["adj_close"]).assign(close=raw["adj_close"])
        if "close" in raw.columns:
            factor = raw["adj_close"] / raw["close"]
            for col in prices:
                adjusted[col] = raw[col] * factor
        else:
            adjusted = adjusted.drop(columns=prices)       # no traded prices to scale
        bases[PriceBasis.ADJUSTED] = adjusted
    return bases


def _clean_symbol(symbol: str | None, path: Path) -> str:
    if symbol is None or not symbol.strip():
        raise BarFileError(f"{path.name} names no symbol: say which symbol its bars are")
    cleaned = symbol.strip().upper()
    if len(cleaned) > 20 or any(ch.isspace() for ch in cleaned):
        raise BarFileError(f"{symbol!r} isn't a symbol")
    return cleaned


class FileImportService:
    """What the GUI calls: imports into the store it was built with."""

    def __init__(self, store: BarStore) -> None:
        self._store = store

    def import_bars(self, path: str | Path, symbol: str | None = None) -> list[ImportedSeries]:
        return import_bar_file(self._store, path, symbol)


class FileSource:
    """The ``file`` source: imported bars, from the store (module docstring).
    Implements the ``DataSource`` protocol."""

    name = FILE_SOURCE

    def __init__(self, store: BarStore) -> None:
        self._store = store

    def get_history(self, symbol: str, start: datetime, end: datetime,
                    basis: PriceBasis = PriceBasis.TRADED) -> PriceHistory:
        symbol = symbol.upper().strip()
        key = InstrumentKey(symbol)
        frame = self._store.query_bars(key, self.name, DAILY, start, end, basis)
        if len(frame) == 0:
            other = next(b for b in PriceBasis if b is not basis)
            if len(self._store.query_bars(key, self.name, DAILY, start, end, other)):
                column = "an adj_close" if basis is PriceBasis.ADJUSTED else "a close"
                raise ImportedBasisMissingError(
                    f"{symbol} was imported with {other.value} prices only: choose "
                    f"{other.value.capitalize()}, or import a file with {column} column")
            raise NoImportedBarsError(
                f"no imported {symbol} bars from {start:%Y-%m-%d} to {end:%Y-%m-%d}: import a "
                "CSV from the Dashboard's Market data card")
        # Columns the file didn't have are stored as NULL (as in CachedSource).
        return PriceHistory(symbol=symbol, source=self.name,
                            frame=frame.dropna(axis=1, how="all"), basis=basis)

    def get_quote(self, symbol: str) -> Quote:
        raise NoMarketDataError(f"{symbol}: imported files have no live quotes")
