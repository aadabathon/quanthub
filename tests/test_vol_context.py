"""Realized vols + GARCH forecast term structure from a named source."""

from datetime import datetime

import numpy as np
import pytest

from quant_hub.data.sources import SyntheticSource
from quant_hub.data.stats import annualized_volatility
from quant_hub.services.market_service import MarketService
from quant_hub.services.vol_context import REALIZED_WINDOWS, VolContext, load_vol_context

NOW = datetime(2026, 9, 25)


@pytest.fixture(scope="module")
def context():
    return load_vol_context(MarketService([SyntheticSource()]), "spy", "synthetic", 1825, now=NOW)


def test_realized_windows_are_trailing_vols(context):
    history = SyntheticSource().get_history("SPY", datetime(2021, 9, 26), NOW)
    returns = history.log_returns()
    assert [w for w, _ in context.realized] == list(REALIZED_WINDOWS)
    for window, vol in context.realized:
        assert vol == pytest.approx(annualized_volatility(returns.iloc[-window:]))


def test_garch_curve_starts_near_next_variance_and_tends_to_long_run(context):
    curve = context.garch_vol(np.array([365.0 / 252.0, 1e6]))
    assert curve[0] == pytest.approx(np.sqrt(context.next_variance * 252), rel=1e-9)
    assert curve[1] == pytest.approx(np.sqrt(context.garch.unconditional_variance * 252), rel=1e-3)


def test_synthetic_gbm_vol_is_recovered_roughly(context):
    # The synthetic source is GBM (constant vol): GARCH's long-run vol and
    # the 1y realized vol should agree to within sampling error.
    long_run = float(np.sqrt(context.garch.unconditional_variance * 252))
    assert long_run == pytest.approx(dict(context.realized)[252], rel=0.25)


def test_calendar_trading_conversion():
    assert VolContext.calendar_days(252) == pytest.approx(365.0)


@pytest.mark.parametrize("symbol,lookback", [("", 1825), ("SPY", 30)])
def test_rejects_unusable_requests(symbol, lookback):
    with pytest.raises(ValueError):
        load_vol_context(MarketService([SyntheticSource()]), symbol, "synthetic", lookback, now=NOW)
