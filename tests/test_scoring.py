"""Scoring the desk (D4): what happened, the scores, the test, calibration, regimes."""

import math
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.desk import LEVELS, Claim, Outcome
from quant_hub.data.trading_calendar import trading_days
from quant_hub.models import scoring as S

NOW = datetime(2026, 12, 1, 18, 0)


def brute_fall(values) -> float:
    return max(1 - values[v] / values[u] for u in range(len(values))
               for v in range(u, len(values)))


class TestResolution:
    def test_a_symbols_path_and_its_three_numbers(self):
        closes = pd.DataFrame({"A": [100.0, 110.0, 99.0, 105.0]})
        values = S.path_values(closes, {"A": 1.0})
        ret, vol, fall = S.resolve_path(values)
        assert ret == pytest.approx(0.05)
        logs = np.diff(np.log([100, 110, 99, 105]))
        assert vol == pytest.approx(math.sqrt(252 / 3 * (logs ** 2).sum()))
        assert fall == pytest.approx(brute_fall([100, 110, 99, 105]))     # 110 -> 99

    def test_the_book_holds_its_units_with_cash_idle(self):
        closes = pd.DataFrame({"A": [10.0, 12.0], "B": [50.0, 45.0]})
        # 10 of A (100), 2 of B (100), cash 50: weights 0.4, 0.4, 0.2.
        values = S.path_values(closes, {"A": 0.4, "B": 0.4}, cash=0.2)
        assert values[-1] == pytest.approx((10 * 12 + 2 * 45 + 50) / 250)

    def test_only_ratios_matter_so_a_later_adjustment_doesnt(self):
        closes = pd.DataFrame({"A": [100.0, 103.0, 101.0]})
        scaled = closes * 0.97                       # a dividend adjusts the whole past
        assert S.resolve_path(S.path_values(closes, {"A": 1.0})) == pytest.approx(
            S.resolve_path(S.path_values(scaled, {"A": 1.0})))

    def test_a_path_to_zero_is_refused(self):
        with pytest.raises(ValueError, match="zero"):
            S.resolve_path(np.array([1.0, 0.5, -0.1]))


class TestScores:
    def test_the_quantile_score_by_hand(self):
        q = (-0.1, -0.05, 0.0, 0.05, 0.1)
        # y = 0.02: below the 75% and 95% lines only.
        by_hand = sum((float(0.02 < qt) - t) * (qt - 0.02) for t, qt in zip(LEVELS, q)) / 5
        assert S.quantile_score(q, 0.02) == pytest.approx(by_hand) and by_hand > 0

    def test_it_is_proper_the_true_quantiles_score_best(self):
        rng = np.random.default_rng(1)
        y = rng.standard_normal(200_000)
        from statistics import NormalDist
        true = [NormalDist().inv_cdf(t) for t in LEVELS]
        for wrong in ([x * 1.3 for x in true], [x * 0.7 for x in true], [x + 0.2 for x in true]):
            honest = np.mean([S.quantile_score(true, v) for v in y[:20_000]])
            other = np.mean([S.quantile_score(wrong, v) for v in y[:20_000]])
            assert honest < other

    def test_brier_and_the_clipped_log_score(self):
        assert S.brier(0.2, True) == pytest.approx(0.64)
        assert S.brier(0.2, False) == pytest.approx(0.04)
        assert S.log_score(0.0, True) == pytest.approx(-math.log(1e-4))     # 9.2, not infinity
        assert S.log_score(0.25, False) == pytest.approx(-math.log(0.75))

    def test_a_claim_against_its_outcome(self):
        out = Outcome(1, "book", 21, "resolved", -0.12, 0.3, 0.15, "", "test", NOW)
        y, score, log = S.score(Claim("book.fall", 21, event=">= 0.1", probability=0.3), out)
        assert (y, score) == (0.15, pytest.approx(0.49)) and log == pytest.approx(-math.log(0.3))
        y, score, log = S.score(Claim("book.return", 21, (-0.2, -0.1, 0, 0.1, 0.2)), out)
        assert y == -0.12 and log is None
        lost = Outcome(1, "book", 21, "unresolvable", None, None, None, "delisted", "test", NOW)
        assert S.score(Claim("book.vol", 21, (0.1, 0.2, 0.3, 0.4, 0.5)), lost) is None


