"""The What-if job (ALLOCATION A3, D89, services/whatif.py) on a made-up book:
today's vector analysed equals Portfolio's analysis of the snapshot; the crash
replay is the vector's β through the stored market; a grader's grades reach
the rows and none are invented without one; the builders and references come
from the context; missing history is named; named sets are kept."""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.series import FetchedSeries, SeriesMeta
from quant_hub.data.sources import SyntheticSource
from quant_hub.models.stress import crash_stress
from quant_hub.services.book import PORTFOLIO_STATE_KEY, Grades, PortfolioBook
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.file_import import FileSource
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService
from quant_hub.services.whatif import MAX_SHARPE, REFERENCES, WhatIfService
from quant_hub.storage.position_store import PositionStore
from quant_hub.storage.series_store import SeriesStore
from quant_hub.storage.state_store import ViewStateStore
from book_fixtures import NOW, bars, export


def french(store: SeriesStore) -> None:
    """Made-up French market and bills, 1990–1996, with a slide."""
    dates = pd.bdate_range("1990-01-02", "1996-12-31")
    excess = np.random.default_rng(9).normal(0.0003, 0.01, len(dates))
    excess[600:660] -= 0.006

    def series(name, values):
        return FetchedSeries(SeriesMeta("french", name, name, "fraction", "daily", "made up"),
                             tuple(d.strftime("%Y-%m-%d") for d in dates),
                             tuple(float(v) for v in values))
    store.save([series("ff3.mkt_rf", excess), series("ff3.rf", np.full(len(dates), 0.0002))],
               pd.Timestamp("2026-11-04").to_pydatetime(), "h")


@pytest.fixture
def world(tmp_path):
    store_of_bars, _ = bars()
    market = MarketService(sources=[SyntheticSource(), FileSource(store_of_bars)])
    portfolio = PortfolioService(market, PositionStore(":memory:"))
    state = ViewStateStore(":memory:")
    state.save(PORTFOLIO_STATE_KEY, {"source": "file", "benchmark": "SPY", "lookback": "730",
                                     "rate": "4.0"})
    path = tmp_path / "positions.csv"
    path.write_text(export("10/22/2026", {"AAA": 40, "BBB": 20, "CCC": 10}, 3000),
                    encoding="utf-8")
    portfolio.import_positions(path)
    series = SeriesStore(":memory:")
    book = PortfolioBook(portfolio, market, state, now=lambda: NOW)
    return portfolio, book, state, series


def service(world, grader=None, with_series=True):
    portfolio, book, state, series = world
    external = ExternalDataService(series) if with_series else None
    return WhatIfService(portfolio, book, state, external, grader, now=lambda: NOW)


def test_todays_vector_is_the_books_and_analyses_the_same(world):
    whatif = service(world)
    ctx = whatif.context()
    assert ctx.today == {"AAA": pytest.approx(0.4), "BBB": pytest.approx(0.2),
                         "CCC": pytest.approx(0.1)}
    assert ctx.core == frozenset() and ctx.source == "file" and ctx.benchmark == "SPY"
    assert ctx.book.label.endswith("the newest snapshot on Portfolio")
    (row,) = whatif.compare(ctx, {"Today": ctx.today}).rows
    actual = ctx.book.view
    assert row.view.report.vol == pytest.approx(actual.report.vol, rel=1e-9)
    assert row.view.report.beta == pytest.approx(actual.report.beta, rel=1e-9)
    assert row.beta[0] == pytest.approx(actual.regressions.beta("Portfolio")[0], rel=1e-9)


def test_the_crash_replay_is_the_vectors_beta_through_the_stored_market(world):
    whatif = service(world)
    ctx = whatif.context()
    (blank,) = whatif.compare(ctx, {"Today": ctx.today}).rows
    assert blank.crash is None and "Fetch Ken French factors" in blank.crash_note
    french(world[3])
    (row,) = whatif.compare(ctx, {"Today": ctx.today}).rows
    market = ExternalDataService(world[3]).series("french", "ff3.mkt_rf").values
    bills = ExternalDataService(world[3]).series("french", "ff3.rf").values
    beta, error = row.beta
    expected = crash_stress(market, bills, beta, error)
    assert row.crash_note == "" and row.crash.worst.depth == pytest.approx(expected.worst.depth)
    assert row.crash.band is not None                       # β's error gives the band


