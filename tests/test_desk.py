"""The desk's record (D1): claims, snapshots, decisions, the baseline, the store."""

import json
import math
from datetime import date, datetime
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.desk import (
    LEVELS,
    Book,
    Claim,
    Decision,
    Holding,
    Proposal,
    Snapshot,
    VoiceOutput,
    event_happened,
    resolves_on,
)
from quant_hub.models import baseline as B
from quant_hub.storage.desk_store import DeskStore, DeskStoreError

AS_OF = date(2026, 11, 20)
QUANTILES = (-0.08, -0.03, 0.0, 0.03, 0.09)


def book() -> Book:
    return Book(AS_OF, (Holding("AAA", 10.0, 50.0), Holding("BBB", 5.0, 100.0)), cash=500.0)


def snapshot(taken: datetime = datetime(2026, 11, 20, 17, 0), *voices) -> Snapshot:
    voices = voices or (VoiceOutput("baseline", "1", (Claim("book.return", 21, QUANTILES),),
                                    Proposal(), "why"),)
    return Snapshot(taken, AS_OF, "file", "1.1.0", book(), voices)


class TestClaims:
    def test_quantiles_or_a_probability(self):
        assert Claim("book.return", 21, QUANTILES).kind == "quantiles"
        p = Claim("AAA.return", 63, event="<= -0.2", probability=0.05)
        assert (p.kind, p.key) == ("probability", "AAA.return@63 <= -0.2")

    @pytest.mark.parametrize("make, message", [
        (lambda: Claim("book", 21, QUANTILES), "target"),
        (lambda: Claim("book.price", 21, QUANTILES), "resolve"),
        (lambda: Claim("book.return", 0, QUANTILES), "horizon"),
        (lambda: Claim("book.return", 21), "one of them"),
        (lambda: Claim("book.return", 21, QUANTILES, "<= 0", 0.5), "one of them"),
        (lambda: Claim("book.return", 21, (0.1, 0.0, 0.0, 0.0, 0.0)), "decrease"),
        (lambda: Claim("book.return", 21, (0.0, 0.1)), "5 finite"),
        (lambda: Claim("book.return", 21, event="about -10%", probability=0.1), "an event"),
        (lambda: Claim("book.return", 21, event="<= -0.1", probability=1.5), "between 0 and 1"),
    ])
    def test_a_claim_that_cant_be_scored_is_refused(self, make, message):
        with pytest.raises(ValueError, match=message):
            make()

    def test_events_and_resolve_days(self):
        assert event_happened("<= -0.1", -0.1) and not event_happened("< -0.1", -0.1)
        assert event_happened("> 0.05", 0.06) and not event_happened(">= 0.05", 0.04)
        assert resolves_on(date(2026, 11, 20), 1) == date(2026, 11, 23)       # a weekend
        assert resolves_on(date(2026, 11, 25), 1) == date(2026, 11, 27)       # Thanksgiving
        assert resolves_on(date(2026, 11, 20), 21) == date(2026, 12, 22)      # 21 sessions


class TestSnapshot:
    def test_it_round_trips_through_canonical_json(self):
        s = snapshot()
        assert Snapshot.from_json(s.to_json()) == s
        assert s.to_json() == Snapshot.from_json(s.to_json()).to_json()
        assert json.loads(s.to_json())["book"]["holdings"][0] == {
            "symbol": "AAA", "units": 10.0, "price": 50.0}

    def test_its_hash_changes_with_anything_in_it(self):
        s = snapshot()
        assert len(s.sha256) == 64 and s.sha256 == snapshot().sha256
        assert s.sha256 != snapshot(datetime(2026, 11, 20, 17, 1)).sha256

    def test_the_book_weighs_its_holdings_against_cash(self):
        b = book()
        assert b.value == 1500.0
        assert b.weights() == {"AAA": pytest.approx(1 / 3), "BBB": pytest.approx(1 / 3)}

    def test_a_voice_says_each_claim_once(self):
        claim = Claim("book.return", 21, QUANTILES)
        with pytest.raises(ValueError, match="each claim once"):
            VoiceOutput("v", "1", (claim, claim))

    def test_a_decision_cites_its_snapshot_and_states_what_it_expects(self):
        d = Decision(datetime(2026, 11, 21, 10), snapshot().sha256, "owner", (("AAA", -5.0),),
                     "trim: the stock's 21-day spread is wider than the book's",
                     (Claim("AAA.return", 21, event="< 0", probability=0.6),))
        assert Decision.from_json(d.to_json()) == d
        with pytest.raises(ValueError, match="SHA-256"):
            Decision(datetime(2026, 11, 21), "abc", "owner", (), "")


