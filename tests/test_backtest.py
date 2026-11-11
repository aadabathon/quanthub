"""R1 accounting checked against cash/shares, not just shifted array formulas."""

import itertools

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.backtest import (
    BacktestConfig, backtest, walk_forward_splits, walk_forward_targets,
)


def frame(values, columns=None):
    values = np.asarray(values, dtype=float)
    if values.ndim == 1:
        values = values[:, None]
    return pd.DataFrame(values, index=pd.bdate_range("2020-01-02", periods=len(values)),
                        columns=columns or [f"A{i}" for i in range(values.shape[1])])


def targets(prices, values):
    return pd.DataFrame(np.broadcast_to(values, (len(prices) - 1, prices.shape[1])).copy(),
                        index=prices.index[:-1], columns=prices.columns)


def test_one_bar_lag_and_final_mark_without_liquidation():
    prices = frame([100, 200, 220, 110])
    weights = targets(prices, [[0], [1], [0]])
    result = backtest(prices, weights)
    # The jump at date 1 is known when its decision is made; it is NOT earned.
    np.testing.assert_allclose(result.net_returns, [0, 0.1, 0])
    np.testing.assert_allclose(result.equity, [100000, 100000, 110000, 110000])
    assert result.net_returns.index.equals(prices.index[1:])
    assert result.trades.index.equals(prices.index[:-1])


def test_buy_and_hold_matches_the_price_ratio_and_only_pays_entry():
    prices = frame([100, 110, 80, 130])
    result = backtest(prices, targets(prices, 1), BacktestConfig(half_spread_bps=12,
                                                              commission_bps=3))
    c = 15 / 10000
    np.testing.assert_allclose(result.equity.iloc[1:],
                               100000 / (1 + c) * prices.iloc[1:, 0] / 100, rtol=1e-13)
    assert result.fees.iloc[0] == pytest.approx(100000 * c / (1 + c), rel=1e-12)
    np.testing.assert_allclose(result.fees.iloc[1:], 0, atol=1e-9)
    assert result.ending_weights.iloc[0] == 1
    assert result.gross_equity.iloc[-1] == pytest.approx(130000)


def test_flat_market_round_trip_cost_identity():
    prices = frame([100] * 4)
    result = backtest(prices, targets(prices, [[0], [1], [0]]),
                      BacktestConfig(half_spread_bps=50, commission_bps=50))
    assert result.equity.iloc[-1] == pytest.approx(100000 * 0.99 / 1.01, rel=1e-13)
    assert result.fees.sum() == pytest.approx(100000 - result.equity.iloc[-1], rel=1e-12)
    np.testing.assert_allclose(result.net_returns, [0, 1 / 1.01 - 1, -0.01], atol=1e-14)


def test_constant_target_weights_rebalance_market_drift():
    prices = frame([[100, 100], [110, 100], [110, 100]])
    result = backtest(prices, targets(prices, [0.5, 0.5]), BacktestConfig(half_spread_bps=10))
    np.testing.assert_allclose(result.pretrade_weights.iloc[1], [55 / 105, 50 / 105])
    assert result.trades.iloc[1, 0] < 0 < result.trades.iloc[1, 1]
    assert result.turnover.iloc[1] > 0
    assert result.net_returns.iloc[1] < 0      # flat prices still incur rebalance fees


def test_idle_cash_compounds_the_stated_effective_annual_rate():
    prices = frame(np.full(253, 100))
    result = backtest(prices, targets(prices, 0), BacktestConfig(cash_rate=0.05))
    assert result.equity.iloc[-1] == pytest.approx(105000, rel=1e-12)
    assert result.fees.sum() == result.turnover.sum() == 0


def test_explicit_leverage_and_short_cash_financing():
    prices = frame([100, 120])
    config = BacktestConfig(max_gross_exposure=2, allow_short=True,
                            cash_rate=0.01, periods_per_year=1, commission_bps=30)
    long = backtest(prices, targets(prices, 2), config)
    short = backtest(prices, targets(prices, -0.5), config)
    assert long.equity.iloc[-1] == pytest.approx(100000 / 1.006 * 1.39, rel=1e-12)
    assert short.equity.iloc[-1] == pytest.approx(100000 / 1.0015 * 0.915, rel=1e-12)


