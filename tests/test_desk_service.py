"""Taking a snapshot (D1): the book rolled forward, priced point in time, frozen."""

import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.desk import Decision
from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot
from quant_hub.data.sources import DataSource
from quant_hub.data.types import PriceHistory
from quant_hub.services.activity import ActivityService
from quant_hub.services.desk import (PAPERS, VOICES, DeskService, default_source,
                                     tail_voice)
from quant_hub.services.market_service import MarketService
from quant_hub.storage.activity_store import ActivityStore
from quant_hub.storage.desk_store import DeskStore
from quant_hub.storage.canon_store import load_canon
from quant_hub.storage.position_store import PositionStore

NOW = datetime(2026, 11, 24, 17, 0)
HISTORY = ("date,account,kind,symbol,quantity,price,commission,fees,amount\n"
           "2026-11-12,Brokerage,buy,BBB,4,25.00,0,0,-100.00\n"
           "2026-11-13,Brokerage,sell,AAA,-2,40.00,0,0,80.00\n")


class Bars(DataSource):
    """Daily closes ending on a chosen day per symbol; anything else is missing."""

    name = "test"

    def __init__(self, ends: dict[str, str], sessions: int = 300) -> None:
        self._ends, self._sessions = ends, sessions

    def get_history(self, symbol, start, end, basis):
        if symbol not in self._ends:
            raise ValueError(f"no bars for {symbol}")
        index = pd.bdate_range(end=self._ends[symbol], periods=self._sessions)
        rng = np.random.default_rng(len(symbol))
        closes = 40 * np.exp(np.cumsum(rng.normal(0, 0.01, len(index))))
        return PriceHistory(symbol, self.name, pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": np.full(len(index), 1e6)}, index=index), basis)

    def get_quote(self, symbol):
        raise NotImplementedError


@pytest.fixture
def parts(tmp_path):
    activity_store, positions = ActivityStore(":memory:"), PositionStore(":memory:")
    activity = ActivityService(activity_store, positions)
    put = OptionContract("AAA", "put", 30.0, date(2026, 12, 18))
    export = PositionSnapshot((Position("AAA", Holding.EQUITY, 10.0, "Brokerage"),
                               Position("AAA261218P30", Holding.OPTION, 1.0, "Brokerage",
                                        option=put),
                               Position("MMFXX", Holding.CASH, 500.0, "Brokerage", value=500.0)),
                              datetime(2026, 11, 10), "Generic", "nov10.csv")
    positions.save(export, datetime(2026, 11, 10, 18), "aa")
    path = tmp_path / "history.csv"
    path.write_text(HISTORY, encoding="utf-8")
    activity.import_activity(path)
    store = DeskStore(":memory:")
    yield activity, store, tmp_path
    for s in (activity_store, positions, store):
        s.close()


def service(parts, ends, **kw) -> DeskService:
    activity, store, folder = parts
    return DeskService(store, MarketService([Bars(ends)]), activity, folder / "desk", **kw)


def test_the_book_is_the_newest_export_rolled_forward(parts):
    held = parts[0].current_holdings()
    assert (held.base, held.base_day, held.through) == ("nov10.csv", date(2026, 11, 10),
                                                        date(2026, 11, 13))
    assert held.units == {"AAA": 8.0, "AAA261218P30": 1.0, "BBB": 4.0}
    assert held.options == {"AAA261218P30"} and held.cash == pytest.approx(480.0)


def test_a_snapshot_prices_every_holding_on_one_day(parts):
    taken = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-19"}).take("test", NOW)
    book = taken.snapshot.book
    assert book.as_of == date(2026, 11, 19)                  # the last day both had a close
    assert [h.symbol for h in book.holdings] == ["AAA", "BBB"]
    assert book.unpriced == ("AAA261218P30 (an option)",)
    assert book.units_as_of is None                          # the units are older than the prices
    assert [v.voice for v in taken.snapshot.voices] == ["baseline", "garch", "regimes",
                                                         "bootstrap"]


def test_a_holding_without_bars_is_named_not_guessed(parts):
    taken = service(parts, {"AAA": "2026-11-20"}).take("test", NOW)
    assert [h.symbol for h in taken.snapshot.book.holdings] == ["AAA"]
    assert any(line.startswith("BBB (no bars") for line in taken.snapshot.book.unpriced)


def test_units_newer_than_the_prices_are_dated(parts):
    taken = service(parts, {"AAA": "2026-11-11", "BBB": "2026-11-11"}).take("test", NOW)
    book = taken.snapshot.book
    assert (book.as_of, book.units_as_of) == (date(2026, 11, 11), date(2026, 11, 13))


def test_one_snapshot_per_data_day(parts):
    desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})
    first = desk.take("test", NOW)
    again = desk.take("test", datetime(2026, 11, 25, 17, 0))     # stale prices, a day later
    assert again.id == first.id and again.path is None and "nothing new" in again.silent[0]
    assert len(parts[1].snapshots()) == 1


