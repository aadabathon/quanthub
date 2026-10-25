"""REGIME.md → RuleSet (W7): the committed template reads with every rule
off; a filled-in copy (made-up values and tickers) reads every kind of row;
and every unreadable value names its line."""

from datetime import date
from pathlib import Path

import pytest

from quant_hub.data.regime import Effect, Grade, Limit, SleeveKind
from quant_hub.importers.regime_md import RegimeFileError, parse_regime, read_regime

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "REGIME.md"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")


def fill(replacements: dict[str, str], text: str = TEMPLATE) -> str:
    """The template with whole lines replaced; each must exist exactly once,
    so a test breaks loudly when the template drifts."""
    for old, new in replacements.items():
        assert text.count(old) == 1, f"template line not found once: {old!r}"
        text = text.replace(old, new)
    return text


ROW = {   # template rows, by label, as the template writes them
    "weight": "| Max single holding weight | ___ | % of equity | Holding weight, options counted by delta (*today*) | ___ |",
    "vol": "| Portfolio volatility band | ___ – ___ | % a year | *Volatility · annual* tile (*today*) | ___ |",
    "beta": "| Beta band | ___ – ___ | vs the benchmark | *Beta* tile (*today*); ± SE (W2) | ___ |",
    "cash": "| Cash range | ___ – ___ | % of equity | Cash and money-market lines (*today*) | ___ |",
    "neff": "| Minimum effective holdings | ___ | count | *Effective holdings* N_eff (*today*) | ___ |",
    "sizing": "| Sizing cap | ___ | fraction f of estimated Kelly | Kelly f\\* and its SE (*today*; per sleeve in W3) | ___ |",
    "delta": "| Net delta band | ___ – ___ | % of equity | Aggregate delta dollars across positions over equity (W3) | ___ |",
    "vega": "| Vega limit | ___ | $ per vol point | Aggregate vega (W3) | ___ |",
    "short": "| Short options allowed | ___ | yes / covered only / no | Option positions with negative quantity (*today*) | ___ |",
    "dte": "| Minimum days to expiry held | ___ | calendar days | Days to expiry from the snapshot date (*today*) | ___ |",
    "hurdle": "| A stream must clear its growth hurdle | ___ | e.g. estimate above the hurdle, or by ___ SE | (μᵢ − r) − Cov(Rᵢ, R_p), ± SE (W3) | ___ |",
    "rho": "| Max correlation between streams | ___ | \\|ρ\\| of regression residuals | Residual correlation matrix (W2) | ___ |",
    "calm": "| Calm-regime probability | ≥ ___ % | Markov filtered probability of the lowest-vol regime, K = ___ (*today*: Markov Lab; never smoothed or Viterbi) | ___ |",
    "volfc": "| Volatility forecast | ≤ ___ % a year | GARCH expected vol over ___ days (*today*: GARCH Lab, vol context) | ___ |",
    "odds": "| Regime odds ahead | ≥ ___ % calm in ___ days | Markov odds of the calm regime that many days ahead (*today*) | ___ |",
    "own": "| Your own indicators | ___ | Anything else you watch, separated by `;`. Each is a manual tick box on the Review page | ___ |",
    "universe": "| Universe | ___ | Holdings + Markets watchlist + this list | — |",
    "margin": "| DCF margin of safety to add a name | ≥ ___ % | 1 − price / value per share (W6) | ___ |",
    "implied": "| Implied growth vs history | ≤ ___ × trailing revenue CAGR | Reverse DCF, every root shown (W6) | ___ |",
    "missing": "| Missing filing fields allowed | ___ | The loud-failure list: missing fields named, never filled in (W5) | ___ |",
    "sleeve": "| ___ | ___ | ___ | ___ % of equity |",
    "lookback": "| Lookback for risk numbers | ___ calendar days | Portfolio page *Lookback*; 730 by default, at least 120 |",
}


