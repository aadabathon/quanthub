"""The literature canon (Lit-1): its rules hold, and it covers the code."""
from pathlib import Path

import pytest

import quant_hub
from quant_hub.data.literature import CanonError, CanonPaper, Verification, canon_from_dicts, surname
from quant_hub.storage.canon_store import load_canon

REPO = Path(quant_hub.__file__).parent.parent
# Model modules with no paper of their own, and why.
NOT_FROM_A_PAPER = {
    "backtest.py": "daily accounting (R1)",
    "optimize.py": "a numerical minimizer, Nelder–Mead",
    "research_worlds.py": "synthetic controls with planted edges",
}


@pytest.fixture(scope="module")
def canon():
    return load_canon()


def test_the_shipped_canon_loads_and_every_entry_is_checked_or_says_why(canon):
    assert len(canon.papers) >= 200
    assert {p.strand for p in canon.papers} == {s.id for s in canon.strands}
    for paper in canon.papers:
        assert paper.verified.via in {"crossref", "arxiv", "none"}, paper.id
        assert paper.verified.checked, paper.id
        if paper.verified.via == "none":
            assert paper.verified.note, paper.id
        else:
            assert set(paper.verified.matched) == {"title", "first_author", "year"}, paper.id
            assert paper.doi or paper.arxiv, paper.id


def test_every_model_module_is_implemented_from_the_literature(canon):
    """Coverage (LITERATURE_PLAN Lit-1): adding a model without its papers
    fails here, and so does exempting a module the canon already covers."""
    covered = {path for p in canon.papers for path in p.lives_in}
    models = {p.name for p in (REPO / "quant_hub" / "models").glob("*.py") if p.name != "__init__.py"}
    missing = {m for m in models - NOT_FROM_A_PAPER.keys() if f"quant_hub/models/{m}" not in covered}
    assert not missing, f"no canon paper lives in {sorted(missing)}"
    assert not {f"quant_hub/models/{m}" for m in NOT_FROM_A_PAPER} & covered


def test_every_lives_in_path_exists(canon):
    for paper in canon.papers:
        for path in paper.lives_in:
            assert (REPO / path).is_file(), f"{paper.id}: {path}"


def test_ids_your_notes_hang_on_are_kept(canon):
    # The D42 seeds and the papers imported into the owner's catalog.
    for paper_id in ("bachelier1900", "sharpe1964", "famafrench1993", "dupire1994",
                     "gatheraljacquier2012", "markowitz1952", "blackscholes1973",
                     "carrmadan1999", "famafrench2004"):
        assert paper_id in canon


def test_links_read_both_ways(canon):
    later = {p.id: link.kind for p, link in canon.cited_by("bachelier1900")}
    assert later["mandelbrot1963"] == "challenges" and later["osborne1959"] == "corrects"
    assert canon.paper("blackscholes1973").source_url == "https://doi.org/10.1086/260062"


# --------------------------------------------------- the rules, on a toy canon

TIMELINE = {"strands": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
            "eras": [{"start": 1900, "end": 1950, "name": "Early"}]}
CHECKED = {"via": "crossref", "checked": "2026-11-02", "matched": ["title", "first_author", "year"]}


def entry(pid, year, links=(), **extra):
    return {"id": pid, "title": f"Paper {pid}", "authors": ["Ann Author"], "year": year,
            "venue": "J", "doi": f"10.1/{pid}", "changed": "Something.", "lives_in": [],
            "links": [{"to": to, "kind": kind} for to, kind in links], "verified": CHECKED, **extra}


def problems(*entries, strand="a"):
    with pytest.raises(CanonError) as caught:
        canon_from_dicts(TIMELINE, {strand: list(entries)})
    return " | ".join(caught.value.problems)


def test_a_valid_toy_canon_builds():
    canon = canon_from_dicts(TIMELINE, {"a": [entry("old1900", 1900)],
                                        "b": [entry("new1950", 1950, [("old1900", "builds_on")])]})
    assert canon.paper("new1950").strand == "b"
    assert [p.id for p, _ in canon.cited_by("old1900")] == ["new1950"]


def test_links_must_point_back_in_time():
    assert "point back in time" in problems(entry("old1900", 1900, [("new1950", "builds_on")]),
                                            entry("new1950", 1950))


def test_the_time_rule_reads_the_circulated_year():
    # Published 2014 but circulated 2012, so a 2013 paper can build on it.
    canon_from_dicts(TIMELINE, {"a": [entry("ssvi2012", 2014, circulated=2012),
                                      entry("user2013", 2013, [("ssvi2012", "builds_on")])]})
    assert "circulated after" in problems(entry("odd2000", 2000, circulated=2001))


def test_same_year_cycles_are_caught_but_independent_discoveries_are_not():
    assert "cycle" in problems(entry("x1973", 1973, [("y1973", "builds_on")]),
                               entry("y1973", 1973, [("x1973", "generalizes")]))
    canon_from_dicts(TIMELINE, {"a": [entry("x1973", 1973, [("y1973", "independent")]),
                                      entry("y1973", 1973, [("x1973", "independent")])]})


@pytest.mark.parametrize("bad, expected", [
    ([entry("dup1990", 1990), entry("dup1990", 1991)], "duplicate id"),
    ([entry("x1990", 1990, [("ghost", "builds_on")])], "missing paper"),
    ([entry("x1990", 1990, [("x1990", "builds_on")])], "itself"),
    ([entry("y1980", 1980), entry("x1990", 1990, [("y1980", "admires")])], "unknown link kind"),
    ([entry("Bad Id", 1990)], "lower-case"),
    ([entry("x1990", 1990, doi="")], "no DOI"),
    ([entry("x1990", 1990, doi="", verified={"via": "none", "checked": "2026-11-02"})], "needs a note"),
    ([entry("x1990", 1990, changed="")], "required"),
])
def test_validation_names_each_problem(bad, expected):
    assert expected in problems(*bad)


def test_unknown_strand_and_book_without_identifier():
    with pytest.raises(CanonError, match="unknown strand"):
        canon_from_dicts(TIMELINE, {"zzz": [entry("x1990", 1990)]})
    book = entry("w1938", 1938, doi="", verified={"via": "none", "checked": "2026-11-02",
                                                  "note": "A book; no DOI."})
    assert canon_from_dicts(TIMELINE, {"a": [book]}).paper("w1938").source_url == ""


# ------------------------------------------------------------------- labels

def paper(authors, year=1990, **extra):
    return CanonPaper("p", "T", tuple(authors), year, "J", "a", "C", Verification("none"), **extra)


@pytest.mark.parametrize("name, expected", [
    ("Robert E. Lucas Jr.", "Lucas"), ("Alfred Cowles 3rd", "Cowles"),
    ("Werner F. M. De Bondt", "De Bondt"), ("J. Bradford De Long", "De Long"),
    ("Ľuboš Pástor", "Pástor"), ("Mark Britten-Jones", "Britten-Jones"),
])
def test_surnames(name, expected):
    assert surname(name) == expected


def test_labels_name_one_two_or_many_authors_and_the_year_it_circulated():
    assert paper(["Fischer Black", "Myron Scholes"], 1973).label == "Black–Scholes 1973"
    assert paper(["A B", "C D", "E F"], 1969).label == "B et al. 1969"
    assert paper(["Jim Gatheral"], 2014, circulated=2012).label == "Gatheral 2012"
    assert paper(["Marcos López de Prado"], label_override="López de Prado 2016").label \
        == "López de Prado 2016"
