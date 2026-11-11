"""Tests for IBKR quote normalization (quant_hub.broker.ibkr.get_market_quote).

No TWS: a fake app object replaces ibapi, and each test replays a callback
sequence recorded from the live shakedown (2026-08-14) into the broker's
``_on_*`` handlers while ``get_market_quote`` blocks on a worker thread.
The behaviors under test are the M6 fixes:

* entitlement advisories (10167/10089) never fail the request and never
  touch connection state;
* delayed tick ids and the marketDataType callback normalize into
  ``MarketDataMode`` — no tick codes escape the broker;
* zero ticks before the timeout -> ``NoMarketDataError`` carrying the
  advisory text;
* the trade-time tick becomes the quote timestamp.
"""

import threading
import time
from datetime import datetime

import pytest

from quant_hub.broker.base import ConnectionState
from quant_hub.broker.ibkr import IBKRBroker
from quant_hub.broker.types import (
    ContractSpec,
    InvalidContractError,
    NoMarketDataError,
    QuoteResult,
)
from quant_hub.config import AppConfig
from quant_hub.data.types import MarketDataMode
from quant_hub.events import EventBus

SPY = ContractSpec(symbol="SPY")


class FakeApp:
    """Records reqMktData/cancelMktData; the tests play the callbacks."""

    def __init__(self):
        self.requests = []
        self.cancels = []

    def reqMktData(self, reqId, contract, tick_list, snapshot, regulatory, options):
        self.requests.append((reqId, contract.symbol if hasattr(contract, "symbol") else None, snapshot))

    def cancelMktData(self, reqId):
        self.cancels.append(reqId)


@pytest.fixture
def broker():
    config = AppConfig(quote_timeout_s=0.6)
    b = IBKRBroker(config, EventBus())
    b._state = ConnectionState.CONNECTED  # bypass the live handshake
    b._app = FakeApp()
    return b


def run_quote(broker, feed):
    """Call get_market_quote on a thread; run ``feed(req_id)`` once the
    request registers; return the result or the raised exception."""
    outcome = {}

    def call():
        try:
            outcome["result"] = broker.get_market_quote(SPY)
        except Exception as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=call)
    thread.start()
    deadline = time.time() + 2.0
    while not broker._app.requests and time.time() < deadline:
        time.sleep(0.005)
    assert broker._app.requests, "reqMktData was never sent"
    req_id = broker._app.requests[0][0]
    feed(req_id)
    thread.join(timeout=5.0)
    assert not thread.is_alive(), "get_market_quote never returned"
    return outcome


class TestLive:
    def test_live_ticks_yield_live_quote(self, broker):
        def feed(req_id):
            broker._on_market_data_type(req_id, 1)
            broker._on_tick_string(req_id, 45, "1786740303")
            broker._on_tick_price(req_id, 4, 645.21)

        outcome = run_quote(broker, feed)
        result = outcome["result"]
        assert isinstance(result, QuoteResult)
        assert result.price == 645.21
        assert result.mode is MarketDataMode.LIVE
        assert result.at == datetime.fromtimestamp(1786740303)

    def test_stream_is_cancelled_after_completion(self, broker):
        outcome = run_quote(broker, lambda r: broker._on_tick_price(r, 4, 100.0))
        assert "result" in outcome
        assert broker._app.cancels == [broker._app.requests[0][0]]


class TestDelayed:
    def test_advisory_then_delayed_ticks_is_a_delayed_quote(self, broker):
        """The live-observed 10167 sequence: advisory, then delayed burst."""

        def feed(req_id):
            broker._on_market_data_type(req_id, 3)
            broker._on_error(req_id, 10167,
                             "Requested market data is not subscribed. "
                             "Displaying delayed market data.")
            broker._on_tick_price(req_id, 68, 776.0)   # DELAYED_LAST
            broker._on_tick_string(req_id, 88, "1786740303")
            broker._on_tick_price(req_id, 75, 777.88)  # DELAYED_CLOSE

        outcome = run_quote(broker, feed)
        result = outcome["result"]
        assert result.price == 776.0  # delayed LAST outranks delayed CLOSE
        assert result.mode is MarketDataMode.DELAYED
        assert result.at == datetime.fromtimestamp(1786740303)

    def test_delayed_close_used_when_no_last(self, broker):
        def feed(req_id):
            broker._on_market_data_type(req_id, 4)
            broker._on_tick_price(req_id, 75, 82.59)

        result = run_quote(broker, feed)["result"]
        assert result.price == 82.59
        assert result.mode is MarketDataMode.DELAYED_FROZEN

    def test_connection_state_untouched_by_advisories(self, broker):
        def feed(req_id):
            broker._on_error(req_id, 10167, "…delayed…")
            broker._on_tick_price(req_id, 68, 10.0)

        run_quote(broker, feed)
        assert broker.state is ConnectionState.CONNECTED


class TestUnavailable:
    def test_advisory_with_no_ticks_times_out_with_reason(self, broker):
        """The live-observed AAPL/10089 sequence: advisory, then silence."""

        def feed(req_id):
            broker._on_error(req_id, 10089,
                             "Requested market data requires additional "
                             "subscription for API.")

        outcome = run_quote(broker, feed)
        error = outcome["error"]
        assert isinstance(error, NoMarketDataError)
        assert "additional subscription" in str(error)
        assert broker.state is ConnectionState.CONNECTED

    def test_silence_times_out_generically(self, broker):
        outcome = run_quote(broker, lambda req_id: None)
        assert isinstance(outcome["error"], NoMarketDataError)
        assert "no ticks within" in str(outcome["error"])

    def test_unavailable_is_not_a_quote_mode(self):
        assert not hasattr(MarketDataMode, "UNAVAILABLE")

    def test_invalid_contract_fails_fast(self, broker):
        started = time.time()
        outcome = run_quote(
            broker,
            lambda r: broker._on_error(r, 200, "No security definition found"),
        )
        assert isinstance(outcome["error"], InvalidContractError)
        # Fatal errors wake the waiter immediately — no timeout burn.
        assert time.time() - started < 0.5


class TestSourceModes:
    def test_synthetic_quotes_are_stamped_synthetic(self):
        from quant_hub.data.sources import SyntheticSource

        quote = SyntheticSource().get_quote("SPY")
        assert quote.mode is MarketDataMode.SYNTHETIC
