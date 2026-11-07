"""REGIME_MODEL Q3c: the evidence math, October's ported rules, and the run
end to end (``models/regime_evidence.py``, ``models/timing_rules.py``,
``services/regime_evidence.py``)."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.regime_evidence import (
    KEEP_RULE,
    KEEP_RULES,
    crisis_drawdowns,
    growth_gain,
    growth_rule,
    portfolio_returns,
    shift_test,
    survives_publication,
    tail_shift_test,
    timing_alpha,
    timing_t,
)
from quant_hub.models.regression import ALPHA, NEWEY_WEST, ols
from quant_hub.models.timing_rules import (
    FLOOR,
    family,
    spike_vol,
    valuation_rules,
    vol_rules,
)


def market(n=4000, seed=0, planted=0.0):
    """Daily excess returns; with ``planted``, a signal z_t known at t moves
    the next day's return."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("1990-01-01", periods=n)
    z = rng.normal(0, 1, n)
    x = 0.0003 + rng.normal(0, 0.01, n)
    x[1:] += planted * z[:-1]
    return pd.Series(x, index=days), pd.Series(z, index=days)


def persistent(n, seed, rho=0.995):
    """A slow β path with no timing in it."""
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    out[0] = 0.0
    for t in range(1, n):
        out[t] = rho * out[t - 1] + rng.normal(0, 0.05)
    return 1 + out


# ---------------------------------------------------------------- timing α

def test_timing_t_is_scale_free_and_zero_without_timing():
    x, z = market(planted=0.002)
    beta = 1 + 0.3 * z
    B = np.column_stack([beta.to_numpy()[:-1], 3 * beta.to_numpy()[:-1],
                         np.full(len(x) - 1, 0.7)])
    t = timing_t(B, x.to_numpy()[1:])
    assert t[0] == pytest.approx(t[1], rel=1e-12) and t[0] > 5
    assert t[2] == 0.0                                       # constant β: no timing
    still = timing_alpha(pd.Series(0.7, index=x.index), x)
    assert still.alpha == 0.0 and still.t_classical == 0.0


def test_timing_alpha_is_ols_with_newey_west():
    x, z = market(planted=0.001, seed=1)
    beta = 1 + 0.5 * z
    fit = timing_alpha(beta, x)
    b = beta.shift(1).iloc[1:].to_numpy()
    y = b * x.iloc[1:].to_numpy()
    reference = ols(y, np.column_stack([np.ones(len(y)), x.iloc[1:].to_numpy()]),
                    (ALPHA, "market"))
    assert fit.alpha == pytest.approx(252 * reference.value(ALPHA), rel=1e-12)
    assert fit.error == pytest.approx(252 * reference.error(ALPHA, NEWEY_WEST), rel=1e-12)
    assert fit.days == len(y)


# ------------------------------------------------------- the shift test

def test_planted_timing_passes_the_family_test():
    x, z = market(planted=0.0015, seed=2)
    paths = pd.DataFrame({"planted": 1 + 0.3 * z,
                          **{f"noise {k}": persistent(len(x), 10 + k) for k in range(5)}},
                         index=x.index)
    test = shift_test(paths, x, draws=300, min_shift=500, seed=3)
    assert test.p_family < 0.01
    assert test.row("planted")["adjusted"] < 0.01
    assert all(test.adjusted >= test.p)                      # adjusting never helps
    assert test.days == len(x) - 1


def test_a_noise_family_passes_at_about_its_level():
    hits = []
    for seed in range(100):
        x, _ = market(n=3000, seed=500 + seed)
        paths = pd.DataFrame({f"noise {k}": persistent(len(x), 1000 * seed + k)
                              for k in range(5)}, index=x.index)
        hits.append(shift_test(paths, x, draws=200, min_shift=300, seed=seed).p_family <= 0.05)
    assert np.mean(hits) <= 0.12                             # nominal 5%, 100 seeds


def test_the_tail_shift_finds_planted_skill_and_not_noise():
    rng = np.random.default_rng(4)
    n = 600
    days = pd.date_range("1950-01-31", periods=n, freq="ME")
    x = rng.normal(0, 1, n)
    p = 1 / (1 + np.exp(-(-1.5 + 1.2 * x)))
    events = pd.Series((rng.random(n) < p).astype(float), index=days)
    base = pd.Series(events.expanding().mean().shift(1).fillna(0.2).to_numpy(), index=days)
    noise = 1 / (1 + np.exp(-(-1.5 + 1.2 * rng.normal(0, 1, n))))
    test = tail_shift_test({"planted": (pd.Series(p, index=days), base, events),
                            "noise": (pd.Series(noise, index=days), base, events)},
                           draws=200, min_shift=24, seed=5)
    assert test.row("planted")["skill"] > 0 and test.row("planted")["adjusted"] < 0.01
    assert test.row("noise")["skill"] < test.row("planted")["skill"]
    assert test.row("noise")["p"] > 0.05


