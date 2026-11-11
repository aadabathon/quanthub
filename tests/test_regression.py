"""Regressions of daily returns (W2), following the module docstring's
validation plan: hand cases, invariance, the Bartlett form, coverage against
the closed forms, Dimson, rolling, residual correlation, the portfolio
identity, rejections."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.portfolio import MIN_DAYS, TRADING_DAYS
from quant_hub.models.regression import (
    ALPHA,
    CLASSICAL,
    METHODS,
    NEWEY_WEST,
    WHITE,
    design,
    lag_name,
    newey_west_lags,
    ols,
    regress_returns,
    residual_correlation,
    rolling_regression,
)

Z = NormalDist().inv_cdf(0.975)


def line(T=300, alpha=0.001, beta=1.2, noise=0.01, seed=0):
    rng = np.random.default_rng(seed)
    f = rng.normal(0.0005, 0.01, T)
    y = alpha + beta * f + rng.normal(0, noise, T)
    return y, np.column_stack([np.ones(T), f])


def covers(estimate, truth, se):
    return abs(estimate - truth) <= Z * se


# ------------------------------------------------------------------ hand cases
class TestHandCases:
    def test_coefficients_are_the_normal_equations_and_polyfit(self):
        y, X = line()
        fit = ols(y, X, (ALPHA, "f"))
        assert np.allclose(fit.coef, np.linalg.solve(X.T @ X, X.T @ y), atol=1e-12)
        slope, intercept = np.polyfit(X[:, 1], y, 1)
        assert fit.value("f") == pytest.approx(slope) and fit.value(ALPHA) == pytest.approx(intercept)

    def test_one_factor_beta_is_cov_over_var_and_matches_raw_returns(self):
        rng = np.random.default_rng(1)
        idx = pd.bdate_range("2025-01-01", periods=250)
        f = pd.Series(rng.normal(0.0004, 0.01, 250), idx)
        y = 0.0002 + 0.7 * f + rng.normal(0, 0.008, 250)
        rate = 0.04
        fit = regress_returns(pd.DataFrame({"Y": y}), pd.DataFrame({"SPY": f}), rate)["Y"]
        # The Portfolio tile's beta: Cov/Var of raw returns. Excess returns
        # move only the intercept, by (1 - beta) r/252.
        beta_tile = np.cov(y, f)[0, 1] / np.var(f, ddof=1)
        assert fit.value("SPY") == pytest.approx(beta_tile, abs=1e-12)
        raw = ols(y.to_numpy(), np.column_stack([np.ones(250), f]), (ALPHA, "SPY"))
        assert raw.value(ALPHA) == pytest.approx(
            fit.value(ALPHA) + (1 - fit.value("SPY")) * rate / TRADING_DAYS, abs=1e-12)

    def test_r2_residual_sd_and_annual_units(self):
        y, X = line(noise=0.02)
        fit = ols(y, X, (ALPHA, "f"))
        e = y - X @ fit.coef
        assert fit.r2 == pytest.approx(1 - e @ e / np.sum((y - y.mean())**2))
        assert fit.residual_sd == pytest.approx(math.sqrt(e @ e / (len(y) - 2)))
        assert fit.alpha_annual == pytest.approx(252 * fit.value(ALPHA))
        assert fit.residual_vol_annual == pytest.approx(fit.residual_sd * math.sqrt(252))
        assert fit.alpha_error_annual(WHITE) == pytest.approx(252 * fit.error(ALPHA, WHITE))


# ----------------------------------------------------------------- invariance
def test_recombining_factors_changes_nothing_fitted():
    rng = np.random.default_rng(4)
    T = 400
    F = rng.standard_normal((T, 2)) @ np.array([[1, 0.9], [0, 0.4]])
    X = np.column_stack([np.ones(T), F])
    y = X @ [0.1, 1.0, 0.3] + rng.standard_normal(T)
    A = np.array([[1, 0, 0], [0, 1, -1], [0, 0, 1.0]])           # (f1, f2) -> (f1, f2 - f1)
    a, b = ols(y, X), ols(y, X @ A)
    inv = np.linalg.inv(A)
    assert np.allclose(X @ a.coef, (X @ A) @ b.coef, atol=1e-12)
    assert b.r2 == pytest.approx(a.r2, abs=1e-12)
    assert np.allclose(b.coef, inv @ a.coef, atol=1e-12)
    for method in METHODS:
        assert np.allclose(b.covariance[method], inv @ a.covariance[method] @ inv.T, atol=1e-12)


# ------------------------------------------------------------------- Bartlett
class TestBartlett:
    def test_the_long_run_covariance_is_a_sum_of_block_outer_products(self):
        rng = np.random.default_rng(1)
        g = rng.standard_normal((300, 3)) * rng.exponential(1, (300, 1))
        L = 5
        S = g.T @ g
        for lag in range(1, L + 1):
            G = g[lag:].T @ g[:-lag]
            S += (1 - lag / (L + 1)) * (G + G.T)
        pad = np.vstack([np.zeros((L, 3)), g, np.zeros((L, 3))])
        blocks = np.array([pad[s:s + L + 1].sum(0) for s in range(len(g) + L)])
        assert np.allclose(S, blocks.T @ blocks / (L + 1), atol=1e-10)

    def test_covariances_are_psd_and_no_lags_is_white(self):
        rng = np.random.default_rng(2)
        for seed in range(20):
            y, X = line(T=120, seed=seed)
            y = y * rng.exponential(1, len(y))                   # wild heteroskedasticity
            fit = ols(y, X, lags=7)
            for method in METHODS:
                assert np.linalg.eigvalsh(fit.covariance[method]).min() >= -1e-18
        y, X = line()
        assert np.allclose(ols(y, X, lags=0).covariance[NEWEY_WEST],
                           ols(y, X).covariance[WHITE])

    def test_the_lag_rule(self):
        assert [newey_west_lags(T) for T in (252, 504, 1260)] == [4, 5, 7]


# ------------------------------------------------------------------- coverage
def coverage(simulate, index, truth, reps=3000, T=504, seed=0):
    """Share of 95% intervals covering the truth, by method."""
    rng = np.random.default_rng(seed)
    lags = newey_west_lags(T)
    hits = dict.fromkeys(METHODS, 0)
    for _ in range(reps):
        y, X = simulate(T, rng)
        fit = ols(y, X, lags=lags)
        for method in METHODS:
            hits[method] += covers(fit.coef[index], truth, fit.standard_errors(method)[index])
    return {method: count / reps for method, count in hits.items()}


def band(p, reps=3000, k=3.0):
    return k * math.sqrt(p * (1 - p) / reps)


class TestCoverage:
    def test_iid_errors_all_three_cover(self):
        def world(T, rng):
            f = rng.standard_normal(T)
            return 0.1 + 0.9 * f + rng.standard_normal(T), np.column_stack([np.ones(T), f])
        for method, share in coverage(world, 1, 0.9, seed=9).items():
            assert share == pytest.approx(0.95, abs=band(0.95)), method

    def test_variance_regimes_classical_beta_follows_the_closed_form(self):
        share, high = 0.2, 9.0                                   # 20% of days at 9x variance
        rho = ((1 - share) + share * high**2) / ((1 - share) + share * high)**2
        expected = 2 * NormalDist().cdf(Z / math.sqrt(rho)) - 1

        def world(T, rng):
            h = np.ones(T)
            h[rng.permutation(T)[:round(share * T)]] = high
            f = np.sqrt(h) * rng.standard_normal(T)
            e = 0.8 * np.sqrt(h) * rng.standard_normal(T)
            return 1.2 * f + e, np.column_stack([np.ones(T), f])
        result = coverage(world, 1, 1.2, seed=11)
        assert expected == pytest.approx(0.784, abs=1e-3)
        assert result[CLASSICAL] == pytest.approx(expected, abs=band(expected))
        assert result[WHITE] == pytest.approx(0.95, abs=band(0.95))
        assert result[NEWEY_WEST] == pytest.approx(0.95, abs=band(0.95))

    def test_autocorrelated_residuals_need_newey_west_for_alpha(self):
        phi = 0.3
        expected = 2 * NormalDist().cdf(Z * math.sqrt((1 - phi) / (1 + phi))) - 1

        def world(T, rng):
            eta = rng.standard_normal(T)
            e = np.empty(T)
            e[0] = eta[0] / math.sqrt(1 - phi**2)
            for t in range(1, T):
                e[t] = phi * e[t - 1] + eta[t]
            f = rng.standard_normal(T)
            return f + e, np.column_stack([np.ones(T), f])
        result = coverage(world, 0, 0.0, seed=8)
        assert result[WHITE] == pytest.approx(expected, abs=band(expected))
        assert 0.92 <= result[NEWEY_WEST] <= 0.975


# --------------------------------------------------------------------- Dimson
def test_dimson_sum_beta_recovers_a_stale_price():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2020-01-01", periods=2001)
    f = pd.Series(rng.normal(0, 0.01, 2001), idx)
    y = 0.6 * f + 0.4 * f.shift(1) + rng.normal(0, 0.005, 2001)
    returns, factors = pd.DataFrame({"Y": y.iloc[1:]}), pd.DataFrame({"SPY": f.iloc[1:]})
    plain = regress_returns(returns, factors, 0.0)["Y"]
    lagged = regress_returns(returns, factors, 0.0, dimson_lags=1)["Y"]
    assert plain.value("SPY") == pytest.approx(0.6, abs=3 * plain.error("SPY"))
    total, error = lagged.total(("SPY", lag_name("SPY", 1)))
    assert total == pytest.approx(1.0, abs=3 * error)
    assert lagged.observations == plain.observations - 1


# -------------------------------------------------------------------- rolling
class TestRolling:
    @pytest.fixture
    def data(self):
        rng = np.random.default_rng(5)
        idx = pd.bdate_range("2024-01-01", periods=300)
        f = pd.DataFrame({"SPY": rng.normal(0, 0.01, 300)}, idx)
        y = pd.Series(0.9 * f["SPY"].to_numpy() + rng.normal(0, 0.01, 300), idx)
        return y, design(f)

    def test_each_window_is_the_regression_on_its_slice(self, data):
        y, X = data
        roll = rolling_regression(y, X, 126)
        assert len(roll) == 300 - 126 + 1 and roll.index[0] == y.index[125]
        fit = ols(y.iloc[100:226], X.iloc[100:226], tuple(X.columns), newey_west_lags(126))
        row = roll.loc[y.index[225]]
        assert row["SPY"] == pytest.approx(fit.value("SPY"))
        assert row["SPY se"] == pytest.approx(fit.error("SPY", NEWEY_WEST))

    def test_a_rolling_sum_of_coefficients(self, data):
        y, X = data
        X = X.assign(**{lag_name("SPY", 1): X["SPY"].shift(1)}).iloc[1:]
        y = y.iloc[1:]
        names = ("SPY", lag_name("SPY", 1))
        roll = rolling_regression(y, X, 126, total=names)
        fit = ols(y.iloc[:126], X.iloc[:126], tuple(X.columns), newey_west_lags(126))
        assert tuple(roll.iloc[0][["total", "total se"]]) == pytest.approx(fit.total(names))

    def test_the_future_cannot_change_the_past(self, data):
        y, X = data
        before = rolling_regression(y, X, 126)
        shocked = y.copy()
        shocked.iloc[200:] *= -5.0
        after = rolling_regression(shocked, X, 126)
        assert before.loc[:y.index[199]].equals(after.loc[:y.index[199]])
        assert not before.loc[y.index[200]:].equals(after.loc[y.index[200]:])


# -------------------------------------------------------- residual correlation
def test_fisher_intervals_cover_under_independence():
    rng = np.random.default_rng(6)
    reps, hits = 2000, 0
    for _ in range(reps):
        frame = pd.DataFrame(rng.standard_normal((504, 2)), columns=["A", "B"])
        corr, lower, upper = residual_correlation(frame)
        hits += lower.loc["A", "B"] <= 0.0 <= upper.loc["A", "B"]
        assert corr.loc["A", "A"] == lower.loc["A", "A"] == upper.loc["A", "A"] == 1.0
    assert hits / reps == pytest.approx(0.95, abs=band(0.95, reps))


# --------------------------------------------------------- portfolio identity
def test_the_portfolio_coefficients_are_the_weighted_holdings():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2024-01-01", periods=400)
    f = pd.DataFrame({"SPY": rng.normal(0, 0.01, 400), "TLT": rng.normal(0, 0.008, 400)}, idx)
    holdings = pd.DataFrame({s: b * f["SPY"] + rng.normal(0, 0.01, 400)
                             for s, b in (("A", 1.3), ("B", 0.4), ("C", 0.9))}, idx)
    w = pd.Series({"A": 0.5, "B": 0.3, "C": 0.1})                 # 10% cash
    fits = regress_returns(holdings, f, 0.04)
    # The portfolio's excess return is the weighted holdings' plus cash's
    # share, so regress it with cash at its own weight 0.1.
    portfolio = (holdings * w).sum(axis=1) + (1 - w.sum()) * 0.04 / 252
    fit = regress_returns(pd.DataFrame({"P": portfolio}), f, 0.04)["P"]
    for i in range(3):
        assert fit.coef[i] == pytest.approx(sum(w[s] * fits[s].coef[i] for s in w.index),
                                            abs=1e-12)


# ----------------------------------------------------------------- rejections
class TestRejections:
    def test_bad_inputs(self):
        y, X = line(T=100)
        with pytest.raises(ValueError, match="collinear"):
            ols(y, np.column_stack([X, X[:, 1]]))
        with pytest.raises(ValueError, match="can't fit"):
            ols(y[:2], X[:2])
        with pytest.raises(ValueError, match="lags"):
            ols(y, X, lags=100)
        with pytest.raises(ValueError, match="finite"):
            ols(np.r_[np.nan, y[1:]], X)
        with pytest.raises(ValueError, match="names"):
            ols(y, X, ("only one",))

    def test_too_few_days_and_short_windows(self):
        idx = pd.bdate_range("2025-01-01", periods=MIN_DAYS - 1)
        frame = pd.DataFrame({"X": np.linspace(-0.01, 0.01, len(idx))}, idx)
        with pytest.raises(ValueError, match=f"needs {MIN_DAYS}"):
            regress_returns(frame, frame.rename(columns={"X": "SPY"}), 0.0)
        y, X = line(T=200)
        with pytest.raises(ValueError, match="at least"):
            rolling_regression(pd.Series(y), pd.DataFrame(X), MIN_DAYS - 1)
