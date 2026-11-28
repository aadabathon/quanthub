"""Trades weighed on the desk (A3): the book after, and every voice on both."""

import json
from datetime import date, datetime

import pytest

from desk_fixtures import NOW, service
from quant_hub.data.desk import Book, Holding
from quant_hub.models.weighing import Move, apply, parse_move
from quant_hub.services import desk_report as report

ENDS = {"AAA": "2026-11-20", "BBB": "2026-11-20", "SPY": "2026-11-20"}
BOOK = Book(date(2026, 11, 20), (Holding("AAA", 10.0, 50.0), Holding("BBB", 4.0, 25.0)),
            200.0, ("AAA261218P30 (an option)",))


class TestParsing:
    @pytest.mark.parametrize("text, move", [
        ("MSFT +5", Move("MSFT", 5.0)),
        ("msft -2.5", Move("MSFT", -2.5)),
        ("MSFT:+5", Move("MSFT", 5.0)),
        ("MSFT -$1,000", Move("MSFT", -1000.0, True)),
        ("MSFT −$500", Move("MSFT", -500.0, True)),
    ])
    def test_units_or_dollars(self, text, move):
        assert parse_move(text) == move

    @pytest.mark.parametrize("text, message", [
        ("MSFT", "SYMBOL and units or dollars"),
        ("MSFT 0", "moves something"),
        ("MSFT lots", "isn't units or dollars"),
    ])
    def test_what_isnt_a_trade_says_why(self, text, message):
        with pytest.raises(ValueError, match=message):
            parse_move(text)


class TestTheBookAfter:
    def test_value_moves_between_holdings_and_cash_and_no_further(self):
        traded = apply(BOOK, (Move("AAA", -4.0), Move("BBB", 100.0, True)), {})
        after = {h.symbol: h.units for h in traded.book.holdings}
        assert after == {"AAA": 6.0, "BBB": 8.0}
        assert traded.book.cash == pytest.approx(200.0 + 4 * 50.0 - 100.0)
        assert traded.book.value == pytest.approx(BOOK.value)
        assert traded.to_cash == pytest.approx(100.0)
        assert not traded.shorts and not traded.margin
        assert traded.book.unpriced == BOOK.unpriced

    def test_a_name_sold_out_leaves_and_a_new_one_comes_in(self):
        traded = apply(BOOK, (Move("AAA", -10.0), Move("CCC", 2.0)), {"CCC": 20.0})
        assert [(h.symbol, h.units, h.price) for h in traded.book.holdings] == [
            ("BBB", 4.0, 25.0), ("CCC", 2.0, 20.0)]
        assert traded.book.value == pytest.approx(BOOK.value)

    def test_moves_on_one_name_add_up(self):
        traded = apply(BOOK, (Move("AAA", 2.0), Move("AAA", -50.0, True)), {})
        assert traded.trades == (("AAA", 1.0, 50.0),)

    def test_a_short_and_a_loan_are_flagged(self):
        traded = apply(BOOK, (Move("BBB", -6.0), Move("AAA", 10.0)), {})
        assert traded.shorts == ("BBB",)
        assert traded.margin and traded.book.cash == pytest.approx(200.0 + 150.0 - 500.0)

    @pytest.mark.parametrize("moves, prices, message", [
        ((), {}, "no trades"),
        ((Move("AAA261218P30", 1.0),), {}, "isn't priced in this snapshot"),
        ((Move("CCC", 1.0),), {}, "no close for CCC"),
    ])
    def test_what_cant_be_weighed(self, moves, prices, message):
        with pytest.raises(ValueError, match=message):
            apply(BOOK, moves, prices)


class TestTheService:
    def test_the_book_as_held_reproduces_the_stored_claims(self, parts):
        desk = service(parts, ENDS)
        taken = desk.take("test", NOW)
        w = desk.whatif((Move("BBB", 1.0),))
        assert w.differs == () and w.silent == ()
        assert [v.claims for v in w.held.voices] == [v.claims for v in taken.snapshot.voices]
        assert w.after.book.value == pytest.approx(w.held.book.value)
        assert [v.voice for v in w.after.voices] == [v.voice for v in w.held.voices]

    def test_a_trade_moves_the_books_claims(self, parts):
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        w = desk.whatif((Move("AAA", -8.0),))                # out of the name entirely
        assert "AAA" not in {h.symbol for h in w.after.book.holdings}
        doc = json.loads(json.dumps(report.whatif(w, datetime(2026, 11, 23, 8)),
                                    allow_nan=False))
        assert doc["kind"] == "whatif" and doc["reproduces"]
        assert doc["after"]["cash_usd"] > doc["before"]["cash_usd"]
        rows = [r for r in doc["claims"] if r["voice"] == "baseline"
                and r["key"] == "book.return@21" and r["level"] == 0.05]
        (row,) = rows
        assert row["change_usd"] != 0 and all(r["target"].startswith("book.")
                                               for r in doc["claims"])
        assert [len(h) for h in doc["headline"]] == [len(doc["headline"][0])] * 2

    def test_a_name_not_held_is_priced_from_the_snapshots_source(self, parts):
        desk = service(parts, {**ENDS, "CCC": "2026-11-20"})
        desk.take("test", NOW)
        w = desk.whatif((Move("CCC", 500.0, True),))
        (ccc,) = [h for h in w.after.book.holdings if h.symbol == "CCC"]
        assert ccc.units * ccc.price == pytest.approx(500.0)
        assert w.differs == ()

    def test_a_name_without_a_close_that_day_cant_be_weighed(self, parts):
        desk = service(parts, {**ENDS, "CCC": "2026-11-19"})
        desk.take("test", NOW)
        with pytest.raises(ValueError, match="no close on 2026-11-20"):
            desk.whatif((Move("CCC", 1.0),))
        with pytest.raises(ValueError, match="no snapshot #9"):
            desk.whatif((Move("BBB", 1.0),), snapshot_id=9)


class TestTheCommand:
    @pytest.fixture
    def cli(self, parts, monkeypatch, tmp_path):
        from quant_hub import desk as cli
        desk = service(parts, ENDS)

        class Core:
            config = type("Config", (), {"backup_folder": tmp_path,
                                         "db_path": tmp_path / "market_data.db"})()

            def __init__(self):
                self.desk, self.activity = desk, parts[0]

            def shutdown(self):
                pass
        monkeypatch.setattr(cli, "build_core", Core)
        return cli, desk

    def test_text_and_json(self, cli, capsys):
        main, desk = cli
        desk.take("test", NOW)
        assert main.main(["whatif", "--trade", "BBB -$50", "--trade", "AAA +1"]) == 0
        out = capsys.readouterr().out
        assert "what-if on snapshot #1 as of 2026-11-20" in out
        assert "sell 2.00 BBB @ 25.00" not in out and "sell " in out and "buy 1.00 AAA" in out
        assert "return@21 5%" in out and "either way" in out
        assert main.main(["whatif", "--trade", "BBB +1", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["kind"] == "whatif"

    def test_what_cant_be_weighed_says_so(self, cli, capsys):
        main, desk = cli
        assert main.main(["whatif", "--trade", "BBB +1"]) == 1
        assert "no snapshot to weigh" in capsys.readouterr().err
        desk.take("test", NOW)
        assert main.main(["whatif", "--trade", "BBB zero"]) == 1
        assert "isn't units or dollars" in capsys.readouterr().err
        with pytest.raises(SystemExit):
            main.main(["whatif"])                            # a trade is required
