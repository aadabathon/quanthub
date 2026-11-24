"""The replay against positions snapshots (TR2)."""

from datetime import date, datetime

import pytest

from quant_hub.data.activity import Kind, Transaction
from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot
from quant_hub.models.ledger import reconcile


def snap(day: date, *holdings: tuple, cash: float = 1000.0, account: str = "Brokerage",
         name: str = "") -> PositionSnapshot:
    positions = [Position(symbol, Holding.EQUITY, quantity, account)
                 for symbol, quantity in holdings]
    positions.append(Position("MMFXX", Holding.CASH, cash, account, value=cash))
    return PositionSnapshot(tuple(positions), datetime.combine(day, datetime.min.time()),
                            "Generic", name or f"{day}.csv")


def trade(day: date, symbol: str, quantity: float, price: float = 10.0,
          account: str = "Brokerage") -> Transaction:
    kind = Kind.BUY if quantity > 0 else Kind.SELL
    return Transaction(day, kind, -quantity * price, account=account, symbol=symbol,
                       quantity=quantity, price=price)


JUN1, JUN15, JUL1 = date(2026, 6, 1), date(2026, 6, 15), date(2026, 7, 1)
HISTORY = [trade(date(2026, 5, 4), "AAA", 10), trade(date(2026, 5, 20), "BBB", 5),
           trade(JUN15, "AAA", 4), trade(JUN15, "BBB", -5)]


class TestReplay:
    def test_a_complete_history_explains_every_snapshot(self):
        result = reconcile(HISTORY, [snap(JUN1, ("AAA", 10), ("BBB", 5)),
                                     snap(JUL1, ("AAA", 14))])
        assert result.reconciled and result.opening == ()
        (interval,) = result.intervals
        assert {(h.symbol, h.before, h.after, h.traded) for h in interval.holdings} == {
            ("AAA", 10.0, 14.0, 4.0), ("BBB", 5.0, 0.0, -5.0)}
        assert (result.first_day, result.last_day) == (date(2026, 5, 4), JUN15)

    def test_a_missing_trade_is_a_residual_of_its_size(self):
        result = reconcile(HISTORY[:3], [snap(JUN1, ("AAA", 10), ("BBB", 5)),
                                         snap(JUL1, ("AAA", 14))])
        (gap,) = result.intervals[0].gaps
        assert (gap.symbol, gap.residual) == ("BBB", pytest.approx(-5.0))
        assert not result.reconciled

    def test_a_position_held_before_the_history_is_an_opening_gap(self):
        result = reconcile(HISTORY[1:], [snap(JUN1, ("AAA", 10), ("BBB", 5))])
        (gap,) = result.opening
        assert (gap.symbol, gap.after, gap.traded, gap.residual) == ("AAA", 10.0, 0.0, 10.0)

    def test_order_of_snapshots_does_not_matter_but_one_per_day(self):
        ordered = reconcile(HISTORY, [snap(JUL1, ("AAA", 14)), snap(JUN1, ("AAA", 10), ("BBB", 5))])
        assert ordered.intervals[0].start.date() == JUN1
        with pytest.raises(ValueError, match="same day"):
            reconcile(HISTORY, [snap(JUN1), snap(JUN1, name="again.csv")])


class TestSameDay:
    def test_a_trade_on_the_later_snapshots_day_may_be_in_it(self):
        result = reconcile(HISTORY, [snap(JUN1, ("AAA", 10), ("BBB", 5)),
                                     snap(JUN15, ("AAA", 14))])
        aaa = next(h for h in result.intervals[0].holdings if h.symbol == "AAA")
        assert aaa.reconciled and aaa.placement == "2026-06-15's trades before the later snapshot"

    def test_or_after_it(self):
        result = reconcile(HISTORY, [snap(JUN1, ("AAA", 10), ("BBB", 5)),
                                     snap(JUN15, ("AAA", 10), ("BBB", 5))])
        assert result.reconciled
        aaa = next(h for h in result.intervals[0].holdings if h.symbol == "AAA")
        assert aaa.placement == "2026-06-15's trades after the later snapshot"

    def test_a_trade_on_the_earlier_snapshots_day_may_be_already_in_it(self):
        result = reconcile(HISTORY, [snap(JUN15, ("AAA", 14)), snap(JUL1, ("AAA", 14))])
        assert result.reconciled and result.intervals[0].holdings[0].traded == 0.0

    def test_the_first_snapshots_day_counts_for_the_opening(self):
        assert reconcile(HISTORY, [snap(JUN15, ("AAA", 14))]).opening == ()
        assert reconcile(HISTORY, [snap(JUN15, ("AAA", 10), ("BBB", 5))]).opening == ()


