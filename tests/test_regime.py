"""Regime checks (W7), following the module docstring's test plan: breaches
and their noise rate, Fisher's scale, persistence, one day dominating, the
market-state gates, the screen's implied growth, rejections."""

import math
from datetime import date, datetime
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.regime import Effect, Grade, Limit, Measure, Rule
from quant_hub.models.garch import (
    GARCH11Parameters,
    fit_garch11,
    next_variance,
    simulate_garch11,
)
from quant_hub.models.markov import GaussianHMM, hmm_filter, hmm_smoother, simulate_hmm
from quant_hub.models.regime import (
    DOMINANCE_NULL,
    DOMINANCE_SEED,
    Outcome,
    breach_rate,
    calibrate_dominance,
    calm_regime,
    check,
    dominance,
    dominance_null,
    dominance_threshold,
    implied_growth_check,
    persistence,
    run_length,
    volatility_forecast,
)
from quant_hub.models.regression import Z95, residual_correlation

PHI = NormalDist().cdf
U, S = 1.2, 0.05                       # an upper limit and an estimate's error


def upper_rule(k: float, grade=Grade.WARN) -> Rule:
    return Rule("4.beta", "Beta band", "beta", Limit(upper=U), grade, evidence=k)


def breached_share(rule: Rule, estimates: np.ndarray, error: float) -> float:
    outcomes = [check(rule, Measure.estimated(m, error)).outcome for m in estimates]
    return sum(o is Outcome.BREACH for o in outcomes) / len(outcomes)


def tolerance(p: float, n: int) -> float:
    return 4.0 * math.sqrt(p * (1.0 - p) / n)


# ---------------------------------------------------------------- breaches

@pytest.mark.parametrize("k", [0.0, 1.0, 1.645, 2.0])
def test_noise_breaches_one_minus_phi_k_of_reviews_on_the_boundary(k):
    n = 20_000
    estimates = np.random.default_rng(11).normal(U, S, n)
    expected = 1.0 - PHI(k)
    assert breach_rate(k) == pytest.approx(expected)
    assert abs(breached_share(upper_rule(k), estimates, S) - expected) < tolerance(expected, n)


def test_a_true_excess_is_caught_with_one_minus_phi_k_minus_shift():
    n, k, shift = 20_000, 1.645, 1.5
    estimates = np.random.default_rng(12).normal(U + shift * S, S, n)
    expected = 1.0 - PHI(k - shift)
    assert breach_rate(k, shift) == pytest.approx(expected)
    assert abs(breached_share(upper_rule(k), estimates, S) - expected) < tolerance(expected, n)


def test_past_the_limit_but_not_by_k_errors_is_near_and_ungraded():
    rule = upper_rule(1.645)
    near = check(rule, Measure.estimated(U + S, S))
    breach = check(rule, Measure.estimated(U + 2 * S, S))
    held = check(rule, Measure.estimated(U - S, S))
    assert (near.outcome, near.consequence) == (Outcome.NEAR, None)
    assert near.z == pytest.approx(1.0) and near.excess == pytest.approx(S)
    assert (breach.outcome, breach.consequence, breach.bound) == (Outcome.BREACH, Grade.WARN, U)
    assert breach.z == pytest.approx(2.0)
    assert (held.outcome, held.bound, held.excess) == (Outcome.PASS, None, None)


def test_bands_check_both_sides_and_exact_measures_breach_when_past_at_all():
    band = Rule("4.cash", "Cash range", "cash", Limit(0.10, 0.40), Grade.NOTE, evidence=2.0)
    assert check(band, Measure.exact(0.40)).outcome is Outcome.PASS        # on the limit holds
    low = check(band, Measure.exact(0.05))
    high = check(band, Measure.exact(0.41))
    assert (low.outcome, low.bound, low.excess, low.z) == (Outcome.BREACH, 0.10, pytest.approx(0.05), None)
    assert (high.outcome, high.bound) == (Outcome.BREACH, 0.40)
    assert check(band, Measure.estimated(0.41, 0.0)).outcome is Outcome.BREACH  # zero error is exact