def walk(sigma: float, n: int, seed: int, columns=("AAA", "BBB")) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2025-01-02", periods=n)
    return pd.DataFrame(rng.normal(0.0, sigma, (n, len(columns))), index=index, columns=columns)


class TestBaseline:
    def test_the_chi2_quantile_is_exact(self):
        # A closed form: with k = 2, χ² is exponential, x = -2 ln(1 - τ).
        for tau in LEVELS:
            assert B.chi2_quantile(2, tau) == pytest.approx(-2 * math.log(1 - tau), rel=1e-10)
        # Tables: k = 21 is 11.591 at 5% and 32.671 at 95%.
        assert B.chi2_quantile(21, 0.05) == pytest.approx(11.5913, abs=5e-4)
        assert B.chi2_quantile(21, 0.95) == pytest.approx(32.6706, abs=5e-4)
        rng = np.random.default_rng(7)
        for k in (5, 21, 63):
            draws = rng.chisquare(k, 400_000)
            for tau in LEVELS:
                assert B.chi2_quantile(k, tau) == pytest.approx(np.quantile(draws, tau), rel=0.006)

    def test_the_quantiles_and_fall_odds_of_a_driftless_walk(self):
        q = B.return_quantiles(0.01, 21)
        z = NormalDist().inv_cdf(0.05)
        assert q[2] == 0.0 and q[0] == pytest.approx(math.expm1(z * 0.01 * math.sqrt(21)))
        # P(R <= q_0.05) is 5% by construction.
        assert B.fall_probability(0.01, 21, q[0]) == pytest.approx(0.05, abs=1e-6)

    def test_its_vol_claim_covers_simulated_realized_vol(self):
        sigma, h = 0.012, 21
        rng = np.random.default_rng(11)
        realized = np.sqrt(252 * np.mean(rng.normal(0, sigma, (200_000, h)) ** 2, axis=1))
        q = B.vol_quantiles(sigma, h)
        for tau, value in zip(LEVELS, q):
            assert np.mean(realized <= value) == pytest.approx(tau, abs=0.01)

    def test_the_book_replays_todays_weights_over_the_past(self):
        returns = walk(0.01, 100, 1)
        series = B.book_log_returns(book(), returns)
        expected = np.log1p((np.expm1(returns["AAA"]) + np.expm1(returns["BBB"])) / 3)
        assert np.allclose(series.to_numpy(), expected.to_numpy())

    def test_its_claims_and_proposal(self):
        out = B.baseline(book(), walk(0.01, 100, 2))
        keys = [c.key for c in out.claims]
        assert keys[:3] == ["book.return@5", "book.return@21", "book.return@63"]
        assert "book.return@21 <= -0.1" in keys and "book.vol@21" in keys
        assert {"AAA.return@21", "BBB.return@21"} <= set(keys)
        assert out.proposal.hold and out.parameters["window"] == 63

    def test_too_little_history_says_so(self):
        with pytest.raises(ValueError, match="63 needed"):
            B.baseline(book(), walk(0.01, 40, 3))


class TestStore:
    @pytest.fixture
    def store(self):
        s = DeskStore(":memory:")
        yield s
        s.close()

    def test_a_snapshot_is_stored_once_with_its_claims(self, store):
        s = snapshot()
        first, again = store.save_snapshot(s), store.save_snapshot(s)
        assert first == again and store.load(first) == s and store.load(sha256=s.sha256) == s
        assert store.load() == s
        (summary,) = store.snapshots()
        assert (summary.as_of, summary.claims) == (AS_OF, 1)

    def test_claims_come_due_on_their_day(self, store):
        store.save_snapshot(snapshot())
        assert store.claims_due(date(2026, 12, 21)) == []
        (due,) = store.claims_due(date(2026, 12, 22))
        assert (due.voice, due.claim.key, due.resolves_on) == (
            "baseline", "book.return@21", date(2026, 12, 22))

    def test_a_decision_needs_its_snapshot_stored(self, store):
        d = Decision(datetime(2026, 11, 21), snapshot().sha256, "llm", (), "hold")
        with pytest.raises(DeskStoreError, match="stored first"):
            store.save_decision(d)
        store.save_snapshot(snapshot())
        assert store.save_decision(d) == store.save_decision(d)
        assert store.decisions() == [d]

    def test_nothing_can_be_changed_or_removed(self):
        public = {name for name in dir(DeskStore) if not name.startswith("_")}
        assert not {n for n in public if n.startswith(("update", "delete", "remove", "edit"))}
