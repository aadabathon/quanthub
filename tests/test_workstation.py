"""Tests for services/workstation.py: store overview, windowed history, and
the GBM forecast fan (statistically checked against its closed forms)."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.types import PriceHistory
from quant_hub.services.workstation import (
    FAN_PERCENTILES,
    WorkstationService,
    gbm_forecast,
)
from quant_hub.storage.bar_store import BarStore, InstrumentKey

DAILY = "1 day"


def bars(dates: pd.DatetimeIndex, closes: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": closes, "high": closes * 1.01, "low": closes * 0.99,
         "close": closes, "volume": np.full(len(closes), 1e6)},
        index=dates,
    )


@pytest.fixture
def store(tmp_path):
    store = BarStore(tmp_path / "bars.db")
    dates = pd.bdate_range("2024-01-02", periods=400)
    closes = 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, len(dates))))
    store.insert_bars(InstrumentKey("AAA"), "ibkr", DAILY, bars(dates, closes))
    store.insert_bars(InstrumentKey("ONE"), "ibkr", DAILY,
                      bars(pd.DatetimeIndex(["2024-03-01"]), np.array([50.0])))
    yield store
    store.close()


def summary_for(service, symbol):
    return next(s.summary for s in service.overview().series if s.symbol == symbol)


class TestOverview:
    def test_counts_and_last_synced(self, store):
        overview = WorkstationService(store).overview()
        assert [s.symbol for s in overview.series] == ["AAA", "ONE"]
        assert overview.total_bars == 401
        assert overview.last_synced == pd.bdate_range("2024-01-02", periods=400)[-1]

    def test_change_is_last_bar_simple_return(self, store):
        aaa = WorkstationService(store).overview().series[0]
        closes = store.query_bars(InstrumentKey("AAA"), "ibkr", DAILY,
                                  aaa.summary.first, aaa.summary.last)["close"].to_numpy()
        assert aaa.last_close == pytest.approx(closes[-1])
        assert aaa.change_1d == pytest.approx(closes[-1] / closes[-2] - 1)

    def test_single_bar_series_has_nan_change(self, store):
        one = WorkstationService(store).overview().series[1]
        assert one.last_close == 50.0 and math.isnan(one.change_1d)

    def test_spark_covers_trailing_year_only(self, store):
        aaa = WorkstationService(store).overview(spark_window="1Y").series[0]
        last = aaa.summary.last
        expected = len(pd.bdate_range(last - pd.DateOffset(years=1), last))
        assert len(aaa.spark) == expected

    def test_empty_store(self, tmp_path):
        empty = BarStore(tmp_path / "empty.db")
        overview = WorkstationService(empty).overview()
        assert overview.series == () and overview.total_bars == 0
        assert overview.last_synced is None
        empty.close()


class TestHistory:
    def test_windows_are_anchored_at_last_bar(self, store):
        service = WorkstationService(store)
        summary = summary_for(service, "AAA")
        month = service.history(summary, "1M")
        assert month.end == summary.last
        assert month.start >= summary.last - pd.DateOffset(months=1)
        assert len(service.history(summary, "ALL")) == summary.n_bars

    def test_window_never_starts_before_first_bar(self, store):
        service = WorkstationService(store)
        summary = summary_for(service, "AAA")
        assert service.history(summary, "5Y").start == summary.first

    def test_unknown_window_rejected(self, store):
        service = WorkstationService(store)
        with pytest.raises(ValueError, match="unknown window"):
            service.history(summary_for(service, "AAA"), "2W")


def gbm_history(n: int = 253, mu: float = 0.08, sigma: float = 0.2) -> PriceHistory:
    rng = np.random.default_rng(3)
    dt = 1 / 252
    log_steps = (mu - sigma**2 / 2) * dt + sigma * np.sqrt(dt) * rng.standard_normal(n - 1)
    closes = 100 * np.exp(np.concatenate([[0.0], np.cumsum(log_steps)]))
    return PriceHistory("GBM", "test", pd.DataFrame(
        {"close": closes}, index=pd.bdate_range("2025-01-02", periods=n)))


class TestForecast:
    def test_shape_and_join_to_history(self):
        history = gbm_history()
        fc = gbm_forecast(history, horizon_days=60, n_paths=50, seed=1)
        assert fc.paths.shape == (50, 61)
        assert np.all(fc.paths[:, 0] == history.closes.iloc[-1])
        assert fc.dates[0] == history.end and len(fc.dates) == 61
        assert set(fc.percentiles) == set(FAN_PERCENTILES)

    def test_percentile_bands_are_ordered(self):
        fc = gbm_forecast(gbm_history(), horizon_days=60, n_paths=500, seed=1)
        stacked = np.vstack([fc.percentiles[q] for q in FAN_PERCENTILES])
        assert np.all(np.diff(stacked, axis=0) >= 0)

    def test_seeded_is_deterministic(self):
        a = gbm_forecast(gbm_history(), horizon_days=20, n_paths=10, seed=5)
        b = gbm_forecast(gbm_history(), horizon_days=20, n_paths=10, seed=5)
        np.testing.assert_array_equal(a.paths, b.paths)

    def test_terminal_quantiles_match_closed_form(self):
        """S_T is lognormal: q-quantile = S0 exp((μ̂ - σ̂²/2)T + z_q σ̂ √T).
        Sample-quantile s.e. in log space ≈ sqrt(q(1-q)) / (φ(z_q) √n) · σ̂√T;
        with n = 40,000 that is < 0.3% of price, so 1.5% is > 5 s.e."""
        history = gbm_history()
        fc = gbm_forecast(history, horizon_days=252, n_paths=40_000, seed=11)
        s0, T = history.closes.iloc[-1], 1.0
        mu, sigma = fc.estimate.mu_hat, fc.estimate.sigma_hat
        for q, z in ((5, -1.6448536), (50, 0.0), (95, 1.6448536)):
            closed = s0 * math.exp((mu - sigma**2 / 2) * T + z * sigma * math.sqrt(T))
            assert fc.percentiles[q][-1] == pytest.approx(closed, rel=0.015)

    @pytest.mark.parametrize("kwargs, match", [
        ({"horizon_days": 0}, "horizon_days"),
        ({"n_paths": 1}, "n_paths"),
    ])
    def test_invalid_arguments(self, kwargs, match):
        with pytest.raises(ValueError, match=match):
            gbm_forecast(gbm_history(), **kwargs)

    def test_too_short_history(self):
        with pytest.raises(ValueError, match="at least 3 closes"):
            gbm_forecast(gbm_history(n=2))
