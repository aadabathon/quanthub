"""Option-chain acquisition (C1): the chain contract's per-expiry carry, the
provider-neutral selection rules, the broker's option requests against a
stand-in for the ibapi client, and the IBKR adapter against a fake broker."""

import math
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from quant_hub.broker.option_chain import (
    NEW_YORK,
    IBKROptionChainSource,
    choose_class,
    days_to_expiry,
    underlying_spec,
)
from quant_hub.broker.types import (
    NoMarketDataError,
    OptionContractSpec,
    OptionParams,
    OptionQuoteResult,
)
from quant_hub.data.options import (
    ChainRequest,
    ExpiryCarry,
    OptionChain,
    OptionChainSource,
    OptionQuote,
    band_strikes,
    nearest_strikes,
    select_expiries,
)
from quant_hub.data.types import MarketDataMode
from quant_hub.models.black_scholes import price_array
from quant_hub.models.vol_surface import chain_implied_vols


def chain(carry=None, quotes=None) -> OptionChain:
    quotes = quotes or (OptionQuote("call", 105.0, 30.0, 1.0, 1.2),
                        OptionQuote("put", 95.0, 30.0, 0.9, 1.1))
    return OptionChain("X", "test", datetime(2026, 10, 15), 100.0, 0.04, 0.01, quotes,
                       carry or {})


# ------------------------------------------------------------------ contract

class TestCarry:
    def test_flat_carry_without_overrides(self):
        c = chain()
        assert c.forward(30.0) == pytest.approx(100.0 * math.exp(0.03 * 30 / 365))
        assert c.discount(30.0) == pytest.approx(math.exp(-0.04 * 30 / 365))
        assert c.rate_at(30.0) == 0.04 and c.dividend_yield_at(30.0) == 0.01

    def test_override_reproduces_forward_and_discount_through_rate_and_yield(self):
        c = chain(carry={30.0: ExpiryCarry(forward=101.3, discount=0.9962)})
        years = 30 / 365
        r, q = c.rate_at(30.0), c.dividend_yield_at(30.0)
        assert c.forward(30.0) == 101.3 and c.discount(30.0) == 0.9962
        assert 100.0 * math.exp((r - q) * years) == pytest.approx(101.3, rel=1e-12)
        assert math.exp(-r * years) == pytest.approx(0.9962, rel=1e-12)
        assert c.rate_at(60.0) == 0.04          # other expiries keep the flat carry

    def test_carry_mapping_is_read_only(self):
        c = chain(carry={30.0: ExpiryCarry(101.0, 0.99)})
        with pytest.raises(TypeError):
            c.carry[60.0] = ExpiryCarry(102.0, 0.98)

    def test_implied_vols_use_each_expiry_carry(self):
        # Price a call under a carry different from the flat one; inverting
        # the chain must recover the vol under that carry.
        carry = ExpiryCarry(forward=102.0, discount=0.99)
        probe = chain(carry={30.0: carry})
        r, q = probe.rate_at(30.0), probe.dividend_yield_at(30.0)
        price = float(price_array(100.0, 105.0, 30.0, r, 0.22, "call", q))
        quotes = (OptionQuote("call", 105.0, 30.0, price - 0.01, price + 0.01),)
        ivs = chain_implied_vols(chain(carry={30.0: carry}, quotes=quotes))
        assert ivs.iv[0] == pytest.approx(0.22, abs=1e-6)
        flat = chain_implied_vols(chain(quotes=quotes))
        assert abs(flat.iv[0] - 0.22) > 0.01

    @pytest.mark.parametrize("kwargs", [{"forward": 0.0, "discount": 0.9},
                                        {"forward": 100.0, "discount": 0.0},
                                        {"forward": math.nan, "discount": 0.9}])
    def test_invalid_carry_rejected(self, kwargs):
        with pytest.raises(ValueError):
            ExpiryCarry(**kwargs)

    @pytest.mark.parametrize("iv", [0.0, -0.2, math.inf])
    def test_invalid_reference_iv_rejected(self, iv):
        with pytest.raises(ValueError):
            OptionQuote("call", 100.0, 30.0, 1.0, 1.1, reference_iv=iv)


# ----------------------------------------------------------------- selection

