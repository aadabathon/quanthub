"""Saved valuations: immutable records, like research runs (W6, D48).

A record holds what a valuation was made of and what it said: the
assumptions, the filing data's fingerprint, the price and its provenance,
and the result. It is never rewritten. Reopening compares the fingerprint
with today's filing data, so a restatement or a new 10-K since shows.
Notes are the one editable field. JSON is opaque here: the store knows no
model or service types (layering).

Schema v5 (``storage/schema.py``) adds the table to older files, after the
backup. Every public method holds the connection lock.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quant_hub.storage.bar_store import StorageError

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Frozen v5 definition, also used by the 4 -> 5 schema migration.
V5_TABLES = (
    "CREATE TABLE IF NOT EXISTS valuations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "saved_at TEXT NOT NULL, ticker TEXT NOT NULL, company TEXT NOT NULL, "
    "payload TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '')",
)


@dataclass(frozen=True)
class SavedValuation:
    id: int
    saved_at: datetime
    ticker: str
    company: str
    payload: dict
    notes: str


class ValuationStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                for statement in V5_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the valuation store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, ticker: str, company: str, payload: dict, saved_at: datetime,
             notes: str = "") -> int:
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO valuations (saved_at, ticker, company, payload, notes) "
                        "VALUES (?,?,?,?,?)",
                        (saved_at.strftime(_TIME_FORMAT), ticker, company,
                         json.dumps(payload, allow_nan=False), notes))
                return int(cursor.lastrowid)
            except (sqlite3.Error, ValueError) as exc:
                raise StorageError(f"could not save the valuation: {exc}") from exc

    def saved(self) -> list[SavedValuation]:
        """Every record, newest first."""
        return [self._row(r) for r in self._read(
            "SELECT id, saved_at, ticker, company, payload, notes FROM valuations "
            "ORDER BY saved_at DESC, id DESC")]

    def load(self, valuation_id: int) -> SavedValuation:
        rows = self._read("SELECT id, saved_at, ticker, company, payload, notes FROM valuations "
                          "WHERE id=?", (valuation_id,))
        if not rows:
            raise StorageError(f"no saved valuation #{valuation_id}")
        return self._row(rows[0])

    def save_notes(self, valuation_id: int, notes: str) -> None:
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute("UPDATE valuations SET notes=? WHERE id=?",
                                       (notes, valuation_id))
            except sqlite3.Error as exc:
                raise StorageError(f"could not save notes: {exc}") from exc

    @staticmethod
    def _row(row: tuple) -> SavedValuation:
        vid, saved_at, ticker, company, payload, notes = row
        return SavedValuation(int(vid), datetime.strptime(saved_at, _TIME_FORMAT), ticker,
                              company, json.loads(payload), notes)

    def _read(self, sql: str, arguments: tuple = ()) -> list[tuple]:
        with self._lock:
            try:
                return self._conn.execute(sql, arguments).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"valuation read failed: {exc}") from exc
