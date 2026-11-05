"""Hedges and their yardstick (HEDGE_PLAN U1 part 1, models/hedge.py), on
made-up markets: the unhedged book at h = 0 exactly, timing (decided at t,
held to t + 1), crash windows found by rule, Δ log growth's sign where the
answer is known and its Newey–West error against the truth, cost outside
the windows only, linear cash against convex trend, and refusals. Option
programs (part 2): Black–Scholes marks, settlement at intrinsic, the plan's
worked premium, the collar's parity, losses falling with h, a spread's cap,
Δg's sign for dear and cheap puts, and whole contracts."""

import math

import numpy as np
import pandas as pd
import pytest

from quant_hub.models import hedge as H
from quant_hub.models.black_scholes import price_array


def world(n=2_520, seed=1, drift=0.0003, vol=0.01, rate=0.04):
    """Made-up daily market total returns and steady bills."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2000-01-03", periods=n)
    market = pd.Series(rate / 252 + drift + rng.normal(0, vol, n), index=dates)
    return market, pd.Series(rate / 252, index=dates)


def regimes(n_cycles=8, half=126, seed=2):
    """Alternating half-years of rally and slide, a little noise: trend's world."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(half, 0.002) if i % 2 == 0 else np.full(half, -0.002)
                            for i in range(2 * n_cycles)])
    dates = pd.bdate_range("2000-01-03", periods=len(drift))
    market = pd.Series(drift + rng.normal(0, 0.004, len(drift)), index=dates)
    return market, pd.Series(0.0, index=dates)


# ---------------------------------------------------------------- the book

def test_h_zero_or_w_zero_is_the_book_exactly():
    market, rf = world()
    book = H.beta_book(market, rf, 0.8)
    assert book.to_numpy() == pytest.approx((rf + 0.8 * (market - rf)).to_numpy())
    exposure = H.trend_exposure(market, rf, "sma")
    pd.testing.assert_series_equal(H.scaled(book, rf, exposure, 0.0), book, check_names=False)
    pd.testing.assert_series_equal(H.cash(book, rf, 0.0), book, check_names=False)
    pd.testing.assert_series_equal(H.blend(book, market, 0.0), book, check_names=False)


def test_an_exposure_decided_at_close_t_applies_to_day_t_plus_one():
    market, rf = world(n=10)
    book = H.beta_book(market, rf, 1.0)
    exposure = pd.Series(1.0, index=market.index)
    exposure.iloc[4] = 0.0                                   # out at close 4 only
    hedged = H.scaled(book, rf, exposure, 1.0)
    assert hedged.iloc[4] == book.iloc[4]                    # day 4 itself is held
    assert hedged.iloc[5] == pytest.approx(rf.iloc[5])       # day 5 is in bills
    assert hedged.iloc[6] == book.iloc[6]


def test_cash_and_another_stream_are_daily_mixes():
    market, rf = world()
    book = H.beta_book(market, rf, 0.9)
    assert H.cash(book, rf, 0.3).to_numpy() == pytest.approx((0.7 * book + 0.3 * rf).to_numpy())
    other = market * 0.2
    assert H.blend(book, other, 0.25).to_numpy() == pytest.approx((0.75 * book + 0.25 * other).to_numpy())


# --------------------------------------------------------------- the rules

def test_the_trend_rules_go_out_in_a_slide_and_wait_out_their_warm_up():
    market, rf = regimes()
    sma = H.trend_exposure(market, rf, "sma", window=50)
    assert sma.iloc[:49].eq(1.0).all()                       # warm-up: no hedge
    slide = slice(126 + 60, 252)                             # well into the first slide
    rally = slice(252 + 60, 378)
    assert sma.iloc[slide].mean() < 0.1 and sma.iloc[rally].mean() > 0.9
    momentum = H.trend_exposure(market, rf, "momentum", window=40)
    assert momentum.iloc[slide].mean() < 0.1 and momentum.iloc[rally].mean() > 0.9


