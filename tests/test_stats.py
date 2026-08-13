"""Tests for return statistics, using hand-computable series."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.stats import max_drawdown, summary_stats
from quant_hub.data.types import PriceHistory


def history_from(closes, freq="B"):
    index = pd.bdate_range("2024-01-01", periods=len(closes))
    return PriceHistory(
        symbol="TEST", source="test", frame=pd.DataFrame({"close": closes}, index=index)
    )


class TestMaxDrawdown:
    def test_monotonic_rise_has_zero_drawdown(self):
        h = history_from([100, 101, 102, 103])
        assert max_drawdown(h.closes) == 0.0

    def test_known_drawdown(self):
        # Peak 120 -> trough 90 is -25%; later recovery doesn't change it.
        h = history_from([100, 120, 90, 110, 115])
        assert max_drawdown(h.closes) == pytest.approx(90 / 120 - 1)

    def test_drawdown_measured_from_running_peak_not_start(self):
        h = history_from([100, 80, 160, 120])
        assert max_drawdown(h.closes) == pytest.approx(120 / 160 - 1)


class TestSummaryStats:
    def test_total_return_and_cagr(self):
        # Doubling over 252 daily periods (253 prices) = 100% total, CAGR 100%.
        closes = 100 * 2 ** (np.arange(253) / 252)
        stats = summary_stats(history_from(closes))
        assert stats.total_return == pytest.approx(1.0)
        assert stats.cagr == pytest.approx(1.0)
        assert stats.n_observations == 253

    def test_constant_prices(self):
        stats = summary_stats(history_from([100.0] * 50))
        assert stats.total_return == 0.0
        assert stats.annualized_vol == 0.0
        assert math.isnan(stats.sharpe)  # 0/0 is undefined, not zero
        assert stats.max_drawdown == 0.0

    def test_annualized_vol_matches_manual_computation(self):
        rng = np.random.default_rng(1)
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 500)))
        h = history_from(closes)
        stats = summary_stats(h)
        manual = float(h.log_returns().std(ddof=1)) * math.sqrt(252)
        assert stats.annualized_vol == pytest.approx(manual)

    def test_requires_two_prices(self):
        with pytest.raises(ValueError):
            summary_stats(history_from([100.0]))

    def test_rejects_bad_periods_per_year(self):
        with pytest.raises(ValueError):
            summary_stats(history_from([100.0, 101.0]), periods_per_year=0)
