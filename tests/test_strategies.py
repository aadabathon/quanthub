"""Out-of-sample invariance and controlled statistical worlds (R2)."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.backtest import backtest
from quant_hub.models.portfolio import scale_to_risk
from quant_hub.models.garch import fit_garch11
from quant_hub.models.research_worlds import WORLDS, research_world
from quant_hub.models.strategies import STRATEGIES, StrategySpec, strategy_targets


@pytest.mark.parametrize("name", STRATEGIES)
def test_future_prices_cannot_change_earlier_decisions(name):
    prices = research_world(WORLDS[1], 150, 8)
    spec = StrategySpec(name, lookback=5, train_size=110, refit_every=25, threshold=.5)
    original = strategy_targets(prices, spec)
    changed = prices.copy()
    changed.iloc[131:] *= np.exp(np.linspace(0, 1, len(changed) - 131))[:, None]
    altered = strategy_targets(changed, spec)
    pd.testing.assert_frame_equal(original.targets.iloc[:131], altered.targets.iloc[:131])
    assert np.all(original.targets.iloc[:110] == 0)
    assert original.targets.sum(axis=1).max() <= 1 + 1e-12
    assert all(f["train_end"] < f["test_start"] for f in original.fits)


def test_sizing_closed_form_and_cap():
    cov = np.diag([.01**2, .02**2])
    b = np.array([.5, .5])
    sized = scale_to_risk(b, cov, .005)
    assert np.sqrt(sized @ cov @ sized) == pytest.approx(.005)
    np.testing.assert_allclose(scale_to_risk(b, cov, 1, .4), [.2, .2])
    np.testing.assert_array_equal(scale_to_risk(b, np.zeros((2, 2)), .1), [0, 0])
    with pytest.raises(ValueError, match="semidefinite"):
        scale_to_risk(b, np.diag([-1, 1]), .1)


def test_garch_first_decision_uses_the_forecast_after_observing_that_close():
    prices = research_world(WORLDS[0], 150, 12)
    spec = StrategySpec(name="GARCH volatility", lookback=10, train_size=110, target_vol=.05)
    targets = strategy_targets(prices, spec).targets
    logp = np.log(prices.iloc[:, 0].to_numpy())
    fit = fit_garch11(np.diff(logp[:110]))
    p = fit.parameters
    r_last_train, r_today = logp[109] - logp[108], logp[110] - logp[109]
    h_today = p.omega + p.alpha * (r_last_train - p.mean)**2 + p.beta * fit.conditional_variances[-1]
    h_tomorrow = p.omega + p.alpha * (r_today - p.mean)**2 + p.beta * h_today
    simple_variance = np.expm1(h_tomorrow) * np.exp(2 * p.mean + h_tomorrow)
    assert targets.iloc[110, 0] == pytest.approx(min(1, .05 / np.sqrt(252 * simple_variance)))


def test_worlds_reproducible_and_stationary_ar_moments():
    prices = research_world(WORLDS[1], 20000, 44)
    pd.testing.assert_frame_equal(prices, research_world(WORLDS[1], 20000, 44))
    r = np.diff(np.log(prices.iloc[:, 0]))
    assert np.corrcoef(r[1:], r[:-1])[0, 1] == pytest.approx(.6, abs=.02)
    assert r.std() == pytest.approx(.008 / np.sqrt(1 - .6**2), rel=.03)
    null = research_world(WORLDS[0], 20000, 44)
    assert null.pct_change().dropna().to_numpy().mean() == pytest.approx(0, abs=.00025)


def test_momentum_finds_planted_edge_across_independent_worlds():
    totals = []
    spec = StrategySpec(lookback=2, train_size=30, refit_every=250)
    for seed in range(12):
        prices = research_world(WORLDS[1], 600, seed)
        signals = strategy_targets(prices, spec)
        result = backtest(prices.iloc[30:], signals.targets.iloc[30:])
        totals.append(result.net_returns.mean())
    assert np.mean(totals) > .001
    assert sum(t > 0 for t in totals) >= 11


@pytest.mark.parametrize("kwargs", [{"train_size": 10}, {"lookback": 0},
                                    {"target_vol": 0}, {"gross_cap": 2},
                                    {"name": "smoother"}, {"seed": -1}])
def test_invalid_specs(kwargs):
    with pytest.raises(ValueError):
        replace(StrategySpec(), **kwargs)
