"""R1 performance definitions and statistical calibration on known nulls."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.backtest_stats import (
    deflated_sharpe, performance_summary, rolling_sharpe, sharpe_estimate,
)


def series(values):
    return pd.Series(values, index=pd.bdate_range("2020-01-02", periods=len(values)))


def test_rolling_sharpe_uses_only_the_complete_past_window_and_cash_rate():
    returns = series([.01, .02, 0, .01, .01, .01])
    actual = rolling_sharpe(returns, 3, cash_rate=1.001**4 - 1, periods_per_year=4)
    assert actual.iloc[:2].isna().all()
    assert actual.iloc[2] == pytest.approx(1.8)
    assert actual.iloc[3] == pytest.approx(1.8)
    assert np.isnan(actual.iloc[-1])
    changed = returns.copy()
    changed.iloc[4:] = [-.1, .2]
    pd.testing.assert_series_equal(actual.iloc[:4], rolling_sharpe(changed, 3, cash_rate=1.001**4 - 1, periods_per_year=4).iloc[:4])
    with pytest.raises(ValueError, match="window"):
        rolling_sharpe(returns, 1)


def test_hand_computed_wealth_drawdown_sortino_and_turnover():
    returns = series([-0.1, 0.2, -0.05, 0.1])
    summary = performance_summary(returns, periods_per_year=4,
                                  turnover=series([1, 0.2, 0.1, 0.3]))
    terminal = 0.9 * 1.2 * 0.95 * 1.1
    assert summary.total_return == pytest.approx(terminal - 1)
    assert summary.cagr == pytest.approx(terminal - 1)
    assert summary.max_drawdown == pytest.approx(-0.1)    # includes the starting capital
    assert summary.volatility == pytest.approx(np.std(returns, ddof=1) * 2)
    # The lower partial moment divides by ALL four days, not the two losers.
    assert summary.sortino == pytest.approx(2 * returns.mean() / math.sqrt((0.1**2 + 0.05**2) / 4))
    assert summary.turnover_total == pytest.approx(1.6)
    assert summary.turnover_annual == pytest.approx(1.6)


def test_sharpe_error_uses_plain_skewness_and_non_excess_kurtosis():
    returns = series([-0.03, -0.01, 0.0, 0.01, 0.015, 0.02, 0.04])
    estimate = sharpe_estimate(returns, periods_per_year=1)
    x = returns.to_numpy()
    s = x.mean() / x.std(ddof=1)
    z = (x - x.mean()) / x.std(ddof=0)
    skew, kurt = np.mean(z**3), np.mean(z**4)
    expected = math.sqrt((1 - skew * s + (kurt - 1) * s**2 / 4) / (len(x) - 1))
    assert estimate.sharpe == pytest.approx(s)
    assert estimate.skewness == pytest.approx(skew)
    assert estimate.kurtosis == pytest.approx(kurt)
    assert estimate.standard_error_iid == pytest.approx(expected)
    assert estimate.probability_above(s) == 0.5


def test_cash_rate_is_an_effective_rate_and_cagr_counts_return_periods():
    cash = math.expm1(math.log1p(0.05) / 252)
    summary = performance_summary(series([cash] * 252), cash_rate=0.05)
    assert summary.cagr == pytest.approx(0.05, rel=1e-12)
    assert math.isnan(summary.sharpe.sharpe)
    assert math.isnan(summary.sortino)
    assert summary.turnover_total is None


@pytest.mark.parametrize("constant", [0, 0.01, -0.02])
def test_constant_returns_have_no_defined_sharpe(constant):
    estimate = sharpe_estimate(series([constant] * 17))
    assert math.isnan(estimate.sharpe)
    assert math.isnan(estimate.standard_error_iid)
    assert math.isnan(estimate.probability_above())


def test_short_samples_report_undefined_estimation_error():
    one = performance_summary(series([0.01]))
    assert math.isnan(one.volatility)
    assert math.isnan(one.sharpe.sharpe)
    two = sharpe_estimate(series([-0.01, 0.02]))
    assert math.isfinite(two.sharpe)
    assert math.isnan(two.standard_error_iid)
    assert math.isnan(two.probability_above())


@pytest.mark.parametrize("true_daily_sharpe", [0.0, 0.12])
def test_iid_sharpe_standard_error_and_interval_coverage(true_daily_sharpe):
    # Independent Gaussian samples: compare empirical sampling variability
    # with the reported delta-method SE, and check nominal 95% coverage.
    rng = np.random.default_rng(72)
    n, repeats = 504, 1200
    estimates, errors = [], []
    for x in rng.normal(true_daily_sharpe * 0.01, 0.01, (repeats, n)):
        estimate = sharpe_estimate(pd.Series(x), periods_per_year=1)
        estimates.append(estimate.sharpe)
        errors.append(estimate.standard_error_iid)
    estimates, errors = np.asarray(estimates), np.asarray(errors)
    assert estimates.std(ddof=1) == pytest.approx(errors.mean(), rel=0.08)
    assert 0.93 < np.mean(np.abs(estimates - true_daily_sharpe) < 1.96 * errors) < 0.97
    gaussian_se = math.sqrt((1 + true_daily_sharpe**2 / 2) / (n - 1))
    assert errors.mean() == pytest.approx(gaussian_se, rel=0.03)


def test_deflation_threshold_matches_simulated_normal_maxima():
    # Fix sample sd(trial Sharpes)=1, so the threshold is the expected max
    # of N standard normals. The paper's extreme-value approximation has
    # a small finite-N error, independent of Monte Carlo standard error.
    n = 100
    trial_sharpes = np.linspace(-1, 1, n)
    trial_sharpes /= trial_sharpes.std(ddof=1)
    result = deflated_sharpe(series([-0.01, 0, 0.01]), trial_sharpes, n,
                             periods_per_year=1)
    maximum = np.random.default_rng(8).normal(size=(40000, n)).max(axis=1)
    assert abs(result.benchmark_sharpe - maximum.mean()) < 0.05


def test_one_effective_trial_is_ordinary_psr_and_annualization_cancels():
    returns = series(np.random.default_rng(9).normal(0.0005, 0.01, 504))
    estimate = sharpe_estimate(returns)
    one = deflated_sharpe(returns, [estimate.sharpe], 1)
    assert one.benchmark_sharpe == 0
    assert one.probability == estimate.probability_above()
    daily_trials = np.linspace(-0.1, 0.2, 40)
    daily = deflated_sharpe(returns, daily_trials, 25, periods_per_year=1)
    annual = deflated_sharpe(returns, daily_trials * math.sqrt(252), 25)
    assert annual.probability == pytest.approx(daily.probability, abs=1e-14)
    assert annual.benchmark_sharpe == pytest.approx(daily.benchmark_sharpe * math.sqrt(252))


def test_more_independent_trials_raise_the_threshold_and_lower_dsr():
    returns = series(np.random.default_rng(10).normal(0.0005, 0.01, 1000))
    trial_sharpes = np.linspace(-1, 2, 100)
    one = deflated_sharpe(returns, trial_sharpes, 1)
    ten = deflated_sharpe(returns, trial_sharpes, 10)
    hundred = deflated_sharpe(returns, trial_sharpes, 100)
    assert one.benchmark_sharpe < ten.benchmark_sharpe < hundred.benchmark_sharpe
    assert one.probability > ten.probability > hundred.probability


def test_best_of_many_null_trials_is_deflated_but_a_planted_edge_survives():
    rng = np.random.default_rng(14)
    ordinary, corrected = [], []
    n, trials = 504, 40
    for _ in range(180):
        returns = rng.normal(0, 0.01, (n, trials))
        sharpes = returns.mean(axis=0) / returns.std(axis=0, ddof=1) * math.sqrt(252)
        best = int(sharpes.argmax())
        result = deflated_sharpe(pd.Series(returns[:, best]), sharpes, trials)
        ordinary.append(result.estimate.probability_above())
        corrected.append(result.probability)
    assert np.mean(np.array(ordinary) > 0.95) > 0.65
    assert np.mean(np.array(corrected) > 0.95) < 0.15
    edge = pd.Series(rng.normal(0.0025, 0.01, n))
    selected = sharpe_estimate(edge)
    dsr = deflated_sharpe(edge, np.r_[rng.normal(0, 0.7, trials - 1), selected.sharpe], trials)
    assert dsr.probability > 0.99


@pytest.mark.parametrize("values", [[], [0, np.nan], [0, np.inf], [0, -1], [0, -1.1]])
def test_invalid_realized_returns_are_rejected(values):
    with pytest.raises(ValueError):
        performance_summary(series(values))


@pytest.mark.parametrize("kwargs", [{"cash_rate": -1}, {"cash_rate": np.inf},
                                     {"periods_per_year": 0}, {"periods_per_year": True}])
def test_invalid_reporting_conventions_are_rejected(kwargs):
    with pytest.raises(ValueError):
        sharpe_estimate(series([-0.01, 0.02, 0.01]), **kwargs)


@pytest.mark.parametrize("trial_sharpes,count", [([], 1), ([0, np.nan], 2), ([1, 2], 0),
    ([1, 2], 3), ([1, 2], 1.5), ([1, 2], True), ([1, 1], 2), ([[1, 2]], 1)])
def test_invalid_trial_histories_are_rejected(trial_sharpes, count):
    with pytest.raises(ValueError):
        deflated_sharpe(series([-0.01, 0, 0.01]), trial_sharpes, count)


def test_missing_or_negative_turnover_and_duplicate_dates_are_rejected():
    r = series([-0.01, 0.01, 0.02])
    for bad in (series([1]), series([1, -0.1, 0]), series([1, 0, np.nan])):
        with pytest.raises(ValueError, match="turnover"):
            performance_summary(r, turnover=bad)
    with pytest.raises(ValueError, match="chronological"):
        sharpe_estimate(r.set_axis([r.index[0]] * len(r)))
    with pytest.raises(ValueError, match="benchmark"):
        sharpe_estimate(r).probability_above(float("inf"))