class TestWhatMoves:
    def test_other_rows_move_nothing_so_they_show_as_residuals(self):
        odd = Transaction(JUN15, Kind.OTHER, 0.0, account="Brokerage", symbol="AAA",
                          quantity=3.0)
        result = reconcile([*HISTORY[:2], odd], [snap(JUN1, ("AAA", 10), ("BBB", 5)),
                                                 snap(JUL1, ("AAA", 13), ("BBB", 5))])
        (gap,) = result.intervals[0].gaps
        assert gap.residual == pytest.approx(3.0)

    def test_a_cash_fund_is_cash(self):
        dividend = Transaction(JUN15, Kind.DIVIDEND, 2.5, account="Brokerage", symbol="MMFXX")
        reinvest = Transaction(JUN15, Kind.REINVEST, -2.5, account="Brokerage", symbol="MMFXX",
                               quantity=2.5, price=1.0)
        result = reconcile([*HISTORY[:2], dividend, reinvest],
                           [snap(JUN1, ("AAA", 10), ("BBB", 5), cash=1000.0),
                            snap(JUL1, ("AAA", 10), ("BBB", 5), cash=1002.5)])
        interval = result.intervals[0]
        assert interval.reconciled and {h.symbol for h in interval.holdings} == {"AAA", "BBB"}
        assert interval.cash_moved == pytest.approx(2.5)
        assert interval.cash_residual == pytest.approx(0.0)

    def test_cash_follows_the_amounts(self):
        result = reconcile(HISTORY, [snap(JUN1, ("AAA", 10), ("BBB", 5), cash=1000.0),
                                     snap(JUL1, ("AAA", 14), cash=1000.0)])
        interval = result.intervals[0]
        assert interval.cash_moved == pytest.approx(10.0)          # -40 bought, +50 sold
        assert interval.cash_residual == pytest.approx(-10.0)

    def test_an_option_is_its_own_holding(self):
        put = OptionContract("AAA", "put", 5.0, date(2026, 6, 19))
        bought = Transaction(JUN15, Kind.BUY, -6.0, account="Brokerage", symbol="AAA260619P5",
                             quantity=2.0, price=0.03, option=put)
        expired = Transaction(date(2026, 6, 22), Kind.EXPIRE, 0.0, account="Brokerage",
                              symbol="AAA260619P5", quantity=-2.0, option=put)
        result = reconcile([*HISTORY[:2], bought, expired],
                           [snap(JUN1, ("AAA", 10), ("BBB", 5)), snap(JUL1, ("AAA", 10), ("BBB", 5))])
        assert result.reconciled
        option = next(h for h in result.intervals[0].holdings if h.symbol == "AAA260619P5")
        assert (option.before, option.after, option.traded) == (0.0, 0.0, 0.0)


class TestAccounts:
    def test_snapshots_naming_the_historys_accounts_are_compared_by_account(self):
        moved = [trade(date(2026, 5, 4), "AAA", 10, account="IRA"),
                 trade(date(2026, 5, 4), "AAA", 5, account="Brokerage")]
        both = PositionSnapshot((Position("AAA", Holding.EQUITY, 10.0, "IRA"),
                                 Position("AAA", Holding.EQUITY, 5.0, "Brokerage")),
                                datetime(2026, 6, 1), "Generic", "both.csv")
        result = reconcile(moved, [both])
        assert result.by_account and result.opening == ()
        swapped = PositionSnapshot((Position("AAA", Holding.EQUITY, 5.0, "IRA"),
                                    Position("AAA", Holding.EQUITY, 10.0, "Brokerage")),
                                   datetime(2026, 6, 1), "Generic", "swapped.csv")
        assert len(reconcile(moved, [swapped]).opening) == 2

    def test_an_all_accounts_view_is_compared_by_symbol(self):
        moved = [trade(date(2026, 5, 4), "AAA", 10, account="IRA"),
                 trade(date(2026, 5, 4), "AAA", 5, account="Brokerage")]
        result = reconcile(moved, [snap(JUN1, ("AAA", 15), account="All Accounts")])
        assert not result.by_account and result.opening == ()
