"""Activity (TR1): a transaction's rules, its key, and occurrence numbers."""

from datetime import date

import pytest

from quant_hub.data.activity import ActivityFile, Kind, Transaction, occurrences
from quant_hub.data.positions import OptionContract

DAY = date(2026, 3, 2)
CALL = OptionContract("AAPL", "call", 250.0, date(2026, 6, 19))


def buy(**changes) -> Transaction:
    fields = dict(trade_date=DAY, kind=Kind.BUY, amount=-1800.0, account="Brokerage",
                  symbol="AAPL", quantity=10.0, price=180.0)
    return Transaction(**{**fields, **changes})


class TestTrades:
    def test_a_buy_pays_quantity_times_price_plus_costs(self):
        t = buy(amount=-1801.5, commission=1.0, fees=0.5)
        assert t.expected_amount == pytest.approx(-1801.5) and t.multiplier == 1.0

    def test_a_sell_receives_quantity_times_price_less_costs(self):
        t = buy(kind=Kind.SELL, quantity=-10.0, amount=1799.98, fees=0.02)
        assert t.expected_amount == pytest.approx(1799.98)

    def test_an_option_trade_uses_the_multiplier(self):
        t = buy(symbol="AAPL260619C250", quantity=2.0, price=4.15, amount=-830.0, option=CALL)
        assert t.multiplier == 100.0 and t.expected_amount == pytest.approx(-830.0)

    def test_an_option_amount_without_the_multiplier_is_refused(self):
        with pytest.raises(ValueError, match="× 100"):
            buy(symbol="AAPL260619C250", quantity=2.0, price=4.15, amount=-8.30, option=CALL)

    @pytest.mark.parametrize("amount, ok", [(-18.01, True), (-17.99, True),
                                            (-18.02, False), (-18.10, False)])
    def test_one_share_allows_a_cent_and_a_half(self, amount, ok):
        # $0.01 for the amount's rounding, plus half a cent for the price's.
        t = buy(quantity=1.0, price=18.0, amount=-18.0)
        assert t.amount_tolerance == pytest.approx(0.015)
        if ok:
            buy(quantity=1.0, price=18.0, amount=amount)
        else:
            with pytest.raises(ValueError, match="doesn't match"):
                buy(quantity=1.0, price=18.0, amount=amount)

    def test_a_price_rounded_to_the_cent_on_a_cheap_stock(self):
        # 400 shares at a true 1.526 post $610.40; the price column says 1.53.
        t = buy(kind=Kind.SELL, quantity=-400.0, price=1.53, amount=610.40)
        assert t.amount_tolerance == pytest.approx(2.01)
        assert abs(t.amount - t.expected_amount) == pytest.approx(1.60)
        with pytest.raises(ValueError, match="doesn't match"):
            buy(kind=Kind.SELL, quantity=-400.0, price=1.53, amount=609.00)

    def test_the_tolerance_grows_with_shares_not_dollars(self):
        buy(quantity=10_000.0, price=180.0, amount=-1_800_050.0)           # $50.01 allowed
        with pytest.raises(ValueError, match="doesn't match"):
            buy(quantity=10_000.0, price=180.0, amount=-1_800_051.0)

    def test_an_options_tolerance_counts_its_shares(self):
        t = buy(symbol="AAPL260619C250", quantity=2.0, price=4.15, amount=-830.0, option=CALL)
        assert t.amount_tolerance == pytest.approx(1.01)          # 200 shares' half cents

    def test_a_reinvested_dividend_is_a_trade(self):
        t = Transaction(DAY, Kind.REINVEST, -12.5, symbol="VTI", quantity=0.05, price=250.0)
        assert t.expected_amount == pytest.approx(-12.5)


