"""The store file's schema version and its backup before migrating (F1, D36)."""

import sqlite3
from datetime import datetime

import pandas as pd
import pytest

from quant_hub.storage import schema
from quant_hub.storage.bar_store import BarStore, InstrumentKey, StorageError
from quant_hub.storage.schema import SCHEMA_VERSION, prepare_store

NOW = datetime(2026, 10, 15, 19, 30, 5)


def clock():
    return NOW


def rows(path, sql):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def seeded_store(path) -> int:
    """A store from before versioning: bars, no schema_migrations."""
    store = BarStore(path)
    dates = pd.bdate_range("2025-01-02", periods=40)
    frame = pd.DataFrame({"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                          "volume": 1e6}, index=dates)
    store.insert_bars(InstrumentKey("AAA"), "ibkr", "1 day", frame)
    store.close()
    return len(dates)


def test_a_new_file_records_the_current_version_and_needs_no_backup(tmp_path):
    db = tmp_path / "market_data.db"
    result = prepare_store(db, clock)
    assert (result.version, result.migrated_from, result.backup) == (SCHEMA_VERSION, None, None)
    assert rows(db, "SELECT version, backup FROM schema_migrations") == [(SCHEMA_VERSION, None)]
    assert not (tmp_path / "backups").exists()
    BarStore(db).close()                             # the stores then create their tables


def test_a_file_from_before_versioning_is_backed_up_then_adopted(tmp_path):
    db = tmp_path / "market_data.db"
    count = seeded_store(db)
    result = prepare_store(db, clock)
    assert result.migrated_from == 0 and result.version == SCHEMA_VERSION
    assert result.backup == tmp_path / "backups" / "market_data.v0.20261015-193005.db"
    # The backup is the whole store as it was, and the migration row names it.
    assert rows(result.backup, "SELECT COUNT(*) FROM bars") == [(count,)]
    assert rows(result.backup, "SELECT name FROM sqlite_master WHERE name = "
                               "'schema_migrations'") == []
    assert rows(db, "SELECT version, applied_at, backup FROM schema_migrations") == [
        (1, "2026-10-15T19:30:05", str(result.backup))]
    assert rows(db, "SELECT COUNT(*) FROM bars") == [(count,)]
    # Up to date now: the next start changes nothing and copies nothing.
    again = prepare_store(db, clock)
    assert again.migrated_from is None and again.backup is None
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_backups_never_overwrite_each_other(tmp_path):
    db = tmp_path / "market_data.db"
    seeded_store(db)
    conn = sqlite3.connect(str(db))
    try:
        first = schema._backup(conn, db, 0, NOW)
        second = schema._backup(conn, db, 0, NOW)       # same second, same version
    finally:
        conn.close()
    assert first != second and first.exists() and second.exists()


def test_a_file_from_a_newer_app_is_refused_untouched(tmp_path):
    db = tmp_path / "market_data.db"
    prepare_store(db, clock)
    conn = sqlite3.connect(str(db))
    with conn:
        conn.execute("INSERT INTO schema_migrations VALUES (?, 'later', NULL)",
                     (SCHEMA_VERSION + 1,))
    conn.close()
    with pytest.raises(StorageError, match="newer Quant Hub"):
        prepare_store(db, clock)
    assert not (tmp_path / "backups").exists()


def test_a_failed_step_rolls_back_and_keeps_the_backup(tmp_path, monkeypatch):
    db = tmp_path / "market_data.db"
    seeded_store(db)

    def broken(conn):
        conn.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("step blew up")

    monkeypatch.setitem(schema._STEPS, 0, broken)
    with pytest.raises(StorageError, match="unchanged at v0"):
        prepare_store(db, clock)
    names = {name for (name,) in rows(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "half_done" not in names and "schema_migrations" not in names   # all rolled back
    assert len(list((tmp_path / "backups").iterdir())) == 1


def test_memory_stores_skip_versioning():
    assert prepare_store(":memory:").migrated_from is None
