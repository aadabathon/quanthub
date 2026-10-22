"""The store file's schema version, and a backup before any change to it
(F1, D36).

Every store (bars, view state, option chains) lives in one SQLite file
(D17), and each creates its own tables with ``CREATE TABLE IF NOT EXISTS``.
That defines the *current* schema for a new file, but it can't change an
existing one. So the file carries one version for all of them, in a
``schema_migrations`` table (a row per step applied: the version reached,
when, and the backup taken first). ``prepare_store`` runs before any store
opens the file:

* **New file:** nothing to migrate. It records ``SCHEMA_VERSION``, and the
  stores then create their tables.
* **Older file:** it first copies the whole database into ``backups/``
  beside it, with SQLite's online backup API (a consistent snapshot, even
  of a file another connection has open). Then it applies each step in
  order, each in its own transaction, and records it. A failed step rolls
  back and raises: the file stays at the last good version, and the backup
  stays too.
* **Newer file** (written by a later Quant Hub): it refuses, rather than
  let this version's stores write into a schema they don't understand.

Versions:

* **0:** every file from before versioning (tables, no ``schema_migrations``).
* **1** (F1): the 0 → 1 step changes no table. It only adopts the file, so
  the first run after F1 leaves a backup of every existing store.
* **2** (F3, D38): bars gain a price ``basis`` in their key, and the
  ``refreshes`` table appears. SQLite can't change a primary key in place,
  so the step rebuilds ``bars``: every existing bar is ``traded``, which is
  what IBKR's TRADES and every earlier import are.
* **3** (R3, D42): the research tables (``research_runs``,
  ``research_papers``).
* **4** (W5, D47): SEC facts as filed, their filers, and the ticker map
  (``sec_facts``, ``sec_filers``, ``sec_tickers``).
* **5** (W6, D48): saved valuations (``valuations``).

Steps are frozen: each one spells out its own SQL, never the stores'
current schema, which keeps moving. A test holds a migrated file and a new
one to the same tables.

Backups are never deleted automatically: they're the only copy of history
IBKR won't serve again.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quant_hub.storage.bar_store import StorageError

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5
BACKUP_DIR = "backups"

_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    backup     TEXT
);
"""


def _adopt(conn: sqlite3.Connection) -> None:
    """0 -> 1: the pre-versioning tables already are version 1."""


_BARS_V2 = """
CREATE TABLE bars_v2 (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    source        TEXT NOT NULL,
    bar_size      TEXT NOT NULL,
    basis         TEXT NOT NULL CHECK (basis IN ('traded', 'adjusted')),
    ts            TEXT NOT NULL,
    open   REAL CHECK (open   IS NULL OR open  > 0),
    high   REAL CHECK (high   IS NULL OR high  > 0),
    low    REAL CHECK (low    IS NULL OR low   > 0),
    close  REAL NOT NULL CHECK (close > 0),
    volume REAL CHECK (volume IS NULL OR volume >= 0),
    CHECK (high IS NULL OR low IS NULL OR high >= low),
    PRIMARY KEY (instrument_id, source, bar_size, basis, ts)
) WITHOUT ROWID
"""

_REFRESHES_V2 = """
CREATE TABLE IF NOT EXISTS refreshes (
    instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    source        TEXT NOT NULL,
    bar_size      TEXT NOT NULL,
    basis         TEXT NOT NULL,
    refreshed_on  TEXT NOT NULL,
    PRIMARY KEY (instrument_id, source, bar_size, basis)
) WITHOUT ROWID
"""


def _price_basis(conn: sqlite3.Connection) -> None:
    """1 -> 2: bars gain a basis in their key; every stored bar is traded."""
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "bars" in tables:
        conn.execute(_BARS_V2)
        conn.execute(
            "INSERT INTO bars_v2 (instrument_id, source, bar_size, basis, ts, open, high,"
            " low, close, volume) SELECT instrument_id, source, bar_size, 'traded', ts,"
            " open, high, low, close, volume FROM bars")
        conn.execute("DROP TABLE bars")
        conn.execute("ALTER TABLE bars_v2 RENAME TO bars")
    if "instruments" in tables:
        conn.execute(_REFRESHES_V2)


# Step from version v to v + 1, keyed by v.
def _research(conn: sqlite3.Connection) -> None:
    """2 -> 3: append-only experiments and a local paper catalog."""
    from quant_hub.storage.research_store import V3_TABLES
    for statement in V3_TABLES:
        conn.execute(statement)