class TestSelection:
    def test_expiries_nearest_each_target_past_the_minimum(self):
        listed = [0.3, 1.0, 3.0, 8.0, 10.0, 29.0, 31.0, 64.0, 94.0, 170.0, 365.0, 380.0]
        assert select_expiries(listed, ChainRequest()) == [8.0, 29.0, 64.0, 94.0, 170.0, 365.0]

    def test_ties_go_to_the_earlier_expiry_and_targets_can_share_one(self):
        request = ChainRequest(target_dtes=(20, 25), min_dte=2)
        assert select_expiries([10.0, 30.0], request) == [10.0, 30.0]
        assert select_expiries([30.0], request) == [30.0]
        assert select_expiries([1.0], request) == []

    def test_nearest_strikes(self):
        assert nearest_strikes([90, 95, 100, 105, 110, 100], 101.0, 3) == [95, 100, 105]

    def test_band_strikes_cover_the_band_evenly(self):
        request = ChainRequest(strikes_per_expiry=9, k_range_sd=(-2.0, 2.0))
        listed = np.arange(50.0, 150.5, 0.5)
        strikes = band_strikes(listed, 100.0, 0.2, 0.25, request)
        sd = 0.2 * math.sqrt(0.25)
        assert len(strikes) == 9
        assert min(strikes) >= 100 * math.exp(-2 * sd) and max(strikes) <= 100 * math.exp(2 * sd)
        assert min(strikes) == pytest.approx(100 * math.exp(-2 * sd), abs=0.5)
        assert max(strikes) == pytest.approx(100 * math.exp(2 * sd), abs=0.5)

    def test_sparse_listing_gives_fewer_distinct_strikes(self):
        request = ChainRequest(strikes_per_expiry=21)
        assert band_strikes([90.0, 100.0, 110.0], 100.0, 0.2, 0.25, request) == [90.0, 100.0, 110.0]
        assert band_strikes([10.0, 500.0], 100.0, 0.2, 0.25, request) == []

    @pytest.mark.parametrize("kwargs", [{"target_dtes": ()}, {"min_dte": 0},
                                        {"strikes_per_expiry": 1}, {"k_range_sd": (0.5, 1.0)},
                                        {"vol_guess": 0}, {"rate": math.nan}])
    def test_invalid_request_rejected(self, kwargs):
        with pytest.raises(ValueError):
            ChainRequest(**kwargs)


# -------------------------------------------------- broker (stand-in client)

class _FakeTWS:
    """Stands in for ibapi's client: answers requests synchronously through
    the broker's own callback handlers, as the network thread would."""

    def __init__(self, broker, book):
        self.broker, self.book = broker, book    # (expiry, strike, right) -> ticks
        self.live_lines, self.max_lines, self.cancelled = set(), 0, []

    def reqMktData(self, req_id, contract, *_):  # noqa: N802
        self.live_lines.add(req_id)
        self.max_lines = max(self.max_lines, len(self.live_lines))
        key = (contract.lastTradeDateOrContractMonth, contract.strike, contract.right)
        spec = self.book.get(key)
        if spec is None:
            self.broker._on_error(req_id, 200, "No security definition has been found")
            return
        if spec.get("advisory"):
            self.broker._on_error(req_id, 10091, "Part of requested market data requires "
                                                 "additional subscription")
        self.broker._on_market_data_type(req_id, 3)
        for tick, price in spec["ticks"].items():
            self.broker._on_tick_price(req_id, tick, price)
        if "iv" in spec:
            self.broker._on_option_computation(req_id, 83, spec["iv"])

    def cancelMktData(self, req_id):  # noqa: N802
        self.live_lines.discard(req_id)
        self.cancelled.append(req_id)

    def reqContractDetails(self, req_id, contract):  # noqa: N802
        if contract.secType == "OPT":
            expiry = contract.lastTradeDateOrContractMonth
            for strike in {k for (e, k, _r) in self.book if e == expiry}:
                self.broker._on_contract_details(
                    req_id, SimpleNamespace(contract=SimpleNamespace(conId=1, strike=strike)))
        else:
            self.broker._on_contract_details(
                req_id, SimpleNamespace(contract=SimpleNamespace(conId=756733, strike=0.0)))
        self.broker._on_request_done(req_id)

    def reqSecDefOptParams(self, req_id, symbol, _fut, sec_type, con_id):  # noqa: N802
        self.broker._on_option_params(req_id, "SMART", symbol, "100",
                                      {"20261120", "20261023"}, {100.0, 95.0, 105.0})
        self.broker._on_request_done(req_id)


