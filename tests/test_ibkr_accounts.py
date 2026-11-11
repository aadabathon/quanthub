"""IBKR accounts, read-only (E1): the callback parsing that differs between
ibapi 9.81 and 10.x, and the positions / account-summary / managed-accounts
plumbing, driven by a fake network that answers on the calling thread.

No TWS, no sockets: the fake stands in for ``EClient``'s request methods and
calls the broker's own callback handlers, as the network thread would.
"""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("ibapi")

from ibapi.wrapper import EWrapper  # noqa: E402

import quant_hub.broker.ibkr as ibkr  # noqa: E402
from quant_hub.broker.base import ConnectionState  # noqa: E402
from quant_hub.broker.types import (  # noqa: E402
    BrokerError,
    NotConnectedError,
    RequestTimeoutError,
)
from quant_hub.config import AppConfig  # noqa: E402
from quant_hub.events import EventBus  # noqa: E402


def stock(symbol="AAA", con_id=1, currency="USD"):
    return SimpleNamespace(secType="STK", symbol=symbol, conId=con_id, currency=currency,
                           right="", strike=0.0, lastTradeDateOrContractMonth="",
                           multiplier="")


def option(symbol="AAA", right="C", strike=50.0, expiry="20261120", multiplier="100"):
    return SimpleNamespace(secType="OPT", symbol=symbol, conId=7, currency="USD", right=right,
                           strike=strike, lastTradeDateOrContractMonth=expiry,
                           multiplier=multiplier)


# --------------------------------------------------------- 9.81 vs 10.x

class TestErrorSignature:
    def test_the_flag_reads_the_installed_wrapper(self):
        import inspect
        has_time = "errorTime" in inspect.signature(EWrapper.error).parameters
        assert ibkr._ERROR_HAS_TIME is has_time

    def test_before_10_33_the_code_comes_first(self):
        assert ibkr.error_fields((200, "No security definition"), False) == (
            200, "No security definition")
        assert ibkr.error_fields((162, "pacing", ""), False) == (162, "pacing")

    def test_from_10_33_a_timestamp_comes_first(self):
        # The timestamp must never be read as the error code.
        args = (1_761_350_400_000, 504, "Not connected", "")
        assert ibkr.error_fields(args, True) == (504, "Not connected")

    def test_keyword_arguments_win(self):
        assert ibkr.error_fields((), False, {"errorCode": 10167, "errorString": "delayed"}) == (
            10167, "delayed")

    @pytest.mark.parametrize("has_time, args", [
        (False, (200, "No security definition")),
        (True, (1_761_350_400_000, 200, "No security definition", "")),
    ])
    def test_the_wrapper_forwards_code_and_message(self, monkeypatch, has_time, args):
        received = []
        owner = SimpleNamespace(_on_error=lambda *a: received.append(a))
        monkeypatch.setattr(ibkr, "_ERROR_HAS_TIME", has_time)
        ibkr._IBApp(owner).error(5, *args)
        assert received == [(5, 200, "No security definition")]


class TestPositionParsing:
    def test_a_stock_with_a_decimal_quantity(self):
        # 10.x sends quantities as Decimal, 9.81 as float.
        p = ibkr.broker_position("DU0000001", stock(), Decimal("12.5"), 41.25)
        assert (p.sec_type, p.symbol, p.quantity, p.average_cost) == ("STK", "AAA", 12.5, 41.25)
        assert isinstance(p.quantity, float)
        assert p.right is p.strike is p.expiry is p.multiplier is None

    @pytest.mark.parametrize("right, expected", [("C", "C"), ("CALL", "C"), ("P", "P"),
                                                 ("put", "P")])
    def test_an_option_and_its_right(self, right, expected):
        p = ibkr.broker_position("DU0000001", option(right=right), -2.0, 310.0)
        assert p.right == expected
        assert (p.strike, p.expiry, p.multiplier) == (50.0, date(2026, 11, 20), 100.0)
        assert p.quantity == -2.0

    def test_an_expiry_with_a_time_suffix(self):
        p = ibkr.broker_position("U1", option(expiry="20261120 16:00 US/Eastern"), 1, 1.0)
        assert p.expiry == date(2026, 11, 20)

    def test_an_unknown_right_is_refused(self):
        with pytest.raises(ValueError, match="right"):
            ibkr.broker_position("U1", option(right="X"), 1, 1.0)


class TestSummaryRows:
    def test_rows_group_by_account(self):
        rows = [("U1", "NetLiquidation", "10500.25", "USD"),
                ("U1", "TotalCashValue", "500.25", "USD"),
                ("U2", "NetLiquidation", "2000", "USD"),
                ("U1", "GrossPositionValue", "10000", "USD"),
                ("U2", "TotalCashValue", "", "USD")]
        u1, u2 = ibkr.summaries_from_rows(rows)
        assert (u1.account, u1.net_liquidation, u1.total_cash, u1.gross_position_value) == (
            "U1", 10500.25, 500.25, 10000.0)
        assert (u2.account, u2.net_liquidation, u2.total_cash) == ("U2", 2000.0, None)
        assert u2.gross_position_value is None and u1.currency == "USD"


