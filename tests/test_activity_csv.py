"""Activity from CSV (TR1): the generic layout."""

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