def _fundamentals(conn: sqlite3.Connection) -> None:
    """3 -> 4: SEC facts as filed, their filers, and the ticker map (W5)."""
    from quant_hub.storage.fundamentals_store import V4_TABLES
    for statement in V4_TABLES:
        conn.execute(statement)


def _valuations(conn: sqlite3.Connection) -> None:
    """4 -> 5: saved valuations, immutable records (W6)."""
    from quant_hub.storage.valuation_store import V5_TABLES
    for statement in V5_TABLES:
        conn.execute(statement)


_STEPS: dict[int, Callable[[sqlite3.Connection], None]] = {
    0: _adopt, 1: _price_basis, 2: _research, 3: _fundamentals, 4: _valuations,
}


@dataclass(frozen=True)
class StoreSchema:
    """What ``prepare_store`` found and did."""

    version: int                   # the file's version now
    migrated_from: int | None      # None: nothing needed doing
    backup: Path | None            # the copy taken before migrating


def prepare_store(db_path: str | Path,
                  now: Callable[[], datetime] = datetime.now) -> StoreSchema:
    """Bring the file at ``db_path`` to ``SCHEMA_VERSION`` (module docstring)."""
    if str(db_path) == ":memory:":
        return StoreSchema(SCHEMA_VERSION, None, None)
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Autocommit mode: each transaction is opened and closed explicitly
        # below, so table changes are transactional too.
        conn = sqlite3.connect(str(path), isolation_level=None)
    except sqlite3.Error as exc:
        raise StorageError(f"could not open {path}: {exc}") from exc
    try:
        found = _version(conn)
        if found is None:
            _transaction(conn, lambda: _record(conn, SCHEMA_VERSION, now(), None))
            return StoreSchema(SCHEMA_VERSION, None, None)
        if found > SCHEMA_VERSION:
            raise StorageError(
                f"{path} has schema version {found}, written by a newer Quant Hub; this "
                f"version understands up to {SCHEMA_VERSION}. Update the app, or point it "
                "at another store in settings.toml.")
        if found == SCHEMA_VERSION:
            return StoreSchema(found, None, None)
        backup = _backup(conn, path, found, now())
        _logger.info("store backed up to %s before migrating schema v%d -> v%d",
                     backup, found, SCHEMA_VERSION)
        for version in range(found, SCHEMA_VERSION):
            def step(v: int = version) -> None:
                _STEPS[v](conn)
                _record(conn, v + 1, now(), backup)
            try:
                _transaction(conn, step)
            except Exception as exc:
                raise StorageError(
                    f"migrating {path.name} from schema v{version} to v{version + 1} failed "
                    f"({exc}); the file is unchanged at v{version}, and a backup is at "
                    f"{backup}") from exc
        return StoreSchema(SCHEMA_VERSION, found, backup)
    except sqlite3.Error as exc:
        raise StorageError(f"could not prepare {path}: {exc}") from exc
    finally:
        conn.close()


def _version(conn: sqlite3.Connection) -> int | None:
    """The file's version; None for a new (empty) file."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    if not tables:
        return None
    if "schema_migrations" not in tables:
        return 0
    (version,) = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return 0 if version is None else int(version)


def _transaction(conn: sqlite3.Connection, work: Callable[[], None]) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        work()
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _record(conn: sqlite3.Connection, version: int, at: datetime, backup: Path | None) -> None:
    conn.execute(_TABLE)
    conn.execute("INSERT INTO schema_migrations (version, applied_at, backup) VALUES (?, ?, ?)",
                 (version, at.isoformat(timespec="seconds"),
                  str(backup) if backup is not None else None))


def _backup(conn: sqlite3.Connection, path: Path, version: int, at: datetime) -> Path:
    """A consistent copy of the whole file, named for its version and time."""
    folder = path.parent / BACKUP_DIR
    folder.mkdir(exist_ok=True)
    stem = f"{path.stem}.v{version}.{at:%Y%m%d-%H%M%S}"
    target = folder / f"{stem}{path.suffix}"
    counter = 1
    while target.exists():
        target = folder / f"{stem}-{counter}{path.suffix}"
        counter += 1
    copy = sqlite3.connect(str(target))
    try:
        conn.backup(copy)
    finally:
        copy.close()
    return target
