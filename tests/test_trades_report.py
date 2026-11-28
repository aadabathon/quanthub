"""Your trades from the command line, for you or an agent (A4)."""

import json
from datetime import date, datetime

import pytest

from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService
from book_fixtures import export
from desk_fixtures import Bars

HEADER = "date,account,kind,symbol,quantity,price,commission,fees,amount\n"


@pytest.fixture
def cli(parts, monkeypatch):
    from quant_hub import trades as cli
    activity, _, folder = parts
    market = MarketService([Bars({"AAA": "2026-11-26", "BBB": "2026-11-26",
                                  "SPY": "2026-11-26"}, 400)])
    positions = activity._positions

    class Core:
        config = type("Config", (), {"ibkr_enabled": False, "massive_key": ""})()

        def __init__(self):
            self.activity, self.market_service = activity, market
            self.portfolio = PortfolioService(market, positions)

        def shutdown(self):
            pass
    monkeypatch.setattr(cli, "build_core", Core)
    return cli, folder


def out_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestImports:
    def test_activity_is_stored_once(self, cli, capsys):
        main, folder = cli
        path = folder / "later.csv"
        path.write_text(HEADER + "2026-11-20,Brokerage,buy,BBB,2,25.00,0,0,-50.00\n",
                        encoding="utf-8")
        assert main.main(["import-activity", str(path)]) == 0
        assert "1 transactions added" in capsys.readouterr().out
        assert main.main(["import-activity", str(path), "--json"]) == 0
        doc = out_json(capsys)
        (row,) = doc["files"]
        assert (doc["added"], row["seen_before"], row["last_date"]) == (0, True, "2026-11-20")

    def test_a_bad_file_stores_nothing(self, cli, capsys):
        main, folder = cli
        path = folder / "junk.csv"
        path.write_text("not,a,history\n1,2,3\n", encoding="utf-8")
        assert main.main(["import-activity", str(path)]) == 1
        assert "nothing done" in capsys.readouterr().err

    def test_a_positions_export_once(self, cli, capsys):
        main, folder = cli
        path = folder / "11-20-26_port.csv"
        path.write_text(export("11/20/2026", {"AAA": 8, "BBB": 6}, 430), encoding="utf-8")
        assert main.main(["import-positions", str(path), "--json"]) == 0
        doc = out_json(capsys)
        assert doc["stored"] and doc["as_of"].startswith("2026-11-20") and doc["positions"] == 3
        assert main.main(["import-positions", str(path)]) == 0      # refused, not an error
        assert "not stored: " in capsys.readouterr().out
        assert main.main(["import-positions", str(path), "--json"]) == 0
        doc = out_json(capsys)
        assert not doc["stored"] and "imported before" in doc["refused"]


class TestReading:
    def test_trips_and_the_trades_since(self, cli, capsys):
        main, _ = cli
        assert main.main(["trips", "--since", "2026-11-13", "--json"]) == 0
        doc = out_json(capsys)
        assert [(t["date"], t["kind"], t["symbol"]) for t in doc["trades"]] == [
            ("2026-11-13", "sell", "AAA")]
        assert {t["symbol"] for t in doc["trips"]} == {"AAA", "BBB"}
        assert main.main(["trips"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("round trips:") and "BBB" in out

    def test_timing_and_held_as_data(self, cli, capsys):
        main, _ = cli
        assert main.main(["timing", "--source", "test", "--json"]) == 0
        doc = out_json(capsys)
        assert doc["kind"] == "timing" and "better_days" in doc["units"]
        assert {(w["of"], w["h"]) for w in doc["windows"]} <= {
            (side, h) for side in ("entries", "exits") for h in (5, 10)}
        assert main.main(["held", "--source", "test", "--json"]) == 0
        doc = out_json(capsys)
        assert doc["kind"] == "held" and "twr" in doc and "market" in doc
        assert main.main(["held", "--source", "test"]) == 0
        assert capsys.readouterr().out.startswith(("as held, ", "no returns as held: "))


def test_trips_since_keeps_whats_open_or_touched():
    from types import SimpleNamespace as NS
    from quant_hub.services import trades_report
    old = NS(account="A", symbol="OLD", side="long", opened=date(2025, 1, 2),
             closed=date(2025, 3, 3), pieces=(1,), invested=100.0, realized=10.0,
             return_on_invested=0.1, income=0.0, peak_units=1.0, open_units=0.0,
             option=False)
    live = NS(**{**old.__dict__, "symbol": "LIVE", "closed": None, "open_units": 1.0})
    doc = trades_report.trips((old, live), (), date(2026, 1, 1), datetime(2026, 11, 27))
    assert [t["symbol"] for t in doc["trips"]] == ["LIVE"]
