"""The voices side by side in dollars (D3): claims times the book's value, nothing averaged."""

import math
from datetime import date, datetime

import numpy as np
import pytest

from quant_hub.data.desk import (LEVELS, Book, Claim, Holding, Proposal, Snapshot,
                                 VoiceOutput)
from quant_hub.models import comparison as C

AS_OF = date(2026, 11, 24)
BOOK = Book(AS_OF, (Holding("AAA", 10.0, 100.0), Holding("BBB", 5.0, 200.0)), cash=500.0)
V = 2_500.0                                     # 1,000 + 1,000 + 500


def returns(*qs) -> tuple[float, ...]:
    assert len(qs) == len(LEVELS)
    return tuple(qs)


def voice(name, q21, fall=None, vol=None, proposal=None, q5=None) -> VoiceOutput:
    claims = [Claim("book.return", 21, q21)]
    if q5 is not None:
        claims.append(Claim("book.return", 5, q5))
    if fall is not None:
        claims.append(Claim("book.return", 21, event="<= -0.1", probability=fall))
    if vol is not None:
        claims.append(Claim("book.vol", 21, vol))
    return VoiceOutput(name, "1", tuple(claims), proposal, f"{name} says so")


def snapshot(*voices, book=BOOK) -> Snapshot:
    return Snapshot(datetime(2026, 11, 25, 17, 30), AS_OF, "test", "0", book, voices)


BASE = returns(-0.08, -0.03, 0.0, 0.03, 0.08)
WIDE = returns(-0.12, -0.04, 0.0, 0.04, 0.11)
DRIFT = returns(-0.09, -0.02, 0.01, 0.04, 0.09)


class TestDollars:
    def test_each_quantile_is_the_book_value_times_the_return(self):
        c = C.compare(snapshot(voice("baseline", BASE), voice("garch", WIDE)))
        assert c.value == V
        assert c.band("baseline", 21) == pytest.approx(tuple(V * q for q in BASE))
        assert c.band("garch", 21) == pytest.approx(tuple(V * q for q in WIDE))
        assert c.band("garch", 5) == () and c.horizons() == (21,)

    def test_the_spread_is_lowest_to_highest_and_nothing_is_averaged(self):
        c = C.compare(snapshot(voice("baseline", BASE), voice("garch", WIDE),
                               voice("regimes", DRIFT)))
        worst = next(a for a in c.across if a.kind == "dollars" and a.level == 0.05)
        assert worst.low == ("garch", pytest.approx(-300.0))
        assert worst.high == ("baseline", pytest.approx(-200.0))
        assert worst.apart == pytest.approx(100.0)
        assert worst.against() == pytest.approx({"garch": -100.0, "regimes": -25.0})
        assert list(worst.values) == ["baseline", "garch", "regimes"]   # the order they spoke

    def test_chances_and_volatility_stay_in_their_own_units(self):
        c = C.compare(snapshot(voice("baseline", BASE, fall=0.03, vol=(0.1, 0.12, 0.14, 0.16, 0.2)),
                               voice("regimes", DRIFT, fall=0.07)))
        fall = next(a for a in c.across if a.kind == "probability")
        assert (fall.event, fall.values) == ("<= -0.1", {"baseline": 0.03, "regimes": 0.07})
        assert fall.apart == pytest.approx(0.04)
        vol = next(a for a in c.across if a.kind == "vol" and a.level == 0.5)
        assert vol.values == {"baseline": 0.14} and vol.apart == 0.0
        assert c.daily_move(0.14) == pytest.approx(V * 0.14 / math.sqrt(252))
        assert [a.kind for a in c.across] == ["dollars"] * 5 + ["probability"] + ["vol"] * 5

    def test_a_book_worth_nothing_or_less_is_refused(self):
        broke = Book(AS_OF, (Holding("AAA", 10.0, 100.0),), cash=-1_000.0)
        with pytest.raises(ValueError, match="worth"):
            C.compare(snapshot(voice("baseline", BASE), book=broke))

    def test_claims_on_single_holdings_stay_out_of_the_book_rows(self):
        out = VoiceOutput("baseline", "1", (Claim("book.return", 21, BASE),
                                            Claim("AAA.return", 21, WIDE)), None)
        c = C.compare(snapshot(out))
        assert {a.target for a in c.across} == {"book.return"}


