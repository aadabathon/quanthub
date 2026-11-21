"""Tests for the historical return histogram's math (HRH H1, D84).

Numbered as the module docstring's section 11. Simulations are seeded; a
rate is checked within 3 binomial standard errors.
"""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.return_distribution import (
    AD_FLOOR_P,
    AD_NAME,
    BINS_MAX,
    BINS_MIN,
    JB_NAME,
    MIN_TEST_N,
    Frequency,
    ReturnKind,
    Window,
    _log_sf,
    ad_p_value,
    analyze,
    anderson_darling,
    fd_bin_count,
    jarque_bera,
    jarque_bera_from_moments,
    period_closes,
    period_returns,
    qq_points,
)

N = NormalDist()


def closes_from_simple(returns, start="2024-01-01"):
    """Closes whose simple returns are ``returns``, on business days."""
    prices = 100.0 * np.cumprod(np.concatenate([[1.0], 1.0 + np.asarray(returns, float)]))
    return pd.Series(prices, index=pd.bdate_range(start, periods=len(prices)))


def closes_from_log(returns, index):
    return pd.Series(100.0 * np.exp(np.concatenate([[0.0], np.cumsum(returns)])), index=index)


def explicit(prices, start="2024-01-01"):
    return pd.Series(np.asarray(prices, float), index=pd.bdate_range(start, periods=len(prices)))


def within_binomial(rate, p, reps, k=3.0):
    return abs(rate - p) <= k * math.sqrt(p * (1 - p) / reps)


# 1. Godel's panel, from its own inputs -------------------------------------

class TestGodelPanel:
    def test_jarque_bera(self):
        stat, p = jarque_bera_from_moments(251, 0.032, 1.065)
        assert stat == pytest.approx(11.90, abs=0.005)
        assert p == pytest.approx(0.0026, rel=0.01)

    def test_anderson_darling_p_values(self):
        assert ad_p_value(0.475)[0] == pytest.approx(0.240, abs=5e-4)
        assert ad_p_value(0.408)[0] == pytest.approx(0.347, abs=1e-3)

    def test_latest_move_quantiles_and_vol(self):
        # A sample with exactly Godel's mean and sigma, and its latest move.
        base = np.random.default_rng(5).standard_normal(251)
        sample = 0.000098 + 0.024309 * (base - base.mean()) / base.std(ddof=1)
        r = analyze(closes_from_simple(np.append(sample, -0.0536)), window=Window.ALL)
        assert r.n == 251
        assert r.mean == pytest.approx(0.000098, abs=1e-12)
        assert r.sd == pytest.approx(0.024309, rel=1e-10)
        assert r.latest.z == pytest.approx(-2.209, abs=1e-3)
        assert r.latest.down
        assert r.latest.normal_expected == pytest.approx(3.41, abs=0.01)
        normal = {q.level: q.normal for q in r.quantiles}
        assert normal[0.01] == pytest.approx(-0.05645, abs=5e-6)
        assert normal[0.05] == pytest.approx(-0.03989, abs=5e-6)
        assert normal[0.95] == pytest.approx(0.04008, abs=5e-6)
        assert normal[0.99] == pytest.approx(0.05665, abs=5e-6)
        assert r.annualized_vol == pytest.approx(0.3859, abs=5e-5)

    def test_five_day_annualization(self):
        assert 0.04931 * math.sqrt(252 / 5) == pytest.approx(0.3501, abs=5e-5)
        rng = np.random.default_rng(6)
        r = analyze(closes_from_simple(rng.normal(0, 0.01, 400)), horizon=5)
        assert r.annualized_vol == pytest.approx(r.sd * math.sqrt(252 / 5), rel=1e-12)


# 2. Anderson–Darling by hand ------------------------------------------------

def test_anderson_darling_by_hand():
    x = np.array([-2.0, -1.0, 0.0, 1.0, 3.0])
    a_star, p, bound = anderson_darling(x)
    z = sorted((v - x.mean()) / x.std(ddof=1) for v in x)
    n = len(z)
    a2 = -n - sum((2 * i - 1) * (math.log(N.cdf(z[i - 1])) + math.log(1 - N.cdf(z[n - i])))
                  for i in range(1, n + 1)) / n
    assert a2 == pytest.approx(0.16611, abs=1e-5)
    assert a_star == pytest.approx(a2 * (1 + 0.75 / n + 2.25 / n**2), rel=1e-12)
    assert a_star == pytest.approx(0.20597, abs=1e-5)
    assert p == pytest.approx(0.8708, abs=1e-4)
    assert not bound


# 3. Calibration ------------------------------------------------------------

