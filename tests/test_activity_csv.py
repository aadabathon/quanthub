"""Activity from CSV (TR1, TR1b): the generic layout and Fidelity's."""

import io
from datetime import date

import pytest

from quant_hub.data.activity import Kind
from quant_hub.importers.activity_csv import ActivityFileError, read_activity_csv

GENERIC = """date,account,kind,symbol,quantity,price,commission,fees,amount,description
2026-03-02,Brokerage,buy,AAPL,10,180.00,0,0,-1800.00,bought
03/20/2026,Brokerage,Sell,aapl,-10,$190.00,0,0.02,"$1,899.98",sold
2026-03-31,Brokerage,deposit,,,,,,500.00,
2026-04-01,Brokerage,Transfer In,VTI,5,,,,0,from another broker
2026-04-02,Brokerage,buy,-AAPL260619C250,2,4.15,1.30,0.04,(831.34),opening a call
"""


def read(text: str):
    return read_activity_csv(io.StringIO(text), "history.csv")


class TestGeneric:
    @pytest.fixture
    def file(self):
        return read(GENERIC)

    def test_the_file_and_its_rows(self, file):
        assert (file.source, file.name, file.problems) == ("Generic", "history.csv", ())
        assert [t.kind for t in file.transactions] == [
            Kind.BUY, Kind.SELL, Kind.DEPOSIT, Kind.TRANSFER_IN, Kind.BUY]
        assert (file.first_date, file.last_date) == (date(2026, 3, 2), date(2026, 4, 2))

    def test_a_trade(self, file):
        sell = file.transactions[1]
        assert (sell.trade_date, sell.account, sell.symbol, sell.quantity, sell.price,
                sell.fees, sell.amount, sell.description) == (
            date(2026, 3, 20), "Brokerage", "AAPL", -10.0, 190.0, 0.02, 1899.98, "sold")

    def test_empty_cells_are_zero_or_missing(self, file):
        deposit = file.transactions[2]
        assert (deposit.symbol, deposit.quantity, deposit.price, deposit.commission,
                deposit.fees) == ("", 0.0, None, 0.0, 0.0)

    def test_an_option_is_read_from_its_symbol(self, file):
        option = file.transactions[4]
        assert option.symbol == "AAPL260619C250" and option.multiplier == 100.0
        assert (option.option.underlying, option.option.strike) == ("AAPL", 250.0)
        assert option.amount == -831.34                    # (831.34) is negative


class TestProblems:
    def test_a_bad_row_is_reported_with_its_line_and_the_rest_is_read(self):
        text = GENERIC.replace("2026-03-31,Brokerage,deposit,,,,,,500.00",
                               "2026-03-31,Brokerage,deposit,,,,,,-500.00")
        file = read(text)
        assert len(file.transactions) == 4
        (problem,) = file.problems
        assert problem.line == 4 and "deposit's amount must be positive" in problem.message

    @pytest.mark.parametrize("row, message", [
        ("2026-03-02,B,purchase,AAPL,1,1,0,0,-1", "unknown kind 'purchase'"),
        ("March 2,B,buy,AAPL,1,1,0,0,-1", "isn't a date"),
        ("2026-03-02,B,buy,AAPL,one,1,0,0,-1", "quantity 'one' isn't a number"),
        ("2026-03-02,B,buy,AAPL,1,1,0,0,", "no amount"),
        ("2026-03-02,B,buy,AAPL,1,1,0,0,-5", "doesn't match"),
    ])
    def test_each_kind_of_bad_row(self, row, message):
        file = read("date,account,kind,symbol,quantity,price,commission,fees,amount\n"
                    "2026-03-01,B,deposit,,,,,,10\n" + row + "\n")
        (problem,) = file.problems
        assert problem.line == 3 and message in problem.message

    def test_blank_lines_are_skipped_and_lines_still_count(self):
        file = read("date,kind,amount\n\n2026-03-01,deposit,10\n\n2026-03-02,deposit,-1\n")
        assert [p.line for p in file.problems] == [5]

    def test_a_preamble_before_the_header_is_skipped(self):
        file = read("My history\nexported today\n\ndate,kind,amount\n2026-03-01,deposit,10\n")
        assert len(file.transactions) == 1

    def test_no_header_is_an_error_that_shows_the_layout(self):
        with pytest.raises(ActivityFileError, match="date,account,kind"):
            read("when,what,how much\n2026-03-01,deposit,10\n")

    def test_a_header_and_nothing_else_is_an_error(self):
        with pytest.raises(ActivityFileError, match="no transactions"):
            read("date,kind,amount\n")

    def test_a_missing_file_is_an_error(self, tmp_path):
        with pytest.raises(ActivityFileError, match="couldn't read"):
            read_activity_csv(tmp_path / "absent.csv")

    def test_a_file_on_disk_is_named_after_itself(self, tmp_path):
        path = tmp_path / "history.csv"
        path.write_text("﻿date,kind,amount\n2026-03-01,deposit,10\n", encoding="utf-8")
        assert read_activity_csv(path).name == "history.csv"