def test_vol_targeting_scales_down_only_and_tracks_the_vol():
    rng = np.random.default_rng(4)
    vols = np.concatenate([np.full(500, 0.005), np.full(500, 0.02)])
    dates = pd.bdate_range("2000-01-03", periods=1_000)
    book = pd.Series(rng.normal(0, 1, 1_000) * vols, index=dates)
    e = H.vol_target_exposure(book, target=0.16, window=21)
    assert ((e > 0) & (e <= 1)).all()
    calm, storm = e.iloc[100:480].mean(), e.iloc[600:980].mean()
    assert calm == pytest.approx(1.0)                        # 8% vol: no scaling
    assert storm == pytest.approx(0.16 / (0.02 * math.sqrt(252)), rel=0.1)
    garch = H.vol_target_exposure(book, target=0.16, estimator="garch")
    assert ((garch > 0) & (garch <= 1)).all() and garch.iloc[600:].mean() < garch.iloc[:480].mean()


# ------------------------------------------------------------- yardstick

def test_crash_windows_are_found_by_rule():
    dates = pd.bdate_range("2000-01-03", periods=600)
    r = np.full(600, 0.0008)
    r[100:140] = -0.006                                      # about −21%
    r[500:520] = -0.005                                      # about −10%, after the recovery
    market = pd.Series(r, index=dates)
    (crash,) = H.crash_windows(market)
    assert (crash.peak, crash.trough) == (dates[99].date(), dates[139].date())
    assert crash.depth == pytest.approx(1 - 0.994 ** 40)
    assert len(H.crash_windows(market, depth=0.09)) == 2


def test_cost_counts_only_the_calm_days_and_the_table_counts_the_crash():
    dates = pd.bdate_range("2000-01-03", periods=600)
    r = np.full(600, 0.0008)
    r[100:140] = -0.006
    market = pd.Series(r, index=dates)
    book = market.copy()
    hedged = book.copy()
    hedged.iloc[100:140] = 0.0                               # out exactly through the crash
    a = H.assess(book, hedged, market)
    assert a.cost == pytest.approx(0.0, abs=1e-12)
    (row,) = a.crashes
    assert row.unhedged == pytest.approx(0.994 ** 40 - 1) and row.hedged == pytest.approx(0.0)
    assert row.paid == pytest.approx(1 - 0.994 ** 40) and a.saved == pytest.approx(row.paid)
    assert a.drawdown[1] == pytest.approx(0.0) and a.drawdown[0] == pytest.approx(1 - 0.994 ** 40)


def test_cash_in_a_falling_market_grows_more_and_in_a_rising_one_less():
    for drift, sign in ((-0.0008, 1), (0.0008, -1)):
        market, rf = world(n=20_000, drift=drift, seed=8)
        book = H.beta_book(market, rf, 1.0)
        a = H.assess(book, H.cash(book, rf, 0.5), market)
        assert sign * a.growth > 4 * a.growth_error          # significant, the right way


def test_delta_growth_matches_its_closed_form_and_its_error_the_truth():
    # Cash at w on a book with daily excess x ~ N(μ, s²): d = ln(1 + r + (1−w)x) − ln(1 + r + x).
    market, rf = world(n=50_000, drift=0.0004, vol=0.012, seed=9)
    book = H.beta_book(market, rf, 1.0)
    a = H.assess(book, H.cash(book, rf, 0.4), market)
    d = np.log1p(H.cash(book, rf, 0.4).to_numpy()) - np.log1p(book.to_numpy())
    assert a.growth == pytest.approx(252 * d.mean())
    # d is iid here, so its error is s_d / √T, which Newey–West must recover.
    assert a.growth_error == pytest.approx(252 * d.std(ddof=1) / math.sqrt(len(d)), rel=0.1)


def test_newey_west_widens_the_error_for_autocorrelated_differences():
    rng = np.random.default_rng(12)
    n, phi = 40_000, 0.5
    e = rng.normal(0, 0.001, n)
    d = np.empty(n)
    d[0] = e[0]
    for t in range(1, n):
        d[t] = phi * d[t - 1] + e[t]
    dates = pd.bdate_range("1950-01-03", periods=n)
    book = pd.Series(np.full(n, 0.0002), index=dates)
    hedged = pd.Series(np.expm1(np.log1p(book.to_numpy()) + d), index=dates)
    a = H.assess(book, hedged, book.copy(), windows=())
    truth = 252 * 0.001 / (1 - phi) / math.sqrt(n)            # √(long-run variance / T)
    assert a.growth_error == pytest.approx(truth, rel=0.2)