@pytest.fixture
def broker_with():
    pytest.importorskip("ibapi")
    from quant_hub.broker.base import ConnectionState
    from quant_hub.broker.ibkr import IBKRBroker
    from quant_hub.config import AppConfig
    from quant_hub.events import EventBus

    def make(book):
        broker = IBKRBroker(AppConfig(quote_timeout_s=1.0), EventBus())
        broker._state = ConnectionState.CONNECTED
        broker._app = _FakeTWS(broker, book)
        return broker
    return make


EXPIRY = date(2026, 11, 20)


def option(strike, right):
    return OptionContractSpec("SPY", EXPIRY, strike, right, "SPY")


class TestBrokerOptionQuotes:
    def test_quotes_in_order_with_delayed_ticks_no_bid_and_model_iv(self, broker_with):
        book = {
            ("20261120", 100.0, "C"): {"ticks": {66: 2.1, 67: 2.3}, "iv": 0.18},
            ("20261120", 120.0, "C"): {"ticks": {66: -1.0, 67: 0.05}},     # no bid
            ("20261120", 95.0, "P"): {"ticks": {1: 1.4, 2: 1.5}, "advisory": True},
        }
        broker = broker_with(book)
        results = broker.get_option_quotes([option(100.0, "C"), option(120.0, "C"),
                                            option(95.0, "P")])
        first, no_bid, live = results
        assert (first.bid, first.ask, first.model_iv) == (2.1, 2.3, 0.18)
        assert first.mode is MarketDataMode.DELAYED
        assert (no_bid.bid, no_bid.ask) == (0.0, 0.05)
        assert (live.bid, live.ask) == (1.4, 1.5)     # 10091 is advisory, not failure

    def test_unlisted_no_ask_and_crossed_contracts_are_none(self, broker_with):
        book = {
            ("20261120", 100.0, "C"): {"ticks": {66: 2.1, 67: 2.3}},
            ("20261120", 101.0, "C"): {"ticks": {66: 2.0}},                # no ask
            ("20261120", 102.0, "C"): {"ticks": {66: 2.5, 67: 2.4}},       # crossed
        }
        results = broker_with(book).get_option_quotes(
            [option(100.0, "C"), option(101.0, "C"), option(102.0, "C"), option(999.0, "C")])
        assert results[0] is not None and results[1:] == [None, None, None]

    def test_batches_stay_under_the_line_limit_and_every_line_is_cancelled(self, broker_with):
        book = {("20261120", float(k), "C"): {"ticks": {66: 1.0, 67: 1.1}}
                for k in range(100, 125)}
        broker = broker_with(book)
        contracts = [option(float(k), "C") for k in range(100, 125)]
        results = broker.get_option_quotes(contracts, batch_size=10)
        assert all(r is not None for r in results)
        assert broker._app.max_lines <= 10
        assert len(broker._app.cancelled) == 25 and not broker._app.live_lines
        assert not broker._pending

    def test_option_params_and_listed_strikes(self, broker_with):
        from quant_hub.broker.types import ContractSpec
        book = {("20261120", 100.0, "C"): {"ticks": {}}, ("20261120", 110.0, "P"): {"ticks": {}},
                ("20261023", 105.0, "C"): {"ticks": {}}}
        broker = broker_with(book)
        (listing,) = broker.get_option_params(ContractSpec("SPY"))
        assert listing.expiries == (date(2026, 10, 23), date(2026, 11, 20))
        assert listing.strikes == (95.0, 100.0, 105.0)
        strikes = broker.get_option_strikes("SPY", "SPY", [date(2026, 11, 20), date(2026, 10, 23)])
        assert strikes == {date(2026, 11, 20): (100.0, 110.0), date(2026, 10, 23): (105.0,)}


# -------------------------------------------------------- adapter (fake broker)