@pytest.mark.parametrize("n", [8, 51, 251])
def test_anderson_darling_holds_its_level(n):
    rng = np.random.default_rng(100 + n)
    reps = 2000
    rejections = sum(anderson_darling(rng.standard_normal(n))[1] < 0.05 for _ in range(reps))
    assert within_binomial(rejections / reps, 0.05, reps)


def test_horizon_five_tests_the_non_overlapping_sample():
    rng = np.random.default_rng(55)
    index = pd.bdate_range("2025-01-01", "2026-11-10")
    reps = 600
    tested = overlapping = 0
    for _ in range(reps):
        r = analyze(closes_from_log(rng.normal(0, 0.01, len(index) - 1), index),
                    horizon=5, kind=ReturnKind.LOG)
        tested += r.verdict.rejects
        overlapping += anderson_darling(r.returns.to_numpy())[1] < 0.05
    assert within_binomial(tested / reps, 0.05, reps)
    # Why: the same test on the overlapping returns rejects far too often.
    assert overlapping / reps > 0.15


# 4. Power ------------------------------------------------------------------

def test_fat_tails_are_rejected():
    rng = np.random.default_rng(3)
    reps = 500
    rejections = sum(anderson_darling(rng.standard_t(3, 251))[1] < 0.05 for _ in range(reps))
    assert rejections / reps >= 0.95


# 5. The Q-Q band -------------------------------------------------------------

def test_qq_band_covers_95_percent_pointwise():
    rng = np.random.default_rng(8)
    n, reps = 251, 1000
    inside = np.zeros(n)
    for _ in range(reps):
        qq = qq_points(rng.standard_normal(n))
        inside += (qq.observed >= qq.lower) & (qq.observed <= qq.upper)
    assert 0.94 <= float(np.mean(inside / reps)) <= 0.965


def test_qq_points_are_sorted_and_centred():
    qq = qq_points(np.array([3.0, -1.0, 0.5, 2.0, -2.0, 0.0]))
    assert np.all(np.diff(qq.observed) >= 0)
    assert np.all(qq.lower <= qq.theoretical) and np.all(qq.theoretical <= qq.upper)
    assert qq.n == 6


# 6. The floor and erfc -------------------------------------------------------

def test_floor_is_a_bound():
    assert ad_p_value(10.0) == (AD_FLOOR_P, True)
    assert ad_p_value(500.0) == (AD_FLOOR_P, True)
    p, bound = ad_p_value(9.999)
    assert not bound and p == pytest.approx(AD_FLOOR_P, rel=0.01)
    assert AD_FLOOR_P == pytest.approx(3.765e-24, rel=1e-3)


def test_crash_day_gives_a_finite_statistic():
    x = np.random.default_rng(9).standard_normal(250)
    x = np.append(x, -40 * x.std())       # z of that day is far below -8.26
    a_star, p, bound = anderson_darling(x)
    assert math.isfinite(a_star) and a_star > 10
    assert bound and p == AD_FLOOR_P


def test_log_tail_switches_to_mills_smoothly():
    below = _log_sf(29.9999)
    above = _log_sf(30.0001)
    slope = -30.0 - 1.0 / 30.0           # d/dz of the leading terms at 30
    assert above - below == pytest.approx(slope * 0.0002, abs=1e-6)


# 7. Sample sizes and alignment ---------------------------------------------