def share_ledger(prices, weights, config):
    """Independent sequential ledger. Enumerate trade signs to solve NAV
    analytically; hold actual shares and cash between each pair of closes."""
    cash = config.initial_equity
    shares = np.zeros(prices.shape[1])
    path, fees, trades = [], [], []
    c = config.cost_rate
    for t, price in enumerate(prices.to_numpy()):
        if t:
            cash *= 1 + config.cash_return
        old = shares * price
        before = cash + old.sum()
        path.append(before)
        if t == len(weights):
            break
        w = weights.iloc[t].to_numpy()
        for sign in itertools.product((-1, 1), repeat=len(w)):
            sign = np.asarray(sign)
            after = (before + c * (sign @ old)) / (1 + c * (sign @ w))
            change = after * w - old
            if np.all(sign * change >= -1e-8):
                break
        else:
            raise AssertionError("no consistent trade signs")
        fee = c * np.abs(change).sum()
        assert after + fee == pytest.approx(before, rel=1e-12)
        shares = after * w / price
        cash = before - (after * w).sum() - fee
        fees.append(fee)
        trades.append(change)
    return np.array(path), np.array(fees), np.array(trades)


def test_vectorized_result_matches_an_independent_share_and_cash_ledger():
    rng = np.random.default_rng(42)
    prices = frame(100 * np.exp(np.vstack([np.zeros(3),
                            np.cumsum(rng.normal(0, 0.015, (200, 3)), axis=0)])))
    weights = targets(prices, rng.uniform(-0.3, 0.3, (200, 3)))
    config = BacktestConfig(half_spread_bps=15, commission_bps=5, cash_rate=0.04,
                            allow_short=True)
    result = backtest(prices, weights, config)
    path, fees, trades = share_ledger(prices, weights, config)
    np.testing.assert_allclose(result.equity, path, rtol=1e-12)
    np.testing.assert_allclose(result.fees, fees, rtol=1e-11, atol=1e-9)
    np.testing.assert_allclose(result.trades, trades, rtol=1e-10, atol=1e-7)
    np.testing.assert_allclose(result.fees, np.abs(result.trades).sum(axis=1) * config.cost_rate)
    np.testing.assert_allclose(result.turnover,
                               np.abs(result.trades).sum(axis=1) / result.equity.iloc[:-1])


def test_the_first_loss_counts_in_drawdown_and_inputs_are_copied():
    prices = frame([100, 90, 99])
    weights = targets(prices, 1)
    result = backtest(prices, weights)
    assert result.drawdown.min() == pytest.approx(-0.1)
    prices.iloc[0, 0] = 500
    weights.iloc[0, 0] = 0
    assert result.prices.iloc[0, 0] == 100
    assert result.targets.iloc[0, 0] == 1


def test_walk_forward_windows_and_partial_final_block():
    splits = walk_forward_splits(12, 4, 3)
    assert [(s.train, s.test) for s in splits] == [
        (slice(0, 4), slice(4, 7)), (slice(3, 7), slice(7, 10)),
        (slice(6, 10), slice(10, 12))]
    assert [s.train for s in walk_forward_splits(12, 4, 3, expanding=True)] == [
        slice(0, 4), slice(0, 7), slice(0, 10)]


def test_walk_forward_callbacks_receive_only_their_allowed_history():
    prices = frame(np.arange(100, 113))
    fits, predictions = [], []

    def fit(train):
        fits.append(train.copy())
        return train.index[-1]

    def predict(model, history):
        predictions.append((model, history.index[-1], len(history)))
        assert model < history.index[-1]
        # Callbacks cannot corrupt the caller's data through their argument.
        history.iloc[0, 0] = -100
        return pd.Series([0.5], index=prices.columns)

    weights = walk_forward_targets(prices, fit, predict, 4, 3)
    assert [list(f.index) for f in fits] == [list(prices.index[s]) for s in
                                           (slice(0, 4), slice(3, 7), slice(6, 10))]
    assert [p[2] for p in predictions] == list(range(5, 13))
    assert (weights.iloc[:4] == 0).all().all()
    assert (weights.iloc[4:] == 0.5).all().all()
    assert prices.iloc[0, 0] == 100