# ------------------------------------------------------------ plumbing

class FakeNetwork:
    """``EClient``'s account requests, answered at once on the calling thread
    through the broker's own callback handlers."""

    def __init__(self, broker, positions=(), rows=(), accounts="DU0000001,DU0000002",
                 silent=False, drop=False):
        self.broker, self.positions, self.rows = broker, positions, rows
        self.accounts, self.silent, self.drop = accounts, silent, drop
        self.calls = []

    def reqPositions(self):  # noqa: N802 (ibapi's names)
        self.calls.append("reqPositions")
        if self.drop:
            self.broker._on_connection_closed()
            return
        if self.silent:
            return
        for account, contract, quantity, cost in self.positions:
            self.broker._on_position(account, contract, quantity, cost)
        self.broker._on_request_done(ibkr._POSITIONS_KEY)

    def cancelPositions(self):  # noqa: N802
        self.calls.append("cancelPositions")

    def reqAccountSummary(self, req_id, group, tags):  # noqa: N802
        self.calls.append(("reqAccountSummary", group, tags))
        for row in self.rows:
            self.broker._on_account_summary(req_id, *row)
        self.broker._on_request_done(req_id)

    def cancelAccountSummary(self, req_id):  # noqa: N802
        self.calls.append(("cancelAccountSummary", req_id))

    def reqManagedAccts(self):  # noqa: N802
        self.calls.append("reqManagedAccts")
        self.broker._on_managed_accounts(self.accounts)


def connected(**network):
    broker = ibkr.IBKRBroker(AppConfig(request_timeout_s=0.2), EventBus())
    broker._state = ConnectionState.CONNECTED
    broker._app = FakeNetwork(broker, **network)
    return broker


class TestPositions:
    def test_every_position_then_the_subscription_ends(self):
        broker = connected(positions=[("DU0000001", stock("AAA"), 10, 20.0),
                                      ("DU0000002", option("BBB"), -1, 150.0)])
        positions = broker.get_positions()
        assert [(p.account, p.symbol, p.sec_type) for p in positions] == [
            ("DU0000001", "AAA", "STK"), ("DU0000002", "BBB", "OPT")]
        assert broker._app.calls == ["reqPositions", "cancelPositions"]
        assert ibkr._POSITIONS_KEY not in broker._pending

    def test_an_unreadable_position_fails_the_read(self):
        broker = connected(positions=[("U1", option(right="X"), 1, 1.0)])
        with pytest.raises(BrokerError, match="unreadable position"):
            broker.get_positions()
        assert broker._app.calls[-1] == "cancelPositions"

    def test_no_answer_times_out_and_still_cancels(self):
        broker = connected(silent=True)
        with pytest.raises(RequestTimeoutError):
            broker.get_positions()
        assert broker._app.calls == ["reqPositions", "cancelPositions"]

    def test_a_dropped_connection_fails_the_read(self):
        broker = connected(drop=True)
        with pytest.raises(NotConnectedError):
            broker.get_positions()

    def test_late_updates_after_the_read_are_ignored(self):
        broker = connected()
        broker.get_positions()
        broker._on_position("U1", stock(), 5, 1.0)       # the subscription's tail
        assert ibkr._POSITIONS_KEY not in broker._pending


class TestSummaryAndAccounts:
    def test_the_summary_asks_for_its_tags_and_cancels(self):
        broker = connected(rows=[("DU0000001", "NetLiquidation", "1000", "USD"),
                                 ("DU0000001", "TotalCashValue", "250", "USD")])
        (summary,) = broker.get_account_summary()
        assert (summary.net_liquidation, summary.total_cash) == (1000.0, 250.0)
        request, cancel = broker._app.calls
        assert request == ("reqAccountSummary", "All", ",".join(ibkr.SUMMARY_TAGS))
        assert cancel[0] == "cancelAccountSummary"

    def test_accounts_sent_on_connect_need_no_request(self):
        broker = connected()
        broker._on_managed_accounts("DU0000001, DU0000002,")
        assert broker.get_managed_accounts() == ("DU0000001", "DU0000002")
        assert broker._app.calls == []

    def test_accounts_are_asked_for_when_missing(self):
        broker = connected(accounts="U7")
        assert broker.get_managed_accounts() == ("U7",)
        assert broker._app.calls == ["reqManagedAccts"]

    @pytest.mark.parametrize("method", ["get_positions", "get_account_summary",
                                        "get_managed_accounts"])
    def test_disconnected_reads_say_so(self, method):
        broker = ibkr.IBKRBroker(AppConfig(), EventBus())
        with pytest.raises(NotConnectedError):
            getattr(broker, method)()
