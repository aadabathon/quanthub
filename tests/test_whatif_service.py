"""The What-if job (ALLOCATION A3, services/whatif.py), on the review tests'
made-up book: today's vector analysed equals Portfolio's analysis of the
snapshot, every vector graded as the review grades the book (the core
exempt), the builders and references from the context, missing history
named, and named sets kept in the view-state store."""

import pytest

from quant_hub.models.regime import Outcome
from quant_hub.services.whatif import MAX_SHARPE, REFERENCES, WhatIfService
from quant_hub.storage.state_store import ViewStateStore
from test_review_service import world  # noqa: F401  (the fixture)


@pytest.fixture
def whatif(world):  # noqa: F811
    review = world[0]
    service = WhatIfService(review._portfolio, review, ViewStateStore(":memory:"))
    return service, review


def test_todays_vector_is_the_books_and_analyses_the_same(whatif):
    service, review = whatif
    ctx = service.context()
    assert ctx.today == {"AAA": pytest.approx(0.4), "BBB": pytest.approx(0.2),
                         "CCC": pytest.approx(0.1)}
    assert ctx.core == frozenset({"AAA"}) and ctx.source == "file" and ctx.benchmark == "SPY"
    (row,) = service.compare(ctx, {"Today": ctx.today}).rows
    actual, _ = review.current_view(now=ctx.as_of)            # the same window
    assert row.view.report.vol == pytest.approx(actual.report.vol, rel=1e-9)
    assert row.view.report.beta == pytest.approx(actual.report.beta, rel=1e-9)
    assert row.beta[0] == pytest.approx(actual.regressions.beta("Portfolio")[0], rel=1e-9)


def test_every_vector_is_graded_as_the_review_grades_the_book(whatif):
    service, _ = whatif
    ctx = service.context()
    rows = service.compare(ctx, {"Today": ctx.today, "Index": {"SPY": 0.7}}).rows
    today, index = rows
    weights = {c.item: c.check for c in today.graded.checks if c.check.rule.key == "4.max-weight"}
    assert weights["AAA"].outcome is Outcome.EXEMPT                # A1: the core
    assert weights["BBB"].outcome is Outcome.BREACH and today.fails >= 1
    assert today.graded.netting is not None and today.graded.netting.core == ("AAA",)
    assert {c.check.rule.key for c in today.graded.checks} >= {"4.max-weight", "4.cash", "4.beta",
                                                                "3.size.AAA"}
    assert all(c.check.rule.key[0] in "34" for c in today.graded.checks)
    assert index.graded.netting is None                            # no core held
    assert index.top_risk[0] == "SPY" and index.top_risk[1] == pytest.approx(1.0)


def test_the_builders_and_references_come_from_the_context(whatif):
    service, _ = whatif
    ctx = service.context()
    scaled = service.scaled(ctx, 0.15)
    assert scaled["AAA"] == pytest.approx(0.4)
    assert scaled["BBB"] + scaled["CCC"] == pytest.approx(0.15)
    netted = service.netted(ctx, target=0.6)
    book_beta = sum(w * ctx.betas[s] for s, w in netted.items())
    assert book_beta == pytest.approx(0.6) and set(netted) == {"AAA", "BBB", "CCC"}
    refs = service.references(ctx)
    assert tuple(refs) == REFERENCES and MAX_SHARPE.endswith("(in-sample)")
    for weights in refs.values():
        assert set(weights) <= set(ctx.today) and sum(weights.values()) == pytest.approx(0.7)


def test_a_vector_without_history_says_so(whatif):
    service, _ = whatif
    ctx = service.context()
    (row,) = service.compare(ctx, {"Unknown": {"ZZZ": 0.5}}).rows
    assert row.view is None and "ZZZ" in row.error
    (row,) = service.compare(ctx, {"Too much": {"AAA": 0.8, "BBB": 0.5}}).rows
    assert "leverage" in row.error


def test_named_sets_are_kept_in_the_view_state_store(whatif):
    service, _ = whatif
    assert service.saved() == {}
    service.save(" Barbell ", {"spy": 0.5, "BBB": 0.1})
    service.save("Index", {"SPY": 0.9})
    assert service.saved() == {"Barbell": {"SPY": 0.5, "BBB": 0.1}, "Index": {"SPY": 0.9}}
    service.delete("Barbell")
    assert list(service.saved()) == ["Index"]
    with pytest.raises(ValueError):
        service.save("", {"SPY": 0.5})
    with pytest.raises(ValueError, match="leverage"):
        service.save("Levered", {"SPY": 0.8, "QQQ": 0.4})
