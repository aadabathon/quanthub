"""Lots, round trips and the basis check (TR3)."""

from datetime import date, datetime

import pytest

from quant_hub.data.activity import Kind, Transaction
from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot
from quant_hub.models.lots import basis_checks, round_trips

D = [date(2026, 3, d) for d in range(1, 32)]


def tx(day: int, kind: Kind, q: float, price: float | None, amount: float | None = None,
       symbol: str = "AAA", account: str = "Brokerage", **extra) -> Transaction:
    if amount is None:
        amount = -q * price
    return Transaction(D[day - 1], kind, amount, account=account, symbol=symbol, quantity=q,
                       price=price, **extra)


def buy(day, q, price, **kw):
    return tx(day, Kind.BUY, q, price, **kw)


def sell(day, q, price, **kw):
    return tx(day, Kind.SELL, -q, price, **kw)


class TestFifo:
    def test_a_sale_closes_the_oldest_lots_first(self):
        (trip,) = round_trips([buy(1, 10, 10), buy(2, 10, 12), sell(3, 15, 15)])
        first, second = trip.pieces
        assert (first.units, first.opened, first.pnl) == (10, D[0], pytest.approx(50.0))
        assert (second.units, second.opened, second.pnl) == (5, D[1], pytest.approx(15.0))
        assert trip.closed is None and trip.open_units == 5
        assert trip.realized == pytest.approx(65.0)

    def test_costs_and_fees_are_inside_the_cash_per_unit(self):
        (trip,) = round_trips([buy(1, 10, 10, amount=-101.0, commission=1.0),
                               sell(5, 10, 12, amount=119.5, fees=0.5)])
        (piece,) = trip.pieces
        assert (piece.open_cash, piece.close_cash) == (pytest.approx(-10.1), pytest.approx(11.95))
        assert trip.realized == pytest.approx(18.5) and piece.days_held == 4
        assert (piece.open_fill, piece.close_fill) == (10.0, 12.0)

    def test_flat_to_flat_is_one_round_trip(self):
        trips = round_trips([buy(1, 10, 10), sell(5, 10, 11), buy(8, 4, 9), sell(9, 4, 8)])
        assert [(t.opened, t.closed, t.realized) for t in trips] == [
            (D[0], D[4], pytest.approx(10.0)), (D[7], D[8], pytest.approx(-4.0))]
        assert trips[0].invested == pytest.approx(100.0)
        assert trips[0].return_on_invested == pytest.approx(0.10)

    def test_a_trade_through_zero_closes_one_trip_and_opens_the_next(self):
        trips = round_trips([buy(1, 5, 10), sell(2, 8, 11)])
        assert [(t.side, t.closed, t.peak_units) for t in trips] == [
            ("long", D[1], 5), ("short", None, 3)]
        assert trips[1].open_units == 3

    def test_accounts_and_symbols_are_kept_apart(self):
        trips = round_trips([buy(1, 1, 10), buy(1, 1, 10, account="IRA"),
                             buy(1, 1, 10, symbol="BBB")])
        assert len(trips) == 3


class TestShortsAndOptions:
    def test_a_short_and_its_covers(self):
        (trip,) = round_trips([tx(1, Kind.SHORT, -10, 25.0),
                               tx(5, Kind.COVER, 6, 20.0), tx(8, Kind.COVER, 4, 30.0)])
        assert trip.side == "short" and trip.invested == pytest.approx(250.0)
        assert [p.pnl for p in trip.pieces] == [pytest.approx(30.0), pytest.approx(-20.0)]
        assert trip.realized == pytest.approx(10.0) and trip.closed == D[7]

    def test_an_expired_long_option_loses_its_cost(self):
        put = OptionContract("AAA", "put", 5.0, date(2026, 3, 20))
        (trip,) = round_trips([
            tx(2, Kind.BUY, 2, 0.03, amount=-6.31, symbol="AAA260320P5", option=put),
            tx(23, Kind.EXPIRE, -2, None, amount=0.0, symbol="AAA260320P5", option=put)])
        (piece,) = trip.pieces
        assert trip.option and piece.close_fill == 0.0
        assert trip.realized == pytest.approx(-6.31)