def row(key: str, value: str, consequence: str = "warn", measured: str | None = None) -> str:
    """The template's row with its value (and last cell) filled in."""
    cells = ROW[key].split(" | ")
    cells[1] = value
    if measured is not None:
        cells[-2] = measured
    cells[-1] = f"{consequence} |"
    return " | ".join(cells)


FILLED = fill({
    "Version: ___ · Written: ___ · Reason for this version: ___":
        "Version: 2 · Written: 2026-10-23 · Reason for this version: tighter cash",
    "failing ___ reviews in a row": "failing 3 reviews in a row",
    "| Accounts in scope | ___ |": "| Accounts in scope | Brokerage, IRA |",
    "| Benchmark (the core's yardstick) | ___ |": "| Benchmark (the core's yardstick) | spy |",
    "| Cash rate | ___ % |": "| Cash rate | bills: 4.25 % on 2026-10-21 |",
    ROW["lookback"]: "| Lookback for risk numbers | 730 calendar days | x |",
    ROW["sleeve"]: "| AAA | core | the beta | 40 – 60 % of equity |\n"
                   "| BBB | stream: carry | rates | 10 % of equity |\n"
                   "| CCC | Hedge | crash cover | ___ % of equity |\n"
                   "| DDD | ___ | ___ | ___ % of equity |",
    "Sleeve size outside its intended range: ___ (fail / warn / note)":
        "Sleeve size outside its intended range: warn (fail / warn / note)",
    ROW["weight"]: row("weight", "15", "fail"),
    ROW["vol"]: row("vol", "10 – 20"),
    ROW["beta"]: row("beta", "0.8 – 1.2 by 1.645 SE"),
    ROW["cash"]: row("cash", "___ – 40", "note"),
    ROW["neff"]: row("neff", "5"),
    ROW["sizing"]: row("sizing", "0.5"),
    ROW["delta"]: row("delta", "-20 – 20"),
    ROW["vega"]: row("vega", "$1,500"),
    ROW["short"]: row("short", "covered only", "fail"),
    ROW["dte"]: row("dte", "30", "note"),
    ROW["hurdle"]: row("hurdle", "by 1 SE"),
    ROW["rho"]: row("rho", "0.3 by 1.96 SE", "fail"),
    ROW["calm"]: row("calm", "≥ 60 %", "block adds",
                     "Markov filtered probability, K = 2 (*today*)"),
    ROW["volfc"]: row("volfc", "≤ 25 % a year", "suggest hedge", "GARCH expected vol over 20 days"),
    ROW["odds"]: row("odds", "≥ 50 % calm in 20 days", "note"),
    ROW["own"]: row("own", "credit spreads calm; breadth above 50%", "block adds"),
    ROW["universe"]: "| Universe | AAA, BBB eee | x | — |",
    ROW["margin"]: row("margin", "≥ 25 %"),
    ROW["implied"]: row("implied", "≤ 1.5 × trailing revenue CAGR", "note"),
    ROW["missing"]: row("missing", "2"),
    "before a change (e.g. a research run supporting it): ___":
        "before a change (e.g. a research run supporting it): a week's notice",
})


def test_the_committed_template_reads_with_every_rule_off():
    rs = read_regime(TEMPLATE_PATH)
    assert (rs.version, rs.written, rs.reason, rs.thesis_runs) == ("", None, "", None)
    assert rs.rules == () and rs.sleeves == () and rs.sleeve_consequence is None
    assert rs.settings.benchmark is None and rs.settings.cash_rate is None
    assert rs.market.regimes is None and rs.universe.tickers == ()
    assert [when for when, _, _ in rs.routine] == [
        "Daily", "Weekly", "Monthly / quarterly", "After a trade (placed by hand)"]


@pytest.fixture(scope="module")
def filled():
    return parse_regime(FILLED)


def test_the_header_thesis_and_settings(filled):
    assert (filled.version, filled.written, filled.reason) == ("2", date(2026, 10, 23),
                                                               "tighter cash")
    assert filled.thesis_runs == 3
    s = filled.settings
    assert (s.accounts, s.benchmark, s.lookback_days) == (("Brokerage", "IRA"), "SPY", 730)
    assert s.cash_rate == pytest.approx(0.0425)