class TestTheTest:
    def test_students_t_against_closed_forms_and_tables(self):
        for t in (-3.0, -0.5, 0.0, 1.0, 4.0):
            assert S.student_t_cdf(t, 1) == pytest.approx(0.5 + math.atan(t) / math.pi, abs=1e-12)
            assert S.student_t_cdf(t, 2) == pytest.approx(0.5 + t / (2 * math.sqrt(2 + t * t)),
                                                          abs=1e-12)
        assert S.student_t_cdf(2.228, 10) == pytest.approx(0.975, abs=2e-4)
        assert S.student_t_cdf(1.96, 10_000) == pytest.approx(0.975, abs=2e-4)

    def test_too_few_and_no_variation(self):
        days = sessions(30)
        assert S.skill_test([0.1] * 20, days[:20], 5).reason == \
            "too few: 4 of 5 independent windows"
        assert S.skill_test([0.1] * 25, days[:25], 5).reason == "no variation in the differences"

    def test_the_kept_windows_dont_overlap(self):
        days = sessions(23)
        kept = S.independent(days, 5)
        assert kept[-1] == 22                                   # the latest is always kept
        assert all(len(trading_days(days[a] + timedelta(days=1), days[b])) >= 5
                   for a, b in zip(kept, kept[1:]))
        assert kept == [2, 7, 12, 17, 22]

    def test_it_holds_its_size_on_overlapping_differences(self):
        # d_s = the mean of h consecutive iid shocks: MA(h - 1), as overlapping
        # claims are. With no edge the test rejects at 5% about 5% of the time,
        # which re-weighted variances on all the overlapping claims didn't.
        rng = np.random.default_rng(2)
        h, n, trials = 5, 60, 2000
        days = sessions(n)
        rejected = 0
        for _ in range(trials):
            d = np.convolve(rng.standard_normal(n + h - 1), np.ones(h) / h, mode="valid")
            rejected += S.skill_test(d, days, h).p < 0.05
        assert 0.035 < rejected / trials < 0.065

    def test_it_sees_a_real_edge(self):
        rng = np.random.default_rng(3)
        d = rng.normal(-0.5, 1.0, 200)           # the voice loses 0.5 less a claim
        test = S.skill_test(d, sessions(200), 1)
        assert test.n == 200 and test.p < 0.001 and test.t < 0


def sessions(n: int) -> list[date]:
    return trading_days(date(2025, 1, 2), date(2027, 12, 31))[:n]


def scored(voice, sid, y, score, target="book.return", h=5, regime=None, version="2",
           event=None):
    claim = (Claim(target, h, event=event, probability=0.5) if event else
             Claim(target, h, (-0.1, -0.05, 0.0, 0.05, 0.1)))
    return S.Scored(sid, date(2026, 11, 1) + timedelta(days=sid), voice, version, claim, y, score,
                    None, regime)


class TestTheRecord:
    def test_a_voice_is_paired_with_the_baseline(self):
        rows = []
        for sid in range(12):
            rows += [scored("baseline", sid, 0.0, 1.0), scored("garch", sid, 0.0, 0.8)]
        rows.append(scored("garch", 99, 0.0, 5.0))           # no baseline here: not paired
        series = {s.voice: s for s in S.record(rows)}
        g = series["garch"]
        assert (g.n, g.paired) == (13, 12) and g.skill == pytest.approx(0.2)
        assert g.baseline == pytest.approx(1.0)
        assert g.test.reason == "too few: 2 of 5 independent windows"    # 12 days, 8 sessions
        assert series["baseline"].skill is None

    def test_by_regime_and_versions(self):
        rows = [scored("baseline", 1, 0.0, 1.0, regime="calm"),
                scored("garch", 1, 0.0, 0.5, regime="calm", version="1"),
                scored("baseline", 2, 0.0, 1.0, regime="stormy"),
                scored("garch", 2, 0.0, 2.0, regime="stormy", version="2")]
        calm = {s.voice: s for s in S.record(rows, "calm")}
        stormy = {s.voice: s for s in S.record(rows, "stormy")}
        assert calm["garch"].skill == pytest.approx(0.5)
        assert stormy["garch"].skill == pytest.approx(-1.0)
        assert {s.voice: s for s in S.record(rows)}["garch"].versions == ("1", "2")

    def test_calibration(self):
        rows = [scored("baseline", i, y, 0.0) for i, y in enumerate([-0.2, -0.07, 0.01, 0.2])]
        rows += [scored("baseline", 10 + i, -0.15 if i else 0.0, 0.0, target="book.fall",
                        event=">= 0.1") for i in range(4)]
        cal = {c.form: c for c in S.calibration(rows)}
        assert cal["quantiles"].coverage == (0.25, 0.5, 0.5, 0.75, 0.75)
        assert cal["quantiles"].windows == pytest.approx(4 / 5)
        assert (cal[">= 0.1"].claimed, cal[">= 0.1"].happened) == (0.5, 0.0)


class TestRegimes:
    def test_the_centurys_terciles_and_labels(self):
        rng = np.random.default_rng(4)
        calm = rng.normal(0, 0.005, 2000)
        stormy = rng.normal(0, 0.02, 2000)
        cutoffs = S.regime_cutoffs(np.concatenate([calm, stormy, calm]))
        assert cutoffs[0] < cutoffs[1]
        assert S.regime_label(0.05, cutoffs) == "calm"
        assert S.regime_label(0.40, cutoffs) == "stormy"
        assert S.regime_label(None, cutoffs) is None and S.regime_label(0.2, None) is None