def test_scrambling_the_future_cannot_change_past_predictions_or_equity():
    rng = np.random.default_rng(5)
    prices = frame(100 * np.exp(np.r_[0, np.cumsum(rng.normal(0, 0.02, 59))]))

    def fit(train):
        return float(train.iloc[-1, 0] / train.iloc[0, 0])

    def predict(model, history):
        value = 0.75 if history.iloc[-1, 0] / history.iloc[-4, 0] > model else 0.25
        return pd.Series([value], index=history.columns)

    changed = prices.copy()
    changed.iloc[35:, 0] = rng.uniform(30, 300, len(changed) - 35)
    first = walk_forward_targets(prices, fit, predict, 10, 5)
    second = walk_forward_targets(changed, fit, predict, 10, 5)
    pd.testing.assert_frame_equal(first.iloc[:35], second.iloc[:35])
    config = BacktestConfig(commission_bps=3)
    one, two = backtest(prices, first, config), backtest(changed, second, config)
    pd.testing.assert_series_equal(one.equity.iloc[:35], two.equity.iloc[:35])


@pytest.mark.parametrize("edit", [
    lambda p: p.iloc[::-1],
    lambda p: p.set_axis([p.index[0]] * len(p)),
    lambda p: p.set_axis(p.index.tz_localize("UTC")),
    lambda p: p.set_axis(p.index + pd.Timedelta(hours=12)),
    lambda p: p.assign(A0=np.nan),
    lambda p: p.assign(A0=0),
    lambda p: p.set_axis([""] , axis=1),
    lambda p: p.iloc[:1],
])
def test_invalid_price_matrices_are_rejected(edit):
    good = frame([100, 101, 102])
    with pytest.raises(ValueError):
        backtest(edit(good), targets(good, 0))


@pytest.mark.parametrize("weights", [[[1], [np.nan]], [[-0.1], [0]], [[1.1], [0]]])
def test_invalid_targets_are_rejected(weights):
    prices = frame([100, 101, 102])
    with pytest.raises(ValueError):
        backtest(prices, targets(prices, weights))


def test_misalignment_and_missing_prediction_columns_are_rejected():
    prices = frame([[100, 100], [101, 102], [102, 103]])
    good = targets(prices, [0.5, 0.5])
    with pytest.raises(ValueError, match="same asset columns"):
        backtest(prices, good.iloc[:, ::-1])
    with pytest.raises(ValueError, match="index"):
        backtest(prices, good.set_axis(prices.index[1:]))
    prices = frame(np.arange(100, 110))
    with pytest.raises(ValueError, match="price columns"):
        walk_forward_targets(prices, lambda p: None,
                             lambda model, history: pd.Series([0], index=["wrong"]), 3, 2)


@pytest.mark.parametrize("kwargs", [
    {"initial_equity": 0}, {"half_spread_bps": -1}, {"cash_rate": -1},
    {"commission_bps": float("inf")}, {"periods_per_year": True},
    {"periods_per_year": 2.5}, {"allow_short": "yes"},
    {"max_gross_exposure": 0}, {"half_spread_bps": 10000},
])
def test_invalid_config_is_rejected(kwargs):
    with pytest.raises(ValueError):
        BacktestConfig(**kwargs)


def test_insolvency_and_unaffordable_liquidation_are_rejected():
    prices = frame([100, 40])
    with pytest.raises(ValueError, match="exhausts equity"):
        backtest(prices, targets(prices, 2), BacktestConfig(max_gross_exposure=2))
    prices = frame([100, 51, 51])
    with pytest.raises(ValueError, match="liquidation"):
        backtest(prices, targets(prices, [[2], [0]]),
                  BacktestConfig(max_gross_exposure=2, half_spread_bps=1000))


@pytest.mark.parametrize("args", [(10, 10, 2), (10, 1, 2), (10, 3, 0), (10, 3, True)])
def test_invalid_walk_forward_plans_are_rejected(args):
    with pytest.raises(ValueError):
        walk_forward_splits(*args)
