"""Daily chain snapshots (D63): due only on trading days after 09:45 New York,
once per symbol per day, with the wide request; failures don't stop the rest."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quant_hub.data.options import OptionChain, OptionQuote
from quant_hub.services.chain_collector import VARIANCE_REQUEST, ChainCollector
from quant_hub.services.option_chains import ChainService
from quant_hub.storage.chain_store import ChainStore

NY = ZoneInfo("America/New_York")
MONDAY_10AM = datetime(2026, 11, 9, 10, 0, tzinfo=NY)


class FakeSource:
    name = "ibkr"

    def __init__(self, clock, failing=()):
        self.clock, self.failing, self.requests = clock, set(failing), []

    def get_chain(self, symbol, request):
        self.requests.append((symbol, request))
        if symbol in self.failing:
            raise TimeoutError(f"{symbol}: TWS didn't answer")
        as_of = self.clock["now"].astimezone().replace(tzinfo=None)    # the store's convention
        return OptionChain(symbol, "ibkr", as_of, 100.0, 0.04, 0.0, (
            OptionQuote("call", 100.0, 30.0, 2.0, 2.1), OptionQuote("put", 95.0, 30.0, 1.0, 1.1)))


@pytest.fixture
def setup():
    store = ChainStore(":memory:")
    clock = {"now": MONDAY_10AM}
    source = FakeSource(clock)
    collector = ChainCollector(ChainService([source], store), store, ["spx", "SPY", "spx"],
                               now=lambda: clock["now"])
    yield collector, source, clock, store
    store.close()


def test_a_trading_day_after_the_open_collects_each_symbol_once(setup):
    collector, source, _, store = setup
    assert collector.watchlist == ("SPX", "SPY") and collector.due() == ["SPX", "SPY"]
    outcomes = collector.run()
    assert [(o.symbol, o.ok, o.quotes) for o in outcomes] == [("SPX", True, 2), ("SPY", True, 2)]
    assert all(request is VARIANCE_REQUEST for _, request in source.requests)
    assert VARIANCE_REQUEST.k_range_sd == (-6.0, 3.0) and VARIANCE_REQUEST.strikes_per_expiry == 41
    assert collector.due() == [] and collector.run() == []          # a second connection: nothing
    assert len(store.snapshots()) == 2


@pytest.mark.parametrize("now", [
    datetime(2026, 11, 9, 9, 44, tzinfo=NY),       # before the quotes settle
    datetime(2026, 11, 8, 12, 0, tzinfo=NY),       # a Sunday
    datetime(2026, 11, 26, 12, 0, tzinfo=NY),      # Thanksgiving
])
def test_nothing_is_due_before_the_open_or_on_a_closed_day(setup, now):
    collector, _, clock, _ = setup
    clock["now"] = now
    assert collector.due() == []


def test_the_next_trading_day_is_due_again_and_the_evening_still_counts(setup):
    collector, _, clock, _ = setup
    collector.run()
    clock["now"] = datetime(2026, 11, 10, 20, 30, tzinfo=NY)        # Tuesday evening
    assert collector.due() == ["SPX", "SPY"]


def test_a_failed_symbol_is_reported_and_the_rest_still_run(setup):
    collector, source, _, _ = setup
    source.failing = {"SPX"}
    outcomes = {o.symbol: o for o in collector.run()}
    assert not outcomes["SPX"].ok and "didn't answer" in outcomes["SPX"].message
    assert outcomes["SPY"].ok
    assert collector.due() == ["SPX"]                               # retried on the next connection


def test_no_watchlist_or_no_live_source_means_nothing_to_do():
    store = ChainStore(":memory:")
    assert ChainCollector(ChainService([], store), store, ["SPX"]).due() == []
    assert ChainCollector(ChainService([FakeSource({"now": MONDAY_10AM})], store), store,
                          [], now=lambda: MONDAY_10AM).due() == []
    store.close()
