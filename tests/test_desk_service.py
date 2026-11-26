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
from quant_hub.services.desk import PAPERS, DeskService, default_source
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
    assert desk.record(decision) == desk.record(decision)


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
    assert cli.main([]) == 0 and core.closed
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
    assert cli.main([]) == 0 and sources == ["massive"]
    assert cli.main(["--source", "ibkr"]) == 0 and sources[-1] == "ibkr"



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
    modules = {"baseline": "quant_hub/models/baseline.py"}
    for voice, ids in PAPERS.items():
        assert ids, voice
        for paper_id in ids:
            assert paper_id in canon, (voice, paper_id)
            assert modules.get(voice, "quant_hub/models/voices.py") in                 canon.paper(paper_id).lives_in, (voice, paper_id)


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
