"""The desk read from the command line, for you or an agent (A1)."""

import json
from datetime import date, datetime

import pytest

from quant_hub.data.desk import LEVELS, Run
from quant_hub.models import scoring
from quant_hub.services import desk_report as report
from quant_hub.services.desk import DeskRecord, DeskService
from quant_hub.services.market_service import MarketService
from quant_hub.storage.desk_store import DeskStore
from desk_fixtures import NOW, Bars, service

MONDAY = datetime(2026, 11, 23, 7, 45)          # the snapshot below is as of Friday 11-20
ENDS = {"AAA": "2026-11-20", "BBB": "2026-11-20"}


def run(started: datetime, snapshot_id: int | None = 1, problems=(), notes=()) -> Run:
    return Run(started, started.replace(minute=started.minute + 5), "test", snapshot_id, 0, 0,
               "copy.db", tuple(problems), tuple(notes))


def backups(tmp_path, n: int = 1):
    folder = tmp_path / "backups" / "daily"
    folder.mkdir(parents=True)
    for i in range(n):
        (folder / f"market_data-2026112{i}-070000.db").write_bytes(b"")
    return folder


def plain(doc: dict) -> dict:
    """The document as an agent reads it: strict JSON, no NaN."""
    return json.loads(json.dumps(doc, allow_nan=False))


class TestSessions:
    @pytest.mark.parametrize("today, last", [
        (date(2026, 11, 23), date(2026, 11, 20)),     # Monday: Friday's close
        (date(2026, 11, 24), date(2026, 11, 23)),
        (date(2026, 11, 27), date(2026, 11, 25)),     # the day after Thanksgiving
        (date(2026, 11, 28), date(2026, 11, 27)),     # Saturday
    ])
    def test_the_last_session_is_the_newest_trading_day_before_today(self, today, last):
        assert report.last_session(today) == last

    def test_sessions_behind(self):
        assert report.sessions_behind(date(2026, 11, 20), date(2026, 11, 23)) == 0
        assert report.sessions_behind(date(2026, 11, 20), date(2026, 11, 25)) == 2
        assert report.sessions_behind(date(2026, 11, 23), date(2026, 11, 23)) == 0  # IBKR's


class TestTheRunLog:
    def test_runs_are_stored_and_read_newest_first(self):
        store = DeskStore(":memory:")
        first = run(datetime(2026, 11, 23, 7, 0), None, ("no snapshot: no positions export",))
        second = run(datetime(2026, 11, 24, 7, 0), None, (), ("silent: tail: no century",))
        store.save_run(first)
        store.save_run(second)
        assert store.runs() == [second, first] and store.runs(1) == [second]
        assert not first.ok and second.ok
        store.close()

    def test_the_daily_run_records_what_it_did(self, parts, monkeypatch, capsys):
        from quant_hub import snapshot as cli
        core = self.core(parts)
        monkeypatch.setattr(cli, "build_core", lambda: core)
        monkeypatch.setattr(core.desk, "take",
                            lambda source, _take=core.desk.take: _take("test", NOW))
        assert cli.main(["--no-backup"]) == 0
        (recorded,) = core.desk.runs()
        assert recorded.ok and recorded.snapshot_id == 1 and recorded.backup == ""
        assert "not priced: AAA261218P30 (an option)" in recorded.notes

    def test_a_failed_snapshot_is_recorded_and_fails_the_run(self, parts, monkeypatch, capsys):
        from quant_hub import snapshot as cli
        core = self.core(parts)
        monkeypatch.setattr(cli, "build_core", lambda: core)

        def broken(source):
            raise RuntimeError("the source went away")
        def unresolved():
            raise ValueError("no bars")
        monkeypatch.setattr(core.desk, "take", broken)
        monkeypatch.setattr(core.desk, "resolve", unresolved)
        assert cli.main(["--no-backup"]) == 1
        (recorded,) = core.desk.runs()
        assert recorded.problems == ("nothing resolved: no bars",
                                     "no snapshot: the source went away")
        assert recorded.snapshot_id is None

    @staticmethod
    def core(parts):
        class Core:
            config = type("Config", (), {"ibkr_enabled": False, "massive_key": ""})()
            desk = service(parts, ENDS)

            def shutdown(self):
                pass
        return Core()