def test_cash_is_linear_and_trend_is_convex():
    market, rf = regimes(n_cycles=12)
    book = H.beta_book(market, rf, 1.0)
    linear = H.assess(book, H.cash(book, rf, 0.3), market)
    assert linear.beta_up == pytest.approx(-0.3, abs=0.03)
    assert linear.beta_down == pytest.approx(-0.3, abs=0.03)
    trend = H.assess(book, H.scaled(book, rf, H.trend_exposure(market, rf, "sma", 50), 1.0),
                     market)
    assert trend.beta_down < trend.beta_up - 0.3             # pays in falls more than it costs
    assert trend.drawdown[1] < trend.drawdown[0]


def test_the_worst_year_is_the_worst_compounded_252_days():
    market, rf = world(n=600)
    book = H.beta_book(market, rf, 1.0)
    a = H.assess(book, H.cash(book, rf, 0.5), market)
    direct = min(np.prod(1 + book.to_numpy()[i:i + 252]) - 1 for i in range(600 - 251))
    assert a.worst_year[0] == pytest.approx(direct)
    short = H.assess(book.iloc[:100], book.iloc[:100], market.iloc[:100])
    assert short.worst_year == (None, None)


@pytest.mark.parametrize("call, message", [
    (lambda m, r, b: H.scaled(b, r, pd.Series(1.0, index=b.index), 1.5), "between 0 and 1"),
    (lambda m, r, b: H.scaled(b, r, pd.Series(2.0, index=b.index), 0.5), "exposure"),
    (lambda m, r, b: H.blend(b, m.iloc[1:], 0.5), "same days"),
    (lambda m, r, b: H.trend_exposure(m, r, "breakout"), "unknown trend rule"),
    (lambda m, r, b: H.trend_exposure(m, r, "sma", window=1), "window"),
    (lambda m, r, b: H.vol_target_exposure(b, 0.0), "target vol"),
    (lambda m, r, b: H.crash_windows(m, depth=1.5), "fraction"),
    (lambda m, r, b: H.beta_book(m, r, float("nan")), "β must be finite"),
    (lambda m, r, b: H.cash(b.where(b.index != b.index[3], -1.0), r, 0.1), "−100%"),
])
def test_what_cant_be_assessed_is_refused(call, message):
    market, rf = world(n=300)
    book = H.beta_book(market, rf, 1.0)
    with pytest.raises(ValueError, match=message):
        call(market, rf, book)


# --------------------------------------------------------- option programs


def index_world(returns, start="2000-01-03", rate=0.04, vix=0.2):
    """An index with the given daily price returns, its β-1 book (no
    dividends, so the book is the index plus nothing), and flat inputs."""
    dates = pd.bdate_range(start, periods=len(returns))
    r = pd.Series(returns, index=dates, dtype=float)
    spot = 100.0 * (1.0 + r).cumprod()
    return (r, spot, pd.Series(rate, index=dates), pd.Series(vix, index=dates),
            pd.Series(0.0, index=dates))


def test_h_zero_is_the_book_and_one_position_is_marked_at_black_scholes():
    rng = np.random.default_rng(3)
    book, spot, rate, vix, _ = index_world(rng.normal(0.0002, 0.01, 40))
    zero = H.option_program(book, spot, rate, vix, H.OptionProgram(), 0.0, 1.0)
    assert zero.returns.to_numpy() == pytest.approx(book.to_numpy(), abs=1e-15)
    run = H.option_program(book, spot, rate, vix, H.OptionProgram(0.9, 365), 1.0, 1.0)
    assert run.rolls == 1                                    # one put, held throughout
    days = (book.index - book.index[0]).days.to_numpy()
    k, s = 0.9 * spot.iloc[0], spot.to_numpy()
    puts = price_array(s, k, 365 - days, 0.04, 0.2, "put")
    q = (1 + book.iloc[0]) / s[0]
    holdings = ((1 + book.iloc[0]) - q * puts[0]) * np.concatenate(
        ([1.0], np.cumprod(1 + book.to_numpy()[1:])))
    wealth = np.cumprod(1 + run.returns.to_numpy())
    assert wealth == pytest.approx(holdings + q * puts, rel=1e-12)


