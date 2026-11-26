"""Returns as held (TR6): the cash rule, the book day by day, the two returns."""

import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.activity import Kind, Transaction
from quant_hub.data.positions import Holding, Position, PositionSnapshot
from quant_hub.models import held as H
from quant_hub.models.ledger import cash_lines, reconcile


def row(day, kind, amount, symbol="", quantity=0.0, price=None, settle=None, account="M",
        typ="Margin"):
    return Transaction(day, kind, amount, account=account, symbol=symbol, quantity=quantity,
                       price=price, settle_date=settle, description=f"ROW ({typ})")


class TestCashType:
    def test_read_from_the_rows_wording(self):
        assert row(date(2026, 1, 2), Kind.DEPOSIT, 10.0, typ="Short").cash_type == "short"
        assert row(date(2026, 1, 2), Kind.DEPOSIT, 10.0, typ="Cash").cash_type == "cash"
        assert Transaction(date(2026, 1, 2), Kind.DEPOSIT, 10.0).cash_type == "cash"


class TestTheCashLines:
    """Section 0's rule, case by case."""

    D = date

    def test_a_short_sale_sits_in_the_short_type(self):
        history = [row(self.D(2026, 6, 1), Kind.DEPOSIT, 500.0),
                   row(self.D(2026, 6, 8), Kind.SHORT, 300.0, "XYZ", -3, 100.0,
                       self.D(2026, 6, 9), typ="Short")]
        assert cash_lines(history, set(), self.D(2026, 6, 10)) == pytest.approx(500.0)

    def test_cash_moves_on_settlement(self):
        history = [row(self.D(2026, 6, 1), Kind.DEPOSIT, 500.0),
                   row(self.D(2026, 6, 10), Kind.BUY, -200.0, "XYZ", 2, 100.0,
                       self.D(2026, 6, 11))]
        assert cash_lines(history, set(), self.D(2026, 6, 10)) == pytest.approx(500.0)
        assert cash_lines(history, set(), self.D(2026, 6, 11)) == pytest.approx(300.0)

    def test_the_core_funds_own_reinvestment_keeps_its_cash(self):
        history = [row(self.D(2026, 6, 1), Kind.DEPOSIT, 500.0),
                   row(self.D(2026, 6, 30), Kind.DIVIDEND, 1.25, "MMFXX"),
                   row(self.D(2026, 6, 30), Kind.REINVEST, -1.25, "MMFXX", 1.25, 1.0)]
        assert cash_lines(history, {"MMFXX"}, self.D(2026, 7, 1)) == pytest.approx(501.25)

    def test_a_margin_loan_has_no_line_and_accounts_add_up(self):
        history = [row(self.D(2026, 6, 1), Kind.BUY, -400.0, "XYZ", 4, 100.0,
                       self.D(2026, 6, 2)),                                # on margin
                   row(self.D(2026, 6, 1), Kind.DEPOSIT, 250.0, account="C", typ="Cash")]
        assert cash_lines(history, set(), self.D(2026, 6, 5)) == pytest.approx(250.0)

    def test_the_replay_passes_or_fails_cash_to_the_cent(self):
        history = [row(self.D(2026, 6, 1), Kind.DEPOSIT, 500.0),
                   row(self.D(2026, 6, 3), Kind.SHORT, 300.0, "XYZ", -3, 100.0,
                       self.D(2026, 6, 4), typ="Short")]

        def export(day, cash):
            return PositionSnapshot((Position("XYZ", Holding.EQUITY, -3.0 if day > 2 else 0.0,
                                              "M"),
                                     Position("MMFXX", Holding.CASH, cash, "M", value=cash)),
                                    datetime(2026, 6, day), "Generic", f"{day}.csv")
        good = reconcile(history, [export(2, 500.0), export(5, 500.0)])
        assert good.cash_reconciled and good.opening_cash == (500.0, 500.0)
        bad = reconcile(history, [export(2, 500.0), export(5, 800.0)])  # the short's cash shown
        assert not bad.cash_reconciled and bad.intervals[0].cash_expected == pytest.approx(500.0)


SESSIONS = pd.bdate_range("2026-03-02", periods=30)


def day(i: int) -> date:
    return SESSIONS[i].date()


def closes(values, start=0) -> pd.Series:
    return pd.Series(np.asarray(values, float), index=SESSIONS[start:start + len(values)])