class TestRules:
    @pytest.mark.parametrize("changes, message", [
        (dict(quantity=-10.0), "must be positive"),                     # a buy sells
        (dict(kind=Kind.SELL), "must be negative"),                     # a sell buys
        (dict(price=None), "needs its price"),
        (dict(price=0.0), "price must be positive"),
        (dict(symbol=""), "needs a symbol"),
        (dict(commission=-1.0), "can't be negative"),
        (dict(amount=float("nan")), "must be a number"),
        (dict(settle_date=date(2026, 3, 1)), "settles before"),
        (dict(kind=Kind.EXPIRE, quantity=-1.0, amount=0.0), "only an option"),
    ])
    def test_a_broken_trade_says_what_is_wrong(self, changes, message):
        with pytest.raises(ValueError, match=message):
            buy(**changes)

    def test_cash_only_rows_move_no_shares(self):
        Transaction(DAY, Kind.DIVIDEND, 4.2, symbol="AAPL")
        Transaction(DAY, Kind.INTEREST, 0.31)
        Transaction(DAY, Kind.DIVIDEND, -4.2, symbol="AAPL")          # a reversal is allowed
        with pytest.raises(ValueError, match="quantity must be zero"):
            Transaction(DAY, Kind.DIVIDEND, 4.2, symbol="AAPL", quantity=1.0)

    def test_deposits_and_withdrawals_are_signed_by_direction(self):
        Transaction(DAY, Kind.DEPOSIT, 500.0)
        Transaction(DAY, Kind.WITHDRAWAL, -500.0)
        with pytest.raises(ValueError, match="deposit's amount must be positive"):
            Transaction(DAY, Kind.DEPOSIT, -500.0)
        with pytest.raises(ValueError, match="withdrawal's amount must be negative"):
            Transaction(DAY, Kind.WITHDRAWAL, 500.0)

    def test_splits_and_transfers_move_shares_without_cash(self):
        Transaction(DAY, Kind.SPLIT, 0.0, symbol="AAPL", quantity=30.0)
        Transaction(DAY, Kind.TRANSFER_IN, 0.0, symbol="AAPL", quantity=5.0)
        with pytest.raises(ValueError, match="amount must be zero"):
            Transaction(DAY, Kind.SPLIT, 10.0, symbol="AAPL", quantity=30.0)
        with pytest.raises(ValueError, match="can't be zero"):
            Transaction(DAY, Kind.SPLIT, 0.0, symbol="AAPL")
        with pytest.raises(ValueError, match="must be negative"):
            Transaction(DAY, Kind.TRANSFER_OUT, 0.0, symbol="AAPL", quantity=5.0)

    def test_an_option_event_is_the_options_own_row(self):
        Transaction(DAY, Kind.EXPIRE, 0.0, symbol="AAPL260619C250", quantity=-2.0, option=CALL)

    def test_other_is_kept_whatever_it_holds(self):
        Transaction(DAY, Kind.OTHER, -3.0, symbol="XYZ", quantity=7.0)


class TestKey:
    def test_the_key_is_fixed_decimals_of_the_identifying_fields(self):
        assert buy().key == "Brokerage|2026-03-02|buy|AAPL|10.000000|180.000000|-1800.00"
        assert Transaction(DAY, Kind.DEPOSIT, 500.0).key == "|2026-03-02|deposit||0.000000||500.00"

    def test_minus_zero_and_float_noise_give_the_same_key(self):
        a = Transaction(DAY, Kind.DIVIDEND, 4.2, symbol="AAPL", quantity=-0.0)
        b = Transaction(DAY, Kind.DIVIDEND, 4.2000000001, symbol="AAPL")
        assert a.key == b.key

    def test_fields_outside_the_key_do_not_change_it(self):
        assert buy().key == buy(description="YOU BOUGHT", settle_date=date(2026, 3, 3)).key

    def test_occurrences_count_copies_in_order(self):
        a, b = buy(), buy(quantity=5.0, amount=-900.0)
        assert occurrences([a, b, a, a, b]) == [1, 1, 2, 3, 2]


def test_a_files_span_is_its_first_and_last_trade_date():
    file = ActivityFile((buy(trade_date=date(2026, 3, 9)), buy(), buy(trade_date=date(2026, 3, 5))),
                        "Generic", "history.csv")
    assert (file.first_date, file.last_date) == (date(2026, 3, 2), date(2026, 3, 9))
    assert ActivityFile((), "Generic").first_date is None
