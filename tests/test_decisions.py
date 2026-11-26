"""Recording decisions and backing up the store (D108)."""

import json
import sqlite3
from datetime import date, datetime

import pytest

from quant_hub.config import load_config
from quant_hub.data.desk import Book, Holding, Snapshot, parse_claim, parse_trades
from quant_hub.models.baseline import baseline
from quant_hub.storage.backup import backup_store
from quant_hub.storage.desk_store import DeskStore

NOW = datetime(2026, 11, 27, 9, 0)


class TestParsing:
    def test_quantiles_and_events_as_text(self):
        q = parse_claim("MSFT.return@21 q -12%,-4%,1%,6%,15%")
        assert (q.target, q.horizon) == ("MSFT.return", 21)
        assert q.quantiles == pytest.approx((-0.12, -0.04, 0.01, 0.06, 0.15))
        e = parse_claim("book.fall@63 >= 10% p 25%")
        assert (e.event, e.probability) == (">= 0.1", pytest.approx(0.25))
        assert parse_claim("spy.vol@21 q 0.1 0.12 0.14 0.16 0.2").target == "SPY.vol"
        assert parse_claim("Book.Return@5 <= −5% p 0.1").event == "<= -0.05"

    @pytest.mark.parametrize("text, message", [
        ("book.return q 1,2,3,4,5", "TARGET@SESSIONS"),
        ("book.return@21 q 1,2", "5 quantiles"),
        ("book.return@21 q 5,4,3,2,1", "can't decrease"),
        ("book.fall@63 >= 10% p 1.5", "between 0 and 1"),
        ("book.fall@63 maybe", "after the target"),
        ("book.price@21 q 1,2,3,4,5", ".fall resolve"),
    ])
    def test_what_isnt_a_claim_says_why(self, text, message):
        with pytest.raises(ValueError, match=message):
            parse_claim(text)

    def test_trades(self):
        assert parse_trades("MSFT +5, aapl:-2.5") == (("MSFT", 5.0), ("AAPL", -2.5))
        assert parse_trades("  ") == ()
        with pytest.raises(ValueError, match="moves some units"):
            parse_trades("MSFT 0")


@pytest.fixture
def desk(tmp_path):
    from quant_hub.services.desk import DeskService
    store = DeskStore(":memory:")
    yield DeskService(store, market=None, activity=None), store
    store.close()


def snapshot(day: int = 20) -> Snapshot:
    import numpy as np
    import pandas as pd
    book = Book(date(2026, 11, day), (Holding("AAA", 10.0, 100.0),), 0.0)
    returns = pd.DataFrame({"AAA": np.random.default_rng(1).normal(0, 0.01, 100)},
                           index=pd.bdate_range(end=f"2026-11-{day}", periods=100))
    return Snapshot(datetime(2026, 11, day, 17), book.as_of, "test", "0", book,
                    (baseline(book, returns),))


class TestDeciding:
    def test_a_decision_cites_the_newest_snapshot(self, desk):
        service, store = desk
        store.save_snapshot(snapshot(19))
        newest = store.save_snapshot(snapshot(20))
        decision_id, decision, summary = service.decide(
            "me", (parse_claim("book.fall@21 >= 10% p 20%"),), "trimmed the winner",
            parse_trades("AAA -2"), now=NOW)
        assert summary.id == newest and decision.snapshot_sha == summary.sha256
        assert (decision.author, decision.trades) == ("me", (("AAA", -2.0),))
        assert [d.rationale for d, _ in service.decisions()] == ["trimmed the winner"]
        assert decision_id == 1

    def test_one_named_snapshot(self, desk):
        service, store = desk
        older = store.save_snapshot(snapshot(19))
        store.save_snapshot(snapshot(20))
        _, _, summary = service.decide("llm", (parse_claim("AAA.return@5 q -3%,-1%,0,1%,3%"),),
                                       snapshot_id=older, now=NOW)
        assert summary.id == older

    @pytest.mark.parametrize("author, claims, message", [
        ("me", (), "at least one claim"),
        ("  ", (parse_claim("book.fall@21 >= 10% p 20%"),), "its author"),
    ])
    def test_what_a_decision_needs(self, desk, author, claims, message):
        service, store = desk
        store.save_snapshot(snapshot())
        with pytest.raises(ValueError, match=message):
            service.decide(author, claims, now=NOW)

    def test_nothing_to_decide_on_yet(self, desk):
        with pytest.raises(ValueError, match="no snapshot"):
            desk[0].decide("me", (parse_claim("book.fall@21 >= 10% p 20%"),), now=NOW)


