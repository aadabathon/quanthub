"""REGIME_MODEL Q1: the inputs as they were known at the time, against closed
forms and planted truths (``models/regime_inputs.py``)."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.garch import conditional_variances, fit_garch11
from quant_hub.models.markov import fit_hmm, hmm_filter
from quant_hub.models.regime_inputs import (
    LAGS,
    RegimeInput,
    bond_return,
    excess_cape_yield,
    expanding_percentile,
    expanding_z,
    known_from,
    real_time_cape,
    realized_vol,
    stock_bond_correlation,
    trend_average,
    trend_excess,
    walk_forward,
)

NO_LAGS = dict.fromkeys(LAGS, 0)


def shiller_world(months=480, seed=0):
    """Monthly P, D, E, CPI and GS10 from 1871, dated the 1st."""
    rng = np.random.default_rng(seed)
    index = pd.date_range("1871-01-01", periods=months, freq="MS")
    price = 5 * np.exp(np.cumsum(rng.normal(0.004, 0.04, months)))
    earnings = price * np.exp(rng.normal(math.log(0.06), 0.15, months))
    cpi = 10 * np.exp(np.cumsum(rng.normal(0.002, 0.006, months)))
    gs10 = np.clip(4 + np.cumsum(rng.normal(0, 0.12, months)), 1.0, 12.0)
    series = {"price": price, "dividend": 0.5 * earnings, "earnings": earnings, "cpi": cpi,
              "gs10": gs10}
    return {k: pd.Series(v, index=index) for k, v in series.items()}


def market_world(years=6, seed=1):
    """Daily total returns with two volatility regimes, and a small bill rate."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2010-01-04", periods=252 * years)
    state = np.zeros(len(days), dtype=int)
    for t in range(1, len(days)):
        state[t] = state[t - 1] if rng.random() < 0.99 else 1 - state[t - 1]
    total = rng.normal(0.0004, np.where(state == 0, 0.007, 0.02))
    return pd.Series(total, index=days), pd.Series(0.0001, index=days)


# ------------------------------------------------------------------ scales

def test_the_percentile_counts_earlier_values_with_ties_half():
    rng = np.random.default_rng(2)
    x = rng.integers(0, 6, 300).astype(float)
    x[[10, 50, 51]] = np.nan
    p = expanding_percentile(x, 20)
    earlier: list[float] = []
    for i, v in enumerate(x):
        if np.isnan(v):
            assert np.isnan(p[i])
            continue
        if len(earlier) < 20:
            assert np.isnan(p[i])
        else:
            past = np.array(earlier)
            assert p[i] == pytest.approx((np.sum(past < v) + 0.5 * np.sum(past == v)) / len(past))
        earlier.append(v)


def test_z_uses_the_mean_and_sd_of_earlier_values_only():
    rng = np.random.default_rng(3)
    x = rng.normal(2.0, 0.5, 200)
    x[30] = np.nan
    z = expanding_z(x, 10)
    finite = [i for i in range(len(x)) if not np.isnan(x[i])]
    for k, i in enumerate(finite):
        past = x[finite[:k]]
        if len(past) < 10:
            assert np.isnan(z[i])
        else:
            assert z[i] == pytest.approx((x[i] - past.mean()) / past.std(ddof=1), rel=1e-9)
    assert np.isnan(z[30])
    with pytest.raises(ValueError, match="two earlier values"):
        expanding_z(x, 1)


# --------------------------------------------------------------- valuation

def test_a_monthly_value_waits_for_its_lag():
    s = pd.Series([1.0, 2.0], index=pd.to_datetime(["2020-03-01", "2020-04-01"]))
    assert list(known_from(s, 1).index) == list(pd.to_datetime(["2020-05-01", "2020-06-01"]))
    with pytest.raises(ValueError, match="negative"):
        known_from(s, -1)

    # Change one month of each series: CAPE first moves exactly ℓ + 1 months on.
    world = shiller_world()
    base = real_time_cape(world["price"], world["earnings"], world["cpi"])
    month = pd.Timestamp("1900-06-01")
    for key, lag in (("price", 0), ("cpi", 1), ("earnings", 4)):
        moved = {**world, key: world[key].mask(world[key].index == month, world[key] * 1.5)}
        cape = real_time_cape(moved["price"], moved["earnings"], moved["cpi"])
        changed = cape.index[~np.isclose(cape, base.reindex(cape.index))]
        assert changed[0] == month + pd.DateOffset(months=lag + 1), key


