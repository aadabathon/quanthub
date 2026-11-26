"""The deepest fall and the market as a reference (D2b)."""

import json
import math
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.desk import MARKET, Book, Claim, Holding, Snapshot
from quant_hub.models import baseline as B
from quant_hub.models import voices as V
from quant_hub.models.garch import GARCH11Parameters

AS_OF = date(2026, 11, 20)


def brute_force(path_log_returns: np.ndarray) -> float:
    """max over u <= v of 1 - V_v / V_u, with V_0 = 1, the slow way."""
    values = np.concatenate([[1.0], np.exp(np.cumsum(path_log_returns))])
    return max(1 - values[v] / values[u] for u in range(len(values))
               for v in range(u, len(values)))


class TestDeepestFall:
    def test_it_matches_the_definition_path_by_path(self):
        rng = np.random.default_rng(1)
        paths = rng.normal(0, 0.02, (200, 30))
        falls = B.deepest_falls(paths.T, (10, 30))
        for i in range(len(paths)):
            assert falls[10][i] == pytest.approx(brute_force(paths[i, :10]), abs=1e-12)
            assert falls[30][i] == pytest.approx(brute_force(paths[i]), abs=1e-12)

    def test_a_fall_from_todays_level_counts(self):
        falls = B.deepest_falls(np.array([[math.log(0.9)], [math.log(1.05)]]), (1, 2))
        assert falls[1][0] == pytest.approx(0.1) and falls[2][0] == pytest.approx(0.1)

    def test_a_rise_has_no_fall_and_longer_windows_fall_no_less(self):
        rng = np.random.default_rng(2)
        falls = B.deepest_falls(rng.normal(0, 0.01, (63, 5000)), (21, 63))
        assert (falls[21] >= 0).all() and (falls[63] >= falls[21]).all()
        assert B.deepest_falls(np.full((5, 3), 0.01), (5,))[5] == pytest.approx(np.zeros(3))

    def test_too_short_paths_say_so(self):
        with pytest.raises(ValueError, match="can't show a fall over 21"):
            B.deepest_falls(np.zeros((10, 4)), (21,))


class TestTheNullsFall:
    def test_its_chances_agree_with_an_independent_simulation(self):
        sigma = 0.012
        ours = B.null_falls(sigma, np.random.default_rng(B.NULL_SEED))
        theirs = B.deepest_falls(sigma * np.random.default_rng(99).standard_normal(
            (max(B.FALL_HORIZONS), 100_000)), B.FALL_HORIZONS)
        for h, d in B.FALL_DEPTHS:
            p, q = np.mean(ours[h] >= d), np.mean(theirs[h] >= d)
            se = math.sqrt(max(p * (1 - p), 1e-6) / 100_000 * 2)
            assert abs(p - q) < 4 * se + 1e-9, (h, d, p, q)

    def test_falling_along_the_way_is_likelier_than_ending_down(self):
        # The path includes its end, so P(D_h >= 10%) is at least P(R_h <= -10%).
        from statistics import NormalDist
        sigma, h = 0.02, 21
        p = np.mean(B.null_falls(sigma, np.random.default_rng(3))[h] >= 0.10)
        ending = NormalDist().cdf(math.log(0.9) / (sigma * math.sqrt(h)))
        assert ending < p < 1

    def test_its_claims(self):
        claims = B.walk_claims("book", 0.01, np.random.default_rng(1))
        keys = [c.key for c in claims]
        assert "book.return@252" in keys
        assert ["book.fall@21", "book.fall@63", "book.fall@252"] == [
            c.key for c in claims if c.target == "book.fall" and c.kind == "quantiles"]
        events = [c for c in claims if c.target == "book.fall" and c.kind == "probability"]
        assert [c.event for c in events] == [">= 0.1", ">= 0.1", ">= 0.2", ">= 0.1", ">= 0.2"]
        chance = {(c.horizon, c.event): c.probability for c in events}
        assert chance[(252, ">= 0.1")] >= chance[(63, ">= 0.1")] >= chance[(21, ">= 0.1")]
        assert chance[(63, ">= 0.2")] <= chance[(63, ">= 0.1")]