class TestStatus:
    def test_nothing_stored_says_what_to_do(self, parts, tmp_path):
        doc = plain(report.status(service(parts, ENDS), parts[0], tmp_path / "none", MONDAY))
        assert not doc["ok"] and doc["snapshot"] is None and doc["run"] is None
        assert doc["problems"][:2] == ["no daily run recorded: schedule python -m "
                                       "quant_hub.snapshot", "no snapshot yet"]
        assert any(p.startswith("no backup in") for p in doc["problems"])

    def test_a_current_desk_has_no_problems(self, parts, tmp_path):
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        desk.record_run(run(datetime(2026, 11, 23, 7, 0)))
        doc = plain(report.status(desk, parts[0], backups(tmp_path, 2), MONDAY))
        assert doc["ok"] and doc["problems"] == []
        assert doc["last_session"] == "2026-11-20" and doc["snapshot"]["sessions_behind"] == 0
        assert doc["snapshot"]["sha256"] == desk.summaries()[0].sha256
        assert doc["backup"]["copies"] == 2 and doc["run"]["ok"]
        # The history was imported (by the fixture) after MONDAY: the book is
        # current to that import, not to its last trade.
        assert doc["book"]["history_through"] == "2026-11-13"
        assert doc["book"]["sessions_since"] == 0
        assert doc["notes"] == [
            "not priced in #1: AAA261218P30 (an option)",
            "the history doesn't reconcile with the exports (the Trades page says where); the "
            "book's units are the newest export's, rolled forward"]
        assert doc["history"] == {"first_day": "2026-11-12", "last_day": "2026-11-13",
                                  "reconciled": False, "cash_reconciled": False}
        assert doc["record"] == {"resolved": 0, "unresolvable": 0, "scored": 0,
                                 "next_due": "2026-11-30"}

    def test_a_stale_desk_says_why(self, parts, tmp_path):
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        desk.record_run(run(datetime(2026, 11, 23, 7, 0), 1, ("no backup: disk full",),
                            ("silent: tail: French's market history isn't stored",
                             "silent: nothing new: this book on this day is already #1")))
        doc = plain(report.status(desk, parts[0], backups(tmp_path), datetime(2026, 11, 25, 8)))
        assert doc["problems"] == [
            "the last daily run (2026-11-23 07:00) failed: no backup: disk full",
            "no daily run since 2026-11-23 07:00, before the 2026-11-24 session",
            "a voice couldn't speak: tail: French's market history isn't stored",
            "snapshot #1 is as of 2026-11-20, 2 sessions behind the last (2026-11-24)"]

    def test_an_old_book_is_a_note(self, parts, tmp_path, monkeypatch):
        """With no history import, the book is current to its export's day."""
        activity = parts[0]
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        desk.record_run(run(datetime(2026, 11, 23, 7, 0)))
        monkeypatch.setattr(activity, "imports", lambda: [])
        doc = plain(report.status(desk, activity, backups(tmp_path), MONDAY))
        assert doc["ok"] and doc["book"]["current_to"] == "2026-11-10"
        assert doc["book"]["sessions_since"] == 8
        assert ("the book is current to 2026-11-10 (the newest export or history import), 8 "
                "sessions ago: trades since aren't in it until their exports are imported"
                in doc["notes"])

    def test_only_a_files_first_import_dates_the_book(self, parts, tmp_path, monkeypatch):
        """Importing an old download again doesn't make the book newer."""
        from types import SimpleNamespace as NS
        activity = parts[0]
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        imports = [NS(sha256="old", imported_at=datetime(2026, 11, 23, 9)),    # newest first
                   NS(sha256="new", imported_at=datetime(2026, 11, 18, 9)),
                   NS(sha256="old", imported_at=datetime(2026, 11, 16, 9))]
        monkeypatch.setattr(activity, "imports", lambda: imports)
        doc = plain(report.status(desk, activity, backups(tmp_path), MONDAY))
        assert doc["book"]["history_imported"] == "2026-11-18"
        assert doc["book"]["sessions_since"] == 2

    def test_a_trade_since_the_snapshot_is_a_note(self, parts, tmp_path):
        activity, _, folder = parts
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        desk.record_run(run(datetime(2026, 11, 23, 7, 0)))
        path = folder / "later.csv"
        path.write_text("date,account,kind,symbol,quantity,price,commission,fees,amount\n"
                        "2026-11-20,Brokerage,buy,BBB,2,25.00,0,0,-50.00\n", encoding="utf-8")
        activity.import_activity(path)
        doc = plain(report.status(desk, activity, backups(tmp_path), MONDAY))
        assert doc["ok"]
        assert doc["book"]["changed_since_snapshot"] == [
            {"symbol": "BBB", "snapshot_units": 4.0, "units": 6.0}]
        assert any("changed since snapshot #1 (BBB)" in n for n in doc["notes"])