def test_a_forecast_with_nothing_to_beat_is_left_out_not_passed():
    # No events at all: the base rate is exact and skill is undefined. A NaN
    # compared against the null would read as p = 0, a false pass.
    rng = np.random.default_rng(14)
    n = 400
    days = pd.date_range("1950-01-31", periods=n, freq="ME")
    x = rng.normal(0, 1, n)
    p = 1 / (1 + np.exp(-(-1.5 + 1.2 * x)))
    events = pd.Series((rng.random(n) < p).astype(float), index=days)
    base = pd.Series(events.expanding().mean().shift(1).fillna(0.2).to_numpy(), index=days)
    never = pd.Series(0.0, index=days)
    test = tail_shift_test({"planted": (pd.Series(p, index=days), base, events),
                            "no events": (pd.Series(0.05, index=days), never, never),
                            "the base rate": (base, base, events)},
                           draws=150, min_shift=24, seed=15)
    # A forecast that is the base rate has skill 0 and nothing to test:
    # shifting it would only make it worse, which reads as a small p.
    assert test.untestable == ("no events", "the base rate")
    assert test.row("the base rate")["p"] == 1.0
    assert test.row("no events") == {"skill": pytest.approx(float("nan"), nan_ok=True),
                                     "p": 1.0, "adjusted": 1.0, "testable": False}
    alone = tail_shift_test({"planted": (pd.Series(p, index=days), base, events)},
                            draws=150, min_shift=24, seed=15)
    assert test.row("planted")["adjusted"] == alone.row("planted")["adjusted"]


def test_a_worthless_forecast_on_a_drifting_base_rate_isnt_skilled():
    # The event rate rises over the sample, so the expanding base rate
    # drifts. A forecast that is the base rate plus noise has no skill;
    # shifting the whole forecast would put the drift out of line and read
    # as a small p. Shifting its deviation from the base rate doesn't.
    rng = np.random.default_rng(16)
    n = 600
    days = pd.date_range("1950-01-31", periods=n, freq="ME")
    rate = np.linspace(0.05, 0.45, n)
    events = pd.Series((rng.random(n) < rate).astype(float), index=days)
    base = pd.Series(events.expanding().mean().shift(1).fillna(0.05).to_numpy(), index=days)
    hugging = (base + rng.normal(0, 0.002, n)).clip(0, 1)
    test = tail_shift_test({"hugging": (hugging, base, events)}, draws=300, min_shift=24,
                           seed=17)
    assert test.row("hugging")["p"] > 0.2


def test_the_after_publication_rules_and_their_history():
    # D82: rule 2 asks only that the 95% interval reach positive α. ECY's
    # numbers on 2026-11-05: rule 1 drops it, rule 2 keeps it.
    ecy = {"alpha": -0.0010, "error": 0.0077}
    assert not survives_publication(ecy, rule=1)
    assert survives_publication(ecy, rule=2)
    assert not survives_publication({"alpha": -0.02, "error": 0.005}, rule=2)
    assert survives_publication(None, rule=1) and survives_publication(None, rule=2)
    assert KEEP_RULE == 2 and set(KEEP_RULES) == {1, 2}
    assert "after D81's run" in KEEP_RULES[2]                # chosen after the result
    with pytest.raises(ValueError, match="keep rule"):
        survives_publication(ecy, rule=3)


# ------------------------------------------------------------ the reports

def test_the_growth_rule_and_its_report():
    days = pd.bdate_range("2000-01-03", periods=5)
    mean = pd.Series(0.05, index=days)
    s21, s252 = pd.Series(0.2, index=days), pd.Series(0.18, index=days)
    beta = growth_rule(mean, s21, s252, f=0.5)
    assert beta.iloc[0] == pytest.approx(0.5 * (0.05 + 0.5 * 0.18**2) / 0.04)
    assert (growth_rule(mean, s21, s252, f=1.0, cap=1.0) == 1.0).all()
    with pytest.raises(ValueError):
        growth_rule(mean, s21, s252, f=0)

    x, _ = market(n=600, seed=6)
    bills = pd.Series(0.0001, index=x.index)
    total = x + bills
    gain, error = growth_gain(pd.Series(0.8, index=x.index), total, bills)
    assert gain == pytest.approx(0.0, abs=1e-12) and error == pytest.approx(0.0, abs=1e-12)
    returns = portfolio_returns(pd.Series(1.0, index=x.index), total, bills)
    assert np.allclose(returns, total.iloc[1:])


