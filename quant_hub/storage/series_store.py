"""External time series as fetched, in the store's file (D60, D17).

* ``ext_series``: one row per series. It holds what the series is (title,
  unit, frequency, the citation its source's terms ask for, a vintage
  note), when it was fetched, and the SHA-256 of the file it came from.
* ``ext_values``: the series' dated values.

A fetch replaces each series it brings whole, in one transaction for the
whole file. The sources so far (Ken French's files) deliver their full
history every time and revise it as the CRSP database is updated, so
"as fetched" means the latest file, revisions included. A source that serves
only a recent window would need merging instead; that's decided with that
source.

Schema v7 (``storage/schema.py``) adds the two tables to older files, after
the backup. Every public method holds the connection lock, as the other
stores do.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from quant_hub.data.series import FetchedSeries, SeriesMeta, StoredSeries
from quant_hub.storage.bar_store import StorageError

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Frozen v7 definitions, also used by the 6 -> 7 schema migration.
V7_TABLES = (
    "CREATE TABLE IF NOT EXISTS ext_series (source TEXT NOT NULL, series TEXT NOT NULL, "
    "title TEXT NOT NULL, unit TEXT NOT NULL, frequency TEXT NOT NULL, citation TEXT NOT NULL, "
    "note TEXT NOT NULL, fetched_at TEXT NOT NULL, sha256 TEXT NOT NULL, "
    "PRIMARY KEY (source, series)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS ext_values (source TEXT NOT NULL, series TEXT NOT NULL, "
    "date TEXT NOT NULL, value REAL NOT NULL, "
    "PRIMARY KEY (source, series, date)) WITHOUT ROWID",
)


@dataclass(frozen=True)
class CatalogRow:
    """One stored series, without its values: what the app has, and how fresh."""
    meta: SeriesMeta
    fetched_at: datetime
    count: int
    first: str
    last: str


class SeriesStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                for statement in V7_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the series store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, fetched: Sequence[FetchedSeries], fetched_at: datetime, sha256: str) -> int:
        """Replace every series in ``fetched`` (one transaction); the values kept."""
        stamp = fetched_at.strftime(_TIME_FORMAT)
        with self._lock:
            try:
                with self._conn:
                    for s in fetched:
                        m = s.meta
                        self._conn.execute("DELETE FROM ext_values WHERE source=? AND series=?",
                                           (m.source, m.series))
                        self._conn.executemany(
                            "INSERT INTO ext_values VALUES (?,?,?,?)",
                            [(m.source, m.series, d, v) for d, v in zip(s.dates, s.values)])
                        self._conn.execute(
                            "INSERT INTO ext_series VALUES (?,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(source, series) DO UPDATE SET title=excluded.title, "
                            "unit=excluded.unit, frequency=excluded.frequency, "
                            "citation=excluded.citation, note=excluded.note, "
                            "fetched_at=excluded.fetched_at, sha256=excluded.sha256",
                            (m.source, m.series, m.title, m.unit, m.frequency, m.citation,
                             m.note, stamp, sha256))
                return sum(len(s.dates) for s in fetched)
            except sqlite3.Error as exc:
                raise StorageError(f"could not save series: {exc}") from exc

    def catalog(self, source: str | None = None) -> list[CatalogRow]:
        """Every stored series (or one source's), by source then id."""
        where, args = ("WHERE s.source=?", (source,)) if source else ("", ())
        rows = self._read(
            "SELECT s.source, s.series, s.title, s.unit, s.frequency, s.citation, s.note, "
            "s.fetched_at, COUNT(v.date), MIN(v.date), MAX(v.date) FROM ext_series s "
            "LEFT JOIN ext_values v ON v.source=s.source AND v.series=s.series "
            f"{where} GROUP BY s.source, s.series ORDER BY s.source, s.series", args)
        return [CatalogRow(SeriesMeta(src, ser, title, unit, freq, cite, note),
                           datetime.strptime(fetched, _TIME_FORMAT), int(n), first or "", last or "")
                for src, ser, title, unit, freq, cite, note, fetched, n, first, last in rows]

    def series(self, source: str, series: str) -> StoredSeries | None:
        """One series with its values, or None if it was never fetched."""
        head = self._read("SELECT title, unit, frequency, citation, note, fetched_at, sha256 "
                          "FROM ext_series WHERE source=? AND series=?", (source, series))
        if not head:
            return None
        title, unit, freq, cite, note, fetched, sha = head[0]
        rows = self._read("SELECT date, value FROM ext_values WHERE source=? AND series=? "
                          "ORDER BY date", (source, series))
        values = pd.Series([v for _, v in rows], index=pd.DatetimeIndex([d for d, _ in rows]),
                           dtype=float, name=series)
        return StoredSeries(SeriesMeta(source, series, title, unit, freq, cite, note),
                            datetime.strptime(fetched, _TIME_FORMAT), sha, values)

    def fetched_at(self, source: str, prefix: str = "") -> datetime | None:
        """When the stalest series of ``source`` whose id starts with ``prefix``
        was fetched; None if none was."""
        rows = self._read("SELECT MIN(fetched_at) FROM ext_series WHERE source=? AND series LIKE ?",
                          (source, prefix.replace("%", "") + "%"))
        return datetime.strptime(rows[0][0], _TIME_FORMAT) if rows and rows[0][0] else None

    def _read(self, sql: str, arguments: tuple = ()) -> list[tuple]:
        with self._lock:
            try:
                return self._conn.execute(sql, arguments).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"series read failed: {exc}") from exc