def test_a_gate_breach_carries_its_effect_and_a_missing_measure_says_so():
    gate = Rule("7.calm", "Calm-regime probability", "calm", Limit(lower=0.6),
                Effect.BLOCK_ADDS)
    assert check(gate, Measure.exact(0.4)).consequence is Effect.BLOCK_ADDS
    missing = check(gate, None)
    assert (missing.outcome, missing.consequence, missing.reason) == (
        Outcome.MISSING, None, "not measured")


def test_the_fisher_scale_breach_agrees_with_w2s_interval():
    rng = np.random.default_rng(13)
    T = 200
    for rho in (0.15, 0.3, -0.3, 0.5):
        common = rng.standard_normal(T)
        a = common * math.sqrt(abs(rho)) + rng.standard_normal(T) * math.sqrt(1 - abs(rho))
        b = math.copysign(1.0, rho) * common * math.sqrt(abs(rho)) \
            + rng.standard_normal(T) * math.sqrt(1 - abs(rho))
        corr, lower, upper = residual_correlation(pd.DataFrame({"a": a, "b": b}))
        r = float(corr.loc["a", "b"])
        # |ρ|'s lower 95% bound from W2's interval, on either sign
        bound = float(lower.loc["a", "b"]) if r > 0 else -float(upper.loc["a", "b"])
        for limit in (0.05, 0.1, 0.2, 0.3, 0.4, 0.6):
            rule = Rule("6.rho", "Max correlation", "rho", Limit(upper=limit), Grade.FAIL,
                        evidence=Z95)
            result = check(rule, Measure.of_correlation(r, T))
            assert (result.outcome is Outcome.BREACH) == (bound > limit), (rho, limit)
            assert (result.outcome is Outcome.PASS) == (abs(r) <= limit)


def test_significance_isnt_size_at_500_days():
    rule = Rule("6.rho", "Max correlation", "rho", Limit(upper=0.0), Grade.FAIL, evidence=Z95)
    assert check(rule, Measure.of_correlation(0.09, 500)).outcome is Outcome.BREACH
    assert check(rule, Measure.of_correlation(0.085, 500)).outcome is Outcome.NEAR


# ------------------------------------------------------------- persistence

def test_run_lengths_reset_count_one_review_a_day_and_skip_gaps():
    d = [date(2026, 10, day) for day in range(1, 11)]
    assert run_length([]) == 0
    assert run_length([(d[0], True), (d[1], True), (d[2], False), (d[3], True), (d[4], True)]) == 2
    assert run_length([(d[0], True), (d[9], True)]) == 2                    # a gap neither breaks nor adds
    morning, evening = datetime(2026, 10, 2, 9), datetime(2026, 10, 2, 17)
    assert run_length([(d[0], True), (morning, True), (evening, False)]) == 0  # the day's last counts
    assert run_length([(datetime(2026, 10, 1, 9), True), (morning, False), (evening, True)]) == 2
    with pytest.raises(ValueError, match="time order"):
        run_length([(d[1], True), (d[0], True)])


def test_persistence_warns_until_n_then_fails():
    assert persistence(0, 5) is None
    assert persistence(1, 5) is Grade.WARN and persistence(4, 5) is Grade.WARN
    assert persistence(5, 5) is Grade.FAIL and persistence(9, 5) is Grade.FAIL
    with pytest.raises(ValueError):
        persistence(1, 0)
    with pytest.raises(ValueError):
        persistence(-1, 5)


# ------------------------------------------------------- one day dominating

def test_one_huge_day_gives_the_closed_form_share():
    T, sigma, x = 500, 0.03, 10.0
    r = np.where(np.arange(T) % 2 == 0, sigma, -sigma)
    r[123] = x
    d = dominance(r)
    assert d.share == pytest.approx(x**2 / (x**2 + (T - 1) * sigma**2), abs=1e-12)
    assert (d.day, d.value, d.observations) == (123, x, T)
    assert d.flagged and d.threshold == pytest.approx(0.875)


