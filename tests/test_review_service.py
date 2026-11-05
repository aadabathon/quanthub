"""The daily review (W7), end to end on made-up tickers: the rules file, the
newest export by its inner date, the checks over what each rule governs,
the deciding day removed, the gates, persistence across runs, trade drafts,
the journal, and every stop that names what to fix."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.regime import Effect, Grade
from quant_hub.data.sources import SyntheticSource
from quant_hub.importers.regime_md import RegimeFileError
from quant_hub.models.portfolio import correlated_returns
from quant_hub.models.regime import Outcome
from quant_hub.services.file_import import FileSource
from quant_hub.services.fundamentals import FundamentalsService
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService
from quant_hub.services.review import ReviewError, ReviewService
from quant_hub.services.valuation import ValuationService
from quant_hub.storage.bar_store import BarStore, InstrumentKey
from quant_hub.storage.fundamentals_store import FundamentalsStore
from quant_hub.storage.position_store import PositionStore
from quant_hub.storage.regime_store import RegimeStore
from quant_hub.storage.valuation_store import ValuationStore
from test_regime_md import ROW, fill, row

NOW = datetime(2026, 10, 23, 9, 0)
HEADER = ("Symbol,Quantity,Last,$ Chg,% Chg,Volume,$ Day G/L,% Day G/L,$ Total G/L,"
          "% Total G/L,Value,Basis,Day Range,52W Range,Earnings Date,Div Amt,Div Ex-Date")


def export(day: str, holdings: dict[str, float], cash: float) -> str:
    """A made-up Positions-view download as of ``day`` (MM/DD/YYYY), at $100."""
    lines = [f'"Positions \nAll Accounts \nas of {day} at 4:00:00 PM"', "", HEADER]
    for symbol, quantity in holdings.items():
        lines.append(f"{symbol},{quantity},100.00,0,0,0,--,--,--,--,{quantity * 100:.2f},"
                     "--,--,--,--,--,--")
    lines.append(f"Cash (SPAXX),{cash:.2f},1.00,0,0,0,--,--,--,--,{cash:.2f},--,--,--,--,--,--")
    return "\n".join(lines) + "\n"


def bars(store: BarStore | None = None, end="2026-10-22") -> tuple[BarStore, pd.Timestamp]:
    """560 sessions of made-up daily closes ending at ``end``, under the
    "file" source, and the day of CCC's planted +500% jump."""
    store = store if store is not None else BarStore(":memory:")
    days = pd.bdate_range(end=end, periods=560)
    vols = np.array([0.010, 0.012, 0.015, 0.018])
    corr = np.array([[1, .6, .5, .2], [.6, 1, .5, .2], [.5, .5, 1, .1], [.2, .2, .1, 1]])
    r = correlated_returns(vols, corr, len(days), np.full(4, 0.0003), seed=21)
    frame = pd.DataFrame(r, index=days, columns=["SPY", "AAA", "BBB", "CCC"])
    jump = days[300]
    frame.loc[jump, "CCC"] = 5.0
    for symbol in frame.columns:
        closes = 100.0 * (1.0 + frame[symbol]).cumprod()
        store.insert_bars(InstrumentKey(symbol), "file", "1 day", pd.DataFrame({"close": closes}))
    return store, jump


RULES = fill({
    "Version: ___ · Written: ___ · Reason for this version: ___":
        "Version: 1 · Written: 2026-10-22 · Reason for this version: first",
    "failing ___ reviews in a row": "failing 2 reviews in a row",
    "| History source | ___ |": "| History source | `file` |",
    "| Benchmark (the core's yardstick) | ___ |": "| Benchmark (the core's yardstick) | SPY |",
    "| Cash rate | ___ % |": "| Cash rate | 4 % |",
    ROW["sleeve"]: "| AAA | core | the beta | 30 – 35 % of equity |\n"
                   "| BBB | stream: alpha | x | ___ |\n"
                   "| CCC | stream: beta | y | ___ |",
    "Sleeve size outside its intended range: ___ (fail / warn / note)":
        "Sleeve size outside its intended range: warn (fail / warn / note)",
    "numbers: ___ (as measured": "numbers: without that day (as measured",
    ROW["weight"]: row("weight", "15", "fail"),
    ROW["beta"]: row("beta", "0.5 – 1.5 by 1.645 SE"),
    ROW["cash"]: row("cash", "10 – 40", "note"),
    ROW["hurdle"]: row("hurdle", "by 1 SE"),
    ROW["rho"]: row("rho", "0.9", "fail"),
    'Max beta of a "stream" to the core | ___ | β ± SE | Regression on the benchmark (W2) | ___ |':
        'Max beta of a "stream" to the core | 0 | β ± SE | Regression on the benchmark (W2) | warn |',
    ROW["calm"]: row("calm", "≥ 1 %", "block adds", "Markov, K = 2"),
    ROW["volfc"]: row("volfc", "≤ 300 % a year", "suggest hedge", "GARCH over 20 days"),
    ROW["own"]: row("own", "credit calm", "block adds"),
    ROW["margin"]: row("margin", "≥ 20 %"),
})


