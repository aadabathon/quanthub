"""Daily copies of the store (D108).

The desk's record can't be rebuilt: a forecast made today can't honestly be
made again tomorrow. So the daily run copies the whole store, with
SQLite's online backup (consistent even while the app has it open), to a
folder of dated copies, and keeps the newest ``keep`` of them.

A folder beside the store survives a bad write or a mistaken delete, not
a dead disk. A folder on another disk, or one a cloud drive syncs,
survives that too: ``[backup] folder`` in settings.toml.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def backup_store(db_path: Path, folder: Path, now: datetime, keep: int = 14) -> Path:
    """Copy ``db_path`` to ``folder`` as ``<name>-YYYYMMDD-HHMMSS.db`` and
    remove all but the newest ``keep`` copies; returns the new copy."""
    if keep < 1:
        raise ValueError("keep at least one copy")
    if not db_path.is_file():
        raise FileNotFoundError(f"no store at {db_path}")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{db_path.stem}-{now:%Y%m%d-%H%M%S}.db"
    source = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        copy = sqlite3.connect(str(target))
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        source.close()
    copies = sorted(folder.glob(f"{db_path.stem}-[0-9]*-[0-9]*.db"))
    for old in copies[:-keep]:
        old.unlink()
    return target