class _FakeBroker:
    """Lists expiries a few days to a year out, every strike 80..120 for
    each, and quotes any contract with a model IV of 20%."""

    def __init__(self, today: date):
        self.expiries = tuple(today + timedelta(days=d) for d in (1, 9, 30, 65, 92, 170, 360))
        self.strikes = tuple(float(k) for k in range(60, 141))
        self.quote_requests: list[list[OptionContractSpec]] = []
        self.strike_requests = 0

    def get_market_quote(self, spec):
        return SimpleNamespace(price=100.0)

    def get_option_params(self, spec):
        return [OptionParams("CBOE", spec.symbol, "100", self.expiries[:2], self.strikes),
                OptionParams("SMART", spec.symbol, "100", self.expiries, self.strikes)]

    def get_option_strikes(self, symbol, trading_class, expiries):
        self.strike_requests += 1
        return {e: self.strikes for e in expiries}

    def get_option_quotes(self, contracts):
        self.quote_requests.append(list(contracts))
        return [OptionQuoteResult(c, 1.0, 1.1, MarketDataMode.DELAYED, 0.20)
                if c.strike != 101.0 else None for c in contracts]


NOW = datetime(2026, 10, 15, 11, 0, tzinfo=NEW_YORK)


class TestAdapter:
    def test_underlying_and_trading_class_rules(self):
        assert underlying_spec("spx").sec_type == "IND" and underlying_spec("SPX").exchange == "CBOE"
        assert underlying_spec("SPY").sec_type == "STK"
        listings = [OptionParams("SMART", "SPX", "100", (date(2026, 11, 20),), (1.0,)),
                    OptionParams("SMART", "SPXW", "100", (date(2026, 11, 20),), (1.0,))]
        assert choose_class("SPX", listings).trading_class == "SPXW"
        assert choose_class("SPY", [OptionParams("SMART", "2SPY", "100", (), ()),
                                    OptionParams("SMART", "SPY", "100", (), ())]).trading_class == "SPY"

    def test_days_to_expiry_runs_to_4pm_new_york(self):
        assert days_to_expiry(date(2026, 10, 15), NOW) == pytest.approx(5 / 24)
        assert days_to_expiry(date(2026, 10, 22), NOW) == pytest.approx(7 + 5 / 24)

    def test_chain_flow(self):
        broker = _FakeBroker(NOW.date())
        source = IBKROptionChainSource(broker, now=lambda: NOW)
        assert isinstance(source, OptionChainSource)
        request = ChainRequest(parity_pairs=3, strikes_per_expiry=7)
        result = source.get_chain("spy", request)
        first, second = broker.quote_requests
        # Six expiries: the 1-day one is under min_dte.
        assert len({c.expiry for c in first}) == 6
        # Pass 1: both sides at the three strikes nearest spot.
        assert sorted({c.strike for c in first}) == [99.0, 100.0, 101.0]
        assert {c.right for c in first} == {"C", "P"}
        # Pass 2: out of the money only, nothing quoted twice.
        assert all((c.right == "P") == (c.strike < 100.0) for c in second)
        assert not {(c.expiry, c.strike, c.right) for c in first} & \
            {(c.expiry, c.strike, c.right) for c in second}
        # Missing quotes (strike 101) are dropped; the rest carry the model IV.
        assert all(q.strike != 101.0 for q in result.quotes)
        assert all(q.reference_iv == 0.20 for q in result.quotes)
        assert len(result.expiries) == 6 and result.symbol == "SPY" and result.source == "ibkr"
        assert (result.rate, result.dividend_yield) == (request.rate, request.dividend_yield)

    def test_strike_lists_are_fetched_once_per_day(self):
        broker = _FakeBroker(NOW.date())
        clock = [NOW]
        source = IBKROptionChainSource(broker, now=lambda: clock[0])
        source.get_chain("SPY")
        clock[0] = NOW + timedelta(hours=4)
        source.get_chain("SPY")
        assert broker.strike_requests == 1          # same day: cached
        clock[0] = NOW + timedelta(days=1)
        source.get_chain("SPY")
        assert broker.strike_requests == 2          # next day: fetched again

    def test_no_usable_quotes_raises(self):
        broker = _FakeBroker(NOW.date())
        broker.get_option_quotes = lambda contracts: [None] * len(contracts)
        with pytest.raises(NoMarketDataError):
            IBKROptionChainSource(broker, now=lambda: NOW).get_chain("SPY")