def test_without_a_grader_nothing_is_graded(world):
    whatif = service(world)
    assert not whatif.graded
    ctx = whatif.context()
    (row,) = whatif.compare(ctx, {"Today": ctx.today}).rows
    assert row.grades is None and (row.fails, row.warns) == (0, 0)
    assert row.effective == pytest.approx(row.view.report.n_effective)


def test_a_graders_grades_reach_every_row(world):
    seen = []

    class Grader:
        def grade(self, view, snapshot, book):
            seen.append((snapshot.name, book.benchmark, round(sum(r.weight for r in view.rows), 2)))
            held = {p.symbol for p in snapshot.positions}
            return Grades(fails=len(held & {"BBB"}), warns=2, effective=None,
                          notes=("Effective holdings: no look-through here.",),
                          detail={"held": sorted(held)})

    whatif = service(world, Grader())
    assert whatif.graded
    ctx = whatif.context()
    today, index = whatif.compare(ctx, {"Today": ctx.today, "Index": {"SPY": 0.7}}).rows
    assert [s[:2] for s in seen] == [("Today", "SPY"), ("Index", "SPY")]
    assert (today.fails, today.warns, index.fails) == (1, 2, 0)
    assert today.effective is None                          # the grader left it blank
    assert today.grades.notes == ("Effective holdings: no look-through here.",)
    assert today.grades.detail["held"][:1] == ["AAA"]


def test_the_builders_and_references_come_from_the_context(world):
    whatif = service(world)
    ctx = whatif.context()
    scaled = whatif.scaled(ctx, 0.5)
    assert sum(scaled.values()) == pytest.approx(0.5)       # no core: every name is a pick
    netted = whatif.netted(ctx, target=0.9)                 # no core: nets into the benchmark
    betas = {**ctx.betas, "SPY": 1.0}
    assert sum(w * betas[s] for s, w in netted.items()) == pytest.approx(0.9)
    assert "SPY" in netted and set(netted) - {"SPY"} == {"AAA", "BBB", "CCC"}
    refs = whatif.references(ctx)
    assert tuple(refs) == REFERENCES and MAX_SHARPE.endswith("(in-sample)")
    for weights in refs.values():
        assert set(weights) <= set(ctx.today) and sum(weights.values()) == pytest.approx(0.7)


def test_a_book_with_a_core_nets_into_it(world):
    whatif = service(world)
    ctx = whatif.context()
    cored = replace(ctx, book=replace(ctx.book, core=frozenset({"AAA"})))
    assert whatif.scaled(cored, 0.15)["AAA"] == pytest.approx(0.4)
    netted = whatif.netted(cored, target=0.6)
    assert set(netted) == {"AAA", "BBB", "CCC"}
    assert sum(w * cored.betas[s] for s, w in netted.items()) == pytest.approx(0.6)


def test_a_vector_without_history_says_so(world):
    whatif = service(world)
    ctx = whatif.context()
    (row,) = whatif.compare(ctx, {"Unknown": {"ZZZ": 0.5}}).rows
    assert row.view is None and "ZZZ" in row.error
    (row,) = whatif.compare(ctx, {"Too much": {"AAA": 0.8, "BBB": 0.5}}).rows
    assert "leverage" in row.error


def test_named_sets_are_kept_in_the_view_state_store(world):
    whatif = service(world)
    assert whatif.saved() == {}
    whatif.save(" Barbell ", {"spy": 0.5, "BBB": 0.1})
    whatif.save("Index", {"SPY": 0.9})
    assert whatif.saved() == {"Barbell": {"SPY": 0.5, "BBB": 0.1}, "Index": {"SPY": 0.9}}
    whatif.delete("Barbell")
    assert list(whatif.saved()) == ["Index"]
    with pytest.raises(ValueError):
        whatif.save("", {"SPY": 0.5})
    with pytest.raises(ValueError, match="leverage"):
        whatif.save("Levered", {"SPY": 0.8, "QQQ": 0.4})
