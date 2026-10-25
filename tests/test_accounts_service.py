"""Accounts from the broker as a snapshot (E1, ``services/accounts.py``):
what is kept, what is left out and named, and that a kept IBKR snapshot is
analysed like any import. Made-up tickers and account ids only."""

from datetime import date, datetime

import pytest

from quant_hub.broker.types import AccountSummary, BrokerPosition
from quant_hub.data.positions import Holding
from quant_hub.data.sources import SyntheticSource
from quant_hub.services.accounts import AccountService, broker_snapshot
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService
from quant_hub.storage.position_store import PositionStore

NOW = datetime(2026, 10, 26, 15, 5)


def stk(symbol, quantity, cost=20.0, account="DU0000001", currency="USD"):
    return BrokerPosition(account, symbol, "STK", quantity, cost, 1, currency)


def opt(symbol, quantity, right="P", strike=40.0, multiplier=100.0, account="DU0000001"):
    return BrokerPosition(account, symbol, "OPT", quantity, 250.0, 2, "USD", right, strike,
                          date(2026, 12, 18), multiplier)


def summary(account="DU0000001", cash=1_000.0, nlv=5_000.0, currency="USD"):
    return AccountSummary(account, currency, nlv, cash, nlv - cash)


class TestMapping:
    def test_stocks_options_and_cash(self):
        read = broker_snapshot(["DU0000001"], [stk("AAA", 100, 20.0), opt("AAA", -2)],
                               [summary(cash=1_000.0)], NOW)
        snap = read.snapshot
        assert (snap.source, snap.name, snap.as_of) == ("IBKR", "IBKR positions",
                                                       datetime(2026, 10, 26))
        equity, option, cash = snap.positions
        assert (equity.kind, equity.quantity, equity.cost_basis) == (Holding.EQUITY, 100, 2_000.0)
        assert equity.price is None and equity.value is None   # priced at the source's close
        assert option.kind is Holding.OPTION and option.quantity == -2
        assert (option.option.underlying, option.option.option_type, option.option.strike,
                option.option.expiry) == ("AAA", "put", 40.0, date(2026, 12, 18))
        assert option.cost_basis == -500.0                     # per-contract cost × contracts
        assert (cash.kind, cash.value, cash.account) == (Holding.CASH, 1_000.0, "DU0000001")
        assert read.left_out == ()

    def test_what_isnt_modelled_is_left_out_and_named(self):
        positions = [stk("AAA", 10), stk("EUX", 5, currency="EUR"), opt("BBB", 1, multiplier=10.0),
                     BrokerPosition("DU0000001", "ESZ6", "FUT", 1, 1.0, 3, "USD")]
        read = broker_snapshot(["DU0000001"], positions,
                               [summary(), summary("DU0000002", currency="EUR")], NOW)
        assert [p.symbol for p in read.snapshot.positions] == ["AAA", "CASH"]
        notes = " | ".join(read.left_out)
        for named in ("EUX", "priced in EUR", "BBB", "multiplier 10", "ESZ6",
                      "isn't modelled", "cash in DU0000002"):
            assert named in notes

    def test_a_closed_position_is_dropped(self):
        read = broker_snapshot(["DU0000001"], [stk("AAA", 0), stk("BBB", 3)], [summary()], NOW)
        assert [p.symbol for p in read.snapshot.positions] == ["BBB", "CASH"]
        assert read.left_out == ()

    def test_accounts_keep_their_order_with_cash_last(self):
        read = broker_snapshot(["DU0000002", "DU0000001"],
                               [stk("AAA", 1, account="DU0000001"),
                                stk("BBB", 1, account="DU0000002")],
                               [summary("DU0000001"), summary("DU0000002")], NOW)
        assert [(p.account, p.symbol) for p in read.snapshot.positions] == [
            ("DU0000002", "BBB"), ("DU0000002", "CASH"), ("DU0000001", "AAA"),
            ("DU0000001", "CASH")]
        assert read.snapshot.accounts == ("DU0000002", "DU0000001")

    def test_net_liquidation_sums_only_when_every_account_sent_one(self):
        both = broker_snapshot(["A", "B"], [], [summary("A", nlv=100.0, cash=10.0),
                                                summary("B", nlv=50.0, cash=5.0)], NOW)
        assert both.net_liquidation == 150.0
        missing = AccountSummary("B", "USD", None, 5.0, None)
        partial = broker_snapshot(["A", "B"], [], [summary("A", nlv=100.0, cash=10.0), missing],
                                  NOW)
        assert partial.net_liquidation is None

    def test_nothing_to_model_says_so(self):
        with pytest.raises(ValueError, match="no positions or cash"):
            broker_snapshot(["DU0000001"], [stk("EUX", 5, currency="EUR")], [], NOW)


class FakeBroker:
    def get_managed_accounts(self):
        return ("DU0000001",)

    def get_positions(self):
        return [stk("AAA", 100), stk("BBB", 50)]

    def get_account_summary(self):
        return [summary(cash=2_000.0, nlv=20_000.0)]


def test_a_kept_ibkr_snapshot_is_analysed_like_an_import():
    store = PositionStore(":memory:")
    try:
        portfolio = PortfolioService(MarketService(sources=[SyntheticSource()]), store)
        read = AccountService(FakeBroker()).snapshot(now=NOW)
        snapshot_id = portfolio.keep(read.snapshot)
        loaded = portfolio.load(snapshot_id)
        assert loaded.source == "IBKR" and len(loaded.positions) == 3
        view = portfolio.analyse(loaded, None, "synthetic", "SPY", 400, 0.04, now=NOW)
        assert sorted(r.symbol for r in view.rows) == ["AAA", "BBB"]
        assert view.cash == 2_000.0
        assert all(r.value > 0 for r in view.rows)             # valued at the last close
    finally:
        store.close()