def test_the_export_is_the_snapshot_and_its_hash(parts):
    taken = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"}).take("test", NOW)
    body = json.loads(taken.path.read_text(encoding="utf-8"))
    assert body["sha256"] == taken.snapshot.sha256
    assert body["snapshot"]["as_of"] == "2026-11-20" and len(body["snapshot"]["voices"]) == 4
    assert taken.path.name == f"desk-2026-11-20-{taken.snapshot.sha256[:8]}.json"


def test_a_voice_that_cannot_speak_is_left_out_and_said_so(parts):
    def mute(book, returns):
        raise ValueError("no edge measured yet")
    taken = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"},
                    voices=(mute,)).take("test", NOW)
    assert taken.snapshot.voices == () and taken.silent == ("mute: no edge measured yet",)


def test_a_decision_is_recorded_on_its_snapshot(parts):
    desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})
    taken = desk.take("test", NOW)
    decision = Decision(NOW, taken.snapshot.sha256, "owner", (("BBB", 2.0),), "add")
    assert desk.record_decision(decision) == desk.record_decision(decision)


def test_the_command_line_takes_one_snapshot(parts, monkeypatch, capsys):
    from quant_hub import snapshot as cli

    class Core:
        config = type("Config", (), {"ibkr_enabled": False, "massive_key": ""})()
        desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})

        def shutdown(self):
            self.closed = True

    core = Core()
    monkeypatch.setattr(cli, "build_core", lambda: core)
    monkeypatch.setattr(core.desk, "take",
                        lambda source, _take=core.desk.take: _take("test", NOW))
    assert cli.main(["--no-backup"]) == 0 and core.closed
    out = capsys.readouterr().out
    assert "as of 2026-11-20 from" in out and "not priced: AAA261218P30 (an option)" in out


def test_the_command_line_prefers_massive_when_keyed(parts, monkeypatch):
    from quant_hub import snapshot as cli
    sources = []

    class Core:
        config = type("Config", (), {"ibkr_enabled": True, "massive_key": "k" * 32})()
        desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})

        def shutdown(self):
            pass

    core = Core()
    monkeypatch.setattr(cli, "build_core", lambda: core)
    monkeypatch.setattr(core.desk, "take", lambda source, _take=core.desk.take:
                        (sources.append(source), _take("test", NOW))[1])
    assert cli.main(["--no-backup"]) == 0 and sources == ["massive"]
    assert cli.main(["--source", "ibkr", "--no-backup"]) == 0 and sources[-1] == "ibkr"



def test_the_desk_page_view_puts_a_stored_snapshot_in_dollars(parts):
    desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})
    assert desk.view() is None                                   # nothing stored yet
    first = desk.take("test", NOW)
    view = desk.view()
    assert view.id == first.id and [s.id for s in view.snapshots] == [first.id]
    assert view.comparison is not None and view.problem == ""
    assert view.comparison.voices == ("baseline", "garch", "regimes", "bootstrap")
    assert view.comparison.value == pytest.approx(first.snapshot.book.value)
    assert set(view.holdings) == {"AAA", "BBB"}                 # the baseline's per-holding claims
    assert [p.id for p in view.papers["garch"]] == ["bollerslev1986", "moreiramuir2017"]
    assert [p.id for p in view.papers["baseline"]] == ["osborne1959", "samuelson1965proof"]
    assert desk.view(first.id).id == first.id


