"""File import (F2): validated into the store under "file", served back by
FileSource, and nothing stored from a bad file."""

from datetime import datetime

import pandas as pd
import pytest

from quant_hub.broker.types import BrokerError
from quant_hub.data.sources import DataSource, SyntheticSource
from quant_hub.importers.bar_csv import BarFileError
from quant_hub.services.file_import import (
    FILE_SOURCE,
    FileSource,
    NoImportedBarsError,
    import_bar_file,
    symbol_from_filename,
)
from quant_hub.services.market_service import MarketService
from quant_hub.services.underlying import load_underlying
from quant_hub.storage.bar_store import BarStore, InstrumentKey

JAN = datetime(2024, 1, 1)
DEC = datetime(2024, 12, 31)


@pytest.fixture
def store():
    s = BarStore(":memory:")
    yield s
    s.close()


def csv(tmp_path, name, rows, header="date,open,high,low,close,volume"):
    path = tmp_path / name
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def daily_rows(n=40, start="2024-01-02", close=100.0):
    rows = []
    for i, day in enumerate(pd.bdate_range(start, periods=n)):
        c = close * (1.01 ** (i % 5))
        rows.append(f"{day:%Y-%m-%d},{c:.2f},{c * 1.01:.2f},{c * 0.99:.2f},{c:.2f},1000")
    return rows


def test_bars_are_stored_under_file_and_served_back(tmp_path, store):
    (series,) = import_bar_file(store, csv(tmp_path, "SPY.csv", daily_rows()), "SPY")
    assert (series.symbol, series.layout, series.inserted, series.replaced) == (
        "SPY", "Generic", 40, 0)
    source = FileSource(store)
    assert isinstance(source, DataSource)
    history = source.get_history("spy", JAN, DEC)
    assert history.source == FILE_SOURCE and len(history.closes) == 40
    # A window past the file's end just holds fewer bars; one with none says so.
    assert len(source.get_history("SPY", datetime(2024, 2, 1), DEC).closes) < 40
    with pytest.raises(NoImportedBarsError, match="import a CSV"):
        source.get_history("SPY", datetime(2025, 1, 1), datetime(2025, 6, 1))


def test_importing_again_replaces_the_same_dates(tmp_path, store):
    path = csv(tmp_path, "SPY.csv", daily_rows())
    import_bar_file(store, path, "SPY")
    (again,) = import_bar_file(store, path, "SPY")
    assert (again.inserted, again.replaced) == (0, 40)


def test_bad_rows_are_dropped_and_reported(tmp_path, store):
    rows = daily_rows(10)
    rows[3] = "2024-01-05,10,9,11,10,1000"                      # high < low
    (series,) = import_bar_file(store, csv(tmp_path, "SPY.csv", rows), "SPY")
    assert series.stored == 9 and len(series.report.dropped) == 1
    assert "1 row dropped" in series.describe()


def test_a_bad_symbol_in_a_file_stores_nothing(tmp_path, store):
    path = csv(tmp_path, "both.csv", ["SPY,2024-01-02,470", "QQQ,2024-01-02,-1"],
               header="symbol,date,close")
    with pytest.raises(BarFileError, match="QQQ: no usable bars"):
        import_bar_file(store, path)
    assert store.summary() == []                                  # SPY wasn't stored either


def test_a_file_without_symbols_needs_one(tmp_path, store):
    with pytest.raises(BarFileError, match="names no symbol"):
        import_bar_file(store, csv(tmp_path, "data.csv", daily_rows(3)))


def test_a_file_that_names_its_symbols_ignores_the_given_one(tmp_path, store):
    path = csv(tmp_path, "x.csv", ["IWM,2024-01-02,200"], header="symbol,date,close")
    (series,) = import_bar_file(store, path, "SPY")
    assert series.symbol == "IWM"


def test_imports_sit_beside_the_same_symbol_from_ibkr(tmp_path, store):
    ibkr = pd.DataFrame({"close": [1.0, 2.0]}, index=pd.bdate_range("2024-01-02", periods=2))
    store.insert_bars(InstrumentKey("SPY"), "ibkr", "1 day", ibkr)
    import_bar_file(store, csv(tmp_path, "SPY.csv", daily_rows(5)), "SPY")
    assert sorted((s.source, s.n_bars) for s in store.summary()) == [("file", 5), ("ibkr", 2)]


def test_no_live_quote_so_the_underlying_uses_the_last_close(tmp_path, store):
    import_bar_file(store, csv(tmp_path, "SPY.csv", daily_rows(40)), "SPY")
    with pytest.raises(BrokerError):
        FileSource(store).get_quote("SPY")
    market = MarketService(sources=[SyntheticSource(), FileSource(store)])
    snapshot = load_underlying(market, "SPY", FILE_SOURCE, 400, now=datetime(2024, 3, 1))
    assert snapshot.spot_basis == "last close" and snapshot.source == FILE_SOURCE


@pytest.mark.parametrize("name, symbol", [
    ("SPY.csv", "SPY"), ("brk.b.csv", "BRK.B"), ("BRK-B.csv", "BRK-B"), ("^GSPC.csv", "^GSPC"),
    ("HistoricalData_1697.csv", None), ("SPY (1).csv", None), ("spy_us_d.csv", None),
])
def test_symbols_from_file_names(name, symbol):
    assert symbol_from_filename(name) == symbol
