"""REGIME_MODEL Q2: σ̂, μ̂ and the tail, against planted truths and closed
forms (``models/regime_model.py``)."""

import math
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.regime_inputs import (
    RegimeInput,
    garch_vol_ahead,
    realized_vol,
    trend_excess,
    walk_forward,
)
from quant_hub.models.regime_model import (
    Origins,
    Separation,
    brier,
    episodes,
    fit_logistic,
    inputs_at,
    mean_forecast,
    monotone_decreasing,
    origins,
    r2_out_of_sample,
    tail_forecast,
)
from quant_hub.models.regression import ols
from tests.test_regime_inputs import market_world


def monthly(y, months=1, drawdown=None, start="1950-01-31") -> Origins:
    """Origins one month apart; target i is known ``months`` origins later."""
    n = len(y)
    days = pd.date_range(start, periods=n, freq="ME")
    known = pd.Series([days[i + months] if i + months < n else pd.NaT for i in range(n)],
                      index=days, dtype="datetime64[ns]")
    y = np.where(known.notna(), np.asarray(y, dtype=float), np.nan)
    dd = np.full(n, np.nan) if drawdown is None else np.asarray(drawdown, dtype=float)
    dd = np.where(known.notna(), dd, np.nan)
    return Origins(days, pd.Series(y, index=days), pd.Series(dd, index=days), known, months)


def overlapping(n, months=12, mean=0.005, sd=0.04, seed=0):
    """12-month sums of i.i.d. monthly returns, one per month: the overlap
    the real targets have."""
    r = np.random.default_rng(seed).normal(mean, sd, n + months)
    return np.convolve(r, np.ones(months), "valid")[1:n + 1]


# ----------------------------------------------------------------- origins

def test_origins_are_month_ends_with_their_targets():
    total, bills = market_world(years=3)
    o = origins(total, bills, months=12)
    assert all(d == total.index[total.index.to_period("M") == d.to_period("M")][-1]
               for d in o.days)
    i, j = 3, 15
    window = (total.index > o.days[i]) & (total.index <= o.days[j])
    excess = np.log1p(total) - np.log1p(bills)
    assert o.y.iloc[i] == pytest.approx(float(excess[window].sum()), rel=1e-12)
    level = np.cumprod(1 + total.to_numpy())
    path = level[(total.index >= o.days[i]) & (total.index <= o.days[j])]
    worst = max(1 - path[v] / path[u] for u in range(len(path)) for v in range(u, len(path)))
    assert o.drawdown.iloc[i] == pytest.approx(worst, rel=1e-12)
    assert o.known.iloc[i] == o.days[j]
    assert o.y.iloc[-12:].isna().all() and o.known.iloc[-12:].isna().all()
    assert o.lags == 22


def test_sigma_reads_any_horizon_from_the_walk_forward():
    total, _ = market_world(years=4)
    walk = walk_forward(total, years=2)
    assert garch_vol_ahead(walk, 21).equals(walk.garch_vol)
    day = walk.next_variance.index[100]
    g = walk.refits[int(walk.refit_of[day])].garch
    p, long_run = g.persistence, g.unconditional_variance
    h = walk.next_variance[day]
    year = long_run + (h - long_run) * (1 - p**252) / (252 * (1 - p))
    assert garch_vol_ahead(walk, 252)[day] == pytest.approx(math.sqrt(252 * year), rel=1e-12)


# -------------------------------------------------------------------- mean

def test_a_planted_predictor_is_recovered():
    rng = np.random.default_rng(1)
    n = 600
    x = rng.normal(0, 1, n)
    y = 0.04 + 0.03 * x + rng.normal(0, 0.05, n)
    o = monthly(y)
    mf = mean_forecast(o, pd.DataFrame({"signal": x}, index=o.days), signs={}, min_origins=60)
    assert mf.slopes["signal"].iloc[-1] == pytest.approx(0.03, abs=0.008)
    assert mf.used["signal"].iloc[-1]
    assert r2_out_of_sample(o.y, mf.forecast, mf.benchmark) > 0.1