class TestView:
    def test_the_desk_page_as_data(self, parts):
        desk = service(parts, ENDS)
        desk.take("test", NOW)
        view = desk.view()
        doc = plain(report.view(view, MONDAY))
        assert (doc["schema"], doc["kind"]) == (report.SCHEMA, "view")
        book = doc["book"]
        value = view.snapshot.book.value
        assert book["value_usd"] == pytest.approx(value)
        assert sum(h["value_usd"] for h in book["holdings"]) + book["cash_usd"] == (
            pytest.approx(value))
        assert sum(h["weight"] for h in book["holdings"]) + book["cash_weight"] == (
            pytest.approx(1.0))
        assert book["unpriced"] == ["AAA261218P30 (an option)"]
        baseline = next(v for v in doc["voices"] if v["voice"] == "baseline")
        claim = next(c for c in baseline["claims"] if c["key"] == "book.return@21")
        assert claim["levels"] == list(LEVELS) and claim["unit"] == report.UNITS["return"]
        assert claim["quantiles_usd"] == pytest.approx([value * q for q in claim["quantiles"]],
                                                       rel=1e-6)
        assert doc["agreement"] and doc["headline"][0]["target"] == "book.return"
        assert all(h.get("band_21_usd") for h in book["holdings"])

    def test_a_snapshot_is_cited_by_its_stored_hash(self, parts):
        """One stored before the market fields existed re-serializes with
        their defaults, so its hash, computed again, is another number."""
        activity, store, _ = parts
        desk = service(parts, {**ENDS, "SPY": "2026-11-20"})
        desk.take("test", NOW)
        payload = json.loads(store._conn.execute(
            "SELECT payload FROM desk_snapshots").fetchone()[0])
        for key in ("market", "market_price"):
            payload["book"].pop(key)
        older = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        store._conn.execute("UPDATE desk_snapshots SET payload = ?", (older,))
        stored = desk.summaries()[0].sha256
        assert desk.snapshot().sha256 != stored
        assert report.view(desk.view(), MONDAY)["snapshot"]["sha256"] == stored


class TestChanges:
    def test_two_snapshots_side_by_side(self, parts):
        activity, store, folder = parts
        first = service(parts, ENDS)
        first.take("test", NOW)
        later = DeskService(store, MarketService([Bars({"AAA": "2026-11-23",
                                                         "BBB": "2026-11-23"}, 301)]),
                            activity, folder / "desk")
        later.take("test", datetime(2026, 11, 24, 7, 0))
        old, new = later.summaries()[1], later.summaries()[0]
        a, b = later.snapshot(old.id), later.snapshot(new.id)
        doc = plain(report.changes(old, a, new, b, (None, None), MONDAY))
        assert doc["sessions"] == 1 and (doc["old"]["id"], doc["new"]["id"]) == (1, 2)
        aaa = next(h for h in doc["holdings"] if h["symbol"] == "AAA")
        assert not aaa["traded"] and aaa["price_change"] == pytest.approx(   # prices rounded
            aaa["price_usd"][1] / aaa["price_usd"][0] - 1, abs=1e-6)
        assert doc["book"]["change_usd"] == pytest.approx(b.book.value - a.book.value, rel=1e-6)
        rows = [r for r in doc["claims"] if r["voice"] == "baseline"
                and r["key"] == "book.return@21" and r["level"] == 0.05]
        (row,) = rows
        assert row["change"] == pytest.approx(row["new"] - row["old"], abs=1e-6)
        assert row["change_usd"] == pytest.approx(      # new and old are rounded
            b.book.value * row["new"] - a.book.value * row["old"], abs=1e-6 * b.book.value)
        events = [r for r in doc["claims"] if "event" in r]
        assert events and all(r["unit"] == report.UNITS["probability"] for r in events)
        assert [len(h) for h in doc["headline"]] == [len(doc["headline"][0])] * 2


class TestRecord:
    def test_nothing_scored_yet(self):
        doc = plain(report.record(DeskRecord((), (), {}, (), None), MONDAY))
        assert doc["series"] == [] and doc["regimes"] is None

    def test_a_record_as_data(self):
        test = scoring.SkillTest(6, -0.01, -2.1, 0.08, "")
        series = scoring.Series("llm", "book.return", 21, "quantiles", None, 12, 0.031, 12,
                                0.034, 0.088, test, ("decision",))
        calibration = scoring.Calibration("llm", "book.return", 21, "quantiles", 12, 0.6,
                                          (0.0, 0.25, 0.5, 0.75, 1.0), None, None)
        doc = plain(report.record(DeskRecord((), (series,), {"calm": (series,)}, (calibration,),
                                             (0.101, 0.146), 12, 0, date(2026, 12, 2)), MONDAY))
        (row,) = doc["series"]
        assert (row["author"], row["skill"], row["test"]["p"]) == ("llm", 0.088, 0.08)
        assert doc["by_regime"]["calm"][0]["n"] == 12
        assert doc["regimes"] == {"calm_below": 0.101, "stormy_from": 0.146}
        assert doc["calibration"][0]["coverage"]["0.05"] == 0.0