def test_every_voice_names_its_papers_and_each_paper_names_the_voice():
    canon = load_canon()
    modules = {"baseline": "quant_hub/models/baseline.py", "tail": "quant_hub/models/tail.py"}
    for voice, ids in PAPERS.items():
        assert ids, voice
        for paper_id in ids:
            assert paper_id in canon, (voice, paper_id)
            module = modules.get(voice, "quant_hub/models/voices.py")
            assert module in canon.paper(paper_id).lives_in, (voice, paper_id)


@pytest.mark.parametrize("keyed, ibkr, source", [(True, True, "massive"), (True, False, "massive"),
                                                 (False, True, "ibkr"), (False, False, "file")])
def test_the_default_source(keyed, ibkr, source):
    assert default_source(keyed, ibkr) == source


def test_the_market_is_priced_beside_the_book(parts):
    taken = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20",
                            "SPY": "2026-11-20"}).take("test", NOW)
    book = taken.snapshot.book
    assert book.market == "SPY" and book.market_price > 0
    assert [h.symbol for h in book.holdings] == ["AAA", "BBB"]        # priced, not held
    for voice in taken.snapshot.voices:
        assert any(c.target == "SPY.fall" for c in voice.claims), voice.voice


def test_the_market_must_reach_as_of_too(parts):
    taken = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20",
                            "SPY": "2026-11-18"}).take("test", NOW)
    assert taken.snapshot.as_of == date(2026, 11, 18)


def test_without_the_market_the_book_is_still_priced(parts):
    taken = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"}).take("test", NOW)
    book = taken.snapshot.book
    assert (book.market, book.market_price) == ("", None) and len(book.holdings) == 2
    assert not [c for v in taken.snapshot.voices for c in v.claims
                if c.target.startswith("SPY.")]



class French:
    """The outside-data service, as the desk uses it: French's two series."""

    def __init__(self, stored: bool = True) -> None:
        self.stored, self.refreshed = stored, 0

    def refresh_french(self):
        self.refreshed += 1
        return []

    def series(self, source, name):
        if not self.stored:
            raise LookupError(f"{source}/{name} hasn't been fetched yet")
        index = pd.bdate_range("1926-07-01", periods=30 * 252)
        rng = np.random.default_rng(1)
        values = (pd.Series(rng.normal(0.0003, 0.01, len(index)), index=index)
                  if name == "ff3.mkt_rf" else pd.Series(0.0001, index=index))
        return type("Stored", (), {"values": values})()


ENDS = {"AAA": "2026-11-20", "BBB": "2026-11-20", "SPY": "2026-11-20"}


def test_the_tail_voice_speaks_from_the_stored_century(parts):
    french = French()
    taken = service(parts, ENDS, voices=(*VOICES, tail_voice(french)),
                    external=french).take("test", NOW)
    assert french.refreshed == 1                     # refreshed first (a no-op when fresh)
    tail = next(v for v in taken.snapshot.voices if v.voice == "tail")
    assert any(c.key == "book.fall@252 >= 0.2" for c in tail.claims)
    assert tail.parameters["history"].startswith("1926-07-01")


def test_without_the_century_the_tail_voice_says_why(parts):
    french = French(stored=False)
    taken = service(parts, ENDS, voices=(*VOICES, tail_voice(french)),
                    external=french).take("test", NOW)
    assert [v.voice for v in taken.snapshot.voices] == ["baseline", "garch", "regimes",
                                                         "bootstrap"]
    assert taken.silent[0].startswith("tail: French's market history isn't stored")


