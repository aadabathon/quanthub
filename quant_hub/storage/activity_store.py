"""SQLite store for imported activity: every transaction, once (TR1, D90).

Schema (two tables, created idempotently on open, in the same database as
the bars, D17; schema v11)
------------------------------------------------------------------------
``activity_imports``
    One row per file imported: its layout, name and SHA-256, the first and
    last trade dates in it, how many rows it held, how many of them were
    new to the store, and when.
``activity``
    The transactions. Each row's primary key is its ``row_key`` and its
    ``occurrence`` (``data.activity``: the n-th copy of that key in its
    file), and a second insert of the same pair is ignored. So the store
    keeps, for each key, the most copies any one file held, and importing a
    file again, or an export that overlaps an earlier one, adds only what's
    new. Each row also remembers the import that first brought it and its
    place in that file (``seq``), which orders a day's rows as the file did.

The table is ``activity``, not ``trades``: an extension may keep its own
``trades`` table in the same file.

Threading contract: the same as ``BarStore``. The connection is created
with ``check_same_thread=False``, and every public method holds an
internal lock.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quant_hub.data.activity import ActivityFile, Kind, Transaction, occurrences
from quant_hub.data.positions import OptionContract

_logger = logging.getLogger(__name__)

# Frozen: schema step 10 -> 11 (storage/schema.py) creates exactly these.
V11_TABLES = (
    "CREATE TABLE IF NOT EXISTS activity_imports (id INTEGER PRIMARY KEY, source TEXT NOT NULL, "
    "name TEXT NOT NULL, sha256 TEXT NOT NULL, first_date TEXT NOT NULL, "
    "last_date TEXT NOT NULL, rows INTEGER NOT NULL, added INTEGER NOT NULL, "
    "imported_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS activity (row_key TEXT NOT NULL, occurrence INTEGER NOT NULL, "
    "import_id INTEGER NOT NULL REFERENCES activity_imports(id), seq INTEGER NOT NULL, "
    "account TEXT NOT NULL, trade_date TEXT NOT NULL, settle_date TEXT, kind TEXT NOT NULL, "
    "symbol TEXT NOT NULL, quantity REAL NOT NULL, price REAL, commission REAL NOT NULL, "
    "fees REAL NOT NULL, amount REAL NOT NULL, description TEXT NOT NULL, underlying TEXT, "
    "option_type TEXT, strike REAL, expiry TEXT, "
    "PRIMARY KEY (row_key, occurrence)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS activity_by_date ON activity (trade_date)",
)

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_COLUMNS = ("account, trade_date, settle_date, kind, symbol, quantity, price, commission, "
            "fees, amount, description, underlying, option_type, strike, expiry")


class ActivityStoreError(Exception):
    """The activity store could not be opened, read, or written."""


@dataclass(frozen=True)
class ActivityImportSummary:
    """One stored import (the newest first in ``imports``)."""

    id: int
    source: str
    name: str
    sha256: str
    first_date: date
    last_date: date
    rows: int
    added: int
    imported_at: datetime


class ActivityStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            with self._conn:
                for statement in V11_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise ActivityStoreError(f"could not open activity store at {db_path}: "
                                     f"{exc}") from exc
        _logger.info("activity store ready: %s", db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, file: ActivityFile, sha256: str = "",
             imported_at: datetime | None = None) -> ActivityImportSummary:
        """Keep a file's transactions; those already stored are left as
        they are (module docstring). Returns the import, with how many rows
        were new."""
        if not file.transactions:
            raise ActivityStoreError(f"{file.name or 'the file'} has no transactions to save")
        imported_at = imported_at or datetime.now()
        first, last = file.first_date, file.last_date
        rows = [(t.key, n, seq, *_values(t))
                for seq, (t, n) in enumerate(zip(file.transactions,
                                                 occurrences(file.transactions)))]
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO activity_imports (source, name, sha256, first_date,"
                        " last_date, rows, added, imported_at) VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                        (file.source, file.name, sha256, first.isoformat(), last.isoformat(),
                         len(rows), imported_at.strftime(_TIME_FORMAT)))
                    import_id = int(cursor.lastrowid)
                    before = self._conn.total_changes
                    self._conn.executemany(
                        f"INSERT OR IGNORE INTO activity (row_key, occurrence, seq, {_COLUMNS},"
                        f" import_id) VALUES (?, ?, ?, {', '.join('?' * 15)}, {import_id})",
                        rows)
                    added = self._conn.total_changes - before
                    self._conn.execute("UPDATE activity_imports SET added = ? WHERE id = ?",
                                       (added, import_id))
            except sqlite3.Error as exc:
                raise ActivityStoreError(f"could not save {file.name or 'the file'}: "
                                         f"{exc}") from exc
        _logger.info("imported activity from %s: %d rows, %d new (%s to %s)",
                     file.name or "a file", len(rows), added, first, last)
        return ActivityImportSummary(import_id, file.source, file.name, sha256, first, last,
                                     len(rows), added, imported_at.replace(microsecond=0))

    def transactions(self, account: str | None = None, start: date | None = None,
                     end: date | None = None) -> list[Transaction]:
        """Stored transactions by trade date, a day's in the order their
        file listed them; one account's, or all, between ``start`` and
        ``end`` inclusive."""
        clauses, params = [], []
        if account is not None:
            clauses.append("account = ?")
            params.append(account)
        if start is not None:
            clauses.append("trade_date >= ?")
            params.append(start.isoformat())
        if end is not None:
            clauses.append("trade_date <= ?")
            params.append(end.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            try:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM activity{where}"
                    " ORDER BY trade_date, import_id, seq", params).fetchall()
            except sqlite3.Error as exc:
                raise ActivityStoreError(f"could not read the activity: {exc}") from exc
        return [_transaction(row) for row in rows]

    def imports(self) -> list[ActivityImportSummary]:
        """Every import, newest first."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT id, source, name, sha256, first_date, last_date, rows, added,"
                    " imported_at FROM activity_imports ORDER BY imported_at DESC, id DESC"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ActivityStoreError(f"could not list the imports: {exc}") from exc
        return [ActivityImportSummary(int(i), source, name, sha, date.fromisoformat(first),
                                      date.fromisoformat(last), int(n), int(added),
                                      datetime.strptime(at, _TIME_FORMAT))
                for i, source, name, sha, first, last, n, added, at in rows]

    def with_hash(self, sha256: str) -> list[ActivityImportSummary]:
        """Earlier imports of the same file, newest first."""
        return [s for s in self.imports() if sha256 and s.sha256 == sha256]


def _values(t: Transaction) -> tuple:
    o = t.option
    return (t.account, t.trade_date.isoformat(),
            t.settle_date.isoformat() if t.settle_date else None, t.kind.value, t.symbol,
            t.quantity, t.price, t.commission, t.fees, t.amount, t.description,
            o.underlying if o else None, o.option_type if o else None,
            o.strike if o else None, o.expiry.isoformat() if o else None)


def _transaction(row: tuple) -> Transaction:
    (account, trade_date, settle_date, kind, symbol, quantity, price, commission, fees, amount,
     description, underlying, option_type, strike, expiry) = row
    option = (OptionContract(underlying, option_type, strike, date.fromisoformat(expiry))
              if underlying else None)
    return Transaction(
        trade_date=date.fromisoformat(trade_date), kind=Kind(kind), amount=amount,
        account=account, symbol=symbol, quantity=quantity, price=price,
        commission=commission, fees=fees,
        settle_date=date.fromisoformat(settle_date) if settle_date else None,
        description=description, option=option)