class TestTheCommand:
    @pytest.fixture
    def cli(self, parts, monkeypatch, tmp_path):
        from quant_hub import desk as cli
        activity = parts[0]
        desk = service(parts, ENDS)

        class Core:
            config = type("Config", (), {"backup_folder": backups(tmp_path),
                                         "db_path": tmp_path / "market_data.db"})()

            def __init__(self):
                self.desk, self.activity = desk, activity

            def shutdown(self):
                pass
        monkeypatch.setattr(cli, "build_core", Core)
        monkeypatch.setattr(cli, "datetime", type("Clock", (), {"now": staticmethod(
            lambda: MONDAY)}))
        return cli, desk

    def test_nothing_to_show_yet(self, cli, capsys):
        main, _ = cli
        assert main.main(["view"]) == 1 and "no snapshot yet" in capsys.readouterr().err
        assert main.main(["status"]) == 1
        assert "no snapshot yet" in capsys.readouterr().out

    def test_every_command_as_text_and_json(self, cli, capsys):
        main, desk = cli
        desk.take("test", NOW)
        desk.take("test", NOW)                            # one per data day: nothing new
        desk.record_run(run(datetime(2026, 11, 23, 7, 0)))
        assert main.main(["status"]) == 0
        assert "(last session 2026-11-20): current" in capsys.readouterr().out
        assert main.main(["view"]) == 0
        out = capsys.readouterr().out
        assert "snapshot #1 as of 2026-11-20" in out and "return@21 5%" in out
        assert main.main(["changes"]) == 1
        assert "only one snapshot" in capsys.readouterr().err
        assert main.main(["record"]) == 0 and "nothing scored yet" in capsys.readouterr().out
        for command in (["status"], ["view"], ["record", "--scored"]):
            assert main.main([*command, "--json"]) == 0
            assert json.loads(capsys.readouterr().out)["kind"] == command[0]

    def test_a_failed_run_reads_as_one(self, cli, capsys):
        main, desk = cli
        desk.take("test", NOW)
        desk.record_run(run(datetime(2026, 11, 23, 7, 0), None, ("no snapshot: no bars",)))
        assert main.main(["status"]) == 1
        out = capsys.readouterr().out
        assert "FAILED; no snapshot, 0 resolved" in out
        assert "  ! the last daily run (2026-11-23 07:00) failed: no snapshot: no bars" in out

    def test_changes_since_a_date(self, cli, parts, capsys):
        main, desk = cli
        activity, store, folder = parts
        desk.take("test", NOW)
        DeskService(store, MarketService([Bars({"AAA": "2026-11-23", "BBB": "2026-11-23"},
                                                301)]),
                    activity, folder / "desk").take("test", datetime(2026, 11, 24, 7, 0))
        assert main.main(["changes", "--since", "2026-11-20"]) == 0
        assert "#1 (as of 2026-11-20) -> #2 (as of 2026-11-23), 1 session" in (
            capsys.readouterr().out)
        assert main.main(["changes", "--since", "2026-11-19"]) == 1
        assert "no snapshot as of 2026-11-19" in capsys.readouterr().err
        assert main.main(["changes", "--since", "1", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["sessions"] == 1


def test_decisions_listed_as_json(parts, monkeypatch, capsys):
    from quant_hub import decide as cli
    from quant_hub.data.desk import parse_claim
    desk = service(parts, ENDS)
    desk.take("test", NOW)
    desk.decide("llm", (parse_claim("book.fall@21 >= 10% p 20%"),), "calm tail",
                (("BBB", 1.0),), now=MONDAY)

    class Core:
        def __init__(self):
            self.desk = desk

        def shutdown(self):
            pass
    monkeypatch.setattr(cli, "build_core", Core)
    assert cli.main(["--list", "--json"]) == 0
    (decision,) = json.loads(capsys.readouterr().out)["decisions"]
    assert decision["author"] == "llm" and decision["trades"] == [{"symbol": "BBB",
                                                                    "units": 1.0}]
    assert decision["claims"][0]["resolves_on"] == "2026-12-22"
    assert decision["snapshot"]["sha256"] == desk.summaries()[0].sha256
    assert cli.main(["--json"]) == 1
    assert "names the decision's file" in capsys.readouterr().err
