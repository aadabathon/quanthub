"""The regime's versions and the review journal (W7).

* **Versions.** Each distinct text of the owner's ``REGIME.md`` is stored
  once, by its SHA-256, with the header's version, date and reason. A
  version is never rewritten, so a review always says which rules it ran
  under.
* **Reviews.** One immutable record per run: when it ran, under which
  version, the snapshot it read, and what it found (JSON, opaque here: the
  store knows no model or service types). Notes are the one editable field.
* **Trades.** What the owner placed by hand, against the review that
  prompted it: symbol, quantity, price, the reason, and the rule it
  serves. Nothing here places an order (D12).

Schema v6 (``storage/schema.py``) adds the tables to older files, after
the backup. Every public method holds the connection lock.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quant_hub.storage.bar_store import StorageError

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Frozen v6 definition, also used by the 5 -> 6 schema migration.
V6_TABLES = (
    "CREATE TABLE IF NOT EXISTS regime_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "stored_at TEXT NOT NULL, source_hash TEXT NOT NULL UNIQUE, version TEXT NOT NULL, "
    "written TEXT NOT NULL, reason TEXT NOT NULL, text TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "run_at TEXT NOT NULL, version_id INTEGER NOT NULL REFERENCES regime_versions(id), "
    "snapshot_as_of TEXT NOT NULL, payload TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "review_id INTEGER REFERENCES reviews(id), written_at TEXT NOT NULL, "
    "symbol TEXT NOT NULL, quantity REAL NOT NULL, price REAL, reason TEXT NOT NULL, "
    "rule TEXT NOT NULL DEFAULT '')",
)


@dataclass(frozen=True)
class RegimeVersion:
    id: int
    stored_at: datetime
    version: str
    written: str
    reason: str
    text: str


@dataclass(frozen=True)
class ReviewRecord:
    id: int
    run_at: datetime
    version_id: int
    snapshot_as_of: str
    payload: dict
    notes: str


@dataclass(frozen=True)
class TradeRecord:
    id: int
    review_id: int | None
    written_at: datetime
    symbol: str
    quantity: float
    price: float | None
    reason: str
    rule: str


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RegimeStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                for statement in V6_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the regime store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- versions
    def store_version(self, text: str, version: str, written: str, reason: str,
                      stored_at: datetime) -> tuple[int, bool]:
        """(id, whether it's new): the same text is stored once."""
        digest = source_hash(text)
        with self._lock:
            try:
                row = self._conn.execute("SELECT id FROM regime_versions WHERE source_hash=?",
                                         (digest,)).fetchone()
                if row:
                    return int(row[0]), False
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO regime_versions (stored_at, source_hash, version, written, "
                        "reason, text) VALUES (?,?,?,?,?,?)",
                        (stored_at.strftime(_TIME_FORMAT), digest, version, written, reason,
                         text))
                return int(cursor.lastrowid), True
            except sqlite3.Error as exc:
                raise StorageError(f"could not store the regime version: {exc}") from exc

    def versions(self) -> list[RegimeVersion]:
        """Every stored version, newest first."""
        return [self._version(r) for r in self._read(
            "SELECT id, stored_at, version, written, reason, text FROM regime_versions "
            "ORDER BY id DESC")]

    def version(self, version_id: int) -> RegimeVersion:
        rows = self._read("SELECT id, stored_at, version, written, reason, text "
                          "FROM regime_versions WHERE id=?", (version_id,))
        if not rows:
            raise StorageError(f"no regime version #{version_id}")
        return self._version(rows[0])

    # ----------------------------------------------------------------- reviews
    def save_review(self, run_at: datetime, version_id: int, snapshot_as_of: str,
                    payload: dict, notes: str = "") -> int:
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO reviews (run_at, version_id, snapshot_as_of, payload, notes) "
                        "VALUES (?,?,?,?,?)",
                        (run_at.strftime(_TIME_FORMAT), version_id, snapshot_as_of,
                         json.dumps(payload, allow_nan=False), notes))
                return int(cursor.lastrowid)
            except (sqlite3.Error, ValueError) as exc:
                raise StorageError(f"could not save the review: {exc}") from exc

    def reviews(self, limit: int | None = None) -> list[ReviewRecord]:
        """The reviews, newest first (all of them without ``limit``)."""
        sql = ("SELECT id, run_at, version_id, snapshot_as_of, payload, notes FROM reviews "
               "ORDER BY run_at DESC, id DESC")
        rows = self._read(sql + " LIMIT ?", (limit,)) if limit is not None else self._read(sql)
        return [self._review(r) for r in rows]

    def review(self, review_id: int) -> ReviewRecord:
        rows = self._read("SELECT id, run_at, version_id, snapshot_as_of, payload, notes "
                          "FROM reviews WHERE id=?", (review_id,))
        if not rows:
            raise StorageError(f"no review #{review_id}")
        return self._review(rows[0])

    def save_notes(self, review_id: int, notes: str) -> None:
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute("UPDATE reviews SET notes=? WHERE id=?", (notes, review_id))
            except sqlite3.Error as exc:
                raise StorageError(f"could not save notes: {exc}") from exc

    # ------------------------------------------------------------------ trades
    def add_trade(self, review_id: int | None, written_at: datetime, symbol: str,
                  quantity: float, price: float | None, reason: str, rule: str = "") -> int:
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO trades (review_id, written_at, symbol, quantity, price, "
                        "reason, rule) VALUES (?,?,?,?,?,?,?)",
                        (review_id, written_at.strftime(_TIME_FORMAT), symbol, quantity, price,
                         reason, rule))
                return int(cursor.lastrowid)
            except sqlite3.Error as exc:
                raise StorageError(f"could not save the trade: {exc}") from exc

    def trades(self, review_id: int | None = None) -> list[TradeRecord]:
        """Trades, newest first: every one, or one review's."""
        sql = ("SELECT id, review_id, written_at, symbol, quantity, price, reason, rule "
               "FROM trades")
        rows = (self._read(sql + " WHERE review_id=? ORDER BY written_at DESC, id DESC",
                           (review_id,)) if review_id is not None
                else self._read(sql + " ORDER BY written_at DESC, id DESC"))
        return [TradeRecord(int(i), None if r is None else int(r),
                            datetime.strptime(w, _TIME_FORMAT), s, float(q),
                            None if p is None else float(p), reason, rule)
                for i, r, w, s, q, p, reason, rule in rows]

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _version(row: tuple) -> RegimeVersion:
        vid, stored_at, version, written, reason, text = row
        return RegimeVersion(int(vid), datetime.strptime(stored_at, _TIME_FORMAT), version,
                             written, reason, text)

    @staticmethod
    def _review(row: tuple) -> ReviewRecord:
        rid, run_at, version_id, as_of, payload, notes = row
        return ReviewRecord(int(rid), datetime.strptime(run_at, _TIME_FORMAT), int(version_id),
                            as_of, json.loads(payload), notes)

    def _read(self, sql: str, arguments: tuple = ()) -> list[tuple]:
        with self._lock:
            try:
                return self._conn.execute(sql, arguments).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"regime read failed: {exc}") from exc