def frame(n=400, columns=("AAA", MARKET), seed=4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    market = rng.normal(0, 0.01, n)
    data = {"AAA": 1.2 * market + rng.normal(0, 0.008, n), MARKET: market}
    index = pd.bdate_range(end=AS_OF, periods=n)
    return pd.DataFrame({c: data[c] for c in columns}, index=index)


BOOK = Book(AS_OF, (Holding("AAA", 100.0, 10.0),), 0.0, market=MARKET, market_price=500.0)
NO_MARKET = Book(AS_OF, (Holding("AAA", 100.0, 10.0),), 0.0)


class TestTheMarket:
    def test_a_claim_on_the_fall_is_allowed_and_another_target_isnt(self):
        assert Claim("book.fall", 63, event=">= 0.2", probability=0.1).key == "book.fall@63 >= 0.2"
        with pytest.raises(ValueError, match=".fall resolve"):
            Claim("book.drawdown", 63, event=">= 0.2", probability=0.1)

    def test_the_book_keeps_the_market_and_old_snapshots_still_load(self):
        snap = Snapshot(datetime(2026, 11, 20, 17), AS_OF, "test", "0", BOOK, ())
        assert Snapshot.from_json(snap.to_json()) == snap
        old = json.loads(snap.to_json())
        del old["book"]["market"], old["book"]["market_price"]
        loaded = Snapshot.from_json(json.dumps(old))
        assert (loaded.book.market, loaded.book.market_price) == ("", None)

    @pytest.mark.parametrize("voice", [B.baseline, V.garch, V.regimes, V.bootstrap])
    def test_every_voice_makes_the_same_claims_about_the_market(self, voice):
        out = voice(BOOK, frame())
        book = sorted(c.key.removeprefix("book.") for c in out.claims
                      if c.target.startswith("book."))
        market = sorted(c.key.removeprefix(f"{MARKET}.") for c in out.claims
                        if c.target.startswith(f"{MARKET}."))
        assert market == book and out.version == "2"

    @pytest.mark.parametrize("voice", [B.baseline, V.garch, V.regimes, V.bootstrap])
    def test_without_the_market_only_the_book_is_claimed(self, voice):
        out = voice(NO_MARKET, frame())
        assert not [c for c in out.claims if c.target.startswith(f"{MARKET}.")]

    @pytest.mark.parametrize("voice", [V.garch, V.regimes, V.bootstrap])
    def test_the_books_claims_dont_depend_on_the_market(self, voice):
        with_market = [c for c in voice(BOOK, frame()).claims if c.target.startswith("book.")]
        without = [c for c in voice(NO_MARKET, frame(columns=("AAA",))).claims]
        assert with_market == without

    def test_a_held_market_is_claimed_once(self):
        held = Book(AS_OF, (Holding("AAA", 100.0, 10.0), Holding(MARKET, 2.0, 500.0)), 0.0,
                    market=MARKET, market_price=500.0)
        out = B.baseline(held, frame())
        assert [c.key for c in out.claims].count(f"{MARKET}.return@21") == 1

    def test_garch_with_constant_variance_falls_like_the_null(self):
        sigma = 0.01
        flat = GARCH11Parameters(mean=0.0, omega=sigma ** 2, alpha=0.0, beta=0.0)
        ours = B.deepest_falls(V.garch_steps(flat, sigma ** 2, np.random.default_rng(1),
                                             paths=50_000), B.FALL_HORIZONS)
        null = B.null_falls(sigma, np.random.default_rng(2), paths=50_000)
        for h in B.FALL_HORIZONS:
            assert np.median(ours[h]) == pytest.approx(np.median(null[h]), rel=0.02)

    def test_the_bootstrap_resamples_the_book_and_the_market_together(self):
        # A book that is the market: resampled on the same sessions, its
        # claims and the market's are the same numbers.
        same = frame(columns=(MARKET,)).assign(AAA=lambda f: f[MARKET])
        out = V.bootstrap(BOOK, same)
        book = {c.key.removeprefix("book."): c for c in out.claims if c.target.startswith("book.")}
        market = {c.key.removeprefix(f"{MARKET}."): c for c in out.claims
                  if c.target.startswith(f"{MARKET}.")}
        for key, claim in book.items():
            other = market[key]
            assert claim.quantiles == pytest.approx(other.quantiles, abs=1e-12)
            if claim.probability is not None:
                assert claim.probability == pytest.approx(other.probability)
