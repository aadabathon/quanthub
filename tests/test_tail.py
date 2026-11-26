"""The tail voice (D2c): the century, scaled by the book's β."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.desk import MARKET, Book, Holding
from quant_hub.models import tail as T

AS_OF = date(2026, 11, 20)
BOOK = Book(AS_OF, (Holding("AAA", 100.0, 10.0),), 0.0, market=MARKET, market_price=500.0)


def century(excess: np.ndarray, rate: float = 0.0) -> T.Century:
    return T.Century(np.asarray(excess, float), rate, date(1926, 7, 1), date(2026, 9, 30))


def normal_century(mu: float = 0.0003, sigma: float = 0.01, years: int = 40,
                   seed: int = 1) -> T.Century:
    return century(np.random.default_rng(seed).normal(mu, sigma, years * 252))


def returns(beta: float, own: float, n: int = 500, seed: int = 2) -> pd.DataFrame:
    """Daily log returns of AAA = β·market + own noise, in simple terms."""
    rng = np.random.default_rng(seed)
    market = rng.normal(0.0004, 0.01, n)
    book = beta * market + rng.normal(0, own, n)
    index = pd.bdate_range(end=AS_OF, periods=n)
    return pd.DataFrame({"AAA": np.log1p(book), MARKET: np.log1p(market)}, index=index)


class TestBeta:
    def test_it_recovers_a_known_beta_and_own_risk(self):
        r = returns(1.4, 0.006, n=4000)
        fit = T.fit_beta(np.expm1(r["AAA"].to_numpy()), np.expm1(r[MARKET].to_numpy()))
        assert fit.beta == pytest.approx(1.4, abs=3 * fit.beta_se)
        assert fit.residual_sd == pytest.approx(0.006, rel=0.05)
        assert fit.beta_se == pytest.approx(0.006 / (0.01 * math.sqrt(4000)), rel=0.1)

    def test_too_few_sessions_say_so(self):
        with pytest.raises(ValueError, match="250 needed"):
            T.fit_beta(np.zeros(100), np.ones(100))


class TestPaths:
    def test_each_path_is_a_stretch_of_the_century(self):
        excess = np.arange(30 * 252) * 1e-7               # every day different
        market, _ = T.paths(century(excess, 0.0), 1.0, 0.0, np.random.default_rng(3),
                            n_paths=500, days=252)
        steps = np.diff(np.expm1(market), axis=1)
        assert np.allclose(steps, 1e-7)                   # consecutive days, in order

    def test_todays_rate_replaces_the_bill_then(self):
        market, book = T.paths(century(np.zeros(25 * 252), 0.0002), 0.0, 0.0,
                               np.random.default_rng(4), n_paths=10, days=5)
        assert np.allclose(market, math.log1p(0.0002)) and np.allclose(book, market)

    def test_the_book_moves_beta_times_the_market_plus_its_own(self):
        market, book = T.paths(normal_century(), 1.5, 0.0, np.random.default_rng(5),
                               n_paths=200, days=21)
        assert np.allclose(np.expm1(book), 1.5 * np.expm1(market))

    def test_a_wiped_out_day_floors_the_path(self):
        excess = np.zeros(25 * 252)
        excess[::252] = -0.2                              # a crash in every year
        _, book = T.paths(century(excess), 6.0, 0.0, np.random.default_rng(6),
                          n_paths=50, days=252)
        assert np.allclose(book.min(axis=1), math.log(T.FLOOR))


class TestTheVoice:
    def test_its_claims_and_proposal(self):
        out = T.tail(BOOK, returns(1.2, 0.006), normal_century())
        keys = {c.key for c in out.claims}
        assert {"book.return@252", "book.fall@252 >= 0.2", f"{MARKET}.fall@21",
                f"{MARKET}.vol@21"} <= keys
        assert out.proposal.hold and out.voice == "tail"
        assert out.parameters["beta"] == pytest.approx(1.2, abs=0.1)
        assert f"β {out.parameters['beta']:.2f}" in out.why

    def test_the_market_follows_the_century_not_the_book(self):
        a = T.tail(BOOK, returns(0.5, 0.004), normal_century())
        b = T.tail(BOOK, returns(2.0, 0.01, seed=9), normal_century())
        market = lambda out: [c for c in out.claims if c.target.startswith(MARKET)]  # noqa: E731
        assert market(a) == market(b)

    def test_a_drifting_century_claims_its_drift(self):
        rate = 0.0001
        history = century(np.random.default_rng(7).normal(0.0004, 0.008, 40 * 252), rate)
        out = T.tail(BOOK, returns(1.0, 0.004), history)
        median = next(c for c in out.claims if c.key == f"{MARKET}.return@252").quantiles[2]
        # 252 days drawn from the century: about 252 times its mean log return.
        expected = math.expm1(252 * np.mean(np.log1p(rate + history.excess)))
        assert median == pytest.approx(expected, abs=0.01)

    def test_a_higher_beta_falls_further(self):
        low = T.tail(BOOK, returns(0.6, 0.004), normal_century())
        high = T.tail(BOOK, returns(1.8, 0.004), normal_century())
        chance = lambda out: next(c.probability for c in out.claims  # noqa: E731
                                  if c.key == "book.fall@252 >= 0.2")
        assert chance(high) > chance(low)

    def test_the_same_inputs_give_the_same_claims(self):
        r, c = returns(1.1, 0.005), normal_century()
        assert T.tail(BOOK, r, c).claims == T.tail(BOOK, r, c).claims

    def test_without_the_market_it_says_why(self):
        unpriced = Book(AS_OF, BOOK.holdings, 0.0)
        with pytest.raises(ValueError, match="needs the market"):
            T.tail(unpriced, returns(1.0, 0.005), normal_century())

    def test_a_short_history_is_refused(self):
        with pytest.raises(ValueError, match="20 years needed"):
            century(np.zeros(5 * 252))

    def test_the_century_is_read_on_the_days_both_series_have(self):
        index = pd.bdate_range("1926-07-01", periods=25 * 252)
        excess = pd.Series(0.001, index=index)
        bills = pd.Series(0.0001, index=index[10:])
        c = T.Century.from_series(excess, bills)
        assert len(c.excess) == len(index) - 10 and c.rate == 0.0001
        assert c.first == index[10].date()
