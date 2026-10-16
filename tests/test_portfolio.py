"""Portfolio risk (P1): the module docstring's identities, closed forms and
coverage, on a correlated synthetic world with a known covariance."""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.models.portfolio import (
    Exposures,
    OptionExposure,
    correlated_returns,
    risk_report,
    simple_returns,
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
