"""Portfolio risk (P1): the module docstring's identities, closed forms and
coverage, on a correlated synthetic world with a known covariance."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.portfolio import (
    INFLUENCE_SHARE,
    MAD_TO_SD,
    MIN_DAYS,
    Exposures,
    OptionExposure,
    correlated_returns,
    influential_days,
    risk_report,
    robust_share,
    robust_z,
    simple_returns,
    variance_shares,
)

VOLS = np.array([0.012, 0.018, 0.009, 0.015])            # daily
CORR = np.array([[1.0, 0.6, 0.2, 0.5],
                 [0.6, 1.0, 0.1, 0.4],
                 [0.2, 0.1, 1.0, 0.3],
                 [0.5, 0.4, 0.3, 1.0]])
SYMBOLS = ["AAA", "BBB", "CCC", "SPY"]
MEAN = np.array([0.0004, 0.0005, 0.0002, 0.0003])


def world(days=1000, seed=1) -> pd.DataFrame:
    draws = correlated_returns(VOLS, CORR, days, MEAN, seed)
    return pd.DataFrame(draws, columns=SYMBOLS, index=pd.bdate_range("2020-01-02", periods=days))


def holdings(cash=5_000.0) -> Exposures:
    stocks = {"AAA": 40_000.0, "BBB": 30_000.0, "CCC": 25_000.0}
    return Exposures(sum(stocks.values()) + cash, stocks)


@pytest.fixture(scope="module")
def returns():
    return world()


@pytest.fixture(scope="module")
def report(returns):
    return risk_report(holdings(), returns, returns["SPY"], rate=0.04)


class TestIdentities:
    def test_vol_is_the_sd_of_the_replayed_returns(self, report, returns):
        replayed = returns[list(report.symbols)].to_numpy() @ report.weights
        assert report.vol == pytest.approx(np.std(replayed, ddof=1), rel=1e-12)
        assert report.vol_annual == pytest.approx(report.vol * math.sqrt(252))

    def test_contributions_sum_to_the_vol(self, report):
        assert report.contributions.sum() == pytest.approx(report.vol, rel=1e-12)
        assert report.risk_shares.sum() == pytest.approx(1.0, rel=1e-12)

    def test_portfolio_beta_is_the_weighted_sum(self, report, returns):
        replayed = returns[list(report.symbols)].to_numpy() @ report.weights
        m = returns["SPY"].to_numpy()
        direct = np.cov(replayed, m, ddof=1)[0, 1] / np.var(m, ddof=1)
        assert report.beta == pytest.approx(direct, rel=1e-10)
        assert report.beta == pytest.approx(report.weights @ report.betas, rel=1e-12)

    def test_cash_shows_as_net_exposure_below_one(self, report):
        assert report.net_exposure == pytest.approx(95_000 / 100_000)
        assert report.equity == 100_000

    def test_the_replay_pnl_is_dollar_exposure_times_returns(self, report, returns):
        day = returns.index[10]
        expected = sum(v * returns.loc[day, s] for s, v in holdings().stocks.items())
        assert report.pnl[day] == pytest.approx(expected, rel=1e-12)

    def test_effective_number_of_holdings(self, returns):
        equal = Exposures(90_000, {"AAA": 30_000, "BBB": 30_000, "CCC": 30_000})
        assert risk_report(equal, returns, None, 0.04).n_effective == pytest.approx(3.0)
        lopsided = Exposures(90_000, {"AAA": 88_000, "BBB": 1_000, "CCC": 1_000})
        assert risk_report(lopsided, returns, None, 0.04).n_effective < 1.1


class TestValueAtRisk:
    def test_gaussian_var_and_es_closed_forms(self, report):
        for alpha in (0.95, 0.99):
            z = NormalDist().inv_cdf(alpha)
            sigma = report.vol * report.equity
            assert report.var_gaussian[alpha] == pytest.approx(z * sigma, rel=1e-12)
            assert report.es_gaussian[alpha] == pytest.approx(
                sigma * NormalDist().pdf(z) / (1 - alpha), rel=1e-12)
            assert report.var_month(alpha) == pytest.approx(
                report.var_gaussian[alpha] * math.sqrt(21))

    def test_historical_es_is_the_tail_mean_beyond_var(self, report):
        losses = -report.pnl.to_numpy()
        for alpha in (0.95, 0.99):
            assert report.var_historical[alpha] == pytest.approx(np.quantile(losses, alpha))
            tail = losses[losses >= report.var_historical[alpha]]
            assert report.es_historical[alpha] == pytest.approx(tail.mean())
            assert report.es_historical[alpha] >= report.var_historical[alpha]

    def test_historical_var_is_exceeded_about_one_day_in_twenty(self):
        # 5,000 in-sample days set VaR95; on 40,000 fresh days from the same
        # world the loss beats it about 5% of the time. The in-sample quantile
        # itself is uncertain, so the tolerance is wider than binomial alone.
        fit = world(5000, seed=2)
        fresh = world(40000, seed=3)
        report = risk_report(holdings(), fit, None, 0.04)
        fresh_losses = -(fresh[list(report.symbols)].to_numpy() @ report.exposures)
        rate = np.mean(fresh_losses > report.var_historical[0.95])
        assert abs(rate - 0.05) < 0.01

    def test_the_covariance_recovers_the_truth(self):
        report = risk_report(holdings(), world(20000, seed=4), None, 0.04)
        order = [SYMBOLS.index(s) for s in report.symbols]
        assert np.allclose(report.asset_vols, VOLS[order], rtol=0.03)


class TestOptions:
    def put(self, contracts=1.0):
        return OptionExposure("AAA", "put", strike=95.0, days=60, contracts=contracts,
                              spot=100.0, vol=0.25, rate=0.04)

    def test_a_put_is_exposure_against_its_underlying(self):
        put = self.put()
        assert put.delta_dollars() < 0                         # a long put is short delta
        exposures = Exposures(20_000, {"AAA": 10_000.0}, (put,))
        assert exposures.by_underlying()["AAA"] == pytest.approx(10_000 + put.delta_dollars())

    def test_the_replay_reprices_a_protective_put_in_full(self):
        # On a -20% day the put gains more than its delta says: convexity.
        put = self.put()
        crash = np.array([-0.20])
        full = put.scenario_pnl(crash)[0]
        delta_only = put.delta_dollars() * crash[0]
        assert full > delta_only > 0
        # And the replay uses it: the hedged book loses less than 100 shares do.
        returns = world(300, seed=5)
        returns.iloc[-1, returns.columns.get_loc("AAA")] = -0.20
        hedged = risk_report(Exposures(20_000, {"AAA": 10_000.0}, (put,)), returns, None, 0.04)
        naked = risk_report(Exposures(20_000, {"AAA": 10_000.0}), returns, None, 0.04)
        assert hedged.pnl.iloc[-1] > naked.pnl.iloc[-1]
        assert hedged.pnl.iloc[-1] == pytest.approx(-2_000 + full, rel=1e-9)


class TestKelly:
    def test_kelly_and_its_error(self):
        returns = world(504, seed=6)                           # two years
        report = risk_report(holdings(cash=0.0), returns, None, rate=0.03)
        replayed = returns[list(report.symbols)].to_numpy() @ report.weights
        mean = replayed.mean() * 252
        sigma = report.vol_annual
        assert report.mean == pytest.approx(mean)
        assert report.kelly == pytest.approx((mean - 0.03) / sigma**2)
        assert report.kelly_error == pytest.approx(1 / (sigma * math.sqrt(2)))
        # Growth is greatest at f* and its drag at f = 1 is sigma^2 / 2.
        assert report.growth(report.kelly) >= report.growth(report.kelly * 1.1)
        assert report.growth(1.0) == pytest.approx(mean - sigma**2 / 2)


class TestDrawdownAndInputs:
    def test_drawdown_of_a_known_path(self):
        days = 80
        index = pd.bdate_range("2021-01-04", periods=days)
        moves = np.zeros(days)
        moves[10], moves[11], moves[40] = 0.10, -0.25, 0.05
        frame = pd.DataFrame({"AAA": moves, "SPY": moves}, index=index)
        report = risk_report(Exposures(10_000, {"AAA": 10_000.0}), frame, frame["SPY"], 0.0)
        assert report.drawdown.min() == pytest.approx(-0.25)
        assert report.path.iloc[-1] == pytest.approx(1.10 * 0.75 * 1.05)

    def test_simple_returns_align_on_common_days(self):
        index = pd.bdate_range("2024-01-01", periods=5)
        a = pd.Series([100, 101, 102, 103, 104.0], index=index)
        b = pd.Series([50, 51, 52.0], index=index[[0, 2, 4]])
        returns = simple_returns({"A": a, "B": b})
        assert list(returns.index) == [index[2], index[4]]
        assert returns.loc[index[2], "A"] == pytest.approx(102 / 100 - 1)

    @pytest.mark.parametrize("bad", [
        lambda r: risk_report(Exposures(1000, {"ZZZ": 500.0}), r, None, 0.04),
        lambda r: risk_report(holdings(), r.iloc[:30], None, 0.04),
        lambda r: risk_report(holdings(), r, None, 0.04, confidence=(1.2,)),
        lambda r: Exposures(0.0, {"AAA": 1.0}),
    ])
    def test_invalid_inputs_rejected(self, returns, bad):
        with pytest.raises(ValueError):
            bad(returns)


class TestInfluence:
    """The days that decide the numbers: one rule for both pages (module
    docstring, Influence; D53)."""

    @staticmethod
    def jumped(days=500, seed=3, jumps=((250, "BBB", 1.0),)):
        returns = world(days, seed)
        for i, symbol, move in jumps:
            returns.iloc[i, returns.columns.get_loc(symbol)] = move
        return returns

    @staticmethod
    def mix(returns, report):
        return returns[list(report.symbols)].to_numpy() @ report.weights

    def test_a_clean_gaussian_world_flags_nothing(self, report):
        assert report.influence.flagged == ()
        assert report.influence.without is None
        # The largest day is still named, with an ordinary share.
        assert report.influence.largest.variance_share < 0.03

    def test_a_planted_jump_is_flagged_with_its_driver_and_share(self):
        returns = self.jumped()                          # BBB +100%, 30% of equity
        report = risk_report(holdings(), returns, returns["SPY"], rate=0.04)
        (day,) = report.influence.flagged
        assert day.date == returns.index[250]
        assert day.driver == "BBB" and day.driver_return == 1.0
        y = self.mix(returns, report)
        deviation = y - y.mean()
        assert day.variance_share == pytest.approx(deviation[250] ** 2 / (deviation @ deviation),
                                                   rel=1e-12)
        assert day.mix_return == pytest.approx(y[250], rel=1e-12)
        z = robust_z(y)[250]
        assert day.robust_share == pytest.approx(z**2 / (z**2 + len(y) - 1), rel=1e-12)
        assert day.decides_mix and abs(day.robust_z) > math.sqrt(len(y) - 1)
        # It decides BBB's own series too, with BBB's own exact share.
        (bbb,) = day.holdings
        own = returns["BBB"].to_numpy() - returns["BBB"].mean()
        assert bbb.symbol == "BBB" and bbb.holding_return == 1.0
        assert bbb.variance_share == pytest.approx(own[250] ** 2 / (own @ own), rel=1e-12)
        assert report.influence.largest == day

    def test_a_day_can_decide_a_holding_without_deciding_the_mix(self):
        # BBB +60%: 38% of the mix's variance (robust share below a half),
        # 67% of BBB's own (robust share above).
        returns = self.jumped(jumps=((250, "BBB", 0.60),))
        report = risk_report(holdings(), returns, returns["SPY"], rate=0.04)
        (day,) = report.influence.flagged
        assert not day.decides_mix and day.robust_share < INFLUENCE_SHARE
        (bbb,) = day.holdings
        assert bbb.symbol == "BBB" and bbb.robust_share > INFLUENCE_SHARE
        assert report.influence.without is not None

    def test_two_equal_jumps_are_both_flagged_though_each_carries_under_half(self):
        # Each carries under half the variance, so a share-based flag (W7's D)
        # would hide both (masking); the robust share isn't diluted.
        returns = self.jumped(jumps=((100, "AAA", 1.0), (300, "AAA", 1.0)))
        report = risk_report(holdings(), returns, returns["SPY"], rate=0.04)
        flagged = report.influence.flagged
        assert {d.date for d in flagged} == {returns.index[100], returns.index[300]}
        for day in flagged:
            (aaa,) = day.holdings
            assert day.decides_mix and day.variance_share < 0.5
            assert aaa.symbol == "AAA" and aaa.variance_share < 0.5

    def test_without_is_the_report_on_the_remaining_days(self):
        returns = self.jumped()
        report = risk_report(holdings(), returns, returns["SPY"], rate=0.04)
        without = report.influence.without
        rest = returns.drop(returns.index[250])
        direct = risk_report(holdings(), rest, rest["SPY"], rate=0.04, with_influence=False)
        assert without.vol == pytest.approx(direct.vol, rel=1e-12)
        assert without.var_historical == pytest.approx(direct.var_historical, rel=1e-12)
        np.testing.assert_allclose(without.risk_shares, direct.risk_shares, rtol=1e-12)
        assert without.influence is None
        # The leave-out mean: (n ybar - y_S) / (n - 1), annualized per unit of exposure.
        y = self.mix(returns, report)
        leave_out = (len(y) * y.mean() - y[250]) / (len(y) - 1)
        assert without.mean == pytest.approx(leave_out * 252 / report.net_exposure, rel=1e-12)
        assert without.vol < report.vol

    def test_too_few_remaining_days_gives_no_without(self):
        returns = self.jumped(days=MIN_DAYS + 1, jumps=((10, "AAA", 0.5), (20, "AAA", 0.5)))
        report = risk_report(holdings(), returns, returns["SPY"], rate=0.04)
        assert len(report.influence.flagged) == 2
        assert report.influence.without is None

    def test_the_deletion_identity(self):
        # sum_{s != t} (v_s - mean_{-t})^2 = sum d^2 - n/(n-1) d_t^2, so the
        # share s_t is what leaves the sample with the day.
        v = np.random.default_rng(5).standard_t(3, 80)
        n, d = len(v), v - v.mean()
        for t in range(n):
            rest = np.delete(v, t)
            left = float(((rest - rest.mean()) ** 2).sum())
            assert left == pytest.approx(d @ d - n / (n - 1) * d[t] ** 2, rel=1e-10)
        assert variance_shares(v).sum() == pytest.approx(1.0, rel=1e-12)

    def test_the_robust_share_recovers_the_closed_form(self):
        # One day of size x among n - 1 days of variance sigma^2:
        # x^2 / (x^2 + (n - 1) sigma^2), with sigma from the MAD.
        n, sigma, x = 100_000, 0.01, 4.0
        v = np.random.default_rng(12).normal(0.0, sigma, n)
        v[777] = x
        closed = x**2 / (x**2 + (n - 1) * sigma**2)          # 0.615
        # The MAD's sigma has an SE of about 0.37% here, moving the share by ~0.2%.
        assert robust_share(v)[777] == pytest.approx(closed, rel=0.01)
        assert list(influential_days(v)) == [777]

    def test_the_flag_is_z_beyond_root_n_minus_1(self):
        v = np.random.default_rng(8).standard_cauchy(250)       # heavy enough to flag some
        z = robust_z(v)
        expected = np.flatnonzero(np.abs(z) > math.sqrt(len(v) - 1))
        assert len(expected) > 0
        np.testing.assert_array_equal(influential_days(v), expected)

    def test_t3_noise_outweighs_the_rest_at_the_tabulated_rate(self):
        # The docstring's table: 6.8% of 500-day t(3) series flag a day (from
        # 20,000 series; SE 0.18%). 4,000 fresh series: SE 0.40%, so the band
        # is about four combined SEs either side.
        rng = np.random.default_rng(20261026)
        runs = 4_000
        hits = sum(len(influential_days(rng.standard_t(3, 500))) > 0 for _ in range(runs))
        assert 0.050 <= hits / runs <= 0.086

    def test_influence_rejects_short_or_bad_series(self):
        with pytest.raises(ValueError, match="at least two"):
            influential_days(np.ones(1))
        with pytest.raises(ValueError, match="one series"):
            robust_share(np.ones((10, 2)))
        with pytest.raises(ValueError, match="finite"):
            variance_shares(np.r_[np.ones(9), np.nan])

    def test_mad_recovers_sigma_on_a_normal_sample(self):
        rng = np.random.default_rng(11)
        sample = rng.normal(0.0, 0.02, 200_000)
        mad = np.median(np.abs(sample - np.median(sample)))
        # SE of the MAD's sigma estimate is about 1.17 sigma / sqrt(n): 0.26% here.
        assert MAD_TO_SD * mad == pytest.approx(0.02, rel=0.01)
        assert np.std(robust_z(sample)) == pytest.approx(1.0, rel=0.01)

    def test_robust_z_with_a_zero_mad_falls_back_to_the_sd(self):
        values = np.array([0.0] * 7 + [0.01, -0.01, 0.05])
        z = robust_z(values)
        assert z[-1] == pytest.approx(0.05 / np.std(values, ddof=1))
        assert np.all(robust_z(np.zeros(5)) == 0)