def test_a_failed_refresh_doesnt_stop_the_snapshot(parts):
    class Offline(French):
        def refresh_french(self):
            raise OSError("no network")
    french = Offline()
    taken = service(parts, ENDS, voices=(*VOICES, tail_voice(french)),
                    external=french).take("test", NOW)
    assert "tail" in [v.voice for v in taken.snapshot.voices]


# ------------------------------------------------------------------ D4
LATER = {"AAA": "2026-12-31", "BBB": "2026-12-31", "SPY": "2026-12-31"}


class Gappy(Bars):
    """Later bars with one session missing for one symbol."""

    def __init__(self, ends, gap):
        super().__init__(ends)
        self._gap = gap

    def get_history(self, symbol, start, end, basis):
        history = super().get_history(symbol, start, end, basis)
        if symbol != "BBB":
            return history
        frame = history.frame.drop(pd.Timestamp(self._gap))
        return PriceHistory(symbol, self.name, frame, basis)


def later(parts, source=None) -> DeskService:
    activity, store, folder = parts
    return DeskService(store, MarketService([source or Bars(LATER)]), activity)


def test_due_claims_resolve_from_one_series_fetched_now(parts):
    from quant_hub.data.desk import resolves_on
    taken = service(parts, ENDS).take("test", NOW)
    as_of = taken.snapshot.as_of
    day = resolves_on(as_of, 5)
    resolved = later(parts).resolve(datetime(2026, 12, 2, 18))
    store = parts[1]
    assert {(o.subject, o.horizon) for o in store.outcomes()} == {("book", 5), ("SPY", 5)}
    assert resolved.stored == 2 and resolved.pending == ()
    spy = next(o for o in store.outcomes() if o.subject == "SPY")
    from quant_hub.data.types import PriceBasis
    closes = Bars(LATER).get_history("SPY", None, None, PriceBasis.ADJUSTED).frame["close"]
    expected = closes[pd.Timestamp(day)] / closes[pd.Timestamp(as_of)] - 1
    assert spy.ret == pytest.approx(expected)                    # the later series' own ratio
    stored = taken.snapshot.book.market_price
    assert spy.ret != pytest.approx(closes[pd.Timestamp(day)] / stored - 1)
    assert later(parts).resolve(datetime(2026, 12, 2, 18)).stored == 0     # stored once


def test_a_missing_bar_waits_then_is_named(parts):
    from quant_hub.data.desk import resolves_on
    taken = service(parts, ENDS).take("test", NOW)
    gap = resolves_on(taken.snapshot.as_of, 2)
    desk = later(parts, Gappy(LATER, gap))
    first = desk.resolve(datetime(2026, 12, 2, 18))
    assert first.pending and "BBB has no close on" in first.pending[0]
    assert {o.subject for o in parts[1].outcomes()} == {"SPY"}
    final = desk.resolve(datetime(2026, 12, 31, 18))              # well past ten sessions
    book = next(o for o in parts[1].outcomes() if o.subject == "book")
    assert final.unresolvable >= 1 and book.status == "unresolvable"
    assert "BBB has no close on" in book.reason


def test_the_record_scores_voices_and_decisions(parts):
    from quant_hub.data.desk import Claim
    taken = service(parts, ENDS).take("test", NOW)
    desk = later(parts)
    desk.record_decision(Decision(NOW, taken.snapshot.sha256, "trader", (), "hold",
                                  (Claim("book.return", 5, (-0.05, -0.02, 0.0, 0.02, 0.05)),)))
    assert desk.record().scored == ()
    desk.resolve(datetime(2026, 12, 2, 18))
    record = desk.record()
    voices = {s.voice for s in record.scored}
    assert voices == {"baseline", "garch", "regimes", "bootstrap", "trader"}
    series = {(s.voice, s.target, s.form) for s in record.series}
    assert ("trader", "book.return", "quantiles") in series
    garch = next(s for s in record.series if s.voice == "garch" and s.target == "book.return")
    assert garch.paired == 1 and garch.test.reason.startswith("too few")
    assert record.cutoffs is None and record.by_regime == {}     # no century stored here
