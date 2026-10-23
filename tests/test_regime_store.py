"""The regime store (W7): versions stored once by their text, immutable
reviews with editable notes, hand-placed trades, and the v5 → v6 migration."""

import sqlite3
from datetime import datetime

import pytest

from quant_hub.storage.bar_store import StorageError
from quant_hub.storage.regime_store import RegimeStore, source_hash
from quant_hub.storage.schema import SCHEMA_VERSION, prepare_store

NOW = datetime(2026, 10, 23, 9, 30)


@pytest.fixture()
def store():
    s = RegimeStore(":memory:")
    yield s
    s.close()


def test_the_same_text_is_one_version(store):
    first, new = store.store_version("rules v1", "1", "2026-10-22", "first", NOW)
    again, new_again = store.store_version("rules v1", "1", "2026-10-22", "first", NOW)
    second, _ = store.store_version("rules v2", "2", "2026-10-23", "tighter", NOW)
    assert (new, new_again, again) == (True, False, first) and second != first
    assert [v.version for v in store.versions()] == ["2", "1"]          # newest first
    assert store.version(first).text == "rules v1"
    assert source_hash("rules v1") != source_hash("rules v1 ")
    with pytest.raises(StorageError, match="no regime version #99"):
        store.version(99)


def test_reviews_are_kept_whole_and_only_their_notes_change(store):
    vid, _ = store.store_version("rules", "1", "2026-10-22", "first", NOW)
    early = store.save_review(datetime(2026, 10, 22, 17), vid, "2026-10-21", {"checks": [1]})
    late = store.save_review(NOW, vid, "2026-10-21", {"checks": [2]}, notes="calm day")
    assert [r.id for r in store.reviews()] == [late, early]
    assert [r.id for r in store.reviews(limit=1)] == [late]
    store.save_notes(early, "looked again")
    record = store.review(early)
    assert (record.payload, record.notes, record.version_id) == ({"checks": [1]},
                                                                 "looked again", vid)
    with pytest.raises(StorageError, match="could not save the review"):
        store.save_review(NOW, vid, "2026-10-21", {"x": float("nan")})


def test_trades_are_kept_against_their_review(store):
    vid, _ = store.store_version("rules", "1", "2026-10-22", "first", NOW)
    rid = store.save_review(NOW, vid, "2026-10-21", {})
    store.add_trade(rid, NOW, "AAA", 10.0, 101.5, "trimmed to the cap", "4.max-weight")
    store.add_trade(None, datetime(2026, 10, 24), "BBB", -5.0, None, "closed", "")
    assert [t.symbol for t in store.trades()] == ["BBB", "AAA"]
    (trade,) = store.trades(rid)
    assert (trade.quantity, trade.price, trade.rule) == (10.0, 101.5, "4.max-weight")


def test_a_v5_store_migrates_to_v6_with_a_backup(tmp_path):
    path = tmp_path / "market_data.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                     "applied_at TEXT NOT NULL, backup TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES (5, 'earlier', NULL)")
    result = prepare_store(path)
    assert (result.migrated_from, result.version) == (5, SCHEMA_VERSION) and result.backup
    with sqlite3.connect(path) as conn:
        names = {n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"regime_versions", "reviews", "trades"} <= names
    store = RegimeStore(path)
    vid, _ = store.store_version("rules", "1", "2026-10-22", "first", NOW)
    assert store.review(store.save_review(NOW, vid, "2026-10-21", {"ok": True})).payload == {
        "ok": True}
    store.close()
