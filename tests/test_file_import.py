"""File import (F2): validated into the store under "file", served back by
FileSource, and nothing stored from a bad file."""

from datetime import datetime

import pandas as pd
import pytest

from quant_hub.broker.types import BrokerError
from quant_hub.data.sources import DataSource, SyntheticSource
from quant_hub.data.types import PriceBasis
from quant_hub.importers.bar_csv import BarFileError
from quant_hub.services.file_import import (
    FILE_SOURCE,
    FileSource,
    ImportedBasisMissingError,
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
    with pytest.raises(BarFileError, match="QQQ: no usable traded bars"):
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



# ------------------------------------------------------------ bases (F3, D38)
def yahoo_file(tmp_path, n=30):
    rows = []
    for i, day in enumerate(pd.bdate_range("2024-01-02", periods=n)):
        close = 100.0 + i
        factor = 0.98 if i < 10 else 1.0               # one dividend, ex on day 10
        rows.append(f"{day:%Y-%m-%d},{close:.2f},{close + 1:.2f},{close - 1:.2f},{close:.2f},"
                    f"{close * factor:.6f},1000")
    return csv(tmp_path, "SPY.csv", rows, header="Date,Open,High,Low,Close,Adj Close,Volume")


def test_a_yahoo_file_stores_both_bases(tmp_path, store):
    (series,) = import_bar_file(store, yahoo_file(tmp_path), "SPY")
    assert series.bases == (PriceBasis.TRADED, PriceBasis.ADJUSTED)
    assert "traded and adjusted" in series.describe()
    source = FileSource(store)
    traded = source.get_history("SPY", JAN, DEC, PriceBasis.TRADED)
    adjusted = source.get_history("SPY", JAN, DEC, PriceBasis.ADJUSTED)
    assert adjusted.basis is PriceBasis.ADJUSTED
    first = traded.frame.index[0]
    assert adjusted.frame.loc[first, "close"] == pytest.approx(0.98 * traded.frame.loc[first,
                                                                                     "close"])
    # Open, high and low carry the same day's factor; volume doesn't.
    assert adjusted.frame.loc[first, "high"] == pytest.approx(0.98 * traded.frame.loc[first,
                                                                                    "high"])
    assert adjusted.frame.loc[first, "volume"] == traded.frame.loc[first, "volume"]
    assert adjusted.closes.iloc[-1] == traded.closes.iloc[-1]


def test_an_adjusted_only_file_and_the_basis_it_lacks(tmp_path, store):
    path = csv(tmp_path, "SPY.csv", ["2024-01-02,466.7", "2024-01-03,468.1"],
               header="date,adj_close")
    (series,) = import_bar_file(store, path, "SPY")
    assert series.bases == (PriceBasis.ADJUSTED,)
    source = FileSource(store)
    assert len(source.get_history("SPY", JAN, DEC, PriceBasis.ADJUSTED).closes) == 2
    with pytest.raises(ImportedBasisMissingError, match="adjusted prices only: choose Adjusted"):
        source.get_history("SPY", JAN, DEC, PriceBasis.TRADED)


def test_return_math_falls_back_to_traded_where_an_import_has_no_adjusted(tmp_path, store):
    import_bar_file(store, csv(tmp_path, "SPY.csv", daily_rows(40)), "SPY")
    with pytest.raises(ImportedBasisMissingError, match="an adj_close column"):
        FileSource(store).get_history("SPY", JAN, DEC, PriceBasis.ADJUSTED)
    market = MarketService(sources=[SyntheticSource(), FileSource(store)])
    history = market.get_return_history("SPY", JAN, DEC, source=FILE_SOURCE)
    assert history.basis is PriceBasis.TRADED
    # A source with no such symbol at all is still an error.
    with pytest.raises(NoImportedBarsError):
        market.get_return_history("QQQ", JAN, DEC, source=FILE_SOURCE)