class TestCorporateActions:
    def test_a_split_scales_the_lots_without_a_free_lot(self):
        (trip,) = round_trips([buy(1, 10, 100), tx(5, Kind.SPLIT, 10, None, amount=0.0),
                               sell(9, 20, 60)])
        (piece,) = trip.pieces
        assert (piece.units, piece.open_cash) == (pytest.approx(20), pytest.approx(-50.0))
        assert trip.realized == pytest.approx(200.0)

    def test_shares_transferred_in_have_an_unknown_cost(self):
        (trip,) = round_trips([tx(1, Kind.TRANSFER_IN, 10, None, amount=0.0),
                               sell(5, 10, 12)])
        assert trip.pieces[0].pnl is None and trip.realized is None
        assert trip.return_on_invested is None

    def test_shares_transferred_out_have_no_proceeds(self):
        (trip,) = round_trips([buy(1, 10, 10), tx(5, Kind.TRANSFER_OUT, -10, None, amount=0.0)])
        assert trip.pieces[0].pnl is None


class TestTheDay:
    def test_a_day_trade_listed_newest_first_is_a_long(self):
        (trip,) = round_trips([sell(3, 10, 11), buy(3, 10, 10)])     # the broker's order
        assert (trip.side, trip.realized) == ("long", pytest.approx(10.0))

    def test_income_sits_beside_the_trip_even_after_it_closes(self):
        dividend = Transaction(D[3], Kind.DIVIDEND, 1.5, account="Brokerage", symbol="AAA")
        late = Transaction(D[9], Kind.DIVIDEND, 0.5, account="Brokerage", symbol="AAA")
        fee = Transaction(D[4], Kind.FEE, -0.05, account="Brokerage", symbol="AAA")
        (trip,) = round_trips([buy(1, 10, 10), dividend, fee, sell(8, 10, 10), late])
        assert trip.income == pytest.approx(1.95) and trip.realized == pytest.approx(0.0)

    def test_a_cash_fund_is_not_a_position(self):
        reinvest = tx(5, Kind.REINVEST, 2.5, 1.0, symbol="MMFXX")
        assert round_trips([reinvest], cash_funds={"MMFXX"}) == ()
        assert len(round_trips([reinvest])) == 1


def snapshot(day: int, units: float, basis: float, symbol: str = "AAA",
             account: str = "Brokerage") -> PositionSnapshot:
    return PositionSnapshot((Position(symbol, Holding.EQUITY, units, account, cost_basis=basis),
                             Position("MMFXX", Holding.CASH, 100.0, account, value=100.0)),
                            datetime.combine(D[day - 1], datetime.min.time()), "Generic",
                            f"day{day}.csv")


class TestBasis:
    HISTORY = [buy(1, 10, 10), buy(2, 10, 20), sell(3, 10, 25)]

    def test_fifo_and_average_cost_tell_the_brokers_method_apart(self):
        (fifo,) = basis_checks(self.HISTORY, [snapshot(5, 10, 200.0)])
        assert (fifo.fifo, fifo.average) == (pytest.approx(200.0), pytest.approx(150.0))
        assert fifo.fifo_matches and not fifo.average_matches
        (average,) = basis_checks(self.HISTORY, [snapshot(5, 10, 150.0)])
        assert average.average_matches and not average.fifo_matches

    def test_the_snapshots_own_day_is_placed_by_its_units(self):
        before, = basis_checks(self.HISTORY, [snapshot(3, 20, 300.0)])     # before the sale
        after, = basis_checks(self.HISTORY, [snapshot(3, 10, 200.0)])      # after it
        assert before.fifo_matches and after.fifo_matches

    def test_a_snapshot_without_the_historys_accounts_sums_across_them(self):
        history = [buy(1, 5, 10), buy(1, 5, 12, account="IRA")]
        (check,) = basis_checks(history, [snapshot(4, 10, 110.0, account="All Accounts")])
        assert check.account == "" and check.fifo_matches

    def test_a_mismatch_is_shown_not_hidden(self):
        (check,) = basis_checks(self.HISTORY, [snapshot(5, 10, 230.0)])    # say, a wash sale
        assert not check.fifo_matches and not check.average_matches
