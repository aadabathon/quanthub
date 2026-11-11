"""SEC facts in the store and the fundamentals service (W5): round trips, the
v3 -> v4 migration, caching and refresh, the identity, and a failed fetch."""

import sqlite3
from datetime import datetime, timedelta

import pytest

from quant_hub.data.fundamentals import Fact
from quant_hub.providers.sec_edgar import SecError, facts_from_companyfacts
from quant_hub.services.fundamentals import FACTS_STALE_AFTER, FundamentalsService
from quant_hub.storage.fundamentals_store import FundamentalsStore
from quant_hub.storage.schema import SCHEMA_VERSION, prepare_store
from test_sec_edgar import companyfacts

T0 = datetime(2026, 10, 21, 9, 0, 0)


@pytest.fixture
def store():
    s = FundamentalsStore(":memory:")
    yield s
    s.close()


# ---------------------------------------------------------------------- store
class TestStore:
    def test_facts_round_trip_and_a_refetch_replaces_them(self, store):
        facts = facts_from_companyfacts(companyfacts())
        kept = store.save_facts(1234, "DEMO Corp", facts, T0)
        assert kept == len(facts)
        back = store.facts(1234)
        assert sorted(back, key=repr) == sorted(facts, key=repr)
        assert any(f.start is None for f in back)                # instants survive as None
        assert store.filer(1234) == ("DEMO Corp", T0)
        one = [Fact("us-gaap", "Revenues", "USD", "2024-09-29", "2025-09-27", 1.0, 2025, "FY",
                    "10-K", "2025-10-31", "A")]
        store.save_facts(1234, "DEMO Corp.", one, T0 + timedelta(days=2))
        assert store.facts(1234) == one and store.filer(1234)[0] == "DEMO Corp."
        assert store.filer(99) is None

    def test_the_ticker_map(self, store):
        store.save_tickers({"DEMO": (1234, "DEMO Corp"), "DEMOB": (1234, "DEMO Corp")}, T0)
        assert store.ticker("demo") == (1234, "DEMO Corp")
        assert store.ticker_for(1234) == "DEMO" and store.ticker("NONE") is None
        assert store.tickers_fetched_at() == T0

    def test_a_v3_store_migrates_with_a_backup(self, tmp_path):
        path = tmp_path / "market_data.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
                         "applied_at TEXT NOT NULL, backup TEXT)")
            conn.execute("INSERT INTO schema_migrations VALUES (3, 'earlier', NULL)")
            conn.execute("CREATE TABLE owner_data (note TEXT)")
            conn.execute("INSERT INTO owner_data VALUES ('keep')")
        result = prepare_store(path)
        assert (result.migrated_from, result.version) == (3, SCHEMA_VERSION)
        with sqlite3.connect(path) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert {"sec_facts", "sec_filers", "sec_tickers"} <= tables
            assert conn.execute("SELECT note FROM owner_data").fetchone()[0] == "keep"
        with sqlite3.connect(result.backup) as conn:
            assert not conn.execute("SELECT name FROM sqlite_master "
                                    "WHERE name='sec_facts'").fetchall()
        migrated = FundamentalsStore(path)                        # and the store opens on it
        assert migrated.facts(1) == []
        migrated.close()


# -------------------------------------------------------------------- service
class FakeSec:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    def tickers(self):
        self.calls.append("tickers")
        if self.fail:
            raise SecError("couldn't reach the SEC: offline")
        return {"DEMO": (1234, "DEMO Corp")}

    def companyfacts(self, cik):
        self.calls.append(f"facts {cik}")
        if self.fail:
            raise SecError("couldn't reach the SEC: offline")
        return companyfacts()


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def service(store, sec, clock, identity="Test Person test@example.com"):
    return FundamentalsService(store, identity, client_factory=lambda _identity: sec, now=clock)


class TestService:
    def test_first_look_fetches_then_the_store_serves(self, store):
        sec, clock = FakeSec(), Clock()
        svc = service(store, sec, clock)
        hist = svc.history("demo")
        assert sec.calls == ["tickers", "facts 1234"]
        assert hist.ticker == "DEMO" and hist.company == "DEMO Corp"
        assert hist.latest().revenue == 1000.0
        assert "fetched 2026-10-21 09:00" in hist.sources["data"]
        clock.now = T0 + timedelta(hours=6)
        svc.history("DEMO")
        assert sec.calls == ["tickers", "facts 1234"]               # served from the store

    def test_a_day_old_copy_or_refresh_fetches_again(self, store):
        sec, clock = FakeSec(), Clock()
        svc = service(store, sec, clock)
        svc.history("DEMO")
        clock.now = T0 + FACTS_STALE_AFTER + timedelta(minutes=1)
        svc.history("DEMO")
        assert sec.calls.count("facts 1234") == 2 and sec.calls.count("tickers") == 1
        svc.history("DEMO", refresh=True)
        assert sec.calls.count("facts 1234") == 3

    def test_a_cik_needs_no_ticker_map(self, store):
        sec = FakeSec()
        hist = service(store, sec, Clock()).history("CIK0000001234")
        assert sec.calls == ["facts 1234"] and hist.cik == "1234"

    def test_an_unknown_ticker_says_to_try_the_cik(self, store):
        with pytest.raises(ValueError, match="try its CIK"):
            service(store, FakeSec(), Clock()).history("NOPE")

    def test_no_identity_explains_itself_unless_the_store_can_serve(self, store):
        with pytest.raises(SecError, match=r"\[sec\] identity"):
            service(store, FakeSec(), Clock(), identity="").history("DEMO")
        store.save_tickers({"DEMO": (1234, "DEMO Corp")}, T0)
        store.save_facts(1234, "DEMO Corp", facts_from_companyfacts(companyfacts()), T0)
        offline = service(store, FakeSec(), Clock(), identity="")
        assert not offline.has_identity
        assert offline.history("DEMO").latest().revenue == 1000.0

    def test_a_failed_refresh_serves_the_stored_copy_and_says_so(self, store):
        clock = Clock()
        service(store, FakeSec(), clock).history("DEMO")
        clock.now = T0 + timedelta(days=3)
        hist = service(store, FakeSec(fail=True), clock).history("DEMO")
        assert hist.latest().revenue == 1000.0
        assert "refresh failed" in hist.sources["data"] and "offline" in hist.sources["data"]

    def test_a_failed_first_fetch_raises(self, store):
        store.save_tickers({"DEMO": (1234, "DEMO Corp")}, T0)
        with pytest.raises(SecError, match="offline"):
            service(store, FakeSec(fail=True), Clock()).history("DEMO")
