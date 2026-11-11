"""Tests for the SQLite view-state store (quant_hub.storage.state_store).

Every test uses an in-memory database; no network, no GUI. The important
guarantees: unsaved views return an empty dict, save/load roundtrips
arbitrary JSON-serializable shapes, a later save replaces (not merges) the
previous state, and state survives reopening the same file.
"""

import json

import pytest

from quant_hub.storage.state_store import ViewStateStore


@pytest.fixture
def store():
    s = ViewStateStore(":memory:")
    yield s
    s.close()


class TestLoad:
    def test_unknown_view_returns_empty_dict(self, store):
        assert store.load("markets") == {}

    def test_corrupt_row_returns_empty_dict(self, store):
        with store._conn:
            store._conn.execute(
                "INSERT INTO view_state (view, state_json, updated_at)"
                " VALUES ('markets', 'not json', '2026-01-01 00:00:00')"
            )
        assert store.load("markets") == {}


class TestSaveAndLoad:
    def test_roundtrip(self, store):
        state = {"symbols": ["SPY", "QQQ"], "source": "ibkr"}
        store.save("markets", state)
        assert store.load("markets") == state

    def test_roundtrip_nested_and_numeric(self, store):
        state = {"s0": 100.5, "days": 252, "seed": None, "window": {"n": 21}}
        store.save("simulator", state)
        assert store.load("simulator") == state

    def test_save_replaces_previous_state(self, store):
        store.save("markets", {"symbols": ["SPY"], "source": "ibkr"})
        store.save("markets", {"symbols": ["AAPL"], "source": "synthetic"})
        assert store.load("markets") == {"symbols": ["AAPL"], "source": "synthetic"}

    def test_views_are_independent(self, store):
        store.save("markets", {"a": 1})
        store.save("simulator", {"b": 2})
        assert store.load("markets") == {"a": 1}
        assert store.load("simulator") == {"b": 2}

    def test_empty_state_roundtrips(self, store):
        store.save("markets", {})
        assert store.load("markets") == {}


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        db_path = tmp_path / "state.db"
        first = ViewStateStore(db_path)
        first.save("markets", {"symbols": ["SPY"]})
        first.close()

        second = ViewStateStore(db_path)
        try:
            assert second.load("markets") == {"symbols": ["SPY"]}
        finally:
            second.close()

    def test_creates_parent_directory(self, tmp_path):
        db_path = tmp_path / "nested" / "dir" / "state.db"
        s = ViewStateStore(db_path)
        try:
            s.save("markets", {"symbols": ["SPY"]})
            assert db_path.exists()
        finally:
            s.close()


class TestStoredShape:
    def test_state_is_stored_as_json_text(self, store):
        store.save("markets", {"symbols": ["SPY"]})
        (raw,) = store._conn.execute(
            "SELECT state_json FROM view_state WHERE view='markets'"
        ).fetchone()
        assert json.loads(raw) == {"symbols": ["SPY"]}
