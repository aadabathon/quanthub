"""The desk's first voices (D2): the baseline's claims, one assumption changed."""

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.desk import Book, Holding
from quant_hub.models import baseline as B
from quant_hub.models import voices as V
from quant_hub.models.garch import GARCH11Parameters, simulate_garch11

BOOK = Book(date(2026, 11, 20), (Holding("AAA", 100.0, 10.0),), cash=0.0)   # all in one name


def returns(values: np.ndarray) -> pd.DataFrame:
    index = pd.bdate_range("2024-11-01", periods=len(values))
    return pd.DataFrame({"AAA": values}, index=index)


def iid(n: int = 500, sigma: float = 0.01, seed: int = 3) -> pd.DataFrame:
    return returns(np.random.default_rng(seed).normal(0.0, sigma, n))


def book_keys(out) -> list[str]:
    return [c.key for c in out.claims if c.target.startswith("book.")]


@pytest.mark.parametrize("voice", [V.garch, V.regimes, V.bootstrap])
def test_each_voice_makes_the_baselines_book_claims(voice):
    out = voice(BOOK, iid())
    assert book_keys(out) == book_keys(B.baseline(BOOK, iid()))
    assert out.voice in ("garch", "regimes", "bootstrap") and out.why


@pytest.mark.parametrize("voice", [V.garch, V.regimes, V.bootstrap])
def test_the_same_inputs_give_the_same_claims(voice):
    assert voice(BOOK, iid()).claims == voice(BOOK, iid()).claims


@pytest.mark.parametrize("voice", [V.garch, V.regimes, V.bootstrap])
def test_too_little_history_says_so(voice):
    with pytest.raises(ValueError, match="250 needed"):
        voice(BOOK, iid(n=120))


@pytest.mark.parametrize("voice", [V.regimes, V.bootstrap])
def test_on_a_plain_normal_walk_the_simulations_agree_with_the_normal_law(voice):
    # iid N(0, 1%) days: a 21-session return's 5% and 95% quantiles are
    # exp(±1.645 · 1% · √21) − 1, within the sample's and the simulation's error.
    out = voice(BOOK, iid(n=1000, seed=5))
    q = next(c for c in out.claims if c.key == "book.return@21").quantiles
    sigma = 0.01 * math.sqrt(21)
    assert q[0] == pytest.approx(math.expm1(-1.6449 * sigma), abs=0.006)
    assert q[4] == pytest.approx(math.expm1(1.6449 * sigma), abs=0.006)


def test_the_regimes_voice_finds_one_state_in_one_states_data():
    out = V.regimes(BOOK, iid(n=1000, seed=5))
    assert out.parameters["regimes"] == 1


def test_the_bootstrap_claims_no_drift():
    trending = returns(np.random.default_rng(8).normal(0.002, 0.01, 500))
    q = next(c for c in V.bootstrap(BOOK, trending).claims if c.key == "book.return@21").quantiles
    assert abs(q[2]) < 0.005                         # the median: no drift kept


class TestGarch:
    PARAMS = GARCH11Parameters(mean=0.0, omega=2e-6, alpha=0.08, beta=0.9)

    def series(self, shock: float) -> pd.DataFrame:
        r = simulate_garch11(self.PARAMS, 1500, seed=4).returns.copy()
        r[-5:] = shock                               # the last week's moves
        return returns(r)

    def test_after_a_rough_week_it_expects_more_and_proposes_less(self):
        out = V.garch(BOOK, self.series(0.04))
        vol = next(c for c in out.claims if c.key == "book.vol@21").quantiles
        long_run = math.sqrt(252 * 2e-6 / (1 - 0.98))
        assert vol[2] > long_run                     # its median above the long run
        assert not out.proposal.hold and 0 < out.proposal.weights["AAA"] < 1

    def test_after_a_calm_week_it_proposes_holding(self):
        out = V.garch(BOOK, self.series(0.0))
        assert out.proposal.hold

    def test_its_bands_follow_its_own_variance(self):
        out = V.garch(BOOK, self.series(0.04))
        q21 = next(c for c in out.claims if c.key == "book.return@21").quantiles
        q5 = next(c for c in out.claims if c.key == "book.return@5").quantiles
        assert q21[2] == 0.0 and q21[4] - q21[0] > q5[4] - q5[0] > 0


def test_every_voice_is_read_from_the_literature():
    from quant_hub.storage.canon_store import load_canon
    covered = {path for p in load_canon().papers for path in p.lives_in}
    assert "quant_hub/models/voices.py" in covered
