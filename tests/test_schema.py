"""The store file's schema version and its backup before migrating (F1, D36),
and the price-basis migration (F3, D38)."""

import sqlite3
from datetime import datetime

import pandas as pd
import pytest

from quant_hub.data.types import PriceBasis
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


# The store's tables as they were before F3 (versions 0 and 1), spelled out:
# BarStore now creates version 2's.
_V1_TABLES = """
CREATE TABLE instruments (id INTEGER PRIMARY KEY, symbol TEXT NOT NULL,
    sec_type TEXT NOT NULL, exchange TEXT NOT NULL, currency TEXT NOT NULL,
    UNIQUE (symbol, sec_type, exchange, currency));
CREATE TABLE bars (instrument_id INTEGER NOT NULL REFERENCES instruments(id),
    source TEXT NOT NULL, bar_size TEXT NOT NULL, ts TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL NOT NULL CHECK (close > 0), volume REAL,
    PRIMARY KEY (instrument_id, source, bar_size, ts)) WITHOUT ROWID;
CREATE TABLE coverage (instrument_id INTEGER NOT NULL, source TEXT NOT NULL,
    bar_size TEXT NOT NULL, start_ts TEXT NOT NULL, end_ts TEXT NOT NULL);
"""


def seeded_store(path, version: int | None = None) -> int:
    """A store from before F3: version 0 (no schema_migrations) or 1."""
    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(_V1_TABLES)
        conn.execute("INSERT INTO instruments VALUES (1, 'AAA', 'STK', 'SMART', 'USD')")
        dates = pd.bdate_range("2025-01-02", periods=40)
        conn.executemany("INSERT INTO bars VALUES (1, 'ibkr', '1 day', ?, 10, 11, 9, 10.5, 1e6)",
                         [(f"{d:%Y-%m-%d} 00:00:00",) for d in dates])
        if version is not None:
            conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                         "applied_at TEXT NOT NULL, backup TEXT)")
            conn.execute("INSERT INTO schema_migrations VALUES (?, 'earlier', NULL)", (version,))
    conn.close()
    return len(dates)


def table_shapes(path) -> dict:
    """Each store table's columns (name, type, not null, key position)."""
    conn = sqlite3.connect(str(path))
    try:
        return {table: [(r[1], r[2], r[3], r[5])
                        for r in conn.execute(f"PRAGMA table_info({table})")]
                for table in ("bars", "refreshes", "instruments")}
    finally:
        conn.close()


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
        (1, "2026-10-15T19:30:05", str(result.backup)),
        (2, "2026-10-15T19:30:05", str(result.backup)),
        (3, "2026-10-15T19:30:05", str(result.backup)),
        (4, "2026-10-15T19:30:05", str(result.backup))]
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


def test_version_1_gains_the_price_basis_with_every_bar_traded(tmp_path):
    # F3, D38: the first migration that changes a table. Backed up first.
    db = tmp_path / "market_data.db"
    count = seeded_store(db, version=1)
    result = prepare_store(db, clock)
    assert (result.migrated_from, result.version) == (1, SCHEMA_VERSION)
    assert result.backup.name == "market_data.v1.20261015-193005.db"
    assert rows(result.backup, "SELECT COUNT(*) FROM bars") == [(count,)]
    assert rows(db, "SELECT basis, COUNT(*) FROM bars GROUP BY basis") == [("traded", count)]
    # The stores open the migrated file and keep the bases apart.
    store = BarStore(db)
    key = InstrumentKey("AAA")
    traded = store.query_bars(key, "ibkr", "1 day", datetime(2025, 1, 1), datetime(2025, 12, 31))
    assert len(traded) == count and traded["close"].iloc[0] == 10.5
    adjusted = traded * 0.99
    store.insert_bars(key, "ibkr", "1 day", adjusted, PriceBasis.ADJUSTED)
    back = store.query_bars(key, "ibkr", "1 day", datetime(2025, 1, 1), datetime(2025, 12, 31),
                            PriceBasis.ADJUSTED)
    assert back["close"].iloc[0] == pytest.approx(10.395)
    store.close()


def test_a_migrated_file_has_the_same_tables_as_a_new_one(tmp_path):
    old, new = tmp_path / "old.db", tmp_path / "new.db"
    seeded_store(old)
    prepare_store(old, clock)
    BarStore(old).close()
    prepare_store(new, clock)
    BarStore(new).close()
    migrated, fresh = table_shapes(old), table_shapes(new)
    assert migrated["bars"] == fresh["bars"]
    assert migrated["refreshes"] == fresh["refreshes"]