@pytest.mark.parametrize("k", [1, 5, 21])
def test_test_sample_is_non_overlapping_and_aligned(k):
    rng = np.random.default_rng(k)
    closes = closes_from_simple(rng.normal(0, 0.01, 600))
    r = analyze(closes, horizon=k)
    positions = closes.index.get_indexer(r.test_returns.index)
    assert np.all(np.diff(positions) == k)
    assert r.test_returns.index[-1] == r.latest.start
    assert closes.index.get_loc(r.latest.end) - positions[-1] == k
    assert r.test_n in (r.n // k, r.n // k + 1)
    if k == 1:
        assert r.test_returns.equals(r.returns)


# 8. Resampling ---------------------------------------------------------------

class TestResampling:
    def holiday_closes(self, end):
        index = pd.bdate_range("2026-06-22", end).drop(pd.Timestamp("2026-07-03"))
        return pd.Series(np.arange(1.0, len(index) + 1.0), index=index)

    def test_weekly_uses_each_weeks_last_trading_day(self):
        closes = self.holiday_closes("2026-07-15")
        weekly, partial = period_closes(closes, Frequency.WEEK)
        assert list(weekly.index) == [pd.Timestamp(d) for d in
                                      ("2026-06-26", "2026-07-02", "2026-07-10", "2026-07-15")]
        assert weekly.loc["2026-07-02"] == closes.loc["2026-07-02"]
        assert partial

    def test_week_ending_friday_is_complete(self):
        assert not period_closes(self.holiday_closes("2026-07-10"), "week")[1]

    def test_monthly_and_partial_months(self):
        october = pd.bdate_range("2026-08-03", "2026-10-30")
        closes = pd.Series(np.linspace(1, 2, len(october)), index=october)
        monthly, partial = period_closes(closes, Frequency.MONTH)
        assert list(monthly.index) == [pd.Timestamp(d) for d in
                                       ("2026-08-31", "2026-09-30", "2026-10-30")]
        assert not partial                  # 31 October 2026 is a Saturday
        assert period_closes(closes.iloc[:-1], "month")[1]

    def test_days_are_never_partial(self):
        closes = self.holiday_closes("2026-07-15")
        daily, partial = period_closes(closes, "day")
        assert daily.equals(closes) and not partial

    def test_period_returns(self):
        closes = explicit([100.0, 110.0, 121.0, 99.0])
        assert period_returns(closes, 1).to_numpy() == pytest.approx([0.1, 0.1, 99 / 121 - 1])
        assert period_returns(closes, 2, "log").to_numpy() == pytest.approx(
            [math.log(1.21), math.log(0.9)])


# 9. Windows --------------------------------------------------------------------

class TestWindows:
    closes = closes_from_simple(np.random.default_rng(12).normal(0, 0.01, 1000),
                                start="2023-01-02")

    def test_one_year(self):
        r = analyze(self.closes, window="1Y")
        boundary = r.latest.end - pd.DateOffset(years=1)
        assert r.start >= boundary
        before = self.closes.index[self.closes.index.get_loc(r.start) - 1]
        assert before < boundary
        assert r.end < r.latest.end
        assert r.latest.end not in r.returns.index

    def test_ytd(self):
        r = analyze(self.closes, window=Window.YTD)
        assert r.start == pd.Timestamp(r.latest.end.year, 1, 1) or (
            r.start.year == r.latest.end.year and r.start.dayofyear <= 4)
        assert r.latest.end not in r.returns.index

    def test_all_and_longer_than_the_data(self):
        all_ = analyze(self.closes, window=Window.ALL)
        assert all_.n == len(self.closes) - 2
        assert analyze(self.closes, window="5Y").n == all_.n

    def test_six_months_and_two_years(self):
        six = analyze(self.closes, window="6M")
        two = analyze(self.closes, window="2Y")
        assert six.start >= six.latest.end - pd.DateOffset(months=6)
        assert six.n < analyze(self.closes, window="1Y").n < two.n


# 10. Ranks --------------------------------------------------------------------

class TestRanks:
    # Sample returns: 0.01, -1/101, 0.02, -2/102, 0.02, -2/102, -0.01, 1/99,
    # 0.03, -3/103. Equal price pairs make equal floats, so ties are exact.
    prices = [100, 101, 100, 102, 100, 102, 100, 99, 100, 103, 100]

    def analyze_with(self, latest_price):
        return analyze(explicit(self.prices + [latest_price]), window=Window.ALL)

    def test_up_side_counts_ties(self):
        r = self.analyze_with(102)
        assert not r.latest.down
        assert r.latest.count == 3 and r.latest.rank == pytest.approx(0.3)
        assert r.latest.normal_expected == pytest.approx(10 * (1 - N.cdf(r.latest.z)), rel=1e-9)

    def test_down_side_counts_ties(self):
        r = self.analyze_with(99)
        assert r.latest.down and r.latest.count == 4
        assert r.latest.normal_expected == pytest.approx(10 * N.cdf(r.latest.z), rel=1e-9)

    def test_beyond_every_return(self):
        r = self.analyze_with(95)
        assert r.latest.down and r.latest.count == 0 and r.latest.rank == 0.0


# 11. Bins -----------------------------------------------------------------------

class TestBins:
    @pytest.mark.parametrize("n", [51, 251, 1314])
    def test_matches_numpys_fd(self, n):
        x = np.random.default_rng(n).standard_t(3, n)
        numpy_count = len(np.histogram_bin_edges(x, bins="fd")) - 1
        assert fd_bin_count(x) == min(BINS_MAX, max(BINS_MIN, numpy_count))

    def test_clamps(self):
        assert fd_bin_count(np.random.default_rng(1).uniform(size=12)) == BINS_MIN
        assert fd_bin_count(np.random.default_rng(1).standard_cauchy(5000)) == BINS_MAX
        assert fd_bin_count(np.array([0.0, 0.0, 0.0, 0.0, 1.0])) == BINS_MIN

    def test_counts_and_expected_counts(self):
        r = analyze(closes_from_simple(np.random.default_rng(4).normal(0, 0.01, 400)))
        assert int(r.bin_counts.sum()) == r.n
        assert len(r.bin_edges) == len(r.bin_counts) + 1
        low = (r.bin_edges[0] - r.mean) / r.sd
        high = (r.bin_edges[-1] - r.mean) / r.sd
        assert r.bin_expected.sum() == pytest.approx(r.n * (N.cdf(high) - N.cdf(low)), rel=1e-9)

    def test_fixed_count(self):
        r = analyze(closes_from_simple(np.random.default_rng(4).normal(0, 0.01, 400)), bins=30)
        assert len(r.bin_counts) == 30


# 12. Split-sized gaps -----------------------------------------------------------

class TestSplitGaps:
    closes = closes_from_simple(np.random.default_rng(2).normal(0, 0.01, 30))
    gap = closes.index[10]

    def test_spanning_returns_are_named(self):
        r = analyze(self.closes, window=Window.ALL, gap_days=[self.gap.to_pydatetime()])
        assert r.gap_returns == (self.gap,)
        assert self.gap in r.returns.index and not r.gaps_excluded

    def test_longer_horizons_span_more(self):
        r = analyze(self.closes, window=Window.ALL, horizon=3, gap_days=[self.gap])
        assert r.gap_returns == tuple(self.closes.index[10:13])

    def test_exclusion_drops_and_still_names(self):
        kept = analyze(self.closes, window=Window.ALL, horizon=3, gap_days=[self.gap])
        dropped = analyze(self.closes, window=Window.ALL, horizon=3, gap_days=[self.gap],
                          exclude_gaps=True)
        assert dropped.gap_returns == kept.gap_returns and dropped.gaps_excluded
        assert dropped.n == kept.n - 3
        assert not set(kept.gap_returns) & set(dropped.returns.index)
        assert not set(kept.gap_returns) & set(dropped.test_returns.index)

    def test_latest_move_spanning_a_gap(self):
        r = analyze(self.closes, window=Window.ALL, gap_days=[self.closes.index[-1]])
        assert r.latest.spans_gap and r.gap_returns == ()


# 13. Validation and the short test sample -------------------------------------

class TestValidation:
    good = closes_from_simple(np.random.default_rng(0).normal(0, 0.01, 60))

    @pytest.mark.parametrize("bad", [
        [1.0, 2.0, 3.0],
        pd.Series([1.0, 2.0, 3.0]),
        pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-03", "2024-01-02",
                                                         "2024-01-04"])),
        pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-02", "2024-01-02",
                                                         "2024-01-04"])),
        explicit([1.0, 0.0, 2.0]),
        explicit([1.0, float("nan"), 2.0]),
    ])
    def test_bad_closes(self, bad):
        with pytest.raises(ValueError):
            analyze(bad)

    @pytest.mark.parametrize("kwargs", [
        {"horizon": 0}, {"horizon": 1.5}, {"horizon": True}, {"horizon": 60},
        {"alpha": 0.0}, {"alpha": 0.5}, {"alpha": True},
        {"bins": 0}, {"bins": 1001}, {"bins": 2.5}, {"bins": True},
        {"frequency": "hour"}, {"kind": "pct"}, {"window": "3Y"},
    ])
    def test_bad_settings(self, kwargs):
        with pytest.raises(ValueError):
            analyze(self.good, **kwargs)

    def test_too_few_returns_and_equal_returns(self):
        with pytest.raises(ValueError):
            analyze(explicit([100.0, 101.0, 102.0]), window=Window.ALL)
        with pytest.raises(ValueError):
            analyze(explicit([100.0] * 20), window=Window.ALL)

    def test_sample_helpers_validate(self):
        for f in (anderson_darling, jarque_bera, qq_points):
            with pytest.raises(ValueError):
                f([1.0])
            with pytest.raises(ValueError):
                f([2.0, 2.0, 2.0])
        with pytest.raises(ValueError):
            ad_p_value(-0.1)

    def test_short_test_sample_is_a_result_not_an_error(self):
        seven_months = closes_from_simple(np.random.default_rng(0).normal(0, 0.01, 150))
        r = analyze(seven_months, frequency="month", window="All")
        assert 2 <= r.n < MIN_TEST_N
        assert r.tests == () and r.verdict is None
        assert f"at least {MIN_TEST_N}" in r.tests_skipped

    def test_tests_run_and_are_named(self):
        r = analyze(self.good, window=Window.ALL)
        assert [t.name for t in r.tests] == [AD_NAME, JB_NAME]
        assert r.verdict.name == AD_NAME and r.tests_skipped == ""
