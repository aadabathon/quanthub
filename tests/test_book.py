"""Today's book (services/book.py, D89): PortfolioBook is the newest snapshot
analysed with the Portfolio page's settings, the example portfolio before any
import, and a setting Portfolio would refuse is named. Made-up holdings."""

import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.services.book import (
    MIN_LOOKBACK,
    PORTFOLIO_STATE_KEY,
    BookError,
    BookSource,
    PortfolioBook,
    portfolio_settings,
)
from quant_hub.services.file_import import FileSource
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService
from quant_hub.storage.position_store import PositionStore
from quant_hub.storage.state_store import ViewStateStore
from book_fixtures import NOW, bars, export

SETTINGS = {"source": "file", "benchmark": "spy", "lookback": "365", "rate": "5.0"}


@pytest.fixture
def world(tmp_path):
    store_of_bars, _ = bars()
    market = MarketService(sources=[SyntheticSource(), FileSource(store_of_bars)])
    portfolio = PortfolioService(market, PositionStore(":memory:"))
    state = ViewStateStore(":memory:")
    for name, day, holdings, cash in (
            ("older.csv", "10/15/2026", {"AAA": 40, "BBB": 10}, 4000),
            ("newer.csv", "10/22/2026", {"AAA": 40, "BBB": 20, "CCC": 10}, 3000)):
        path = tmp_path / name
        path.write_text(export(day, holdings, cash), encoding="utf-8")
        portfolio.import_positions(path)
    return market, portfolio, state


def test_the_book_is_the_newest_snapshot_as_portfolio_analyses_it(world):
    market, portfolio, state = world
    state.save(PORTFOLIO_STATE_KEY, {**SETTINGS, "mode": "Imported", "account": "x"})
    source = PortfolioBook(portfolio, market, state, now=lambda: NOW)
    assert isinstance(source, BookSource)
    book = source.book()
    assert {p.symbol for p in book.snapshot.positions} >= {"AAA", "BBB", "CCC"}
    assert (book.source, book.benchmark, book.lookback) == ("file", "SPY", 365)
    assert book.rate == pytest.approx(0.05)
    assert book.sleeves == {} and book.core == frozenset()
    assert "newest snapshot" in book.label
    same = portfolio.analyse(book.snapshot, None, "file", "SPY", 365, 0.05, now=NOW)
    assert book.view.report.vol == pytest.approx(same.report.vol, rel=1e-12)
    assert book.view.report.beta == pytest.approx(same.report.beta, rel=1e-12)
    assert source.latest_beta() is None                    # the Hedge Lab starts at 1.00


def test_without_a_saved_record_the_pages_defaults_apply(world):
    _, _, state = world
    settings = portfolio_settings(state, ["synthetic", "file"])
    # ibkr isn't registered here, so the first source, as the page picks it.
    assert (settings.source, settings.benchmark, settings.lookback) == ("synthetic", "SPY", 730)
    assert settings.rate == pytest.approx(0.04)
    state.save(PORTFOLIO_STATE_KEY, {**SETTINGS, "source": "gone"})
    assert portfolio_settings(state, ["synthetic", "file"]).source == "synthetic"


@pytest.mark.parametrize("field, value, named", [
    ("lookback", "two years", "Lookback"),
    ("lookback", str(MIN_LOOKBACK - 1), "Lookback"),
    ("rate", "four", "Rate %"),
    ("benchmark", "  ", "Benchmark"),
])
def test_a_setting_portfolio_would_refuse_is_named(world, field, value, named):
    market, portfolio, state = world
    state.save(PORTFOLIO_STATE_KEY, {**SETTINGS, field: value})
    with pytest.raises(BookError, match=named) as error:
        PortfolioBook(portfolio, market, state).book()
    assert "Portfolio page" in str(error.value)


def test_before_any_import_the_book_is_the_example_portfolio(world):
    market, _, state = world
    empty = PortfolioService(market, PositionStore(":memory:"))
    state.save(PORTFOLIO_STATE_KEY, {**SETTINGS, "source": "synthetic"})
    book = PortfolioBook(empty, market, state, now=lambda: NOW).book()
    assert book.snapshot.name == "example portfolio"
    assert book.label.startswith("the example portfolio")
    assert book.view.report.equity == pytest.approx(100_000, rel=1e-9)