def test_a_useless_predictor_loses_to_the_mean_on_average():
    scores = []
    for seed in range(40):
        rng = np.random.default_rng(100 + seed)
        y, x = rng.normal(0.05, 0.1, 300), rng.normal(0, 1, 300)
        o = monthly(y)
        mf = mean_forecast(o, pd.DataFrame({"noise": x}, index=o.days), signs={},
                           min_origins=60)
        scores.append(r2_out_of_sample(o.y, mf.unfloored, mf.benchmark))
    assert np.mean(scores) < 0


def test_nothing_known_later_moves_a_forecast():
    rng = np.random.default_rng(2)
    n = 400
    x = rng.normal(0, 1, n)
    y = overlapping(n, seed=2) + 0.01 * x
    o = monthly(y, months=12)
    X = pd.DataFrame({"signal": x}, index=o.days)
    full = mean_forecast(o, X, signs={}, min_origins=60)
    # Origin 200's forecast uses only targets known by then: those ending by
    # origin 200, i.e. origins up to 188. Changing a later target moves nothing.
    moved = o.y.copy()
    moved.iloc[189:] += 1.0
    shifted = mean_forecast(Origins(o.days, moved, o.drawdown, o.known, 12), X, signs={},
                            min_origins=60)
    assert shifted.unfloored.iloc[:201].equals(full.unfloored.iloc[:201])
    assert shifted.unfloored.iloc[202] != full.unfloored.iloc[202]
    # Truncating the data changes no earlier forecast.
    cut = monthly(y[:300], months=12)
    short = mean_forecast(cut, X.iloc[:300], signs={}, min_origins=60)
    assert np.allclose(short.unfloored.iloc[:288], full.unfloored.iloc[:288], rtol=0, atol=0,
                       equal_nan=True)


def test_the_restrictions():
    rng = np.random.default_rng(3)
    n = 300
    x = rng.normal(0, 1, n)
    y = 0.05 - 0.03 * x + rng.normal(0, 0.03, n)              # the slope comes out negative
    o = monthly(y)
    X = pd.DataFrame({"valuation.ecy": x}, index=o.days)        # theory expects +
    restricted = mean_forecast(o, X, min_origins=60)
    assert not restricted.used["valuation.ecy"].iloc[-1]
    assert restricted.slopes["valuation.ecy"].iloc[-1] < 0
    assert restricted.unfloored.iloc[-2] == restricted.benchmark.iloc[-2]
    free = mean_forecast(o, X.rename(columns={"valuation.ecy": "correlation.stock_bond"}),
                         min_origins=60)                        # no expected sign
    assert free.used["correlation.stock_bond"].iloc[-1]
    assert free.unfloored.iloc[-2] != free.benchmark.iloc[-2]

    falling = monthly(rng.normal(-0.05, 0.02, n))               # a negative premium
    floored = mean_forecast(falling, pd.DataFrame(index=falling.days), min_origins=60)
    assert floored.unfloored.iloc[-2] < 0 and floored.forecast.iloc[-2] == 0


def test_the_error_is_ols_with_one_input_and_the_mean_with_none():
    rng = np.random.default_rng(4)
    n = 300
    x = rng.normal(0, 1, n)
    o = monthly(overlapping(n, seed=4) + 0.02 * x, months=12)
    day = o.days[-1]
    known = o.y.notna().to_numpy()
    y = o.y.to_numpy()[known]

    alone = mean_forecast(o, pd.DataFrame(index=o.days), min_origins=60)
    mean_only = ols(y, np.ones((len(y), 1)), ("mean",), lags=22)
    assert alone.unfloored[day] == pytest.approx(mean_only.value("mean"), rel=1e-12)
    assert alone.error(day) == pytest.approx(mean_only.error("mean"), rel=1e-10)

    one = mean_forecast(o, pd.DataFrame({"s": x}, index=o.days), signs={}, min_origins=60)
    fit = ols(y, np.column_stack([np.ones(len(y)), x[known]]), ("a", "b"), lags=22)
    c = np.array([1.0, x[-1]])
    assert one.unfloored[day] == pytest.approx(float(c @ fit.coef), rel=1e-12)
    assert one.error(day) == pytest.approx(math.sqrt(c @ fit.covariance["newey-west"] @ c),
                                           rel=1e-10)


