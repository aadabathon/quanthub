"""The variance risk premium (VARIANCE I2): the module docstring's
validation plan, on worlds with known answers."""

import math
from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.trading_calendar import trading_days
from quant_hub.models.garch import GARCH11Parameters, average_variance_forecast
from quant_hub.models.variance_premium import (
    MIN_PERCENTILE_HISTORY,
    ImpliedPoint,
    MissingBar,
    Premium,
    against_garch,
    closes_by_day,
    ex_post_series,
    garch_expected_total,
    independent_windows,
    overlap,
    percentile,
    previous_trading_day,
    realized_total,
    trailing_series,
    window_after,
    window_before,
)

DAYS = trading_days(date(2023, 1, 3), date(2025, 12, 31))
DAILY_SD = 0.16 / math.sqrt(252)
TENOR = 30.0


def gbm_closes(seed: int, days=DAYS) -> pd.Series:
    """A GBM path on the NYSE's trading days: daily log returns N(-σ²/2, σ²)."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(-DAILY_SD**2 / 2, DAILY_SD, len(days))
    return pd.Series(100 * np.exp(np.cumsum(returns)), index=pd.DatetimeIndex(days))


def priced_at(scale: float, snapshot_days) -> list[ImpliedPoint]:
    """Snapshots at 10:00 whose 30-day variance is priced at ``scale``·σ over
    exactly the window's trading days: the paths' own variance at scale 1."""
    return [ImpliedPoint(datetime.combine(d, time(10)), TENOR,
                         (scale * DAILY_SD) ** 2 * len(window_after(d, TENOR)))
            for d in snapshot_days]


SNAPSHOTS = [d for d in DAYS if date(2023, 2, 1) <= d <= date(2025, 11, 14)]


# ---------------------------------------------------------------- GBM world

def test_a_chain_priced_at_the_paths_own_variance_earns_no_premium():
    series = ex_post_series(priced_at(1.0, SNAPSHOTS), gbm_closes(11))
    assert series.pending == 0 and not series.excluded
    mean = series.summary()
    assert mean.windows == len(SNAPSHOTS)
    assert 30 <= mean.independent <= 40                      # about a month each
    assert abs(mean.mean) < 3 * mean.error


def test_a_planted_premium_is_recovered():
    series = ex_post_series(priced_at(1.15, SNAPSHOTS), gbm_closes(12))
    mean = series.summary()
    planted = np.mean([(1.15**2 - 1) * DAILY_SD**2 * len(p.window) / p.premium.years
                       for p in series.points])                # what was priced in, per window
    assert abs(mean.mean - planted) < 3 * mean.error
    assert mean.mean > 5 * mean.error                          # clearly there
    assert mean.vol_points == pytest.approx(0.15 * 0.16, abs=0.01)   # about 2.4 vol points


def test_the_error_covers_about_as_the_docstring_says():
    one_year = [d for d in DAYS if date(2024, 1, 2) <= d <= date(2024, 12, 31)]
    covered = []
    for seed in range(120):
        mean = ex_post_series(priced_at(1.0, one_year), gbm_closes(seed)).summary()
        covered.append(abs(mean.mean) < 1.96 * mean.error)
    assert 0.75 <= np.mean(covered) <= 0.95                 # 85% simulated at one year


# ----------------------------------------------------------- no look-ahead

def test_a_value_appears_only_once_its_window_has_closed():
    closes = gbm_closes(3)
    cut = date(2024, 6, 28)
    early = ex_post_series(priced_at(1.0, SNAPSHOTS), closes[closes.index.date <= cut])
    later = ex_post_series(priced_at(1.0, SNAPSHOTS), closes)
    assert early.pending > 0 and len(later.points) > len(early.points)
    assert all(p.window[-1] <= cut for p in early.points)
    known = {p.as_of: p.premium.variance for p in later.points}
    for p in early.points:                                  # appending bars changes nothing
        assert known[p.as_of] == p.premium.variance
    newest_known = max(p.as_of for p in early.points).date()
    assert window_after(newest_known, TENOR)[-1] <= cut < window_after(
        newest_known + timedelta(days=7), TENOR)[-1]


def test_the_trailing_premium_is_known_at_the_snapshot():
    closes = gbm_closes(4)
    snapshot = SNAPSHOTS[200]
    before = closes[closes.index.date < snapshot]
    point = priced_at(1.0, [snapshot])
    assert (trailing_series(point, before).points[0].premium
            == trailing_series(point, closes).points[0].premium)


def test_early_snapshots_without_trailing_bars_are_named():
    series = trailing_series(priced_at(1.0, [date(2023, 1, 10), date(2023, 3, 1)]),
                             gbm_closes(5))
    assert [p.as_of.date() for p in series.points] == [date(2023, 3, 1)]
    assert series.excluded[0].reason.startswith("no bar on 2022-")


# ------------------------------------------------------------ trading days

def test_windows_count_nyse_trading_days():
    thanksgiving = window_after(date(2025, 11, 26), TENOR)
    assert date(2025, 11, 27) not in thanksgiving and date(2025, 12, 25) not in thanksgiving
    assert (thanksgiving[0], thanksgiving[-1], len(thanksgiving)) == (
        date(2025, 11, 28), date(2025, 12, 26), 20)
    saturday = window_after(date(2025, 11, 1), TENOR)
    assert saturday[0] == date(2025, 11, 3)                 # Monday
    trailing = window_before(date(2025, 12, 1), TENOR)
    assert (trailing[0], trailing[-1], len(trailing)) == (date(2025, 10, 30),
                                                          date(2025, 11, 28), 21)
    assert previous_trading_day(date(2025, 12, 1)) == date(2025, 11, 28)


def test_realized_variance_is_the_sum_of_squared_trading_day_returns():
    days = [date(2025, 11, 24), date(2025, 11, 25), date(2025, 11, 26), date(2025, 11, 28)]
    closes = {date(2025, 11, 21): 100.0, date(2025, 11, 24): 101.0, date(2025, 11, 25): 99.0,
              date(2025, 11, 26): 99.5, date(2025, 11, 27): 500.0,   # a stray holiday bar
              date(2025, 11, 28): 100.5}
    expected = sum(math.log(b / a) ** 2 for a, b in ((100, 101), (101, 99), (99, 99.5),
                                                     (99.5, 100.5)))
    assert realized_total(closes, days) == pytest.approx(expected, rel=1e-14)
    with pytest.raises(MissingBar, match="2025-11-21"):
        realized_total({k: v for k, v in closes.items() if k != date(2025, 11, 21)}, days)


PARAMS = GARCH11Parameters(mean=0.0, omega=2e-6, alpha=0.08, beta=0.90)


def test_the_garch_sum_matches_its_closed_form():
    h = 3e-4
    drifting = GARCH11Parameters(mean=4e-4, omega=2e-6, alpha=0.08, beta=0.90)
    for n in (1, 5, 21):
        assert garch_expected_total(PARAMS, h, n) == pytest.approx(
            n * float(average_variance_forecast(PARAMS, h, np.array([n]))[0]), rel=1e-12)
        for skip in (1, 3):
            p, long_run = PARAMS.persistence, PARAMS.unconditional_variance
            explicit = sum(long_run + p ** (j - 1) * (h - long_run)
                           for j in range(skip + 1, skip + n + 1))
            assert garch_expected_total(PARAMS, h, n, skip) == pytest.approx(explicit, rel=1e-12)
            assert garch_expected_total(drifting, h, n, skip) == pytest.approx(
                explicit + n * 4e-4**2, rel=1e-12)            # RV is zero-mean: μ² counts


def test_against_garch_skips_the_days_before_the_window():
    point = ImpliedPoint(datetime(2025, 12, 1, 10), TENOR, 0.0025)
    n = len(window_after(date(2025, 12, 1), TENOR))
    friday = against_garch(point, PARAMS, 3e-4, last_bar=date(2025, 11, 28))
    monday = against_garch(point, PARAMS, 3e-4, last_bar=date(2025, 12, 1))
    assert friday.against == pytest.approx(garch_expected_total(PARAMS, 3e-4, n, skip=1))
    assert monday.against == pytest.approx(garch_expected_total(PARAMS, 3e-4, n, skip=0))
    assert friday.years == monday.years == TENOR / 365
    with pytest.raises(ValueError, match="look ahead"):
        against_garch(point, PARAMS, 3e-4, last_bar=date(2025, 12, 2))


# --------------------------------------------------------------------- D57

def test_a_window_over_a_split_sized_gap_is_left_out_and_named():
    gap = date(2024, 3, 13)
    series = ex_post_series(priced_at(1.0, SNAPSHOTS), gbm_closes(6), gap_days={gap})
    touched = [d for d in SNAPSHOTS if gap in window_after(d, TENOR)]
    assert len(series.excluded) == len(touched) > 15
    assert all("2024-03-13" in e.reason and "D57" in e.reason for e in series.excluded)
    assert len(series.points) == len(SNAPSHOTS) - len(touched)


def test_a_window_missing_a_bar_is_left_out_and_named():
    closes = gbm_closes(7)
    hole = pd.Timestamp(2024, 5, 15)
    series = ex_post_series(priced_at(1.0, SNAPSHOTS), closes.drop(hole))
    assert series.excluded and all(e.reason == "no bar on 2024-05-15" for e in series.excluded)


# ------------------------------------------------------- the small pieces

def test_overlap_and_independent_windows():
    windows = [window_after(d, TENOR) for d in SNAPSHOTS[:252]]
    assert 19 <= overlap(windows) <= 22
    assert 11 <= independent_windows(windows) <= 13
    monthly = [window_after(d, TENOR) for d in (date(2024, 1, 2), date(2024, 2, 5),
                                                 date(2024, 3, 6))]
    assert overlap(monthly) == 0 and independent_windows(monthly) == 3


def test_a_mean_needs_two_independent_windows():
    series = ex_post_series(priced_at(1.0, SNAPSHOTS[:10]), gbm_closes(8))
    assert len(series.points) == 10 and series.summary() is None


def test_the_premium_reads_in_variance_and_vol_points():
    premium = Premium(implied=0.04 * 0.25, against=0.0225 * 0.25, years=0.25)
    assert premium.variance == pytest.approx(0.0175)
    assert premium.implied_vol == pytest.approx(0.20) and premium.against_vol == pytest.approx(0.15)
    assert premium.vol_points == pytest.approx(0.05)


def test_percentile():
    history = list(range(MIN_PERCENTILE_HISTORY))
    assert percentile(5, history) == pytest.approx(5.5 / 20)
    assert percentile(-1, history) == 0.0 and percentile(99, history) == 1.0
    assert percentile(5, history[:-1]) is None


def test_inputs_are_validated():
    with pytest.raises(ValueError, match="calendar day"):
        window_after(date(2025, 1, 2), 0)
    with pytest.raises(ValueError, match="positive"):
        ImpliedPoint(datetime(2025, 1, 2), TENOR, 0.0)
    with pytest.raises(ValueError, match="n >= 1"):
        garch_expected_total(PARAMS, 3e-4, 0)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        closes_by_day(pd.Series([1.0, 2.0]))
    with pytest.raises(ValueError, match="empty window"):
        realized_total({}, [])
    with pytest.raises(ValueError, match="finite"):
        percentile(math.nan, [0.0] * 20)