def test_a_put_held_through_a_crash_settles_at_intrinsic():
    returns = np.full(80, -0.005)                            # a steady slide past expiry
    book, spot, rate, vix, _ = index_world(returns)
    program = H.OptionProgram(0.95, 91, half_spread=0.01)
    run = H.option_program(book, spot, rate, vix, program, 1.0, 1.0)
    days = (book.index - book.index[0]).days.to_numpy()
    j = int(np.searchsorted(days, 91))                       # the settlement day
    s0, k = spot.iloc[0], 0.95 * spot.iloc[0]
    p0 = float(price_array(s0, k, 91, 0.04, 0.2, "put"))
    a0 = 1 + book.iloc[0]
    q = a0 / s0
    after = (a0 - q * p0 * 1.01) * np.prod(1 + book.to_numpy()[1:j + 1]) + q * max(k - spot.iloc[j], 0)
    wealth_j = float(np.prod(1 + run.returns.to_numpy()[:j + 1]))
    # Day j also buys the next put, paying its half-spread: add it back to compare.
    s_j = spot.iloc[j]
    next_fee = 0.01 * (after / s_j) * float(price_array(s_j, 0.95 * s_j, 91, 0.04, 0.2, "put"))
    assert wealth_j + next_fee == pytest.approx(after, rel=1e-12)
    assert run.payoff > 0 and run.rolls == 2


def test_a_rolled_puts_premium_is_the_plans_worked_price():
    # HEDGE_PLAN's table: 10% OTM, 3 months, at 21% vol (15% ATM + 6): 0.72 per 100.
    book, spot, rate, vix, _ = index_world(np.zeros(756), vix=0.21)
    run = H.option_program(book, spot, rate, vix, H.OptionProgram(0.9, 91), 1.0, 1.0,
                           dividend=0.013)
    price = float(price_array(100.0, 90.0, 91, 0.04, 0.21, "put", 0.013))
    assert price == pytest.approx(0.720, abs=5e-4)
    years = 756 / 252
    assert run.premium * years / run.rolls == pytest.approx(price / 100.0, rel=1e-9)
    assert run.payoff == 0.0                                  # flat index: they expire worthless


def test_a_collar_sells_the_call_its_put_pays_for():
    vol = lambda k: 0.2 + 0.4 * max(0.0, math.log(100.0 / k))  # noqa: E731  (a put skew)
    k_c = H.collar_strike(100.0, 90.0, 91, 0.04, 0.013, vol)
    put = float(price_array(100.0, 90.0, 91, 0.04, vol(90.0), "put", 0.013))
    call = float(price_array(100.0, k_c, 91, 0.04, vol(k_c), "call", 0.013))
    assert call == pytest.approx(put, rel=1e-9) and k_c > 100.0
    forward = 100.0 * math.exp((0.04 - 0.013) * 91 / 365)
    at_forward = H.collar_strike(100.0, forward, 91, 0.04, 0.013, lambda k: 0.2)
    assert at_forward == pytest.approx(forward, rel=1e-9)     # parity: C(F) = P(F)


def test_crash_losses_fall_as_the_put_hedge_grows_and_a_spread_caps_it():
    rng = np.random.default_rng(5)
    r = rng.normal(0.0005, 0.008, 1_000)
    r[400:460] = -0.008                                      # about −38%
    book, spot, rate, vix, _ = index_world(r)
    losses = []
    for h in (0.0, 0.25, 0.5, 1.0):
        run = H.option_program(book, spot, rate, vix, H.OptionProgram(0.95, 91), h, 1.0)
        (crash,) = [c for c in H.assess(book, run.returns, book).crashes if c.window.depth > 0.3]
        losses.append(crash.hedged)
    assert losses == sorted(losses)                          # less lost as h grows
    put = H.option_program(book, spot, rate, vix, H.OptionProgram(0.95, 91), 1.0, 1.0)
    spread = H.option_program(book, spot, rate, vix, H.OptionProgram(0.95, 91, short_put=0.85),
                              1.0, 1.0)
    assert spread.premium < put.premium and spread.payoff < put.payoff