def test_the_means_interval_covers_on_overlapping_targets():
    # Reported, like D76's: Newey–West with L = 22 on 12-month sums sampled
    # monthly. 29 years of known targets.
    covered = []
    for seed in range(200):
        o = monthly(overlapping(360, seed=1000 + seed), months=12)
        mf = mean_forecast(o, pd.DataFrame(index=o.days), min_origins=340)
        day = o.days[-1]
        covered.append(abs(mf.unfloored[day] - 12 * 0.005) <= 1.96 * mf.error(day))
    assert 0.85 <= np.mean(covered) <= 0.98


# -------------------------------------------------------------------- tail

def test_irls_has_the_closed_form_with_one_binary_regressor():
    rng = np.random.default_rng(5)
    x = rng.integers(0, 2, 2000).astype(float)
    e = (rng.random(2000) < np.where(x == 1, 0.35, 0.1)).astype(float)
    p0, p1 = e[x == 0].mean(), e[x == 1].mean()
    model = fit_logistic(e, x)

    def logit(p):
        return math.log(p / (1 - p))

    assert model.intercept == pytest.approx(logit(p0), abs=1e-9)
    assert model.slope == pytest.approx(logit(p1) - logit(p0), abs=1e-9)
    assert fit_logistic(e).intercept == pytest.approx(logit(e.mean()), abs=1e-12)
    with pytest.raises(Separation):
        fit_logistic([0, 0, 1, 1], [1.0, 2.0, 3.0, 4.0])
    with pytest.raises(Separation):
        fit_logistic([0, 0, 0], [1.0, 2.0, 3.0])


def test_episodes_and_the_monotone_projection():
    assert episodes([0, 1, 1, 0, 1, 0, 0, 1, 1, 1]) == 3
    assert episodes([1, 1, 0]) == 1 and episodes([]) == 0
    ordered = np.array([0.4, 0.3, 0.3, 0.1])
    assert np.array_equal(monotone_decreasing(ordered), ordered)
    assert monotone_decreasing([0.3, 0.4, 0.2, 0.1]) == pytest.approx([0.35, 0.35, 0.2, 0.1])
    rng = np.random.default_rng(6)
    for _ in range(50):
        out = monotone_decreasing(rng.random(4))
        assert np.all(np.diff(out) <= 1e-15)


