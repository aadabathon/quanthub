"""Positions from CSV (P2): the Fidelity preset and the generic layout."""

import io
from datetime import date, datetime

import pytest

from quant_hub.data.positions import Holding, Position, PositionSnapshot
from quant_hub.importers.positions_csv import (
    PositionFileError,
    parse_option_symbol,
    read_positions_csv,
    read_positions_files,
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


# The Positions view's download, as the owner's real exports are laid out
# (made-up holdings): a quoted preamble with trailing spaces, cash per core
# fund, a holding in two accounts as two rows, a Totals row, a footer.
FIDELITY_VIEW = (
    '"Positions \nAll Accounts \nas of 10/21/2026 at 11:18:16 AM"\n'
    "\n"
    "Symbol,Quantity,Last,$ Chg,% Chg,Volume,$ Day G/L,% Day G/L,$ Total G/L,% Total G/L,"
    "Value,Basis,Day Range,52W Range,Earnings Date,Div Amt,Div Ex-Date\n"
    "Cash (USD),0,1.00,0.00,0.00,0,--,--,--,--,--,--,0.00-0.00,0.00-0.00,--,0.00,--\n"
    'AAPL,3,250.00,-0.37,-0.15,"2,000,000",-1.11,-0.15,75.00,11.11,750.00,675.00,'
    "248.00-252.00,190.00-260.00,Oct-30-2026,0.26,Aug-11-2026\n"
    'Cash (SPAXX),"1,250.00",1.00,0.00,0.00,0,--,--,--,--,"1,250.00",--,0.00-0.00,'
    "3.20-3.80,--,0.0032,Nov-3-2025\n"
    'VTI,2.5,300.00,1.00,0.33,"4,000,000",2.50,0.33,50.00,7.14,750.00,700.00,'
    "298.00-301.00,250.00-310.00,--,1.10,Sep-25-2026\n"
    "Cash (SPAXX),400.00,1.00,0.00,0.00,0,--,--,--,--,400.00,--,0.00-0.00,3.20-3.80,--,"
    "0.0032,Nov-3-2025\n"
    'AAPL,1,250.00,-0.37,-0.15,"2,000,000",-0.37,-0.15,10.00,4.17,250.00,240.00,'
    "248.00-252.00,190.00-260.00,Oct-30-2026,0.26,Aug-11-2026\n"
    "Totals,,,,,,0.00,--,0.00,--,0.00,0.00,,,,,\n"
    "\n"
    "\n"
    "Disclosure\n"
    '"The data and information in this spreadsheet is provided to you solely for your use '
    "and is not for distribution. \n"
    'Brokerage services are provided by Fidelity Brokerage Services LLC (FBS)."\n'
)


class TestFidelityPositionsView:
    @pytest.fixture
    def snapshot(self) -> PositionSnapshot:
        return read_positions_csv(io.StringIO(FIDELITY_VIEW), "10-21-26_port.csv")

    def test_the_layout_and_its_date(self, snapshot):
        assert snapshot.source == "Fidelity positions view"
        assert snapshot.as_of == datetime(2026, 10, 21)           # the date only
        assert [p.symbol for p in snapshot.positions] == [
            "CASH", "AAPL", "SPAXX", "VTI", "SPAXX", "AAPL"]        # Totals skipped

    def test_every_row_belongs_to_the_view(self, snapshot):
        assert snapshot.accounts == ("All Accounts",)
        apple = [p for p in snapshot.positions if p.symbol == "AAPL"]
        assert [p.quantity for p in apple] == [3, 1]               # two accounts' rows, kept
        assert sum(p.market_value for p in apple) == pytest.approx(1000.00)

    def test_cash_lines(self, snapshot):
        cash = [p for p in snapshot.positions if p.kind is Holding.CASH]
        assert [(p.symbol, p.market_value) for p in cash] == [
            ("CASH", 0.0), ("SPAXX", pytest.approx(1250.00)), ("SPAXX", pytest.approx(400.00))]

    def test_holdings(self, snapshot):
        aapl, vti = snapshot.positions[1], snapshot.positions[3]
        assert (aapl.kind, aapl.quantity, aapl.price, aapl.value, aapl.cost_basis) == (
            Holding.EQUITY, 3, 250.00, 750.00, 675.00)
        assert vti.quantity == 2.5                                  # fractional shares

    def test_a_single_account_view_names_its_account(self):
        snapshot = read_positions_csv(io.StringIO(
            FIDELITY_VIEW.replace("All Accounts", "Individual")))
        assert snapshot.accounts == ("Individual",)

    def test_the_file_as_saved(self, tmp_path):
        """The real downloads carry a byte-order mark and Windows line ends."""
        path = tmp_path / "10-21-26_port.csv"
        path.write_bytes(b"\xef\xbb\xbf" + FIDELITY_VIEW.replace("\n", "\r\n").encode("utf-8"))
        snapshot = read_positions_csv(path)
        assert snapshot.name == "10-21-26_port.csv"
        assert snapshot.source == "Fidelity positions view"
        assert snapshot.as_of == datetime(2026, 10, 21) and len(snapshot.positions) == 6


def view_export(account: str, day: str = "10/21/2026") -> str:
    """The Positions view's download for one account (made-up holdings)."""
    return FIDELITY_VIEW.replace("All Accounts", account).replace("10/21/2026", day)


class TestOneExportPerAccount:
    @pytest.fixture
    def files(self, tmp_path):
        def write(name: str, text: str):
            path = tmp_path / name
            path.write_text(text, encoding="utf-8")
            return path
        return write

    def test_two_accounts_become_one_snapshot(self, files):
        snapshot = read_positions_files([files("ind.csv", view_export("Individual")),
                                         files("roth.csv", view_export("ROTH IRA"))])
        assert snapshot.accounts == ("Individual", "ROTH IRA")
        assert snapshot.name == "ind.csv + roth.csv"
        assert snapshot.source == "Fidelity positions view"
        assert snapshot.as_of == datetime(2026, 10, 21)
        assert len(snapshot.positions) == 12 and len(snapshot.for_account("ROTH IRA")) == 6

    def test_one_file_is_read_as_it_is(self, files):
        path = files("all.csv", FIDELITY_VIEW)
        assert read_positions_files([path]) == read_positions_csv(path)

    def test_different_days_are_refused(self, files):
        with pytest.raises(PositionFileError, match="different days"):
            read_positions_files([files("a.csv", view_export("Individual")),
                                  files("b.csv", view_export("ROTH IRA", "10/20/2026"))])

    def test_the_same_account_twice_is_refused(self, files):
        with pytest.raises(PositionFileError, match="'Individual' is in both a.csv and b.csv"):
            read_positions_files([files("a.csv", view_export("Individual")),
                                  files("b.csv", view_export("Individual"))])

    def test_an_all_accounts_export_beside_others_is_refused(self, files):
        with pytest.raises(PositionFileError, match="already holds every account"):
            read_positions_files([files("all.csv", FIDELITY_VIEW),
                                  files("roth.csv", view_export("ROTH IRA"))])

    def test_rows_without_an_account_are_refused(self, files):
        generic = "symbol,quantity,price\nAAPL,5,250\n"
        with pytest.raises(PositionFileError, match="rows without an account"):
            read_positions_files([files("g.csv", generic),
                                  files("roth.csv", view_export("ROTH IRA"))])


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