@pytest.fixture
def world(tmp_path):
    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "10-15-26_port.csv").write_text(export("10/15/2026", {"AAA": 40, "BBB": 10}, 4000))
    (folder / "10-22-26_port.csv").write_text(export("10/22/2026",
                                                     {"AAA": 40, "BBB": 20, "CCC": 10}, 3000))
    (folder / "notes.csv").write_text("not,a,positions,file\n1,2,3,4\n")
    regime = tmp_path / "REGIME.md"
    regime.write_text(RULES, encoding="utf-8")
    store_of_bars, jump = bars()
    market = MarketService(sources=[SyntheticSource(), FileSource(store_of_bars)])
    portfolio = PortfolioService(market, PositionStore(":memory:"))
    valuation = ValuationService(FundamentalsService(FundamentalsStore(":memory:"), ""), market,
                                 ValuationStore(":memory:"))
    store = RegimeStore(":memory:")
    clock = iter([NOW, NOW, datetime(2026, 10, 24, 9, 0), datetime(2026, 10, 24, 9, 0)])
    service = ReviewService(portfolio, valuation, store, regime, folder,
                            now=lambda: next(clock, datetime(2026, 10, 25)))
    return service, store, regime, folder, jump


def by_key(review, key):
    return [c for c in review.checks if c.check.rule.key == key]


def test_a_review_reads_the_newest_export_and_checks_every_rule(world):
    service, store, _, _, _ = world
    review = service.run({"credit calm": True})
    assert review.files == ("10-22-26_port.csv",)
    assert any(s.startswith("notes.csv") for s in review.skipped)
    assert review.snapshot.as_of.date() == date(2026, 10, 22)
    assert {c.check.rule.key for c in review.checks} >= {
        "4.max-weight", "4.beta", "4.cash", "6.hurdle", "6.correlation", "6.stream-beta",
        "7.calm", "7.volatility", "7.indicator.1", "8.margin", "3.size.AAA"}
    weights = {c.item: c.check for c in by_key(review, "4.max-weight")}
    assert set(weights) == {"AAA", "BBB", "CCC"}
    assert weights["BBB"].outcome is Outcome.BREACH and weights["BBB"].consequence is Grade.FAIL
    assert weights["CCC"].outcome is Outcome.PASS
    # AAA is the core: over the cap, shown, not graded (A1).
    assert weights["AAA"].outcome is Outcome.EXEMPT and weights["AAA"].consequence is None
    assert weights["AAA"].measure.estimate == pytest.approx(0.4)
    (cash,) = by_key(review, "4.cash")
    assert cash.check.outcome is Outcome.PASS and cash.check.measure.estimate == pytest.approx(0.3)
    (size,) = by_key(review, "3.size.AAA")
    assert size.check.outcome is Outcome.BREACH and size.check.bound == pytest.approx(0.35)


def test_the_deciding_day_is_flagged_and_removed_as_section_4_chose(world):
    service, *_, jump = world
    review = service.run({"credit calm": True})
    (day,) = review.influential
    assert day.date.date() == jump.date() and day.decides_mix
    assert [h.symbol for h in day.holdings] == ["CCC"]
    assert review.removed_days == (jump.date(),)
    assert review.graded is not review.view
    assert review.graded.report.vol_annual < review.view.report.vol_annual
    # One rule for both pages: the review grades on the numbers the Portfolio
    # page shows as WITHOUT (D53).
    without = review.view.report.influence.without
    assert review.graded.report.vol == pytest.approx(without.vol, rel=1e-12)
    assert review.graded.report.var_historical == pytest.approx(without.var_historical,
                                                                rel=1e-12)