def test_the_share_is_scale_free_and_a_gaussian_series_is_not_flagged():
    r = np.random.default_rng(14).standard_normal(500) * 0.01
    assert dominance(r).share == pytest.approx(dominance(r * 37.0).share, rel=1e-12)
    assert not dominance(r).flagged


def test_the_null_exceeds_its_threshold_one_time_in_a_thousand():
    runs, T = 20_000, 500
    shares = dominance_null(T, runs, rng=99)                # a fresh seed, not the table's
    exceed = int((shares > dominance_threshold(T)).sum())
    # Expected 20; the band allows for the table's own sampling error.
    assert 5 <= exceed <= 40


def test_the_calibration_reproduces_its_first_row_exactly():
    assert DOMINANCE_SEED == 20261023
    assert calibrate_dominance(grid=(60,)) == (DOMINANCE_NULL[0],)


def test_the_threshold_interpolates_in_log_days_and_holds_past_the_grid():
    for t, q in DOMINANCE_NULL:
        assert dominance_threshold(t) == pytest.approx(q)
    between = dominance_threshold(354)                       # √(250·500): halfway in log T
    assert between == pytest.approx((0.907 + 0.875) / 2, abs=1e-3)
    assert dominance_threshold(10_000) == pytest.approx(DOMINANCE_NULL[-1][1])
    with pytest.raises(ValueError, match="calibrated from 60"):
        dominance_threshold(59)


def test_dominance_rejects_short_flat_or_bad_series():
    with pytest.raises(ValueError, match="needs 60"):
        dominance(np.ones(10))
    with pytest.raises(ValueError, match="no movement"):
        dominance(np.zeros(100))
    with pytest.raises(ValueError, match="finite"):
        dominance(np.r_[np.ones(99), np.nan])
    with pytest.raises(ValueError, match="one series"):
        dominance(np.ones((100, 2)))


# --------------------------------------------------------- market-state gates

TWO_REGIMES = GaussianHMM(np.array([0.0005, -0.001]), np.array([0.008, 0.025]),
                          np.array([[0.98, 0.02], [0.05, 0.95]]))


def test_the_calm_gate_reads_filtered_probabilities_never_smoothed():
    r = simulate_hmm(TWO_REGIMES, 600, seed=3).returns
    gate = calm_regime(r, 2)
    calm = int(np.argmin(gate.fit.model.sigmas))
    filtered = hmm_filter(gate.fit.model, r)
    smoothed = hmm_smoother(filtered).smoothed[:, calm]
    assert gate.history == pytest.approx(filtered.filtered[:, calm])
    assert gate.probability == gate.history[-1]
    assert gate.history[-1] == pytest.approx(smoothed[-1], abs=1e-12)    # equal at T ...
    assert np.max(np.abs(gate.history[:-1] - smoothed[:-1])) > 0.1       # ... not before


def test_the_calm_odds_are_the_filtered_row_times_p_to_the_h():
    r = simulate_hmm(TWO_REGIMES, 600, seed=4).returns
    gate = calm_regime(r, 2, horizons=(1, 5, 20))
    model = gate.fit.model
    calm = int(np.argmin(model.sigmas))
    alpha = hmm_filter(model, r).filtered[-1]
    for h in (1, 5, 20):
        expected = (alpha @ np.linalg.matrix_power(model.transition, h))[calm]
        assert gate.odds[h] == pytest.approx(expected, abs=1e-12)


def test_the_volatility_forecast_matches_a_monte_carlo_of_the_recursion():
    truth = GARCH11Parameters(mean=0.0003, omega=2e-6, alpha=0.08, beta=0.9)
    r = simulate_garch11(truth, 1_500, seed=5).returns
    horizon = 20
    fit = fit_garch11(r)
    p = fit.parameters
    h = np.full(40_000, next_variance(p, float(r[-1]), float(fit.conditional_variances[-1])))
    rng = np.random.default_rng(6)
    total = np.zeros_like(h)
    for _ in range(horizon):
        total += h
        shock = np.sqrt(h) * rng.standard_normal(len(h))
        h = p.omega + p.alpha * shock**2 + p.beta * h
    monte_carlo = math.sqrt(252 * float(np.mean(total / horizon)))
    assert volatility_forecast(r, horizon) == pytest.approx(monte_carlo, rel=0.01)


