"""SQLite-backed store for historical OHLCV bars and their sync coverage.

Schema (three tables; created idempotently on open)
---------------------------------------------------
``instruments``
    One row per distinct instrument identity
    ``(symbol, sec_type, exchange, currency)`` — a bar is never identified
    by symbol alone, because the same ticker can exist as different
    security types or currencies.
``bars``
    One row per observation, primary key
    ``(instrument_id, source, bar_size, ts)``. Uniqueness is enforced *by
    the database*, not by Python. ``close`` is required and positive;
    ``open/high/low/volume`` may be NULL (close-only rows are legal, per
    the ``PriceHistory`` schema). Re-inserting an existing key **replaces**
    the row: a re-fetched bar supersedes the stored one, which is what
    keeps a partial same-day bar from fossilizing.
``coverage``
    Date intervals successfully synced per ``(instrument, source,
    bar_size)``. Kept separate from bar existence because weekends and
    holidays make "no bar" ambiguous. Rows are merged into maximal
    disjoint ranges on every write (see ``data/ranges.py``).

Time representation
-------------------
``ts`` is ISO-8601 text (``YYYY-MM-DD HH:MM:SS``), which orders
lexicographically = chronologically. Daily bars carry the session date at
midnight, timezone-naive by convention (``data/bars.py``); coverage
endpoints are stored as dates.

Threading contract
------------------
The store is opened on the main thread at startup but used from the
single task-worker thread (D9). The connection is created with
``check_same_thread=False`` and every public method holds an internal
lock, so the store is safe under the current one-worker model and will
not corrupt under an accidental second caller — but it is a desktop
research store, not a concurrent database.

Inputs are expected to be *validated* frames (``data/bars.py``); the
store does not re-validate defensively (project convention), but the
schema's CHECK constraints are a database-level backstop, and their
violation raises :class:`StorageError`.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from quant_hub.data.ranges import DateRange, merge_ranges

_logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    id        INTEGER PRIMARY KEY,
    symbol    TEXT NOT NULL,
    sec_type  TEXT NOT NULL,
    exchange  TEXT NOT NULL,
    currency  TEXT NOT NULL,
    UNIQUE (symbol, sec_type, exchange, currency)
);

CREATE TABLE IF NOT EXISTS bars (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    source        TEXT NOT NULL,
    bar_size      TEXT NOT NULL,
    ts            TEXT NOT NULL,
    open   REAL CHECK (open   IS NULL OR open  > 0),
    high   REAL CHECK (high   IS NULL OR high  > 0),
    low    REAL CHECK (low    IS NULL OR low   > 0),
    close  REAL NOT NULL CHECK (close > 0),
    volume REAL CHECK (volume IS NULL OR volume >= 0),
    CHECK (high IS NULL OR low IS NULL OR high >= low),
    PRIMARY KEY (instrument_id, source, bar_size, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS coverage (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    source        TEXT NOT NULL,
    bar_size      TEXT NOT NULL,
    start_ts      TEXT NOT NULL,
    end_ts        TEXT NOT NULL,
    CHECK (start_ts <= end_ts)
);
CREATE INDEX IF NOT EXISTS idx_coverage_key
    ON coverage (instrument_id, source, bar_size);
"""

_BAR_COLUMNS = ("open", "high", "low", "close", "volume")


class StorageError(Exception):
    """A database-level failure (wraps ``sqlite3.Error``)."""


@dataclass(frozen=True)
class InstrumentKey:
    """Instrument identity as persisted. Mirrors the broker's ContractSpec
    fields on purpose, but is defined here so storage stays broker-free."""

    symbol: str
    sec_type: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.symbol or not self.symbol.strip():
            raise ValueError("InstrumentKey.symbol must be non-empty")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())


@dataclass(frozen=True)
class InsertResult:
    """Outcome of one bar insert, for the ingestion log."""

    inserted: int
    replaced: int


