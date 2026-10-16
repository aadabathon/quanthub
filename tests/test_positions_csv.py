"""Positions from CSV (P2): the Fidelity preset and the generic layout."""

import io
from datetime import date, datetime

import pytest

from quant_hub.data.positions import Holding, Position, PositionSnapshot
from quant_hub.importers.positions_csv import (
    PositionFileError,
    parse_option_symbol,
    read_positions_csv,
)

FIDELITY = '''Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type
Z12345678,Individual,SPAXX**,HELD IN MONEY MARKET,,,,"$2,345.67",,,,,4.12%,,,Cash,
Z12345678,Individual,AAPL,APPLE INC,50,$227.52,+$1.20,"$11,376.00",+$60.00,+0.53%,"+$3,876.00",+51.68%,19.98%,"$7,500.00",$150.00,Cash,
Z12345678,Individual,VTI,VANGUARD INDEX FDS TOTAL STK MKT,120,$289.10,-$0.50,"$34,692.00",-$60.00,-0.17%,"+$9,692.00",+38.77%,60.93%,"$25,000.00",$208.33,Cash,
Z12345678,Individual, -AAPL260116C250,AAPL JAN 16 2026 $250 CALL,-1,$4.15,-$0.20,-$415.00,+$20.00,+4.60%,+$85.00,+17.00%,-0.73%,$500.00,$5.00,Margin,
Z12345678,Individual,Pending Activity,,,,,-$120.00,,,,,,,,,
X87654321,ROTH IRA,QQQ,INVESCO QQQ TR,30,$510.00,+$2.00,"$15,300.00",+$60.00,+0.39%,"+$3,300.00",+27.50%,100.00%,"$12,000.00",$400.00,Cash,

"The data and information in this spreadsheet is provided to you solely for your use and is not for distribution."

"Brokerage services are provided by Fidelity Brokerage Services LLC (FBS), 900 Salem Street, Smithfield, RI 02917."

"Date downloaded Oct-16-2026 10:15 a.m ET"
'''


class TestFidelity:
    @pytest.fixture
    def snapshot(self) -> PositionSnapshot:
        return read_positions_csv(io.StringIO(FIDELITY), "Portfolio_Positions.csv")

    def test_the_file_and_its_date(self, snapshot):
        assert snapshot.source == "Fidelity" and snapshot.name == "Portfolio_Positions.csv"
        assert snapshot.as_of == datetime(2026, 10, 16)
        assert snapshot.accounts == ("Individual", "ROTH IRA")
        assert [p.symbol for p in snapshot.positions] == [
            "SPAXX", "AAPL", "VTI", "AAPL260116C250", "QQQ"]       # pending skipped

    def test_money_market_is_cash(self, snapshot):
        cash = snapshot.positions[0]
        assert cash.kind is Holding.CASH and cash.market_value == pytest.approx(2345.67)

    def test_stocks_and_etfs(self, snapshot):
        aapl = snapshot.positions[1]
        assert (aapl.kind, aapl.quantity, aapl.price, aapl.value, aapl.cost_basis) == (
            Holding.EQUITY, 50, 227.52, 11376.00, 7500.00)

    def test_a_short_call(self, snapshot):
        call = snapshot.positions[3]
        assert call.kind is Holding.OPTION and call.quantity == -1
        assert call.option.underlying == "AAPL" and call.option.option_type == "call"
        assert (call.option.strike, call.option.expiry) == (250.0, date(2026, 1, 16))
        assert call.market_value == pytest.approx(-415.00)

    def test_accounts_stay_separable(self, snapshot):
        assert [p.symbol for p in snapshot.for_account("ROTH IRA")] == ["QQQ"]
        assert len(snapshot.for_account(None)) == 5


class TestGeneric:
    def test_the_generic_layout(self):
        snapshot = read_positions_csv(io.StringIO(
            "symbol,quantity,price,cost_basis,account\n"
            "AAPL,50,227.52,7500,Brokerage\n"
            "-SPY261218P550,2,8.40,,Brokerage\n"
            "CASH,1250,,,Brokerage\n"))
        assert snapshot.source == "Generic"
        aapl, put, cash = snapshot.positions
        assert aapl.market_value == pytest.approx(50 * 227.52)
        assert put.option.option_type == "put"
        assert put.market_value == pytest.approx(2 * 100 * 8.40)
        assert cash.kind is Holding.CASH and cash.market_value == 1250

    def test_problems_are_named(self):
        with pytest.raises(PositionFileError, match="no header"):
            read_positions_csv(io.StringIO("ticker,shares\nAAPL,5\n"))
        with pytest.raises(PositionFileError, match="AAPL: no quantity"):
            read_positions_csv(io.StringIO("symbol,quantity\nAAPL,\n"))
        with pytest.raises(PositionFileError, match="isn't a number"):
            read_positions_csv(io.StringIO("symbol,quantity\nAAPL,lots\n"))


@pytest.mark.parametrize("symbol, expected", [
    ("-AAPL260116C250", ("AAPL", "call", 250.0, date(2026, 1, 16))),
    ("SPY241220P450.5", ("SPY", "put", 450.5, date(2024, 12, 20))),
    (" -BRK.B270115C500", ("BRK.B", "call", 500.0, date(2027, 1, 15))),
    ("AAPL", None),
    ("-AAPL261341C250", None),                                 # no 41st of month 13
])
def test_option_symbols(symbol, expected):
    option = parse_option_symbol(symbol)
    if expected is None:
        assert option is None
    else:
        assert (option.underlying, option.option_type, option.strike, option.expiry) == expected


def test_positions_are_validated():
    with pytest.raises(ValueError):
        Position("AAPL", Holding.OPTION, 1)                     # an option needs its contract
    with pytest.raises(ValueError):
        PositionSnapshot((), datetime(2026, 1, 1), "Generic")