def test_crisis_drawdowns_find_the_markets_own_peak_and_trough():
    days = pd.bdate_range("2007-01-01", "2010-12-31")
    path = np.r_[np.full(200, 0.001), np.full(300, -0.003), np.full(len(days) - 500, 0.002)]
    total = pd.Series(path, index=days)
    table = crisis_drawdowns({"hold": total, "half": 0.5 * total}, total,
                             (("2007–09", "2007-06-01", "2009-06-30"),))
    wealth = (1 + total).cumprod().loc["2007-06-01":"2009-06-30"]
    assert table.loc["2007–09", "hold"] == pytest.approx(float((wealth / wealth.cummax()).min() - 1))
    assert table.loc["2007–09", "half"] > table.loc["2007–09", "hold"]


# --------------------------------------------------------- October's rules

def test_octobers_rules_have_their_shapes():
    x, _ = market(n=1500, seed=7)
    sigma = spike_vol(x)
    rules = vol_rules(sigma)
    for name in ("contrarian", "mirror", "ratchet", "after spike"):
        valid = rules[name].dropna()
        assert valid.between(FLOOR - 1e-12, 1.0 + 1e-12).all(), name
    assert (rules["vol target"].dropna() <= 1.0).all()
    assert rules["contrarian"].add(rules["mirror"]).dropna().eq(1.7).all()
    assert sigma.iloc[:20].isna().all()                       # 21 days of warm-up
    no_pct = pd.Series(np.nan, index=x.index)
    valuation = valuation_rules(x, no_pct, no_pct)
    assert (valuation["CAPE cheap"] == FLOOR).all() and (valuation["ECY cheap"] == FLOOR).all()
    assert len(family(x, no_pct, no_pct)) == 31


# ------------------------------------------------------------ end to end

def test_the_evidence_run_end_to_end(tmp_path):
    from quant_hub.data.sources import SyntheticSource
    from quant_hub.services.market_service import MarketService
    from quant_hub.services.regime_evidence import run_evidence
    from quant_hub.services.regime_inputs import BILLS, MARKET, SHILLER
    from quant_hub.services.research import ResearchService
    from quant_hub.storage.research_store import ResearchStore
    from quant_hub.storage.schema import prepare_store
    from tests.test_regime_inputs import market_world, shiller_world
    from tests.test_regime_inputs_service import Store

    total, bills = market_world(years=14, seed=8)
    world = shiller_world(months=400, start="1990-01-01")
    store = Store({MARKET: total - bills, BILLS: bills,
                   **{("shiller", sid): world[key] for key, sid in SHILLER.items()}})
    db = tmp_path / "research.db"
    prepare_store(db)
    research_store = ResearchStore(db)
    research = ResearchService(MarketService([SyntheticSource()]), research_store)
    try:
        run = run_evidence(store, research, draws=60, refit_years=2, min_origins=24,
                           min_episodes=1, min_shift_days=252, min_shift_months=12,
                           progress=lambda _: None)
        rules = [e for e in run.ledger if e["kind"] != "tail"]
        assert len(rules) == len(run.family.names) == 31 + 18 + 8
        assert {e["kind"] for e in rules} == {"october", "rule", "difference"}
        assert all(e["adjusted"] >= e["p"] for e in rules)
        assert len([e for e in run.ledger if e["kind"] == "tail"]) == 9 * 4
        assert set(run.kept_mean) <= set(run.paths.columns.str.removeprefix("Δ "))
        assert run.kept_mean == run.kept_by_rule[KEEP_RULE]
        assert set(run.kept_by_rule[1]) <= set(run.kept_by_rule[2])   # rule 2 only loosens
        reopened = research.reopen(run.run_id)
        details = reopened.payload["details"]
        assert details["ledger_name"] == "regime" and details["family"]["paths"] == 57
        assert details["keep_rule"]["version"] == 2
        assert set(details["keep_rule"]["kept_by_rule"]) == {"1", "2"}
        assert len(details["ledger"]) == len(run.ledger)
        assert reopened.result.targets.max().max() <= 1.0 + 1e-12   # provisional, unlevered
        np.testing.assert_array_equal(reopened.result.cash, bills.reindex(total.index).iloc[1:])
        assert math.isfinite(details["family"]["p_family"])
    finally:
        research_store.close()