# Made up, in the layout of Fidelity's Activity & Orders history download:
# two blank lines, the header, rows newest first, a blank line, a footer.
FIDELITY = (
    "﻿\n"
    "\n"
    "Run Date,Account,Account Number,Action,Symbol,Description,Type,Price ($),Quantity,"
    "Commission ($),Fees ($),Accrued Interest ($),Amount ($),Settlement Date\n"
    '04/02/2026,Individual,Z12345678,YOU SOLD OPENING TRANSACTION CALL (AAPL) APPLE INC JUN 19 '
    '26 $250 (100 SHS) (Margin), -AAPL260619C250,CALL (AAPL) APPLE INC JUN 19 26 $250,Margin,'
    '4.15,-1,"0.65","0.02","","414.33",04/03/2026\n'
    '03/31/2026,Individual,Z12345678,Electronic Funds Transfer Received (Cash),,No Description,'
    'Cash,"",0,"","","","500",\n'
    "03/31/2026,ROTH IRA,X87654321,REINVESTMENT FIDELITY GOVERNMENT CASH RESERVES (FDRXX) "
    '(Cash),FDRXX,FIDELITY GOVERNMENT CASH RESERVES,Cash,1,2.5,"","","","-2.50",\n'
    "03/31/2026,ROTH IRA,X87654321,DIVIDEND RECEIVED FIDELITY GOVERNMENT CASH RESERVES (FDRXX) "
    '(Cash),FDRXX,FIDELITY GOVERNMENT CASH RESERVES,Cash,"",0,"","","","2.50",\n'
    "03/20/2026,Individual,Z12345678,YOU SOLD PENNY CO INC COM (PNYC) (Margin),PNYC,"
    'PENNY CO INC COM,Margin,1.53,-400,"","","","610.4",03/23/2026\n'
    "03/20/2026,Individual,Z12345678,YOU BOUGHT APPLE INC (AAPL) (Cash),AAPL,APPLE INC,Cash,"
    '180,10,"","","","-1800",03/23/2026\n'
    "03/16/2026,Individual,Z12345678,YOU BOUGHT US TREASURY NOTE (91282CAB7) (Cash),91282CAB7,"
    'US TREASURY NOTE,Cash,99.5,1000,"","","4.10","-999.10",03/17/2026\n'
    "\n"
    '"Made-up disclaimer: a test fixture in the layout of a broker\'s history download."\n'
    "\n"
    '"A second disclaimer line, as the real download has several."\n'
    "\n"
    "1234567.1.0\n"
    "\n"
    "Date downloaded 04/02/2026 9:15 am\n"
)
FIDELITY_BUY = FIDELITY.splitlines()[8]


