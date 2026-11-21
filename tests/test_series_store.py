"""External series in the store (D60): saved as fetched, replaced whole, read offline."""
import math
from datetime import datetime

import pandas as pd
import pytest

from quant_hub.data.series import FetchedSeries, SeriesMeta
from quant_hub.storage.series_store import SeriesStore

T0 = datetime(2026, 11, 3, 21, 0, 0)


def meta(series="ff3.mkt_rf", source="french"):
    return SeriesMeta(source, series, f"title {series}", "fraction", "daily", "cite", "CRSP 202609")


def fetched(series="ff3.mkt_rf", days=("2026-09-29", "2026-09-30"), values=(0.01, -0.02)):
    return FetchedSeries(meta(series), tuple(days), tuple(values))


@pytest.fixture
def store():
    s = SeriesStore(":memory:")
    yield s
    s.close()


def test_a_saved_series_reads_back_with_its_provenance(store):
    assert store.save([fetched(), fetched("ff3.rf", values=(0.0001, 0.0001))], T0, "abc") == 4
    s = store.series("french", "ff3.mkt_rf")
    assert s.meta == meta() and s.fetched_at == T0 and s.sha256 == "abc"
    assert list(s.values) == [0.01, -0.02] and isinstance(s.values.index, pd.DatetimeIndex)
    assert (s.first, s.last) == (pd.Timestamp("2026-09-29"), pd.Timestamp("2026-09-30"))
    assert store.series("french", "nope") is None


def test_a_refetch_replaces_the_series_whole_revisions_included(store):
    store.save([fetched(days=("2026-09-28", "2026-09-29", "2026-09-30"), values=(1, 2, 3))], T0, "a")
    later = datetime(2026, 11, 10)
    store.save([fetched(days=("2026-09-29", "2026-09-30"), values=(2.5, 3))], later, "b")
    s = store.series("french", "ff3.mkt_rf")
    assert list(s.values) == [2.5, 3] and s.fetched_at == later and s.sha256 == "b"


def test_the_catalog_and_the_stalest_fetch_per_prefix(store):
    store.save([fetched("ff3.mkt_rf"), fetched("ff3.rf")], T0, "a")
    store.save([fetched("mom.mom")], datetime(2026, 11, 1), "b")
    rows = store.catalog("french")
    assert [r.meta.series for r in rows] == ["ff3.mkt_rf", "ff3.rf", "mom.mom"]
    assert (rows[0].count, rows[0].first, rows[0].last) == (2, "2026-09-29", "2026-09-30")
    assert store.fetched_at("french", "ff3.") == T0
    assert store.fetched_at("french") == datetime(2026, 11, 1)
    assert store.fetched_at("french", "ff5.") is None and store.catalog("fred") == []


@pytest.mark.parametrize("kwargs, message", [
    (dict(days=("2026-09-29",), values=(1.0, 2.0)), "1 dates but 2 values"),
    (dict(days=("2026-09-30", "2026-09-29")), "strictly increasing"),
    (dict(days=("2026-09-30", "2026-09-30")), "strictly increasing"),
    (dict(values=(1.0, math.nan)), "finite"),
    (dict(days=("20260929", "2026-09-30")), "ISO date"),
    (dict(days=(), values=()), "no values"),
])
def test_a_fetched_series_is_validated(kwargs, message):
    with pytest.raises(ValueError, match=message):
        fetched(**kwargs)


@pytest.mark.parametrize("field, value, message", [
    ("series", "Mkt-RF", "lower-case"), ("unit", "bps", "unknown unit"),
    ("frequency", "hourly", "unknown frequency")])
def test_series_meta_is_validated(field, value, message):
    args = dict(source="french", series="ff3.x", title="t", unit="fraction", frequency="daily",
                citation="c")
    args[field] = value
    with pytest.raises(ValueError, match=message):
        SeriesMeta(**args)