def test_sleeves_ranges_targets_and_their_grade(filled):
    rows = {r.symbol: r for r in filled.sleeves}
    assert rows["AAA"].kind is SleeveKind.CORE and rows["AAA"].intended == Limit(0.40, 0.60)
    assert (rows["BBB"].sleeve, rows["BBB"].target, rows["BBB"].intended) == (
        "stream: carry", pytest.approx(0.10), None)
    assert rows["CCC"].sleeve == "hedge" and rows["CCC"].why == "crash cover"
    assert rows["DDD"].kind is None and rows["DDD"].sleeve is None     # listed, not yet assigned
    assert filled.sleeve_consequence is Grade.WARN
    assert filled.sleeve_of("BBB") == "stream: carry" and filled.sleeve_of("ZZZ") is None


def test_limits_scales_and_evidence(filled):
    r = filled.rule
    assert (r("4.max-weight").limit, r("4.max-weight").consequence) == (Limit(upper=0.15), Grade.FAIL)
    assert r("4.volatility").limit == Limit(0.10, 0.20)
    assert (r("4.beta").limit, r("4.beta").evidence) == (Limit(0.8, 1.2), pytest.approx(1.645))
    assert r("4.cash").limit == Limit(upper=0.40) and r("4.cash").consequence is Grade.NOTE
    assert r("4.effective-holdings").limit == Limit(lower=5.0)
    assert r("4.sizing").limit == Limit(upper=0.5)
    assert r("5.net-delta").limit == Limit(-0.20, 0.20)
    assert r("5.vega").limit == Limit(upper=1500.0)
    assert (r("5.short-options").measure, r("5.short-options").limit) == (
        "uncovered short options", Limit(upper=0.0))
    assert r("5.expiry").limit == Limit(lower=30.0)
    assert (r("6.hurdle").limit, r("6.hurdle").evidence) == (Limit(lower=0.0), 1.0)
    assert (r("6.correlation").limit, r("6.correlation").evidence) == (Limit(upper=0.3),
                                                                      pytest.approx(1.96))
    assert r("8.margin").limit == Limit(lower=0.25)
    assert r("8.implied").limit == Limit(upper=1.5)
    assert r("8.missing").limit == Limit(upper=2.0)
    assert r("4.risk-share") is None                         # blank rows stay off


def test_gates_their_parameters_and_the_owners_indicators(filled):
    r = filled.rule
    assert (r("7.calm").limit, r("7.calm").consequence) == (Limit(lower=0.60), Effect.BLOCK_ADDS)
    assert (r("7.volatility").limit, r("7.volatility").consequence) == (
        Limit(upper=0.25), Effect.SUGGEST_HEDGE)
    assert r("7.odds").limit == Limit(lower=0.50)
    m = filled.market
    assert (m.regimes, m.volatility_horizon, m.odds_horizon) == (2, 20, 20)
    assert m.indicators == ("credit spreads calm", "breadth above 50%")
    assert [r("7.indicator.1").measure, r("7.indicator.2").label] == [
        "indicator: credit spreads calm", "breadth above 50%"]
    assert filled.universe.tickers == ("AAA", "BBB", "EEE")
    assert filled.change_policy == "a week's notice"


def test_short_options_yes_or_no():
    yes = parse_regime(fill({ROW["short"]: row("short", "yes", "fail")}))
    no = parse_regime(fill({ROW["short"]: row("short", "No", "fail")}))
    assert yes.rule("5.short-options") is None
    assert (no.rule("5.short-options").measure, no.rule("5.short-options").limit) == (
        "short options", Limit(upper=0.0))