def test_cape_without_lags_is_shillers_formula_and_ecy_its_definition():
    world = shiller_world()
    p, e, c, g = world["price"], world["earnings"], world["cpi"], world["gs10"]
    cape = real_time_cape(p, e, c, NO_LAGS)
    for n in (130, 200, 479):
        last = p.index[n - 1]                                   # month N − 1, known at N
        real_e = (e / c).iloc[n - 120:n].mean()
        assert cape[p.index[n]] == pytest.approx((p[last] / c[last]) / real_e, rel=1e-12)

    ecy = excess_cape_yield(p, e, c, g)                        # with the real lags
    at = p.index[400]
    k = 400 - 1 - LAGS["earnings"]
    cpi_month = 400 - 1 - LAGS["cpi"]
    cape_n = (p.iloc[399] / c.iloc[cpi_month]) / (e / c).iloc[k - 119:k + 1].mean()
    inflation = (c.iloc[cpi_month] / c.iloc[cpi_month - 120]) ** 0.1 - 1
    assert ecy[at] == pytest.approx(1 / cape_n - (g.iloc[399] / 100 - inflation), rel=1e-12)
    assert ecy.index[0] == p.index[0] + pd.DateOffset(months=120 + LAGS["earnings"])


# --------------------------------------------------------------- the bond

def test_a_par_bonds_month_closed_forms():
    c = np.array([0.02, 0.05, 0.09])
    assert bond_return(c, c) == pytest.approx(c / 12, rel=1e-12)   # unmoved: the coupon
    dy = 1e-4
    t = 10 - 1 / 12
    duration = (1 - (1 + c) ** -t) / c
    assert bond_return(c, c + dy) == pytest.approx(c / 12 - duration * dy, abs=1e-6)
    with pytest.raises(ValueError, match="positive"):
        bond_return([0.03], [0.0])


def test_the_correlation_is_pearsons_on_each_window():
    world = shiller_world()
    p, d, g = world["price"], world["dividend"], world["gs10"]
    rho = stock_bond_correlation(p, d, g, window=36)
    stock = (p + d.shift(LAGS["dividend"]) / 12) / p.shift(1) - 1
    y = g / 100
    bond = pd.Series(bond_return(y.shift(1).iloc[1:], y.iloc[1:]), index=y.index[1:])
    for m in (60, 200, 479):
        window = slice(m - 35, m + 1)
        expected = np.corrcoef(stock.iloc[window], bond.reindex(stock.index).iloc[window])[0, 1]
        assert rho[p.index[m] + pd.DateOffset(months=1)] == pytest.approx(expected, rel=1e-9)
    # The first return needs D four months back; 36 of them end at month 39.
    assert rho.index[0] == p.index[LAGS["dividend"] + 35] + pd.DateOffset(months=1)


def _yield_after(coupon: float, target: float) -> float:
    """The yield at which a par bond bought at ``coupon`` returns ``target``
    over the month: bisection, since its price falls as the yield rises."""
    low, high = 1e-6, 1.0
    for _ in range(80):
        mid = 0.5 * (low + high)
        low, high = (mid, high) if bond_return([coupon], [mid])[0] > target else (low, mid)
    return 0.5 * (low + high)


def test_the_correlation_recovers_a_planted_rho():
    # Plant the returns themselves, bivariate normal with ρ = 0.5, and back
    # out the yields that give the bond's: the function must find ρ.
    rng = np.random.default_rng(4)
    months, window, rho_true = 1000, 900, 0.5
    index = pd.date_range("1800-01-01", periods=months, freq="MS")
    shocks = rng.multivariate_normal([0, 0], [[1, rho_true], [rho_true, 1]], months)
    price = pd.Series(100 * np.cumprod(1 + 0.04 * shocks[:, 0]), index=index)
    yields = [0.08]
    for m in range(1, months):
        yields.append(_yield_after(yields[-1], yields[-1] / 12 + 0.003 * shocks[m, 1]))
    rho = stock_bond_correlation(price, pd.Series(0.0, index=index),
                                 pd.Series(100 * np.array(yields), index=index), window=window)
    assert abs(math.atanh(rho.iloc[-1]) - math.atanh(rho_true)) < 3 / math.sqrt(window - 3)


# ------------------------------------------------------------------- daily