class TestTheBook:
    def test_units_and_cash_by_trade_date(self):
        history = [row(day(0), Kind.DEPOSIT, 1000.0),
                   row(day(2), Kind.BUY, -500.0, "AAA", 5, 100.0, day(3)),
                   row(date(2026, 3, 7), Kind.DIVIDEND, 2.0, "AAA")]   # a Saturday
        b = H.book(history, set(), SESSIONS)
        assert b.units["AAA"].iloc[1] == 0 and b.units["AAA"].iloc[2] == 5
        assert b.cash.iloc[2] == pytest.approx(500.0)                   # trade date, not settle
        assert b.cash.iloc[4] == pytest.approx(500.0) and b.cash.iloc[5] == pytest.approx(502.0)

    def test_core_funds_are_cash_and_unread_rows_move_only_cash(self):
        history = [row(day(0), Kind.DEPOSIT, 100.0),
                   row(day(1), Kind.REINVEST, -1.0, "MMFXX", 1.0, 1.0),
                   row(day(1), Kind.OTHER, -3.0, "WEIRD", 2.0)]
        b = H.book(history, {"MMFXX"}, SESSIONS)
        assert "MMFXX" not in b.units and "WEIRD" not in b.units
        assert b.cash.iloc[1] == pytest.approx(97.0)

    def test_value_carries_a_name_without_a_close_and_says_how_much(self):
        history = [row(day(0), Kind.DEPOSIT, 1000.0),
                   row(day(0), Kind.BUY, -300.0, "AAA", 3, 100.0),
                   row(day(0), Kind.BUY, -100.0, "ZZZ", 10, 10.0)]
        b = H.book(history, set(), SESSIONS)
        v = H.value(b, {"AAA": closes([100.0] * 10 + [110.0] * 20)})
        assert v.value.iloc[15] == pytest.approx(600.0 + 330.0 + 100.0)
        assert v.carried.iloc[15] == pytest.approx(100.0 / 430.0)
        assert v.carried_names == ("ZZZ",)

    def test_flows_are_deposits_withdrawals_and_shares_moved(self):
        history = [row(day(0), Kind.DEPOSIT, 1000.0),
                   row(day(4), Kind.WITHDRAWAL, -200.0),
                   row(day(6), Kind.TRANSFER_IN, 0.0, "AAA", 2.0),
                   row(day(8), Kind.DIVIDEND, 5.0, "AAA")]
        b = H.book(history, set(), SESSIONS)
        v = H.value(b, {"AAA": closes([50.0] * 30)})
        f = H.flows(history, b, v.price)
        assert (f.iloc[0], f.iloc[4], f.iloc[6], f.iloc[8]) == (1000.0, -200.0, 100.0, 0.0)


class TestTheReturns:
    def test_a_deposit_doesnt_move_the_time_weighted_return(self):
        value = pd.Series([100.0, 110.0, 160.0, 176.0], index=SESSIONS[:4])
        flow = pd.Series([0.0, 0.0, 50.0, 0.0], index=SESSIONS[:4])     # +50 on day 2
        daily, stopped = H.time_weighted(value, flow)
        assert np.allclose(daily, [0.10, 0.0, 0.10]) and stopped == ""

    def test_the_money_weighted_rate_by_hand(self):
        rate, note = H.money_weighted(1000.0, [(0.5, 500.0)], 1000 * 1.1 + 500 * 1.1 ** 0.5, 1.0)
        assert rate == pytest.approx(0.10, abs=1e-9) and note == ""
        rate, _ = H.money_weighted(1000.0, [(0.25, -300.0)], 1000 * 1.05 - 300 * 1.05 ** 0.75,
                                   1.0)
        assert rate == pytest.approx(0.05, abs=1e-9)

    def test_money_in_and_out_with_two_rates_says_so(self):
        # -1 now, +2.3 in a year, -1.32 in two: 10% and 20% both solve it.
        rate, note = H.money_weighted(1.0, [(1.0, -2.3)], -1.32, 2.0)
        assert rate is None and note.startswith("no unique rate")

    def test_the_same_dollars_in_the_market(self):
        value = pd.Series([1000.0, 1100.0, 1700.0], index=SESSIONS[:3])
        flow = pd.Series([0.0, 0.0, 500.0], index=SESSIONS[:3])
        spy = pd.Series([100.0, 110.0, 120.0], index=SESSIONS[:3])
        m = H.market_book("SPY", value, flow, spy)
        assert m.value_end == pytest.approx(10 * 120 + 500)           # 10 units, then 500 more
        assert m.twr == pytest.approx(0.2)

    def test_the_window_starts_where_the_book_is_priced(self):
        valued = H.Valued(pd.Series([0.0, 100.0, 100.0, 100.0, 100.0], index=SESSIONS[:5]),
                          pd.Series([0.0, 0.5, 0.05, 0.2, 0.0], index=SESSIONS[:5]),
                          pd.DataFrame(index=SESSIONS[:5]), ())
        assert H.window_start(valued) == 4
        assert H.window_start(valued, limit=0.25) == 2


