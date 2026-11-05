"""SQLite store for imported position snapshots (P2, D33).

Every import is kept, stamped with the moment its file describes
(``as_of``) and when it was imported, so the portfolio's holdings over time
accumulate like the bar and chain history.

Schema (two tables; created idempotently on open, in the same database as
the bars, D17)
--------------------------------------------------------------------------
``position_snapshots``
    One row per import: the layout it came from (``Fidelity``,
    ``Generic``), the file's name, ``as_of`` and ``imported_at``, and the
    SHA-256 of the file(s) it was read from (schema v10, A4; empty for
    older imports and for snapshots that didn't come from a file).
``positions``
    Its lines, in file order (``seq``): account, symbol, kind (equity,
    option, cash), quantity, and price, value and cost basis when the file
    had them; an option's contract in its own columns. Deleting a snapshot
    cascades to its lines.

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

from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot

_logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS position_snapshots (
    id          INTEGER PRIMARY KEY,
    source      TEXT NOT NULL,
    name        TEXT NOT NULL,
    as_of       TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    sha256      TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS positions (
    snapshot_id INTEGER NOT NULL REFERENCES position_snapshots(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    account     TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('equity', 'option', 'cash')),
    quantity    REAL NOT NULL,
    price       REAL,
    value       REAL,
    cost_basis  REAL,
    description TEXT NOT NULL,
    underlying  TEXT,
    option_type TEXT,
    strike      REAL,
    expiry      TEXT,
    PRIMARY KEY (snapshot_id, seq)
) WITHOUT ROWID;
"""

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class PositionStoreError(Exception):
    """The position store could not be opened, read, or written."""


@dataclass(frozen=True)
class PositionSnapshotSummary:
    id: int
    source: str
    name: str
    as_of: datetime
    imported_at: datetime
    positions: int
    sha256: str = ""


class PositionStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        self._path = None if str(db_path) == ":memory:" else Path(db_path)
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise PositionStoreError(f"could not open position store at {db_path}: "
                                     f"{exc}") from exc
        _logger.info("position store ready: %s", db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, snapshot: PositionSnapshot, imported_at: datetime | None = None,
             sha256: str = "") -> int:
        imported_at = imported_at or datetime.now()
        rows = []
        for seq, p in enumerate(snapshot.positions):
            o = p.option
            rows.append((seq, p.account, p.symbol, p.kind.value, p.quantity, p.price, p.value,
                         p.cost_basis, p.description,
                         o.underlying if o else None, o.option_type if o else None,
                         o.strike if o else None, o.expiry.isoformat() if o else None))
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO position_snapshots (source, name, as_of, imported_at,"
                        " sha256) VALUES (?, ?, ?, ?, ?)",
                        (snapshot.source, snapshot.name, snapshot.as_of.strftime(_TIME_FORMAT),
                         imported_at.strftime(_TIME_FORMAT), sha256))
                    snapshot_id = int(cursor.lastrowid)
                    self._conn.executemany(
                        "INSERT INTO positions (snapshot_id, seq, account, symbol, kind,"
                        " quantity, price, value, cost_basis, description, underlying,"
                        " option_type, strike, expiry)"
                        f" VALUES ({snapshot_id}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            except sqlite3.Error as exc:
                raise PositionStoreError(f"could not save the snapshot: {exc}") from exc
        _logger.info("saved position snapshot #%d (%d positions, as of %s)", snapshot_id,
                     len(rows), snapshot.as_of.date())
        return snapshot_id

    def snapshots(self) -> list[PositionSnapshotSummary]:
        """Stored snapshots, newest import first."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT s.id, s.source, s.name, s.as_of, s.imported_at, COUNT(p.seq),"
                    " s.sha256 FROM position_snapshots s"
                    " LEFT JOIN positions p ON p.snapshot_id = s.id"
                    " GROUP BY s.id ORDER BY s.imported_at DESC, s.id DESC").fetchall()
            except sqlite3.Error as exc:
                raise PositionStoreError(f"could not list snapshots: {exc}") from exc
        return [PositionSnapshotSummary(int(i), source, name,
                                        datetime.strptime(as_of, _TIME_FORMAT),
                                        datetime.strptime(at, _TIME_FORMAT), int(n), sha)
                for i, source, name, as_of, at, n, sha in rows]

    def with_hash(self, sha256: str) -> list[PositionSnapshotSummary]:
        """The stored snapshots read from the same file(s), newest first."""
        return [s for s in self.snapshots() if sha256 and s.sha256 == sha256]

    def delete(self, snapshot_id: int, at: datetime | None = None) -> Path | None:
        """Delete one snapshot and its lines, after a backup of the whole
        file (D36's, labelled for the delete). Returns the backup's path
        (None for an in-memory store). Only ever the owner's action (A4)."""
        from quant_hub.storage.schema import backup_file
        at = at or datetime.now()
        with self._lock:
            try:
                if self._conn.execute("SELECT 1 FROM position_snapshots WHERE id = ?",
                                      (snapshot_id,)).fetchone() is None:
                    raise PositionStoreError(f"no position snapshot #{snapshot_id}")
                backup = (backup_file(self._conn, self._path, f"before-deleting-snapshot-"
                                                              f"{snapshot_id}", at)
                          if self._path is not None else None)
                with self._conn:
                    self._conn.execute("DELETE FROM position_snapshots WHERE id = ?",
                                       (snapshot_id,))
            except sqlite3.Error as exc:
                raise PositionStoreError(f"could not delete snapshot #{snapshot_id}: "
                                         f"{exc}") from exc
        _logger.info("deleted position snapshot #%d; the store was backed up to %s",
                     snapshot_id, backup)
        return backup

    def load(self, snapshot_id: int) -> PositionSnapshot:
        with self._lock:
            try:
                head = self._conn.execute(
                    "SELECT source, name, as_of FROM position_snapshots WHERE id = ?",
                    (snapshot_id,)).fetchone()
                rows = self._conn.execute(
                    "SELECT account, symbol, kind, quantity, price, value, cost_basis,"
                    " description, underlying, option_type, strike, expiry FROM positions"
                    " WHERE snapshot_id = ? ORDER BY seq", (snapshot_id,)).fetchall()
            except sqlite3.Error as exc:
                raise PositionStoreError(f"could not load snapshot #{snapshot_id}: "
                                         f"{exc}") from exc
        if head is None:
            raise PositionStoreError(f"no position snapshot #{snapshot_id}")
        positions = []
        for (account, symbol, kind, quantity, price, value, cost, description, underlying,
             option_type, strike, expiry) in rows:
            option = (OptionContract(underlying, option_type, strike, date.fromisoformat(expiry))
                      if underlying else None)
            positions.append(Position(symbol, Holding(kind), quantity, account, price, value,
                                      cost, description, option))
        source, name, as_of = head
        return PositionSnapshot(tuple(positions), datetime.strptime(as_of, _TIME_FORMAT),
                                source, name)
