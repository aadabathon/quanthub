"""Tests for the SQLite bar store (quant_hub.storage.bar_store).

Every test uses a temporary or in-memory database; no network, no GUI.
The important guarantees: database-enforced uniqueness, upsert-on-conflict,
chronological range queries, day-granular coverage tracking independent of
bar existence, and persistence across reopen.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.ranges import DateRange
from quant_hub.storage.bar_store import (
    BarStore,
    InsertResult,
    InstrumentKey,
    StorageError,
)

SPY = InstrumentKey(symbol="SPY")
SOURCE = "ibkr"
DAILY = "1 day"


def frame(dates: list[str], close_offset: float = 0.0) -> pd.DataFrame:
    """A small valid OHLCV frame on the given dates."""
    closes = [100.0 + i + close_offset for i in range(len(dates))]
    return pd.DataFrame(
        {
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
            "volume": [1000.0] * len(dates),
        },
        index=pd.DatetimeIndex(dates),
    )


@pytest.fixture
def store():
    s = BarStore(":memory:")
    yield s
    s.close()


class TestInstrumentKey:
    def test_symbol_normalized(self):
        assert InstrumentKey(symbol=" spy ").symbol == "SPY"

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValueError):
            InstrumentKey(symbol="  ")


class TestInsertAndQuery:
    def test_roundtrip(self, store):
        bars = frame(["2024-01-02", "2024-01-03", "2024-01-04"])
        result = store.insert_bars(SPY, SOURCE, DAILY, bars)
        assert result == InsertResult(inserted=3, replaced=0)

        out = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert len(out) == 3
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]
        assert out["close"].tolist() == [100.0, 101.0, 102.0]

    def test_chronological_even_if_inserted_backwards(self, store):
        bars = frame(["2024-01-04", "2024-01-03", "2024-01-02"])
        store.insert_bars(SPY, SOURCE, DAILY, bars)
        out = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert out.index.is_monotonic_increasing

    def test_range_query_inclusive_bounds(self, store):
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02", "2024-01-03", "2024-01-04"]))
        out = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 3), datetime(2024, 1, 4))
        assert list(out.index) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")]

    def test_query_end_with_time_of_day_includes_that_session(self, store):
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02", "2024-01-03"]))
        out = store.query_bars(
            SPY, SOURCE, DAILY, datetime(2024, 1, 2), datetime(2024, 1, 3, 15, 30)
        )
        assert len(out) == 2

    def test_empty_query_returns_empty_frame(self, store):
        out = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert len(out) == 0
        assert list(out.columns) == ["open", "high", "low", "close", "volume"]

    def test_null_ohlv_allowed_close_required(self, store):
        bars = pd.DataFrame(
            {"close": [50.0], "open": [np.nan], "high": [np.nan],
             "low": [np.nan], "volume": [np.nan]},
            index=pd.DatetimeIndex(["2024-01-02"]),
        )
        store.insert_bars(SPY, SOURCE, DAILY, bars)
        out = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert out["close"].iloc[0] == 50.0
        assert np.isnan(out["open"].iloc[0])

    def test_empty_insert_is_noop(self, store):
        result = store.insert_bars(SPY, SOURCE, DAILY, frame([]))
        assert result == InsertResult(0, 0)


class TestUniquenessAndUpsert:
    def test_reinsert_replaces_and_counts(self, store):
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02", "2024-01-03"]))
        result = store.insert_bars(
            SPY, SOURCE, DAILY, frame(["2024-01-03", "2024-01-04"], close_offset=50.0)
        )
        assert result == InsertResult(inserted=1, replaced=1)
        out = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert len(out) == 3  # no duplicates in the database
        # The re-sent 01-03 bar superseded the original.
        assert out.loc[pd.Timestamp("2024-01-03"), "close"] == 150.0

    def test_identity_separates_bar_sizes_and_sources(self, store):
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02"]))
        store.insert_bars(SPY, SOURCE, "1 week", frame(["2024-01-02"], close_offset=5.0))
        store.insert_bars(SPY, "other-vendor", DAILY, frame(["2024-01-02"], close_offset=9.0))

        daily = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        weekly = store.query_bars(SPY, SOURCE, "1 week", datetime(2024, 1, 1), datetime(2024, 1, 31))
        other = store.query_bars(SPY, "other-vendor", DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert (daily["close"].iloc[0], weekly["close"].iloc[0], other["close"].iloc[0]) == (
            100.0, 105.0, 109.0,
        )

    def test_identity_separates_instruments(self, store):
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02"]))
        store.insert_bars(InstrumentKey("SPY", currency="EUR"), SOURCE, DAILY,
                          frame(["2024-01-02"], close_offset=7.0))
        usd = store.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
        eur = store.query_bars(InstrumentKey("SPY", currency="EUR"), SOURCE, DAILY,
                               datetime(2024, 1, 1), datetime(2024, 1, 31))
        assert usd["close"].iloc[0] == 100.0
        assert eur["close"].iloc[0] == 107.0

    def test_check_constraint_is_a_backstop(self, store):
        bad = pd.DataFrame(
            {"close": [-5.0]}, index=pd.DatetimeIndex(["2024-01-02"])
        )
        with pytest.raises(StorageError):
            store.insert_bars(SPY, SOURCE, DAILY, bad)


class TestCoverage:
    RANGE_A = DateRange(datetime(2024, 1, 1), datetime(2024, 3, 31))
    RANGE_B = DateRange(datetime(2024, 4, 1), datetime(2024, 6, 30))  # adjacent
    RANGE_C = DateRange(datetime(2024, 9, 1), datetime(2024, 9, 30))  # disjoint

    def test_coverage_independent_of_bars(self, store):
        # No bars inserted at all: coverage is still recordable (a synced
        # holiday week legitimately has zero bars).
        store.add_coverage(SPY, SOURCE, DAILY, self.RANGE_A)
        assert store.coverage(SPY, SOURCE, DAILY) == [self.RANGE_A]

    def test_adjacent_ranges_merge_in_db(self, store):
        store.add_coverage(SPY, SOURCE, DAILY, self.RANGE_A)
        store.add_coverage(SPY, SOURCE, DAILY, self.RANGE_B)
        assert store.coverage(SPY, SOURCE, DAILY) == [
            DateRange(datetime(2024, 1, 1), datetime(2024, 6, 30))
        ]

    def test_disjoint_ranges_stay_separate(self, store):
        store.add_coverage(SPY, SOURCE, DAILY, self.RANGE_A)
        store.add_coverage(SPY, SOURCE, DAILY, self.RANGE_C)
        assert store.coverage(SPY, SOURCE, DAILY) == [self.RANGE_A, self.RANGE_C]

    def test_coverage_keyed_by_bar_size(self, store):
        store.add_coverage(SPY, SOURCE, DAILY, self.RANGE_A)
        assert store.coverage(SPY, SOURCE, "1 week") == []

    def test_no_coverage_for_unknown_instrument(self, store):
        assert store.coverage(InstrumentKey("QQQ"), SOURCE, DAILY) == []


class TestSummary:
    def test_empty_store(self, store):
        assert store.summary() == []

    def test_groups_by_symbol_source_barsize(self, store):
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02", "2024-01-03"]))
        store.insert_bars(InstrumentKey("QQQ"), SOURCE, DAILY, frame(["2024-01-02"]))
        rows = store.summary()
        assert [(r.symbol, r.n_bars) for r in rows] == [("QQQ", 1), ("SPY", 2)]
        spy = rows[1]
        assert spy.first == datetime(2024, 1, 2)
        assert spy.last == datetime(2024, 1, 3)
        assert spy.source == SOURCE and spy.bar_size == DAILY


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        db = tmp_path / "bars.db"
        store = BarStore(db)
        store.insert_bars(SPY, SOURCE, DAILY, frame(["2024-01-02", "2024-01-03"]))
        store.add_coverage(SPY, SOURCE, DAILY, DateRange(datetime(2024, 1, 1), datetime(2024, 1, 31)))
        store.close()

        reopened = BarStore(db)
        try:
            out = reopened.query_bars(SPY, SOURCE, DAILY, datetime(2024, 1, 1), datetime(2024, 1, 31))
            assert len(out) == 2
            assert reopened.coverage(SPY, SOURCE, DAILY) == [
                DateRange(datetime(2024, 1, 1), datetime(2024, 1, 31))
            ]
        finally:
            reopened.close()

    def test_creates_parent_directories(self, tmp_path):
        db = tmp_path / "nested" / "dir" / "bars.db"
        store = BarStore(db)
        store.close()
        assert db.exists()

    def test_schema_init_idempotent(self, tmp_path):
        db = tmp_path / "bars.db"
        BarStore(db).close()
        BarStore(db).close()  # second open must not fail