def test_streams_get_sleeve_level_checks_and_section_1_counts_runs(world):
    service, store, _, _, _ = world
    first = service.run({"credit calm": True})
    betas = {c.item: c.check for c in by_key(first, "6.stream-beta")}
    assert set(betas) == {"stream: alpha", "stream: beta"}
    assert all(c.outcome in (Outcome.BREACH, Outcome.NEAR) for c in betas.values())
    pairs = {c.item for c in by_key(first, "6.correlation")}
    assert "stream: alpha × stream: beta" in pairs and "stream: alpha × core" in pairs
    verdicts = {v.sleeve: v for v in first.streams}
    breached = [s for s, v in verdicts.items() if v.breaches]
    assert breached and all(verdicts[s].run == 1 and verdicts[s].grade is Grade.WARN
                            for s in breached)
    second = service.run({"credit calm": True})                 # the next day
    again = {v.sleeve: v for v in second.streams}
    assert all(again[s].run == 2 and again[s].grade is Grade.FAIL for s in breached
               if again[s].breaches)
    assert len(store.reviews()) == 2
    assert set(store.reviews()[0].payload["streams"]) == {"stream: alpha", "stream: beta"}


def test_gates_indicators_and_valuations_without_a_saved_one(world):
    service, *_ = world
    review = service.run({})
    (calm,) = by_key(review, "7.calm")
    assert calm.check.outcome is Outcome.PASS and review.calm is not None
    assert 0.0 <= review.calm.probability <= 1.0
    (vol,) = by_key(review, "7.volatility")
    assert vol.check.outcome is Outcome.PASS and review.volatility > 0
    (tick,) = by_key(review, "7.indicator.1")
    assert tick.check.outcome is Outcome.BREACH and tick.check.consequence is Effect.BLOCK_ADDS
    margins = by_key(review, "8.margin")
    assert {c.item for c in margins} == {"AAA", "BBB", "CCC"}
    assert all(c.check.outcome is Outcome.MISSING and "no saved valuation" in c.check.reason
               for c in margins)


def test_the_export_before_drafts_the_trades_and_the_journal_keeps_them(world):
    service, store, _, _, _ = world
    review = service.run({"credit calm": True})
    drafts = {d.symbol: d.change for d in review.drafts}
    assert drafts == {"BBB": 10.0, "CCC": 10.0}
    trade = service.add_trade(review.review_id, "bbb", 10, 100.0, "added to the stream",
                              "6.hurdle")
    service.save_notes(review.review_id, "calm week")
    assert service.trades(review.review_id)[0].id == trade
    assert service.trades()[0].symbol == "BBB"
    assert store.review(review.review_id).notes == "calm week"
    with pytest.raises(ValueError, match="symbol and a reason"):
        service.add_trade(None, "AAA", 1, None, " ")
    with pytest.raises(ValueError, match="nonzero"):
        service.add_trade(None, "AAA", 0, None, "x")


def test_a_rules_version_is_stored_once_per_text(world):
    service, store, regime, _, _ = world
    service.run({})
    service.run({})
    assert len(store.versions()) == 1
    regime.write_text(RULES.replace("Reason for this version: first",
                                    "Reason for this version: second"), encoding="utf-8")
    service.run({})
    assert [v.reason for v in store.versions()] == ["second", "first"]


def test_every_stop_names_what_to_fix(world, tmp_path):
    service, store, regime, folder, _ = world
    regime.write_text(RULES.replace("| Cash rate | 4 % |", "| Cash rate | ___ % |"),
                      encoding="utf-8")
    with pytest.raises(ReviewError, match="section 2: 'Cash rate' is blank"):
        service.run({})
    regime.write_text(RULES.replace(row("weight", "15", "fail"), row("weight", "15", "maybe")),
                      encoding="utf-8")
    with pytest.raises(RegimeFileError, match="REGIME.md line"):
        service.run({})
    regime.unlink()
    with pytest.raises(ReviewError, match="no rules at"):
        service.run({})
    regime.write_text(RULES, encoding="utf-8")
    for path in folder.glob("*_port.csv"):
        path.unlink()
    with pytest.raises(ReviewError, match="no positions export"):
        service.run({})
    gone = ReviewService(service._portfolio, service._valuation, store, regime,
                         tmp_path / "missing", now=lambda: NOW)
    with pytest.raises(ReviewError, match="doesn't exist"):
        gone.run({})
    unset = ReviewService(service._portfolio, service._valuation, store, regime, None,
                          now=lambda: NOW)
    with pytest.raises(ReviewError, match="no positions: set \\[positions\\] folder"):
        unset.run({})


