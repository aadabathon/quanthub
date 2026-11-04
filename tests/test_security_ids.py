"""The ISIN bridge (ALLOCATION A1 part 2, D66): ISO 6166's check digit,
IBKR's contract details read for an ISIN (a stand-in network, no TWS), and
the service that serves stored ISINs offline and asks IBKR only when
connected. Made-up symbols; the ISINs are made up with valid check digits,
except ISO's published examples."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from quant_hub.broker.base import ConnectionState
from quant_hub.broker.types import InvalidContractError
from quant_hub.data.holdings import isin_check_digit, valid_isin
from quant_hub.services.security_ids import STALE_AFTER, SecurityIdService
from quant_hub.storage.holdings_store import HoldingsStore

T0 = datetime(2026, 11, 4, 10, 0)


def made_up_isin(body: str) -> str:
    return body + str(isin_check_digit(body))


AAA_ISIN = made_up_isin("US00000AAA0")
BBB_ISIN = made_up_isin("US00000BBB0")


# ----------------------------------------------------------- check digit

@pytest.mark.parametrize("isin", ["US0378331005", "AU0000XVGZA3", "GB0002634946"])
def test_isos_published_examples_check(isin):
    assert valid_isin(isin) and isin_check_digit(isin[:11]) == int(isin[11])


@pytest.mark.parametrize("text", ["US0378331006", "us0378331005", "US037833100", "0S0378331005",
                                  "US03783310055", "", "N/A"])
def test_a_wrong_digit_or_form_is_refused(text):
    assert not valid_isin(text)


def test_a_body_must_be_eleven_letters_or_digits():
    with pytest.raises(ValueError):
        isin_check_digit("US-37833100")


# ------------------------------------------------------- IBKR (stand-in)

def details(symbol, *isins):
    return SimpleNamespace(contract=SimpleNamespace(conId=1, symbol=symbol, strike=0.0),
                           secIdList=[SimpleNamespace(tag="ISIN", value=i) for i in isins])


class FakeNetwork:
    def __init__(self, broker, answers):
        self.broker, self.answers, self.asked = broker, answers, []

    def reqContractDetails(self, req_id, contract):  # noqa: N802 (ibapi's name)
        self.asked.append((contract.symbol, contract.secType, contract.exchange))
        for d in self.answers.get(contract.symbol, ()):
            self.broker._on_contract_details(req_id, d)
        self.broker._on_request_done(req_id)


@pytest.fixture
def broker_with():
    pytest.importorskip("ibapi")
    from quant_hub.broker.ibkr import IBKRBroker
    from quant_hub.config import AppConfig
    from quant_hub.events import EventBus

    def make(answers):
        broker = IBKRBroker(AppConfig(request_timeout_s=0.2), EventBus())
        broker._state = ConnectionState.CONNECTED
        broker._app = FakeNetwork(broker, answers)
        return broker
    return make


def test_the_isin_comes_from_the_contract_details(broker_with):
    from quant_hub.broker.types import ContractSpec
    broker = broker_with({"AAA": [details("AAA", AAA_ISIN.lower())],
                          "BBB": [details("BBB")],
                          "TWO": [details("TWO", AAA_ISIN), details("TWO", AAA_ISIN)],
                          "AMB": [details("AMB", AAA_ISIN), details("AMB", BBB_ISIN)]})
    assert broker.get_isin(ContractSpec("aaa")) == AAA_ISIN
    assert broker._app.asked[0] == ("AAA", "STK", "SMART")
    assert broker.get_isin(ContractSpec("BBB")) is None               # none listed
    assert broker.get_isin(ContractSpec("TWO")) == AAA_ISIN           # one ISIN, twice
    with pytest.raises(InvalidContractError, match="several ISINs"):
        broker.get_isin(ContractSpec("AMB"))                          # never guessed
    with pytest.raises(InvalidContractError, match="knows no"):
        broker.get_isin(ContractSpec("ZZZ"))
    assert broker._pending == {}


# --------------------------------------------------------------- service

class FakeSource:
    def __init__(self, answers, state=ConnectionState.CONNECTED):
        self.answers, self.state, self.asked = answers, state, []

    def get_isin(self, spec):
        self.asked.append(spec.symbol)
        answer = self.answers.get(spec.symbol)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def setup():
    store = HoldingsStore(":memory:")
    clock = {"now": T0}
    yield store, clock
    store.close()


def test_a_connected_lookup_stores_valid_isins_and_says_why_for_the_rest(setup):
    store, clock = setup
    source = FakeSource({"AAA": AAA_ISIN, "NON": None, "BAD": "US0000000001",
                         "ERR": InvalidContractError("IBKR knows no STK ERR")})
    service = SecurityIdService(store, source, now=lambda: clock["now"])
    found, missing = service.isins(["aaa", "NON", "BAD", "ERR", " "])
    assert found == {"AAA": AAA_ISIN}
    assert missing == {"NON": "IBKR lists no ISIN for it",
                       "BAD": "IBKR's ISIN US0000000001 fails its check digit",
                       "ERR": "IBKR: IBKR knows no STK ERR"}
    assert store.security_id("AAA") == (AAA_ISIN, T0) and store.security_id("BAD") is None
    source.asked.clear()
    assert service.isins(["AAA"]) == ({"AAA": AAA_ISIN}, {})          # stored: not asked again
    assert source.asked == []


def test_stored_isins_are_served_offline_and_nothing_is_looked_up(setup):
    store, clock = setup
    store.save_security_id("AAA", AAA_ISIN, "ibkr", T0 - 2 * STALE_AFTER)
    offline = SecurityIdService(store, FakeSource({}, ConnectionState.DISCONNECTED),
                                now=lambda: clock["now"])
    assert offline.isins(["AAA", "BBB"]) == ({"AAA": AAA_ISIN}, {"BBB": "TWS isn't connected"})
    off = SecurityIdService(store, None, now=lambda: clock["now"])
    assert off.isins(["BBB"]) == ({}, {"BBB": "IBKR is off in settings"})


def test_a_stale_isin_is_asked_again_and_kept_when_the_answer_fails(setup):
    store, clock = setup
    store.save_security_id("AAA", AAA_ISIN, "ibkr", T0)
    clock["now"] = T0 + STALE_AFTER + timedelta(days=1)
    failing = FakeSource({"AAA": InvalidContractError("IBKR knows no STK AAA")})
    service = SecurityIdService(store, failing, now=lambda: clock["now"])
    assert service.isins(["AAA"]) == ({"AAA": AAA_ISIN}, {}) and failing.asked == ["AAA"]
    renamed = SecurityIdService(store, FakeSource({"AAA": BBB_ISIN}), now=lambda: clock["now"])
    assert renamed.isins(["AAA"]) == ({"AAA": BBB_ISIN}, {})          # a ticker passed on
    assert store.security_id("AAA") == (BBB_ISIN, clock["now"])