def test_the_daily_inputs_match_their_definitions():
    total, bills = market_world(years=3)
    at = total.index[600]
    r = np.log1p(total)
    excess = trend_excess(total, bills)
    assert excess[at] == pytest.approx(float((r - np.log1p(bills)).loc[:at].iloc[-252:].sum()))
    level = np.exp(r.cumsum())
    average = trend_average(total)
    assert average[at] == pytest.approx(float(level[at] / level.loc[:at].iloc[-200:].mean() - 1))
    vol = realized_vol(total)
    assert vol[at] == pytest.approx(math.sqrt(252 * float((r.loc[:at].iloc[-21:] ** 2).mean())))
    assert excess.index[0] == total.index[251] and vol.index[0] == total.index[20]


def test_the_walk_forward_refits_each_january_on_the_years_before():
    total, _ = market_world(years=6)
    walk = walk_forward(total, years=2, horizon=21, regimes=2)
    firsts = [total.index[total.index.year == y][0] for y in range(2012, 2016)]
    assert [r.day for r in walk.refits] == firsts
    assert walk.garch_vol.index[0] == walk.calm.index[0] == firsts[0]   # nothing before

    # The 2013 refit: parameters from [2011-01-03, 2013-01-01) only, run from
    # the window's start; its value on the refit day matches by hand.
    refit = walk.refits[1]
    r = np.log1p(total)
    window = r[(r.index >= refit.window_start) & (r.index < refit.day)]
    assert refit.window_start == r.index[r.index >= pd.Timestamp("2011-01-01")][0]
    assert refit.observations == len(window) and not refit.problem
    garch = fit_garch11(window.to_numpy()).parameters
    run = r[(r.index >= refit.window_start) & (r.index <= refit.day)].to_numpy()
    h = conditional_variances(run, garch)
    following = garch.omega + garch.alpha * (run[-1] - garch.mean) ** 2 + garch.beta * h[-1]
    p, long_run = garch.persistence, garch.unconditional_variance
    average = long_run + (following - long_run) * (1 - p**21) / (21 * (1 - p))
    assert walk.garch_vol[refit.day] == pytest.approx(math.sqrt(252 * average), rel=1e-12)
    model = fit_hmm(window.to_numpy(), 2).model
    alpha = hmm_filter(model, run).filtered[-1, int(np.argmin(model.sigmas))]
    assert walk.calm[refit.day] == pytest.approx(alpha, rel=1e-12)


# --------------------------------------------------------------- look-ahead

def _everything(total, bills, world):
    walk = walk_forward(total, years=2)
    raw = {
        "realized": realized_vol(total), "excess": trend_excess(total, bills),
        "average": trend_average(total), "garch": walk.garch_vol, "calm": walk.calm,
        "cape": real_time_cape(world["price"], world["earnings"], world["cpi"]),
        "ecy": excess_cape_yield(world["price"], world["earnings"], world["cpi"],
                                 world["gs10"]),
        "rho": stock_bond_correlation(world["price"], world["dividend"], world["gs10"]),
    }
    return {k: RegimeInput.build(k, k, "", "", v, 20) for k, v in raw.items()}


def test_nothing_later_changes_an_earlier_value():
    total, bills = market_world(years=6)
    world = shiller_world()
    cut_day, cut_month = pd.Timestamp("2014-07-15"), pd.Timestamp("1890-03-01")
    full = _everything(total, bills, world)
    short = _everything(total[:cut_day], bills[:cut_day],
                        {k: v[:cut_month] for k, v in world.items()})
    for name, item in short.items():
        assert len(item.raw) > 20, name
        for scale in ("raw", "percentile", "z"):
            early, late = getattr(item, scale), getattr(full[name], scale)
            assert np.allclose(early, late.reindex(early.index), rtol=0, atol=0,
                               equal_nan=True), (name, scale)


def test_on_days_carries_the_latest_known_value():
    raw = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2020-01-01", "2020-02-01",
                                                           "2020-03-01"]))
    item = RegimeInput.build("x", "x", "monthly", "", raw, 2)
    days = pd.to_datetime(["2019-12-31", "2020-01-31", "2020-02-03", "2020-03-02"])
    frame = item.on_days(days)
    assert np.isnan(frame["raw"].iloc[0])
    assert list(frame["raw"].iloc[1:]) == [1.0, 2.0, 3.0]
    assert frame["percentile"].iloc[-1] == 1.0               # 3 above both earlier