def test_the_whole_report_end_to_end():
    history = [row(day(0), Kind.DEPOSIT, 1000.0),
               row(day(0), Kind.BUY, -1000.0, "AAA", 10, 100.0),
               row(day(10), Kind.DEPOSIT, 550.0),
               row(day(10), Kind.BUY, -550.0, "AAA", 5, 110.0)]
    prices = closes(list(np.linspace(100, 110, 11)) + list(np.linspace(110, 121, 19)))
    report = H.held(history, set(), SESSIONS, {"AAA": prices}, ("SPY", prices))
    assert report.start == day(0) and report.end == day(29) and report.reason == ""
    assert report.value.iloc[-1] == pytest.approx(15 * 121.0)
    # The deposit counts at the start of its day and sits idle until the buy at
    # the close, so that day's return is the 10 shares' gain over V + F.
    p = prices.to_numpy()
    on_deposit_day = 1 + 10 * (p[10] - p[9]) / (10 * p[9] + 550)
    assert report.returns.twr == pytest.approx(p[9] / p[0] * on_deposit_day * p[29] / p[10] - 1)
    assert report.deposits == pytest.approx(550.0)
    assert report.market.value_end == pytest.approx(15 * 121.0)     # the market is AAA here
    assert report.returns.mwr == pytest.approx(report.market.mwr)


def test_the_service_measures_from_the_stored_history(tmp_path):
    from quant_hub.data.types import PriceHistory
    from quant_hub.services.activity import ActivityService
    from quant_hub.services.market_service import MarketService
    from quant_hub.storage.activity_store import ActivityStore

    class Source:
        name = "test"

        def get_history(self, symbol, start, end, basis):
            if symbol not in ("AAA", "SPY"):
                raise ValueError(f"no bars for {symbol}")
            index = pd.bdate_range("2026-01-02", "2026-04-30")
            c = np.linspace(10, 12, len(index)) if symbol == "AAA" else \
                np.linspace(400, 420, len(index))
            return PriceHistory(symbol, "test", pd.DataFrame(
                {"open": c, "high": c, "low": c, "close": c, "volume": np.full(len(index), 1e6)},
                index=index), basis)

        def get_quote(self, symbol):
            raise NotImplementedError

    path = tmp_path / "history.csv"
    path.write_text("date,account,kind,symbol,quantity,price,commission,fees,amount\n"
                    "2026-03-02,Brokerage,deposit,,,,,,200.00\n"
                    "2026-03-02,Brokerage,buy,AAA,10,10.00,0,0,-100.00\n"
                    "2026-03-03,Brokerage,buy,ZZZ,1,5.00,0,0,-5.00\n", encoding="utf-8")
    store = ActivityStore(":memory:")
    service = ActivityService(store)
    service.import_activity(path)
    report, missing = service.held(MarketService([Source()]), "test",
                                   now=datetime(2026, 4, 30, 18))
    assert "ZZZ" in missing and report.carried_names == ("ZZZ",)
    assert report.start == date(2026, 3, 2) and report.returns.twr > 0
    assert report.market is not None and math.isfinite(report.market.value_end)
    store.close()


def test_the_desks_cash_comes_from_the_history_once_it_reconciles(tmp_path):
    from quant_hub.services.activity import ActivityService
    from quant_hub.storage.activity_store import ActivityStore
    from quant_hub.storage.position_store import PositionStore

    path = tmp_path / "history.csv"
    path.write_text("date,account,kind,symbol,quantity,price,commission,fees,amount,settle_date\n"
                    "2026-03-02,B,deposit,,,,,,1000.00,2026-03-02\n"
                    "2026-03-10,B,buy,AAA,5,100.00,0,0,-500.00,2026-03-11\n", encoding="utf-8")
    store, positions = ActivityStore(":memory:"), PositionStore(":memory:")
    service = ActivityService(store, positions)
    service.import_activity(path)
    # Taken after the buy: its shares are there, its cash hasn't settled.
    export = PositionSnapshot((Position("AAA", Holding.EQUITY, 5.0, "B"),
                               Position("MMFXX", Holding.CASH, 1000.0, "B", value=1000.0)),
                              datetime(2026, 3, 10), "Generic", "mar10.csv")
    positions.save(export, datetime(2026, 3, 10, 15), "aa")
    assert service.reconcile().cash_reconciled
    held = service.current_holdings()
    assert (held.cash, held.cash_from) == (pytest.approx(500.0), "history")
    store.close(), positions.close()