def test_the_gates_reject_bad_settings():
    r = simulate_hmm(TWO_REGIMES, 300, seed=7).returns
    with pytest.raises(ValueError, match="two regimes"):
        calm_regime(r, 1)
    with pytest.raises(ValueError, match="one day or more"):
        calm_regime(r, 2, horizons=(0,))
    with pytest.raises(ValueError, match="one day or more"):
        volatility_forecast(r, 0)


# ------------------------------------------------------------------ screens

IMPLIED = Rule("8.implied", "Implied growth vs history", "implied growth", Limit(upper=1.5),
               Grade.WARN)


def test_implied_growth_against_a_multiple_of_history():
    held = implied_growth_check(IMPLIED, [0.12], 0.10)
    breached = implied_growth_check(IMPLIED, [0.18], 0.10)
    assert held.outcome is Outcome.PASS
    assert (breached.outcome, breached.bound) == (Outcome.BREACH, pytest.approx(0.15))
    assert breached.excess == pytest.approx(0.03)


def test_several_roots_are_ambiguous_and_none_or_shrinking_history_is_missing():
    two = implied_growth_check(IMPLIED, [-0.149, 0.159], 0.10)
    assert two.outcome is Outcome.AMBIGUOUS and "2 growth rates" in two.reason
    assert "-14.9%" in two.reason and "+15.9%" in two.reason
    none = implied_growth_check(IMPLIED, [], 0.10)
    assert (none.outcome, none.reason) == (Outcome.MISSING, "no growth rate fits the price")
    shrinking = implied_growth_check(IMPLIED, [0.05], -0.02)
    assert shrinking.outcome is Outcome.MISSING and "isn't positive" in shrinking.reason
    with pytest.raises(ValueError, match="upper multiple"):
        implied_growth_check(Rule("8.x", "x", "x", Limit(lower=1.0), Grade.WARN), [0.1], 0.1)


# --------------------------------------------------------------- rejections

def test_the_records_reject_what_they_cant_mean():
    with pytest.raises(ValueError, match="needs a lower bound"):
        Limit()
    with pytest.raises(ValueError, match="above the upper"):
        Limit(2.0, 1.0)
    with pytest.raises(ValueError, match="finite"):
        Limit(upper=math.inf)
    with pytest.raises(ValueError, match="finite"):
        Measure(math.nan)
    with pytest.raises(ValueError, match="not negative"):
        Measure.estimated(1.0, -0.1)
    with pytest.raises(ValueError, match="more than three"):
        Measure.of_correlation(0.2, 3)
    with pytest.raises(ValueError, match="strictly between"):
        Measure.of_correlation(1.0, 100)
    with pytest.raises(ValueError, match="sample size"):
        Measure(0.2, 0.01, observations=100, correlation=True)
    with pytest.raises(ValueError, match="key and a measure"):
        Rule("", "x", "beta", Limit(upper=1.0), Grade.WARN)
    with pytest.raises(ValueError, match="evidence"):
        Rule("4.x", "x", "beta", Limit(upper=1.0), Grade.WARN, evidence=-1.0)
    with pytest.raises(ValueError, match="not negative"):
        breach_rate(-0.5)


def test_a_correlation_rule_is_an_upper_limit_on_its_size():
    rho = Measure.of_correlation(0.3, 100)
    with pytest.raises(ValueError, match="from above only"):
        check(Rule("6.rho", "x", "rho", Limit(lower=0.1), Grade.WARN), rho)
    with pytest.raises(ValueError, match=r"in \[0, 1\)"):
        check(Rule("6.rho", "x", "rho", Limit(upper=1.0), Grade.WARN), rho)
