"""Tests for return statistics, using hand-computable series."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.stats import (
    analyze_history,
    annualized_volatility,
    cagr,
    drawdown_series,
    estimate_gbm,
    excess_kurtosis,
    geometric_mean_return,
    max_drawdown,
    rolling_volatility,
    skewness,
    summary_stats,
    total_return,
)
from quant_hub.data.synthetic import GeometricBrownianMotion
from quant_hub.data.types import PriceHistory


def history_from(closes, freq="B"):
    index = pd.bdate_range("2024-01-01", periods=len(closes))
    return PriceHistory(
        symbol="TEST", source="test", frame=pd.DataFrame({"close": closes}, index=index)
    )


def series_from(values):
    return pd.Series(values, index=pd.bdate_range("2024-01-01", periods=len(values)))


class TestReturns:
    def test_simple_returns_hand_computed(self):
        h = history_from([100, 110, 99])
        assert h.simple_returns().tolist() == pytest.approx([0.10, -0.10])

    def test_log_returns_hand_computed(self):
        h = history_from([100, 110, 99])
        assert h.log_returns().tolist() == pytest.approx(
            [math.log(1.1), math.log(0.9)]
        )

    def test_log_returns_sum_to_log_total(self):
        # Time-additivity: sum of log returns == ln(S_n / S_0).
        h = history_from([100, 104, 97, 113, 108])
        assert float(h.log_returns().sum()) == pytest.approx(math.log(108 / 100))

    def test_total_return(self):
        assert total_return(series_from([100, 110, 99])) == pytest.approx(-0.01)

    def test_geometric_mean_return(self):
        # (99/100)^(1/2) - 1 over two periods.
        got = geometric_mean_return(series_from([100, 110, 99]))
        assert got == pytest.approx(math.sqrt(0.99) - 1)

    def test_geometric_leq_arithmetic_mean(self):
        h = history_from([100, 120, 96, 110])
        arith = float(h.simple_returns().mean())
        assert geometric_mean_return(h.closes) <= arith

    def test_require_two_prices(self):
        for fn in (total_return, geometric_mean_return, cagr):
            with pytest.raises(ValueError):
                fn(series_from([100.0]))


class TestCAGR:
    def test_doubling_in_one_trading_year(self):
        # 253 prices = 252 daily periods = T of exactly one year.
        closes = 100 * 2 ** (np.arange(253) / 252)
        assert cagr(series_from(closes)) == pytest.approx(1.0)

    def test_two_year_horizon(self):
        # 21% total over 504 daily periods (T = 2y): CAGR = 1.21^(1/2) - 1 = 10%.
        closes = [100.0] + [0.0] * 503
        closes = 100 * 1.21 ** (np.arange(505) / 504)
        assert cagr(series_from(closes)) == pytest.approx(0.10)

    def test_observed_periods_not_calendar(self):
        # 5 weekly prices with p=52: 4 periods = 4/52 years.
        closes = series_from([100, 101, 102, 103, 104])
        expected = (104 / 100) ** (52 / 4) - 1
        assert cagr(closes, periods_per_year=52) == pytest.approx(expected)

    def test_rejects_bad_periods_per_year(self):
        with pytest.raises(ValueError):
            cagr(series_from([100.0, 101.0]), periods_per_year=0)


class TestVolatility:
    def test_annualized_matches_manual(self):
        rng = np.random.default_rng(1)
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
        h = history_from(closes)
        manual = float(h.log_returns().std(ddof=1)) * math.sqrt(252)
        assert annualized_volatility(h.log_returns()) == pytest.approx(manual)

    def test_nan_with_single_return(self):
        h = history_from([100.0, 101.0])
        assert math.isnan(annualized_volatility(h.log_returns()))

    def test_rolling_window_hand_computed(self):
        h = history_from([100, 110, 99, 105])
        r = h.log_returns()
        rolled = rolling_volatility(r, window=2, periods_per_year=252)
        # First window is dropped; element k is std of (r_k-1, r_k) * sqrt(252).
        expected = [
            np.std([r.iloc[0], r.iloc[1]], ddof=1) * math.sqrt(252),
            np.std([r.iloc[1], r.iloc[2]], ddof=1) * math.sqrt(252),
        ]
        assert len(rolled) == 2
        assert rolled.tolist() == pytest.approx(expected)

    def test_rolling_rejects_window_below_two(self):
        with pytest.raises(ValueError):
            rolling_volatility(series_from([0.01, 0.02]), window=1)


class TestDrawdown:
    def test_series_hand_computed(self):
        dd = drawdown_series(series_from([100, 120, 90, 110, 115]))
        assert dd.tolist() == pytest.approx(
            [0.0, 0.0, 90 / 120 - 1, 110 / 120 - 1, 115 / 120 - 1]
        )

    def test_monotonic_rise_has_zero_drawdown(self):
        assert max_drawdown(series_from([100, 101, 102, 103])) == 0.0

    def test_known_max_drawdown(self):
        # Peak 120 -> trough 90 is -25%; later recovery doesn't change it.
        assert max_drawdown(series_from([100, 120, 90, 110, 115])) == pytest.approx(
            90 / 120 - 1
        )

    def test_drawdown_measured_from_running_peak_not_start(self):
        assert max_drawdown(series_from([100, 80, 160, 120])) == pytest.approx(
            120 / 160 - 1
        )


class TestMoments:
    def test_symmetric_data_has_zero_skew(self):
        assert skewness(series_from([-0.02, 0.0, 0.02])) == pytest.approx(0.0)

    def test_excess_kurtosis_hand_computed(self):
        # For {-a, 0, a}: m2 = 2a^2/3, m4 = 2a^4/3 -> g2 = 1.5 - 3 = -1.5.
        assert excess_kurtosis(series_from([-0.02, 0.0, 0.02])) == pytest.approx(-1.5)

    def test_skew_sign_for_asymmetric_data(self):
        assert skewness(series_from([-0.01, -0.01, -0.01, 0.05])) > 0

    def test_nan_on_zero_variance(self):
        assert math.isnan(skewness(series_from([0.01, 0.01, 0.01])))
        assert math.isnan(excess_kurtosis(series_from([0.01, 0.01, 0.01])))


class TestGBMEstimation:
    def test_identity_mu_equals_log_drift_plus_half_var(self):
        rng = np.random.default_rng(3)
        r = series_from(rng.normal(0.0003, 0.012, 300))
        est = estimate_gbm(r)
        assert est.mu_hat == pytest.approx(est.log_drift_hat + 0.5 * est.sigma_hat**2)

    def test_recovers_true_parameters_from_long_path(self):
        # 40 years of daily data. SE(sigma_hat) ~ sigma/sqrt(2n) ~ 0.001;
        # SE(mu_hat) ~ sigma/sqrt(T) = 0.2/sqrt(40) ~ 0.032. Tolerances = ~4 SE.
        mu, sigma = 0.08, 0.20
        gen = GeometricBrownianMotion(100.0, mu, sigma, dt=1 / 252, seed=7)
        prices = gen.simulate(252 * 40)
        h = history_from(prices)
        est = estimate_gbm(h.log_returns())
        assert est.sigma_hat == pytest.approx(sigma, abs=0.004)
        assert est.mu_hat == pytest.approx(mu, abs=0.13)

    def test_nan_below_two_returns(self):
        est = estimate_gbm(series_from([0.01]))
        assert math.isnan(est.mu_hat)
        assert math.isnan(est.sigma_hat)
        assert math.isnan(est.log_drift_hat)


class TestSummaryStats:
    def test_total_return_and_cagr(self):
        closes = 100 * 2 ** (np.arange(253) / 252)
        stats = summary_stats(history_from(closes))
        assert stats.total_return == pytest.approx(1.0)
        assert stats.cagr == pytest.approx(1.0)
        assert stats.n_observations == 253
        assert stats.start_price == pytest.approx(100.0)
        assert stats.end_price == pytest.approx(200.0)

    def test_constant_prices(self):
        stats = summary_stats(history_from([100.0] * 50))
        assert stats.total_return == 0.0
        assert stats.annualized_vol == 0.0
        assert math.isnan(stats.sharpe)  # 0/0 is undefined, not zero
        assert stats.max_drawdown == 0.0
        assert math.isnan(stats.skewness)  # zero variance
        assert math.isnan(stats.excess_kurtosis)

    def test_per_period_fields_hand_computed(self):
        stats = summary_stats(history_from([100, 110, 99]))
        assert stats.mean_simple_return == pytest.approx(0.0)
        assert stats.mean_log_return == pytest.approx(
            (math.log(1.1) + math.log(0.9)) / 2
        )
        assert stats.geometric_mean_return == pytest.approx(math.sqrt(0.99) - 1)
        assert stats.std_simple_return == pytest.approx(
            np.std([0.10, -0.10], ddof=1)
        )

    def test_requires_two_prices(self):
        with pytest.raises(ValueError):
            summary_stats(history_from([100.0]))

    def test_rejects_bad_periods_per_year(self):
        with pytest.raises(ValueError):
            summary_stats(history_from([100.0, 101.0]), periods_per_year=0)


class TestAnalyzeHistory:
    def test_bundle_is_internally_consistent(self):
        gen = GeometricBrownianMotion(100.0, 0.08, 0.20, seed=11)
        h = history_from(gen.simulate(300))
        a = analyze_history(h, vol_window=21)
        assert a.history is h
        assert len(a.simple_returns) == len(h) - 1
        assert len(a.log_returns) == len(h) - 1
        assert len(a.rolling_vol) == len(h) - 1 - 20  # window-1 dropped
        assert len(a.drawdowns) == len(h)
        assert a.stats.annualized_vol == pytest.approx(
            annualized_volatility(a.log_returns)
        )
        assert a.gbm.sigma_hat == pytest.approx(a.stats.annualized_vol)
        assert a.vol_window == 21
        assert a.periods_per_year == 252