class TestTheCommand:
    @pytest.fixture
    def cli(self, desk, monkeypatch):
        from quant_hub import decide as cli
        service, store = desk
        store.save_snapshot(snapshot())

        class Core:
            def __init__(self):
                self.desk = service

            def shutdown(self):
                pass
        monkeypatch.setattr(cli, "build_core", Core)
        return cli

    def test_from_flags(self, cli, capsys):
        assert cli.main(["--claim", "book.fall@21 >= 10% p 20%", "--trade", "AAA +1",
                         "--why", "adding"]) == 0
        out = capsys.readouterr().out
        assert "decision #1 by me" in out and "book.fall@21 >= 0.1, resolves" in out

    def test_from_an_agents_json(self, cli, capsys, tmp_path):
        path = tmp_path / "decision.json"
        path.write_text(json.dumps({"author": "llm", "why": "the tail voice is calm",
                                    "claims": ["book.return@21 q -6%,-2%,0,2%,6%",
                                               {"target": "book.fall", "horizon": 63,
                                                "event": ">= 0.2", "probability": 0.1}]}),
                        encoding="utf-8")
        assert cli.main(["--json", str(path)]) == 0
        assert "decision #1 by llm" in capsys.readouterr().out
        assert cli.main(["--list"]) == 0
        assert "llm: 2 claims on #1" in capsys.readouterr().out

    def test_a_bad_claim_records_nothing(self, cli, capsys):
        assert cli.main(["--claim", "book.return@21 q 1,2"]) == 1
        assert "not recorded" in capsys.readouterr().err
        assert cli.main(["--list"]) == 0 and capsys.readouterr().out == ""


class TestBackups:
    def store(self, tmp_path):
        path = tmp_path / "market_data.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE t (x)")
            conn.execute("INSERT INTO t VALUES (42)")
        return path

    def test_a_copy_is_the_whole_store(self, tmp_path):
        source = self.store(tmp_path)
        copy = backup_store(source, tmp_path / "daily", NOW)
        assert copy.name == "market_data-20261127-090000.db"
        with sqlite3.connect(copy) as conn:
            assert conn.execute("SELECT x FROM t").fetchall() == [(42,)]

    def test_only_the_newest_are_kept(self, tmp_path):
        source, folder = self.store(tmp_path), tmp_path / "daily"
        for day in range(1, 6):
            backup_store(source, folder, datetime(2026, 11, day, 18), keep=3)
        assert sorted(p.name.split("-")[1] for p in folder.glob("*.db")) == [
            "20261103", "20261104", "20261105"]

    def test_what_cant_be_backed_up(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            backup_store(tmp_path / "missing.db", tmp_path / "daily", NOW)
        with pytest.raises(ValueError, match="at least one"):
            backup_store(self.store(tmp_path), tmp_path / "daily", NOW, keep=0)

    def test_the_settings(self, tmp_path):
        path = tmp_path / "settings.toml"
        path.write_text('[backup]\nfolder = "copies"\nkeep = 30\n', encoding="utf-8")
        config, problems = load_config(path)
        assert config.backup_folder == (tmp_path / "copies").resolve()
        assert (config.backup_keep, problems) == (30, [])
        path.write_text('[backup]\nkeep = 0\n', encoding="utf-8")
        config, problems = load_config(path)
        assert config.backup_keep == 14 and "keep" in problems[0]

    def test_the_daily_run_backs_up_even_when_the_snapshot_fails(self, tmp_path, monkeypatch,
                                                               capsys):
        from quant_hub import snapshot as cli
        source = self.store(tmp_path)

        class Desk:
            def resolve(self):
                raise ValueError("no positions export to start from")

        class Core:
            config = type("Config", (), {"ibkr_enabled": False, "massive_key": "",
                                         "db_path": source, "backup_folder": None,
                                         "backup_keep": 14})()
            desk = Desk()

            def shutdown(self):
                pass
        monkeypatch.setattr(cli, "build_core", Core)
        assert cli.main([]) == 1
        captured = capsys.readouterr()
        assert "no snapshot" in captured.err and "backed up to" in captured.out
        assert len(list((tmp_path / "backups" / "daily").glob("*.db"))) == 1