def test_the_dominant_day_choice():
    assert parse_regime(TEMPLATE).without_dominant_day is False            # blank: as measured
    without = fill({"numbers: ___ (as measured": "numbers: without that day (as measured"})
    assert parse_regime(without).without_dominant_day is True
    measured = fill({"numbers: ___ (as measured": "numbers: as measured (as measured"})
    assert parse_regime(measured).without_dominant_day is False
    with pytest.raises(RegimeFileError, match="isn't 'as measured' or 'without that day'"):
        parse_regime(fill({"numbers: ___ (as measured": "numbers: both (as measured"}))


def test_a_hyphen_between_numbers_is_a_range_not_a_minus():
    rs = parse_regime(fill({ROW["delta"]: row("delta", "5-10")}))
    assert rs.rule("5.net-delta").limit == Limit(0.05, 0.10)
    rs = parse_regime(fill({ROW["delta"]: row("delta", "−20--10")}))
    assert rs.rule("5.net-delta").limit == Limit(-0.20, -0.10)


def line_of(text: str, fragment: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), start=1) if fragment in line)


@pytest.mark.parametrize("key, value, consequence, message", [
    ("weight", "15", "___", "a value without an 'On breach' grade"),
    ("weight", "15", "maybe", "isn't one of fail / warn / note"),
    ("vol", "20 – 10", "warn", "is above the upper"),
    ("vol", "20", "warn", "a range is 'low – high'"),
    ("weight", "10 or 15", "warn", "one number expected"),
    ("hurdle", "mostly", "warn", "write 'above the hurdle' or 'by k SE'"),
    ("rho", "1.2", "warn", r"must be in \[0, 1\)"),
    ("dte", "2.5", "warn", "a count is a whole number"),
    ("short", "sometimes", "warn", "isn't yes, covered only or no"),
    ("calm", "≥ 60 %", "warn", "isn't one of block adds / suggest hedge / note"),
    ("odds", "≥ 50 %", "note", "write '≥ p % calm in h days'"),
])
def test_an_unreadable_row_names_its_line(key, value, consequence, message):
    text = fill({ROW[key]: row(key, value, consequence)})
    with pytest.raises(RegimeFileError, match=message) as error:
        parse_regime(text)
    assert error.value.line == line_of(text, ROW[key].split(" | ")[0])


@pytest.mark.parametrize("replacements, message", [
    ({ROW["weight"]: "| Max weight per name | 15 | % | x | fail |"}, "unknown rule 'Max weight per name'"),
    ({ROW["sleeve"]: "| AAA | stream | x | ___ |"}, "each stream needs its own name"),
    ({ROW["sleeve"]: "| AAA | core: big | x | ___ |"}, "only a stream takes a name"),
    ({ROW["sleeve"]: "| AAA | income | x | ___ |"}, "isn't core, hedge, other or stream"),
    ({ROW["sleeve"]: "| AAA | core | x | ___ |\n| AAA | hedge | x | ___ |"}, "AAA is already in a sleeve"),
    ({ROW["lookback"]: "| Lookback for risk numbers | 90 calendar days | x |"}, "at least 120"),
    ({"| Cash rate | ___ % |": "| Cash rate | the bill rate |"}, "needs a percent"),
    ({"failing ___ reviews": "failing 0 reviews"}, "whole number ≥ 1"),
    ({"Written: ___": "Written: yesterday"}, "'Written' is a date"),
    ({ROW["calm"]: row("calm", "≥ 60 %", "note", "Markov, K = 1")}, "K must be a whole number ≥ 2"),
    ({ROW["weight"]: row("weight", "15", "fail") + "\n" + row("weight", "20", "warn")},
     "is already on line"),
])
def test_structural_mistakes_name_their_line(replacements, message):
    with pytest.raises(RegimeFileError, match=message) as error:
        parse_regime(fill(replacements))
    assert error.value.line >= 1 and str(error.value).startswith("REGIME.md line ")


def test_a_file_without_its_header_line_is_refused():
    with pytest.raises(RegimeFileError, match="no 'Version:"):
        parse_regime(TEMPLATE.replace("Version: ___ · Written: ___ · Reason for this version: ___",
                                      ""))