class TestFidelity:
    @pytest.fixture
    def file(self):
        return read(FIDELITY)

    def test_recognized_by_its_header_and_the_footer_skipped(self, file):
        assert (file.source, len(file.transactions), file.problems) == ("Fidelity", 7, ())
        assert (file.first_date, file.last_date) == (date(2026, 3, 16), date(2026, 4, 2))

    def test_only_wording_seen_in_an_export_gets_a_kind(self, file):
        assert [t.kind for t in file.transactions] == [
            Kind.OTHER, Kind.OTHER, Kind.REINVEST, Kind.DIVIDEND, Kind.SELL, Kind.BUY, Kind.OTHER]

    def test_the_rest_is_kept_as_other_and_named(self, file):
        assert file.unrecognized == (
            "YOU SOLD OPENING TRANSACTION CALL (AAPL) APPLE INC JUN 19 26 $250 (100 SHS) (Margin)",
            "Electronic Funds Transfer Received (Cash)",
            "YOU BOUGHT US TREASURY NOTE (91282CAB7) (Cash)")

    def test_a_buy(self, file):
        buy = file.transactions[5]
        assert (buy.account, buy.symbol, buy.quantity, buy.price, buy.amount, buy.trade_date,
                buy.settle_date, buy.description) == (
            "Individual", "AAPL", 10.0, 180.0, -1800.0, date(2026, 3, 20), date(2026, 3, 23),
            "YOU BOUGHT APPLE INC (AAPL) (Cash)")

    def test_a_price_rounded_to_the_cent_is_accepted(self, file):
        sale = file.transactions[4]
        assert (sale.quantity, sale.price, sale.amount) == (-400.0, 1.53, 610.4)
        assert abs(sale.amount - sale.expected_amount) == pytest.approx(1.60)

    def test_a_money_market_dividend_and_its_reinvestment(self, file):
        reinvest, dividend = file.transactions[2:4]
        assert (dividend.quantity, dividend.price, dividend.amount) == (0.0, None, 2.5)
        assert (reinvest.quantity, reinvest.price, reinvest.amount) == (2.5, 1.0, -2.5)
        assert reinvest.account == "ROTH IRA" and reinvest.settle_date is None

    def test_an_options_row_keeps_its_contract_and_costs(self, file):
        opening = file.transactions[0]
        assert (opening.symbol, opening.option.strike, opening.commission, opening.fees,
                opening.amount) == ("AAPL260619C250", 250.0, 0.65, 0.02, 414.33)

    @pytest.mark.parametrize("change", [
        ("(Cash),AAPL,APPLE INC,Cash,", "(Cash),AAPL,APPLE INC,Short,"),       # Type: Short
        ("YOU BOUGHT APPLE", "YOU BOUGHT SHORT APPLE"),                        # a short's wording
        ('"","","","-1800"', '"","","1.00","-1801"'),                         # accrued interest
    ])
    def test_a_trade_worded_or_typed_as_more_than_a_plain_trade_is_other(self, change):
        old, new = change
        row = FIDELITY_BUY.replace(old, new)
        assert row != FIDELITY_BUY
        file = read(FIDELITY.replace(FIDELITY_BUY, row))
        assert file.transactions[5].kind is Kind.OTHER and file.problems == ()

    def test_a_bad_row_is_reported_with_its_line(self):
        file = read(FIDELITY.replace('"-1800",03/23', '"-1900",03/23'))
        (problem,) = file.problems
        assert problem.line == 9 and "doesn't match" in problem.message
        assert len(file.transactions) == 6

    def test_the_account_number_stands_in_for_a_missing_name(self):
        file = read(FIDELITY.replace(FIDELITY_BUY, FIDELITY_BUY.replace(",Individual,", ",,")))
        assert file.transactions[5].account == "Z12345678"

    def test_a_download_on_disk(self, tmp_path):
        path = tmp_path / "history.csv"
        path.write_text(FIDELITY.lstrip("﻿"), encoding="utf-8-sig")
        assert read_activity_csv(path).source == "Fidelity"