def test_sleeve_totals_are_graded_and_the_core_nets_the_rest(world):
    service, store, regime, _, _ = world
    regime.write_text(RULES.replace(ROW["total"], "| core | 30 – 50 % of equity |\n"
                                                  "| stream: alpha | ___ – 10 % of equity |"),
                      encoding="utf-8")
    review = service.run({"credit calm": True})
    totals = {c.item: c.check for c in review.checks if c.check.rule.key.startswith("3.total.")}
    assert totals["core"].outcome is Outcome.PASS
    assert totals["core"].measure.estimate == pytest.approx(0.4)
    alpha = totals["stream: alpha"]                     # BBB alone, 20% of equity
    assert alpha.outcome is Outcome.BREACH and alpha.consequence is Grade.WARN
    assert alpha.measure.estimate == pytest.approx(0.2) and alpha.bound == pytest.approx(0.1)
    n = review.netting
    assert n.core == ("AAA",) and n.core_weight == pytest.approx(0.4)
    assert n.beta == pytest.approx(review.graded.report.beta)      # the split is exact
    low, high = n.core_for_band                                    # the Beta band, 0.5 – 1.5
    assert n.rest_beta + low * n.core_beta == pytest.approx(0.5)
    assert n.rest_beta + high * n.core_beta == pytest.approx(1.5)
    assert store.review(review.review_id).payload["netting"]["core"] == ["AAA"]


def test_no_core_means_no_netting(world):
    service, _, regime, _, _ = world
    regime.write_text(RULES.replace("| AAA | core | the beta |", "| AAA | other | the beta |"),
                      encoding="utf-8")
    review = service.run({"credit calm": True})
    assert review.netting is None and review.netting_note == ""
    weights = {c.item: c.check for c in by_key(review, "4.max-weight")}
    assert weights["AAA"].outcome is Outcome.BREACH            # not a core: graded as a name


def core_report(bbb_isin: str):
    """A made-up N-PORT report for the core AAA: 100 lines at 1% each. One
    is BBB, by ISIN; the rest carry only a CUSIP. CCC isn't in it."""
    from quant_hub.data.holdings import FundHoldings, Holding
    lines = [Holding("Bee Bee Bee Inc", isin=bbb_isin, pct=0.01)]
    lines += [Holding(f"Other {i}", cusip=f"00000{i:04d}", pct=0.01) for i in range(99)]
    return FundHoldings("AAA", 1, "", "AAA Index Trust", "2026-09-30", "a-1", "2026-10-20",
                        1e9, tuple(lines))


class StubIsins:
    def __init__(self, isins, missing):
        self.result = (isins, missing)

    def isins(self, symbols):
        return self.result


def look_through_world(world, report=None):
    from quant_hub.services.fund_holdings import FundHoldingsService
    from quant_hub.storage.holdings_store import HoldingsStore
    service, _, regime, _, _ = world
    holdings = HoldingsStore(":memory:")
    if report is not None:
        holdings.save(report, NOW, "h")
    service._funds = FundHoldingsService(holdings, "", now=lambda: NOW)
    regime.write_text(RULES.replace(ROW["neff"], row("neff", "5")), encoding="utf-8")
    return service


