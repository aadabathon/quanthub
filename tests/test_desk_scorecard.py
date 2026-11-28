"""The scorecard (A5): you and an agent, head to head with every voice."""

import json
from datetime import date, datetime, timedelta

import pytest

from desk_fixtures import NOW, service
from quant_hub.data.desk import Claim, Outcome, parse_claim
from quant_hub.data.trading_calendar import trading_days
from quant_hub.models import scoring
from quant_hub.models.scoring import Scored, head_to_head
from quant_hub.services import desk_report as report

Q = Claim("book.return", 21, (-0.1, -0.03, 0.0, 0.03, 0.1))
DAYS = trading_days(date(2026, 1, 2), date(2026, 12, 31))


def s(snapshot: int, voice: str, score: float, regime: str | None = None,
      claim: Claim = Q, version: str = "decision") -> Scored:
    return Scored(snapshot, DAYS[snapshot * 21], voice, version, claim, 0.01, score, None,
                  regime)


class TestHeadToHead:
    def test_skill_and_wins_on_the_claims_both_made(self):
        rows = [s(1, "me", 1.0), s(2, "me", 2.0), s(3, "me", 3.0),
                s(1, "garch", 2.0, version="2"), s(2, "garch", 2.0, version="2"),
                s(3, "garch", 2.0, version="2"), s(4, "garch", 9.0, version="2")]
        (duel,) = head_to_head(rows, "me", "garch")
        assert (duel.paired, duel.wins) == (3, 1)                  # snapshot 4 isn't shared
        assert duel.skill == pytest.approx(1 - 6 / 6)
        assert (duel.score, duel.against_score) == (pytest.approx(2.0), pytest.approx(2.0))
        assert duel.test.p is None and duel.test.reason.startswith("too few")

    def test_two_claims_on_one_snapshot_count_once(self):
        rows = [s(1, "me", 1.0), s(1, "me", 3.0), s(1, "llm", 1.0)]
        (duel,) = head_to_head(rows, "me", "llm")
        assert duel.paired == 1 and duel.score == pytest.approx(2.0) and duel.wins == 0

    def test_within_a_regime_and_each_claim_on_its_own(self):
        fall = Claim("book.fall", 63, event=">= 0.1", probability=0.3)
        rows = [s(1, "me", 1.0, "calm"), s(2, "me", 1.0, "stormy"),
                s(1, "llm", 2.0, "calm"), s(2, "llm", 0.5, "stormy"),
                s(1, "me", 0.2, "calm", fall), s(1, "llm", 0.1, "calm", fall)]
        assert [d.target for d in head_to_head(rows, "me", "llm")] == ["book.fall",
                                                                       "book.return"]
        (calm,) = [d for d in head_to_head(rows, "me", "llm", "calm")
                   if d.target == "book.return"]
        assert (calm.paired, calm.wins, calm.skill) == (1, 1, pytest.approx(0.5))

    def test_against_the_baseline_it_is_the_records_skill(self):
        rows = [s(i, "garch", 1.0 + 0.1 * i, version="2") for i in range(1, 9)]
        rows += [s(i, "baseline", 1.2, version="2") for i in range(1, 9)]
        (series,) = [r for r in scoring.record(rows) if r.voice == "garch"]
        (duel,) = head_to_head(rows, "garch", "baseline")
        assert duel.skill == pytest.approx(series.skill)
        assert (duel.test.t, duel.test.p) == (series.test.t, series.test.p)


class TestTheScorecard:
    def scored_desk(self, parts):
        """A snapshot, a decision by you and by the agent, and the 21-session
        outcome, so both are scored beside the voices."""
        _, store, _ = parts
        desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})
        taken = desk.take("test", NOW)
        desk.decide("me", (parse_claim("book.return@21 q -8%,-3%,0,3%,8%"),), now=NOW)
        desk.decide("llm", (parse_claim("book.return@21 q -12%,-4%,0.5%,4%,12%"),), now=NOW)
        store.save_outcome(Outcome(taken.id, "book", 21, "resolved", 0.02, 0.18, 0.04, "",
                                   "test", NOW + timedelta(days=40)))
        return desk

    def test_you_and_the_agent_against_every_author(self, parts):
        desk = self.scored_desk(parts)
        sc = desk.scorecard()
        assert sc.judges == ("llm", "me")
        against = {(d.author, d.against) for d in sc.duels}
        assert {("me", "llm"), ("llm", "me"), ("me", "baseline"), ("llm", "garch")} <= against
        assert all(d.paired == 1 and d.target == "book.return" for d in sc.duels)
        doc = json.loads(json.dumps(report.scorecard(sc, datetime(2026, 12, 31)),
                                    allow_nan=False))
        assert doc["kind"] == "scorecard" and doc["judges"] == ["llm", "me"]
        assert {c["author"] for c in doc["calibration"]} == {"llm", "me"}
        assert any(r["author"] == "me" for r in doc["against_baseline"])

    def test_nothing_decided_yet(self, parts):
        desk = service(parts, {"AAA": "2026-11-20", "BBB": "2026-11-20"})
        desk.take("test", NOW)
        sc = desk.scorecard()
        assert sc.judges == () and sc.duels == ()

    def test_the_command(self, parts, monkeypatch, capsys, tmp_path):
        from quant_hub import desk as cli
        desk = self.scored_desk(parts)

        class Core:
            config = type("Config", (), {"backup_folder": tmp_path,
                                         "db_path": tmp_path / "market_data.db"})()

            def __init__(self):
                self.desk, self.activity = desk, parts[0]

            def shutdown(self):
                pass
        monkeypatch.setattr(cli, "build_core", Core)
        assert cli.main(["scorecard"]) == 0
        out = capsys.readouterr().out
        assert "me:" in out and "vs baseline" in out and "vs llm" in out and "wins" in out
        assert cli.main(["scorecard", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["kind"] == "scorecard"
