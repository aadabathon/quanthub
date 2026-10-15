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

Version 0 is every file from before versioning (it has tables but no
``schema_migrations``). Version 1 is the schema as of F1. The 0 → 1 step
changes no table; it only adopts the file, so the first run after F1
leaves a backup of every existing store. Backups are never deleted
automatically: they're the only copy of history IBKR won't serve again.
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

SCHEMA_VERSION = 1
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


# Step from version v to v + 1, keyed by v.
_STEPS: dict[int, Callable[[sqlite3.Connection], None]] = {0: _adopt}


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
