"""The daily review (W7), end to end on made-up tickers: the rules file, the
newest export by its inner date, the checks over what each rule governs,
the dominant day removed, the gates, persistence across runs, trade drafts,
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
    ROW["weight"]: row("weight", "25", "fail"),
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
    assert weights["AAA"].outcome is Outcome.BREACH and weights["AAA"].consequence is Grade.FAIL
    assert weights["CCC"].outcome is Outcome.PASS
    (cash,) = by_key(review, "4.cash")
    assert cash.check.outcome is Outcome.PASS and cash.check.measure.estimate == pytest.approx(0.3)
    (size,) = by_key(review, "3.size.AAA")
    assert size.check.outcome is Outcome.BREACH and size.check.bound == pytest.approx(0.35)


def test_the_dominant_day_is_flagged_and_removed_as_section_4_chose(world):
    service, *_, jump = world
    review = service.run({"credit calm": True})
    assert review.dominance["CCC"].flagged and not review.dominance["AAA"].flagged
    assert review.removed_days == (jump.date(),)
    assert review.graded is not review.view
    assert review.graded.report.vol_annual < review.view.report.vol_annual


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
    regime.write_text(RULES.replace(row("weight", "25", "fail"), row("weight", "25", "maybe")),
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
