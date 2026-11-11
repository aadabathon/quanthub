"""SEC facts as filed, and the ticker map, in the store's file (W5, D17).

* ``sec_facts``: every fact the mapping reads, per filer, exactly as filed:
  concept, unit, period, value, form, filing date, accession. A refetch
  replaces the filer's facts whole, so restatements arrive with it, and the
  latest-filed rule (``providers/sec_edgar.py``) can be re-run offline.
* ``sec_filers``: each filer's name and when its facts were fetched.
* ``sec_tickers``: the SEC's ticker map, so a ticker resolves offline.

Schema v4 (``storage/schema.py``) adds the three tables to older files,
after the backup. Every public method holds the connection lock, as the
other stores do.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from quant_hub.data.fundamentals import Fact
from quant_hub.storage.bar_store import StorageError

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Frozen v4 definitions, also used by the 3 -> 4 schema migration. An
# instant fact (no start) stores start as '', since key columns can't be NULL.
V4_TABLES = (
    "CREATE TABLE IF NOT EXISTS sec_filers (cik INTEGER PRIMARY KEY, name TEXT NOT NULL, "
    "fetched_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS sec_facts (cik INTEGER NOT NULL, taxonomy TEXT NOT NULL, "
    "concept TEXT NOT NULL, unit TEXT NOT NULL, start TEXT NOT NULL, end_date TEXT NOT NULL, "
    "value REAL NOT NULL, fiscal_year INTEGER, fiscal_period TEXT, form TEXT NOT NULL, "
    "filed TEXT NOT NULL, accession TEXT NOT NULL, "
    "PRIMARY KEY (cik, taxonomy, concept, unit, start, end_date, accession)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS sec_tickers (ticker TEXT PRIMARY KEY, cik INTEGER NOT NULL, "
    "name TEXT NOT NULL, fetched_at TEXT NOT NULL) WITHOUT ROWID",
)


class FundamentalsStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                for statement in V4_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the fundamentals store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- facts
    def save_facts(self, cik: int, name: str, facts: list[Fact], fetched_at: datetime) -> int:
        """Replace a filer's facts with ``facts`` (one transaction); the count kept."""
        rows = [(cik, f.taxonomy, f.concept, f.unit, f.start or "", f.end, f.value,
                 f.fiscal_year, f.fiscal_period, f.form, f.filed, f.accession) for f in facts]
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute("DELETE FROM sec_facts WHERE cik=?", (cik,))
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                    self._conn.execute(
                        "INSERT INTO sec_filers VALUES (?,?,?) ON CONFLICT(cik) DO UPDATE SET "
                        "name=excluded.name, fetched_at=excluded.fetched_at",
                        (cik, name, fetched_at.strftime(_TIME_FORMAT)))
                return int(self._conn.execute("SELECT COUNT(*) FROM sec_facts WHERE cik=?",
                                              (cik,)).fetchone()[0])
            except sqlite3.Error as exc:
                raise StorageError(f"could not save facts for CIK {cik}: {exc}") from exc

    def facts(self, cik: int) -> list[Fact]:
        rows = self._read("SELECT taxonomy, concept, unit, start, end_date, value, fiscal_year, "
                          "fiscal_period, form, filed, accession FROM sec_facts WHERE cik=? "
                          "ORDER BY concept, end_date, filed", (cik,))
        return [Fact(t, c, u, s or None, e, v, fy, fp, form, filed, accn)
                for t, c, u, s, e, v, fy, fp, form, filed, accn in rows]

    def filer(self, cik: int) -> tuple[str, datetime] | None:
        """The filer's name and when its facts were fetched, if ever."""
        rows = self._read("SELECT name, fetched_at FROM sec_filers WHERE cik=?", (cik,))
        if not rows:
            return None
        return rows[0][0], datetime.strptime(rows[0][1], _TIME_FORMAT)

    # -------------------------------------------------------------- tickers
    def save_tickers(self, tickers: dict[str, tuple[int, str]], fetched_at: datetime) -> None:
        stamp = fetched_at.strftime(_TIME_FORMAT)
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute("DELETE FROM sec_tickers")
                    self._conn.executemany(
                        "INSERT OR REPLACE INTO sec_tickers VALUES (?,?,?,?)",
                        [(t.upper(), cik, name, stamp) for t, (cik, name) in tickers.items()])
            except sqlite3.Error as exc:
                raise StorageError(f"could not save the ticker map: {exc}") from exc

    def ticker(self, symbol: str) -> tuple[int, str] | None:
        rows = self._read("SELECT cik, name FROM sec_tickers WHERE ticker=?", (symbol.upper(),))
        return (int(rows[0][0]), rows[0][1]) if rows else None

    def ticker_for(self, cik: int) -> str | None:
        """A filer's first listed ticker, alphabetically, for display."""
        rows = self._read("SELECT ticker FROM sec_tickers WHERE cik=? ORDER BY ticker LIMIT 1",
                          (cik,))
        return rows[0][0] if rows else None

    def tickers_fetched_at(self) -> datetime | None:
        rows = self._read("SELECT MIN(fetched_at) FROM sec_tickers")
        return datetime.strptime(rows[0][0], _TIME_FORMAT) if rows and rows[0][0] else None

    def _read(self, sql: str, arguments: tuple = ()) -> list[tuple]:
        with self._lock:
            try:
                return self._conn.execute(sql, arguments).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"fundamentals read failed: {exc}") from exc
