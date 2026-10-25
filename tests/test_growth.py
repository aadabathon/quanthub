"""Growth and streams (W3), following the module docstring's validation plan:
attribution, margin, the closed form, sizing, coverage, orthogonal streams,
the approximation, options, rejections."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.black_scholes import price_array
from quant_hub.models.growth import (
    ADDS,
    COSTS,
    OVERSIZED,
    UNDERSIZED,
    SleeveGrowth,
    book_greeks,
    growth_report,
    what_if,
)
from quant_hub.models.portfolio import (
    MIN_DAYS,
    TRADING_DAYS,
    Exposures,
    OptionExposure,
    correlated_returns,
    risk_report,
)

R = 0.04
D = TRADING_DAYS
# The derivation's hedge world: a core, a hedge (negative carry, correlation
# -0.5 with the core) and an orthogonal stream, all annual.
MU = np.array([0.08, -0.01, 0.06])
VOL = np.array([0.18, 0.25, 0.20])
CORR = np.array([[1, -0.5, 0.0], [-0.5, 1, 0.0], [0.0, 0.0, 1]])
W = np.array([0.7, 0.1, 0.15])
NAMES = ["core", "hedge", "stream"]


def stock_excess(returns: np.ndarray, w=W, names=NAMES) -> pd.DataFrame:
    idx = pd.bdate_range("2024-01-02", periods=len(returns))
    return pd.DataFrame(w * (returns - R / D), index=idx, columns=names)


def world(T=504, seed=0):
    return correlated_returns(VOL / math.sqrt(D), CORR, T, MU / D, seed=seed)


def book_with_a_put(T=504, seed=1):
    """A real replay: two stocks and a protective put, through risk_report."""
    rets = correlated_returns(np.array([0.012, 0.015]), np.array([[1, 0.4], [0.4, 1]]), T,
                              np.array([0.0004, 0.0003]), seed=seed)
    returns = pd.DataFrame(rets, index=pd.bdate_range("2024-01-02", periods=T),
                           columns=["AAA", "BBB"])
    put = OptionExposure("AAA", "put", 90.0, 60.0, 5.0, 100.0, 0.25, R)
    exposures = Exposures(100_000.0, {"AAA": 50_000.0, "BBB": 30_000.0}, (put,))
    report = risk_report(exposures, returns, None, R)
    E = exposures.equity
    excess = report.position_pnl / E - (report.position_value / E) * R / D
    return report, excess


# ----------------------------------------------------------------- the replay
def test_the_replay_by_position_sums_to_the_whole():
    report, _ = book_with_a_put()
    assert np.allclose(report.position_pnl.sum(axis=1), report.pnl, atol=1e-9)
    assert list(report.position_pnl.columns) == ["AAA", "BBB", "AAA 90 put 60d"]
    assert report.position_underlying["AAA 90 put 60d"] == "AAA"
    assert report.position_value["AAA 90 put 60d"] > 0                 # a long premium


def test_an_options_own_value_is_its_value_share():
    priced = OptionExposure("AAA", "put", 90.0, 60.0, 5.0, 100.0, 0.25, R)
    held = OptionExposure("AAA", "put", 90.0, 60.0, 5.0, 100.0, 0.25, R, value=1234.0)
    assert held.market_value() == 1234.0 != priced.market_value()
    rets = pd.DataFrame(correlated_returns(np.array([0.01]), np.eye(1), 100, np.zeros(1), seed=2),
                        columns=["AAA"])
    report = risk_report(Exposures(10_000.0, {"AAA": 5_000.0}, (held,)), rets, None, R)
    assert report.position_value["AAA 90 put 60d"] == 1234.0


def test_identical_options_get_unique_labels():
    put = OptionExposure("AAA", "put", 90.0, 60.0, 5.0, 100.0, 0.25, R)
    rets = pd.DataFrame(correlated_returns(np.array([0.01]), np.eye(1), 100, np.zeros(1), seed=2),
                        columns=["AAA"])
    report = risk_report(Exposures(10_000.0, {"AAA": 5_000.0}, (put, put)), rets, None, R)
    assert list(report.position_pnl.columns) == ["AAA", "AAA 90 put 60d", "AAA 90 put 60d #2"]


# ----------------------------------------------------------------- attribution
def test_attribution_is_exact_with_options_in_the_book():
    _, excess = book_with_a_put()
    report = growth_report(excess, R)
    assert sum(s.contribution for s in report.sleeves) == pytest.approx(report.growth - R,
                                                                         abs=1e-12)
    grouped = growth_report(excess, R, {"AAA and its put": ["AAA", "AAA 90 put 60d"],
                                        "BBB": ["BBB"]})
    assert grouped.growth == pytest.approx(report.growth, abs=1e-15)
    assert sum(s.contribution for s in grouped.sleeves) == pytest.approx(grouped.growth - R,
                                                                          abs=1e-12)


def test_the_margin_is_the_derivative_of_scaling_a_position():
    _, excess = book_with_a_put()
    report = growth_report(excess, R)
    eps = 1e-6
    for name in ("AAA", "AAA 90 put 60d"):
        up, down = excess.copy(), excess.copy()
        up[name] *= 1 + eps
        down[name] *= 1 - eps
        numeric = (growth_report(up, R).growth - growth_report(down, R).growth) / (2 * eps)
        assert report.sleeve(name).margin == pytest.approx(numeric, rel=1e-6)


# ----------------------------------------------------------------- closed form
def test_stocks_reduce_to_the_weight_formulas_at_sample_moments():
    returns = world()
    report = growth_report(stock_excess(returns), R)
    mu_hat = returns.mean(axis=0) * D
    sigma_hat = np.cov(returns, rowvar=False, ddof=1) * D
    assert report.growth == pytest.approx(R + W @ (mu_hat - R) - 0.5 * W @ sigma_hat @ W,
                                          abs=1e-12)
    sw = sigma_hat @ W
    for i, s in enumerate(report.sleeves):
        assert s.contribution == pytest.approx(W[i] * ((mu_hat[i] - R) - 0.5 * sw[i]), abs=1e-12)
        assert s.margin / W[i] == pytest.approx((mu_hat[i] - R) - sw[i], abs=1e-12)


# --------------------------------------------------------------------- sizing
class TestSizing:
    @pytest.fixture
    def report(self):
        return growth_report(stock_excess(world(T=2520, seed=3)), R)

    def test_growth_optimal_size_beats_a_grid(self, report):
        hedge = report.sleeve("hedge")
        grid = np.linspace(hedge.c_star - 5, hedge.c_star + 5, 2001)
        best = grid[np.argmax([what_if(report, "hedge", c)[0] for c in grid])]
        assert best == pytest.approx(hedge.c_star, abs=0.006)

    def test_growth_optimal_is_min_variance_plus_its_own_kelly(self, report):
        e = report.excess["hedge"]
        for s in report.sleeves:
            series = report.excess[s.name]
            assert s.c_star == pytest.approx(s.c_mv + series.mean() / series.var(ddof=1),
                                             abs=1e-9)
        # And c_mv really minimizes the vol.
        hedge = report.sleeve("hedge")
        vol_at = report.vol + what_if(report, "hedge", hedge.c_mv)[1]
        assert vol_at == pytest.approx(hedge.vol_at_c_mv, abs=1e-12)
        for c in (hedge.c_mv - 0.1, hedge.c_mv + 0.1):
            assert report.vol + what_if(report, "hedge", c)[1] > vol_at
        assert e.var() > 0

    def test_fractional_sizing_keeps_f_two_minus_f(self, report):
        s = report.sleeve("stream")

        def gain(c):                      # growth relative to holding none of it
            return what_if(report, "stream", c)[0] - what_if(report, "stream", 0.0)[0]
        for f in (0.25, 0.5, 0.75):
            assert gain(f * s.c_star) / gain(s.c_star) == pytest.approx(f * (2 - f), abs=1e-9)


# ------------------------------------------------------------------- coverage
def test_both_sizes_cover_and_the_growth_optimum_is_far_noisier():
    S = np.outer(VOL, VOL) * CORR
    wH = np.array([0, W[1], 0])
    wC = W - wH
    var_h, cov = wH @ S @ wH, wH @ S @ wC
    c_mv_true = -cov / var_h
    c_star_true = (wH @ (MU - R) - cov) / var_h
    reps, hits_mv, hits_star, ratio = 1500, 0, 0, []
    for seed in range(reps):
        hedge = growth_report(stock_excess(world(T=504, seed=100 + seed)), R).sleeve("hedge")
        hits_mv += abs(hedge.c_mv - c_mv_true) <= 1.96 * hedge.c_mv_error
        hits_star += abs(hedge.c_star - c_star_true) <= 1.96 * hedge.c_star_error
        ratio.append(hedge.c_star_error / hedge.c_mv_error)
    band = 3 * math.sqrt(0.95 * 0.05 / reps)
    assert hits_mv / reps == pytest.approx(0.95, abs=band)
    assert hits_star / reps == pytest.approx(0.95, abs=band)
    assert np.median(ratio) > 10


# ---------------------------------------------------------- orthogonal streams
def test_orthogonal_streams_each_add_half_their_squared_sharpe():
    rng = np.random.default_rng(4)
    T = 1000
    raw = rng.standard_normal((T, 3))
    raw -= raw.mean(axis=0)
    q, _ = np.linalg.qr(raw)                                    # exactly orthogonal columns
    daily_vol = VOL / math.sqrt(D)
    x = q / q.std(axis=0, ddof=1) * daily_vol + (MU - R) / D     # sample means = (MU - R)/D
    excess = pd.DataFrame(x, index=pd.bdate_range("2023-01-02", periods=T), columns=NAMES)
    first = growth_report(excess, R)
    sizes = {s.name: s.c_star for s in first.sleeves}
    best = growth_report(excess * pd.Series(sizes), R)
    sharpe = (MU - R) / VOL
    assert best.growth == pytest.approx(R + 0.5 * np.sum(sharpe**2), abs=1e-12)


# -------------------------------------------------------------- approximation
def test_the_quadratic_matches_realized_log_growth_on_stock_samples():
    for seed in range(5):
        log_returns = world(T=504, seed=20 + seed)
        simple = np.expm1(log_returns)
        report = growth_report(stock_excess(simple), R)
        assert report.realized == pytest.approx(report.growth, abs=1e-4)    # 0.01% a year
        assert report.cagr == pytest.approx(math.expm1(report.growth))


# --------------------------------------------------------------------- options
class TestOptions:
    def test_a_position_scales_exactly_with_contracts(self):
        one = OptionExposure("AAA", "put", 90.0, 60.0, 1.0, 100.0, 0.25, R)
        three = OptionExposure("AAA", "put", 90.0, 60.0, 3.0, 100.0, 0.25, R)
        moves = np.linspace(-0.1, 0.1, 21)
        assert np.allclose(three.scenario_pnl(moves), 3 * one.scenario_pnl(moves))

    def test_book_greeks_are_leg_sums_and_match_repricing(self):
        legs = (OptionExposure("AAA", "put", 90.0, 60.0, 4.0, 100.0, 0.25, R),
                OptionExposure("AAA", "call", 110.0, 30.0, -2.0, 100.0, 0.25, R),
                OptionExposure("BBB", "call", 50.0, 90.0, 3.0, 48.0, 0.35, R))
        book = book_greeks(legs)
        parts = [book_greeks((leg,)) for leg in legs]
        for field in ("delta", "gamma_move", "vega", "theta"):
            assert getattr(book, field) == pytest.approx(sum(getattr(p, field) for p in parts))
        assert book.carry == pytest.approx(365 * book.theta) and book.contracts == 3

        def value(spot_scale=1.0, vol_shift=0.0, days_shift=0.0):
            return sum(o.contracts * o.multiplier * float(price_array(
                o.spot * spot_scale, o.strike, o.days + days_shift, o.rate, o.vol + vol_shift,
                o.option_type)) for o in legs if o.underlying == "AAA")
        aaa = book_greeks(legs[:2])
        h = 1e-4
        delta = (value(1 + h) - value(1 - h)) / (2 * h)                  # dV/dS times S
        assert aaa.delta == pytest.approx(delta, rel=1e-5)
        gamma = (value(1 + h) - 2 * value() + value(1 - h)) / h**2 * 0.01**2 / 2
        assert aaa.gamma_move == pytest.approx(gamma, rel=1e-3)
        assert aaa.vega == pytest.approx((value(vol_shift=0.005) - value(vol_shift=-0.005)),
                                         rel=1e-3)
        assert aaa.theta == pytest.approx((value(days_shift=-0.5) - value(days_shift=0.5)),
                                          rel=1e-3)


# ------------------------------------------------------------------- verdicts
@pytest.mark.parametrize("contribution, margin, verdict", [
    (0.01, 0.01, ADDS), (0.01, -0.01, OVERSIZED), (-0.01, 0.01, UNDERSIZED),
    (-0.01, -0.01, COSTS)])
def test_the_four_verdicts(contribution, margin, verdict):
    s = SleeveGrowth("x", ("x",), 0.1, 0.2, contribution, margin, 0.0, 0.005, *([np.nan] * 5))
    assert s.verdict == verdict
    assert s.significant == (abs(margin) > 1.96 * 0.005)


# ----------------------------------------------------------------- rejections
class TestRejections:
    def test_bad_inputs(self):
        good = stock_excess(world(T=200))
        with pytest.raises(ValueError, match="column per position"):
            growth_report(pd.DataFrame(index=good.index), R)
        bad = good.copy()
        bad.iloc[3, 0] = np.nan
        with pytest.raises(ValueError, match="finite"):
            growth_report(bad, R)
        with pytest.raises(ValueError, match=f"needs {MIN_DAYS}"):
            growth_report(good.iloc[:MIN_DAYS - 1], R)
        with pytest.raises(ValueError, match="unknown positions"):
            growth_report(good, R, {"x": ["nope"]})
        with pytest.raises(ValueError, match="more than one sleeve"):
            growth_report(good, R, {"a": ["core"], "b": ["core"]})
        with pytest.raises(ValueError, match="at least one position"):
            growth_report(good, R, {"a": []})

    def test_a_sleeve_with_no_variance_has_no_sizes(self):
        good = stock_excess(world(T=200)).assign(flat=0.0)
        flat = growth_report(good, R).sleeve("flat")
        assert math.isnan(flat.c_star) and math.isnan(flat.c_mv)
        assert flat.contribution == 0.0