def _tail_world(n, seed, a=-2.0, b=1.0):
    """Events with a known logistic rate in x, one origin a month, h = 1."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 1, n)
    p = 1 / (1 + np.exp(-(a + b * x)))
    event = rng.random(n) < p
    drawdown = np.where(event, 0.25, 0.05) + rng.uniform(0, 0.04, n)
    return monthly(np.zeros(n), drawdown=drawdown), x, p


def test_the_pooled_tail_is_calibrated():
    o, x, _ = _tail_world(3000, seed=7)
    tf = tail_forecast(o, pd.DataFrame({"volatility.garch": x}, index=o.days),
                       thresholds=(0.10,), min_origins=200)
    p = tf.probability[0.10].to_numpy()
    e = (o.drawdown.to_numpy() >= 0.10).astype(float)
    ok = np.isfinite(p) & o.known.notna().to_numpy()
    p, e = p[ok], e[ok]
    edges = np.quantile(p, np.linspace(0, 1, 11))
    for low, high in pairwise(edges):
        in_bin = (p >= low) & (p <= high)
        expected = p[in_bin].mean()
        assert abs(e[in_bin].mean() - expected) < 3.5 * math.sqrt(expected * (1 - expected)
                                                                   / in_bin.sum())
    assert brier(p, e) < brier(np.full_like(e, e.mean()), e)       # beats the base rate


def test_the_tail_is_monotone_and_its_interval_covers():
    rng = np.random.default_rng(8)
    n = 400
    x = rng.normal(0, 1, n)
    drawdown = np.clip(0.15 + 0.08 * x + rng.normal(0, 0.1, n), 0, 0.9)
    o = monthly(np.zeros(n), drawdown=drawdown)
    X = pd.DataFrame({"volatility.garch": x, "turbulence.calm": rng.normal(0, 1, n)},
                     index=o.days)
    tf = tail_forecast(o, X, min_origins=100)
    rows = tf.probability.dropna()
    assert len(rows) > 250
    assert (np.diff(rows.to_numpy(), axis=1) <= 1e-15).all()
    ordered = (np.diff(tf.pooled.dropna().to_numpy(), axis=1) <= 0).all(axis=1)
    assert np.allclose(rows[ordered], tf.pooled.dropna()[ordered])   # untouched when in order

    covered = []
    for seed in range(100):
        o, x, p = _tail_world(400, seed=200 + seed)
        tf = tail_forecast(o, pd.DataFrame({"volatility.garch": x}, index=o.days),
                           thresholds=(0.10,), min_origins=390)
        low, high = tf.interval(o.days[-1], 0.10)
        covered.append(low <= p[-1] <= high)
    assert 0.88 <= np.mean(covered) <= 0.99


def test_the_tail_interval_covers_on_overlapping_events():
    # Reported, like μ̂'s: 12-month events sampled monthly, with x pure noise,
    # so the true probability is the base rate Φ((c − 12μ)/(σ√12)).
    from statistics import NormalDist
    mu, sigma, c, n = 0.006, 0.045, -0.10, 612
    true = NormalDist().cdf((c - 12 * mu) / (sigma * math.sqrt(12)))
    covered = []
    for seed in range(100):
        rng = np.random.default_rng(3000 + seed)
        sums = np.convolve(rng.normal(mu, sigma, n + 12), np.ones(12), "valid")[1:n + 1]
        o = monthly(np.zeros(n), months=12, drawdown=np.where(sums < c, 0.3, 0.01))
        X = pd.DataFrame({"correlation.stock_bond": rng.normal(0, 1, n)}, index=o.days)
        tf = tail_forecast(o, X, thresholds=(0.10,), min_origins=n - 30, min_episodes=1)
        low, high = tf.interval(o.days[-1], 0.10)
        covered.append(low <= true <= high)
    assert 0.82 <= np.mean(covered) <= 0.98


def test_a_wrong_sign_or_too_few_episodes_gives_the_base_rate():
    o, x, _ = _tail_world(600, seed=9)
    flipped = tail_forecast(o, pd.DataFrame({"turbulence.calm": x}, index=o.days),
                            thresholds=(0.10,), min_origins=200)    # theory expects −
    assert (flipped.fitted[0.10].dropna() == 0).all()
    assert np.allclose(flipped.pooled[0.10].dropna(), flipped.base_rate[0.10].dropna())
    few = tail_forecast(o, pd.DataFrame({"volatility.garch": x}, index=o.days),
                        thresholds=(0.10,), min_origins=200, min_episodes=10_000)
    assert (few.fitted[0.10].dropna() == 0).all()


# ---------------------------------------------------------------- together

def test_the_estimates_run_on_q1s_inputs():
    total, bills = market_world(years=6)
    o = origins(total, bills, months=12)
    inputs = [RegimeInput.build("volatility.realized_21d", "", "daily", "",
                                realized_vol(total), 20),
              RegimeInput.build("trend.excess_12m", "", "daily", "",
                                trend_excess(total, bills), 20)]
    X = inputs_at(o, inputs)
    assert list(X.columns) == ["volatility.realized_21d", "trend.excess_12m"]
    assert X["trend.excess_12m"].iloc[:11].isna().all() and X.iloc[-1].notna().all()
    mf = mean_forecast(o, X, min_origins=24)
    tf = tail_forecast(o, X, thresholds=(0.05, 0.10), min_origins=24, min_episodes=1)
    assert mf.forecast.notna().sum() > 20 and (mf.forecast.dropna() >= 0).all()
    assert tf.probability.notna().all(axis=1).sum() > 20
    assert math.isfinite(mf.error(o.days[-1]))