def test_effective_holdings_look_through_the_core_and_merge_a_stock_held_twice(world):
    from quant_hub.data.holdings import isin_check_digit
    bbb = "US00000BBB0" + str(isin_check_digit("US00000BBB0"))
    service = look_through_world(world, core_report(bbb))
    service._ids = StubIsins({"BBB": bbb}, {"CCC": "IBKR lists no ISIN for it"})
    review = service.run({"credit calm": True})
    (neff,) = by_key(review, "4.effective-holdings")
    # AAA 40% over 100 lines at 1%; BBB 20% plus 0.4% through AAA; CCC 10% alone.
    exposures = [0.2 + 0.4 * 0.01, 0.1] + [0.4 * 0.01] * 99
    gross = sum(exposures)
    assert neff.check.measure.estimate == pytest.approx(gross ** 2 / sum(y * y for y in exposures))
    assert neff.check.outcome is Outcome.PASS               # about 9, over the floor of 5
    notes = " ".join(review.notes)
    assert "AAA's N-PORT report as of 2026-09-30 (100 lines, 100% of its net assets)" in notes
    assert "counting each fund as one name: 2.3" in notes
    assert "Held directly and inside the core: BBB (+0.4% of equity through it)" in notes
    assert "No ISIN for CCC (IBKR lists no ISIN for it)" in notes


def test_without_the_cores_report_effective_holdings_say_how_to_get_it(world):
    service = look_through_world(world)
    review = service.run({"credit calm": True})
    (neff,) = by_key(review, "4.effective-holdings")
    assert neff.check.outcome is Outcome.MISSING
    assert neff.check.reason.startswith("AAA's N-PORT holdings aren't stored")
    assert "[sec] identity" in neff.check.reason


def french_store(days=3_000):
    """Made-up daily market and bill series under French's names, with a
    planted slide, in an in-memory series store."""
    from quant_hub.data.series import FetchedSeries, SeriesMeta
    from quant_hub.storage.series_store import SeriesStore
    dates = pd.bdate_range("1990-01-02", periods=days)
    rng = np.random.default_rng(5)
    excess = rng.normal(0.0003, 0.01, days)
    excess[1000:1100] -= 0.006
    store = SeriesStore(":memory:")

    def series(name, values):
        meta = SeriesMeta("french", name, name, "fraction", "daily", "made up")
        return FetchedSeries(meta, tuple(d.strftime("%Y-%m-%d") for d in dates),
                             tuple(float(v) for v in values))
    store.save([series("ff3.mkt_rf", excess), series("ff3.rf", np.full(days, 0.0001))],
               NOW, "h")
    return store


def test_the_books_beta_is_replayed_through_the_stored_crashes(world):
    from quant_hub.models.stress import worst_fall
    from quant_hub.services.external_data import ExternalDataService
    from quant_hub.services.portfolio import PORTFOLIO_ROW
    service, store, *_ = world
    french = french_store()
    service._external = ExternalDataService(french)
    review = service.run({"credit calm": True})
    crash = review.crash
    beta, error = review.graded.regressions.beta(PORTFOLIO_ROW)
    assert (crash.beta, crash.error) == (pytest.approx(beta), pytest.approx(error))
    market = french.series("french", "ff3.mkt_rf").values
    bills = french.series("french", "ff3.rf").values
    assert crash.worst.depth == pytest.approx(worst_fall(market, bills, beta))
    (_, low), (_, high) = crash.band
    assert low <= crash.worst.depth <= high
    saved = store.review(review.review_id).payload["crash"]
    assert saved["worst"] == pytest.approx(crash.worst.depth) and saved["data"][0] == "1990-01-02"


def test_without_frenchs_market_the_crash_stress_says_how_to_get_it(world):
    from quant_hub.services.external_data import ExternalDataService
    from quant_hub.storage.series_store import SeriesStore
    service, *_ = world
    service._external = ExternalDataService(SeriesStore(":memory:"))
    review = service.run({"credit calm": True})
    assert review.crash is None
    assert "Fetch Ken French factors" in review.crash_note


def test_the_hedge_lab_reads_the_reviews_book_and_its_beta(world):
    from quant_hub.services.external_data import ExternalDataService
    service, *_ = world
    assert service.latest_beta() is None                 # no review has measured one yet
    service._external = ExternalDataService(french_store())
    review = service.run({"credit calm": True})
    assert service.latest_beta() == pytest.approx(review.crash.beta)
    view, rate = service.current_view()
    assert rate == pytest.approx(0.04)
    assert view.report.equity == pytest.approx(review.view.report.equity)
    # The fixture's clock moves a day between the two, so the windows end a day apart.
    assert view.report.beta == pytest.approx(review.view.report.beta, abs=0.01)