@dataclass(frozen=True)
class StoredSeriesSummary:
    """One row of the store's contents overview (per symbol/source/bar-size)."""

    symbol: str
    source: str
    bar_size: str
    n_bars: int
    first: datetime
    last: datetime


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _iso_date(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d")


class BarStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open bar store at {db_path}: {exc}") from exc
        _logger.info("bar store ready: %s", db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------- bars
    def insert_bars(
        self,
        key: InstrumentKey,
        source: str,
        bar_size: str,
        frame: pd.DataFrame,
    ) -> InsertResult:
        """Upsert a validated OHLCV frame. Returns inserted/replaced counts.

        Replacement (same primary key) is deliberate: a re-fetched bar is
        the newer observation. The counts exist for the ingestion log.
        """
        if len(frame) == 0:
            return InsertResult(inserted=0, replaced=0)
        rows = []
        for ts, row in frame.iterrows():
            values = [
                None if pd.isna(row.get(col)) else float(row.get(col))
                for col in _BAR_COLUMNS
            ]
            rows.append((source, bar_size, _iso(ts.to_pydatetime()), *values))

        with self._lock:
            try:
                with self._conn:
                    instrument_id = self._instrument_id(key)
                    existing = {
                        ts
                        for (ts,) in self._conn.execute(
                            "SELECT ts FROM bars WHERE instrument_id=? "
                            "AND source=? AND bar_size=?",
                            (instrument_id, source, bar_size),
                        )
                    }
                    replaced = sum(1 for row in rows if row[2] in existing)
                    self._conn.executemany(
                        "INSERT INTO bars (instrument_id, source, bar_size, ts,"
                        " open, high, low, close, volume)"
                        f" VALUES ({instrument_id}, ?, ?, ?, ?, ?, ?, ?, ?)"
                        " ON CONFLICT (instrument_id, source, bar_size, ts)"
                        " DO UPDATE SET open=excluded.open, high=excluded.high,"
                        " low=excluded.low, close=excluded.close,"
                        " volume=excluded.volume",
                        rows,
                    )
            except sqlite3.Error as exc:
                raise StorageError(f"insert failed for {key.symbol}: {exc}") from exc
        return InsertResult(inserted=len(rows) - replaced, replaced=replaced)

    def query_bars(
        self,
        key: InstrumentKey,
        source: str,
        bar_size: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Bars in ``[start, end]`` (inclusive), chronological. May be empty.

        Returns the canonical OHLCV frame shape (all five columns, NULLs as
        NaN) with a naive ``DatetimeIndex``. The window is widened to whole
        days (coverage and daily bars are day-granular), so a request ending
        "now" includes today's midnight-stamped session bar.
        """
        with self._lock:
            try:
                cursor = self._conn.execute(
                    "SELECT ts, open, high, low, close, volume FROM bars"
                    " WHERE instrument_id = ("
                    "   SELECT id FROM instruments WHERE symbol=? AND sec_type=?"
                    "   AND exchange=? AND currency=?)"
                    " AND source=? AND bar_size=? AND ts BETWEEN ? AND ?"
                    " ORDER BY ts",
                    (
                        key.symbol, key.sec_type, key.exchange, key.currency,
                        source, bar_size,
                        _iso_date(start) + " 00:00:00",
                        _iso_date(end) + " 23:59:59",
                    ),
                )
                records = cursor.fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"query failed for {key.symbol}: {exc}") from exc
        index = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in records])
        return pd.DataFrame(
            {
                col: [r[i + 1] for r in records]
                for i, col in enumerate(_BAR_COLUMNS)
            },
            index=index,
            dtype=float,
        )

    def summary(self) -> list[StoredSeriesSummary]:
        """What the store holds, one row per (symbol, source, bar_size)."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT i.symbol, b.source, b.bar_size, COUNT(*),"
                    " MIN(b.ts), MAX(b.ts)"
                    " FROM bars b JOIN instruments i ON i.id = b.instrument_id"
                    " GROUP BY i.symbol, b.source, b.bar_size ORDER BY i.symbol",
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"summary query failed: {exc}") from exc
        return [
            StoredSeriesSummary(
                symbol=sym, source=src, bar_size=size, n_bars=int(n),
                first=datetime.fromisoformat(first),
                last=datetime.fromisoformat(last),
            )
            for sym, src, size, n, first, last in rows
        ]

    # --------------------------------------------------------------- coverage
    def coverage(
        self, key: InstrumentKey, source: str, bar_size: str
    ) -> list[DateRange]:
        """Merged, ordered coverage ranges for the instrument/source/bar-size."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT start_ts, end_ts FROM coverage"
                    " WHERE instrument_id = ("
                    "   SELECT id FROM instruments WHERE symbol=? AND sec_type=?"
                    "   AND exchange=? AND currency=?)"
                    " AND source=? AND bar_size=? ORDER BY start_ts",
                    (
                        key.symbol, key.sec_type, key.exchange, key.currency,
                        source, bar_size,
                    ),
                ).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"coverage query failed for {key.symbol}: {exc}") from exc
        return merge_ranges(
            DateRange(datetime.fromisoformat(s), datetime.fromisoformat(e))
            for s, e in rows
        )

    def add_coverage(
        self, key: InstrumentKey, source: str, bar_size: str, added: DateRange
    ) -> None:
        """Record ``added`` as synced, merging with existing ranges in place."""
        with self._lock:
            try:
                with self._conn:
                    instrument_id = self._instrument_id(key)
                    rows = self._conn.execute(
                        "SELECT start_ts, end_ts FROM coverage"
                        " WHERE instrument_id=? AND source=? AND bar_size=?",
                        (instrument_id, source, bar_size),
                    ).fetchall()
                    merged = merge_ranges(
                        [
                            DateRange(datetime.fromisoformat(s), datetime.fromisoformat(e))
                            for s, e in rows
                        ]
                        + [added]
                    )
                    self._conn.execute(
                        "DELETE FROM coverage"
                        " WHERE instrument_id=? AND source=? AND bar_size=?",
                        (instrument_id, source, bar_size),
                    )
                    self._conn.executemany(
                        "INSERT INTO coverage (instrument_id, source, bar_size,"
                        " start_ts, end_ts) VALUES (?, ?, ?, ?, ?)",
                        [
                            (
                                instrument_id, source, bar_size,
                                _iso_date(r.start), _iso_date(r.end),
                            )
                            for r in merged
                        ],
                    )
            except sqlite3.Error as exc:
                raise StorageError(f"coverage update failed for {key.symbol}: {exc}") from exc

    # ------------------------------------------------------------------ intern
    def _instrument_id(self, key: InstrumentKey) -> int:
        """Get-or-create. Caller holds the lock and an open transaction."""
        self._conn.execute(
            "INSERT OR IGNORE INTO instruments (symbol, sec_type, exchange,"
            " currency) VALUES (?, ?, ?, ?)",
            (key.symbol, key.sec_type, key.exchange, key.currency),
        )
        (instrument_id,) = self._conn.execute(
            "SELECT id FROM instruments WHERE symbol=? AND sec_type=?"
            " AND exchange=? AND currency=?",
            (key.symbol, key.sec_type, key.exchange, key.currency),
        ).fetchone()
        return int(instrument_id)