def test_delta_growth_has_the_known_sign_for_dear_and_cheap_puts():
    rng = np.random.default_rng(6)
    calm = rng.normal(0.0003, 0.005, 2_520)                  # 8% realized, never a crash
    book, spot, rate, vix, _ = index_world(calm, vix=0.40)   # puts priced at 40%
    dear = H.option_program(book, spot, rate, vix, H.OptionProgram(0.95, 30), 1.0, 1.0)
    a = H.assess(book, dear.returns, book)
    assert a.growth < -4 * a.growth_error
    jumpy = rng.normal(0.0005, 0.004, 2_520)
    jumpy[::126] = -0.15                                     # a −15% day twice a year
    book, spot, rate, vix, _ = index_world(jumpy, vix=0.08)  # priced as if calm
    cheap = H.option_program(book, spot, rate, vix, H.OptionProgram(0.95, 30), 1.0, 1.0)
    b = H.assess(book, cheap.returns, book)
    assert b.growth > 4 * b.growth_error


def test_whole_contracts_round_the_ideal_count():
    exact = H.contracts(1.0, 1.0, 100_000, 500.0)
    assert (exact.ideal, exact.whole, exact.h_effective) == (2.0, 2, 1.0)
    small = H.contracts(0.5, 0.9, 50_000, 500.0)              # 0.45 of a contract
    assert (small.whole, small.h_effective) == (0, 0.0)
    up = H.contracts(0.5, 0.9, 100_000, 500.0)               # 0.9 rounds to 1: over-hedged
    assert up.whole == 1 and up.h_effective == pytest.approx(500 * 100 / (0.9 * 100_000))
    with pytest.raises(ValueError):
        H.contracts(0.5, 0.9, -1.0, 500.0)


@pytest.mark.parametrize("kwargs, message", [
    ({"moneyness": 0.1}, "moneyness"),
    ({"tenor": 0}, "tenor"),
    ({"short_put": 0.95}, "below the long put"),
    ({"short_put": 0.8, "collar": True}, "not both"),
    ({"half_spread": 1.0}, "half-spread"),
])
def test_a_program_that_cant_be_run_is_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        H.OptionProgram(**kwargs)


def test_a_grid_smile_interpolates_and_holds_flat_beyond():
    ks, days = np.array([-0.2, 0.0, 0.2]), np.array([30.0, 90.0])
    grid = np.array([[1.4, 1.0, 0.9], [1.2, 1.0, 0.95]])
    smile = H.grid_smile(ks, days, grid)
    at = lambda k, d: float(smile(np.array([k]), np.array([d / 365.0]))[0])  # noqa: E731
    assert at(0.0, 30) == pytest.approx(1.0) and at(-0.1, 30) == pytest.approx(1.2)
    assert at(-0.2, 60) == pytest.approx(1.3)                 # halfway between tenors
    assert at(-0.5, 10) == pytest.approx(1.4) and at(0.5, 400) == pytest.approx(0.95)
    with pytest.raises(ValueError):
        H.grid_smile(ks, days, grid[:, :2])


def test_a_rolled_puts_cost_a_year_is_its_premium_times_rolls():
    program = H.OptionProgram(0.9, 91)
    cost = H.roll_cost(program, np.array([0.04]), 0.013, np.array([0.21]))[0]
    premium = float(price_array(1.0, 0.9, 91, 0.04, 0.21, "put", 0.013))
    assert cost == pytest.approx(premium * 365 / 91)          # 2.9% a year, the plan's table
    spread = H.roll_cost(H.OptionProgram(0.9, 91, short_put=0.8), np.array([0.04]), 0.013,
                         np.array([0.21]))[0]
    assert 0 < spread < cost
    with pytest.raises(ValueError, match="collar"):
        H.roll_cost(H.OptionProgram(0.9, 91, collar=True), np.array([0.04]), 0.0, np.array([0.2]))