class TestProposals:
    def test_holding_trades_nothing(self):
        c = C.compare(snapshot(voice("baseline", BASE, proposal=Proposal(hold=True)),
                               voice("silent", BASE)))
        assert [(p.hold, p.trades, p.to_cash, p.scale) for p in c.proposals] == [
            (True, (), 0.0, 1.0), (True, (), 0.0, 1.0)]
        assert c.proposals[1].note == "no proposal" and c.effects == ()

    def test_a_uniform_scale_in_dollars(self):
        m = 0.8
        weights = {s: w * m for s, w in BOOK.weights().items()}
        c = C.compare(snapshot(voice("garch", WIDE, proposal=Proposal(False, weights, "scale"))))
        p = c.proposals[0]
        assert p.scale == pytest.approx(m)
        assert [(t.symbol, t.dollars, t.units) for t in p.trades] == [
            ("AAA", pytest.approx(-200.0), pytest.approx(-2.0)),
            ("BBB", pytest.approx(-200.0), pytest.approx(-1.0))]
        assert p.to_cash == pytest.approx(400.0)

    def test_a_change_of_mix_has_trades_but_no_effect(self):
        proposal = Proposal(False, {"AAA": 0.4, "CCC": 0.2}, "rotate")     # BBB left out: sold
        c = C.compare(snapshot(voice("x", WIDE, proposal=proposal)))
        p = c.proposals[0]
        assert p.scale is None and c.effects == ()
        trades = {t.symbol: t for t in p.trades}
        assert trades["BBB"].dollars == pytest.approx(-1_000.0)
        assert trades["CCC"].dollars == pytest.approx(500.0) and trades["CCC"].units is None
        assert p.to_cash == pytest.approx(500.0)

    @pytest.mark.parametrize("weights, m", [
        ({"AAA": 0.2, "BBB": 0.2}, 0.5),
        ({"AAA": 0.0, "BBB": 0.0}, 0.0),
        ({"AAA": 0.2, "BBB": 0.3}, None),
        ({"AAA": -0.4, "BBB": -0.4}, None),
        ({"AAA": 0.4}, None),
    ])
    def test_what_counts_as_one_scale(self, weights, m):
        got = C.scale_of(Proposal(False, weights), BOOK)
        assert got == (pytest.approx(m) if m is not None else None)


class TestEffects:
    def test_a_scaled_book_moves_exactly_m_times_as_much(self):
        # The identity the effects rest on, path by path: units m·n, the
        # raised cash idle, so the dollar change is m times the book's.
        rng = np.random.default_rng(3)
        m = 0.7
        start = np.array([100.0, 200.0])
        units = np.array([10.0, 5.0])
        later = start * np.exp(rng.normal(0, 0.1, (1000, 2)))
        cash = 500.0
        before, after = start @ units + cash, later @ units + cash
        raised = (1 - m) * (start @ units)
        before_scaled = start @ (m * units) + cash + raised
        after_scaled = later @ (m * units) + cash + raised
        assert before_scaled == pytest.approx(before)                 # the same book value
        assert np.allclose(after_scaled - before_scaled, m * (after - before))

    def test_each_proposal_read_under_every_voices_law(self):
        m = 0.75
        weights = {s: w * m for s, w in BOOK.weights().items()}
        c = C.compare(snapshot(voice("baseline", BASE),
                               voice("garch", WIDE, proposal=Proposal(False, weights, "scale")),
                               voice("regimes", DRIFT)))
        effects = {e.law: e for e in c.effects}
        assert set(effects) == {"baseline", "garch", "regimes"}
        assert {e.proposal for e in c.effects} == {"garch"}
        e = effects["regimes"]
        assert e.held == pytest.approx(tuple(V * q for q in DRIFT))
        assert e.proposed == pytest.approx(tuple(m * V * q for q in DRIFT))
        change = dict(zip(LEVELS, e.change))
        assert change[0.05] == pytest.approx((1 - m) * V * 0.09)      # the tail saves
        assert change[0.5] == pytest.approx(-(1 - m) * V * 0.01)      # the drift is given up
        assert dict(zip(LEVELS, effects["baseline"].change))[0.5] == pytest.approx(0.0)


def test_holding_bands_turn_around_for_a_short():
    book = Book(AS_OF, (Holding("AAA", 10.0, 100.0), Holding("SSS", -4.0, 50.0)), cash=2_000.0)
    out = VoiceOutput("baseline", "1", (Claim("book.return", 21, BASE),
                                        Claim("AAA.return", 21, WIDE),
                                        Claim("SSS.return", 21, WIDE)), None)
    bands = C.holding_bands(snapshot(out, book=book))
    assert bands["AAA"] == pytest.approx(tuple(1_000.0 * q for q in WIDE))
    assert bands["SSS"] == pytest.approx(tuple(-200.0 * q for q in reversed(WIDE)))
    assert list(bands["SSS"]) == sorted(bands["SSS"])


def test_the_market_and_the_fall_are_lined_up_beside_the_book():
    from quant_hub.data.desk import MARKET
    book = Book(AS_OF, BOOK.holdings, BOOK.cash, market=MARKET, market_price=500.0)

    def out(name, shift):
        return VoiceOutput(name, "2", (
            Claim("book.return", 21, BASE),
            Claim("book.fall", 21, returns(0.01, 0.03, 0.05 + shift, 0.08, 0.12)),
            Claim("book.fall", 21, event=">= 0.1", probability=0.2 + shift),
            Claim(f"{MARKET}.return", 21, WIDE),
            Claim("OTHER.return", 21, WIDE)), None)
    c = C.compare(snapshot(out("baseline", 0.0), out("garch", 0.02), book=book))
    kinds = [(a.target, a.kind) for a in c.across]
    assert kinds[0] == ("book.return", "dollars") and ("book.fall", "fall") in kinds
    assert all(t.startswith("book.") for t, _ in kinds[:kinds.index((f"{MARKET}.return",
                                                                       "return"))])
    assert "OTHER.return" not in {t for t, _ in kinds}               # neither book nor market
    fall = next(a for a in c.across if a.kind == "fall" and a.level == 0.5)
    assert fall.apart == pytest.approx(0.02) and fall.low[0] == "baseline"
    market = next(a for a in c.across if a.kind == "return" and a.level == 0.05)
    assert market.values == {"baseline": WIDE[0], "garch": WIDE[0]}   # a fraction, not dollars
