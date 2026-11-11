r"""The literature canon: papers, typed links, strands and eras (Lit-1).

This module holds the plain records and
the checks; ``storage/canon_store.py`` reads them from the JSON files in
``data/canon/``, one file per strand plus ``timeline.json`` for the
strands' order and the era bands.

A paper
-------
Each entry is a published paper (or book, or talk) with its metadata, three
sentences in your own words (the question, what it changed, what it
assumed), the repository modules that implement it (``lives_in``), typed
links to earlier canon papers, and a record of how its metadata was
checked. A paper's strand is the file it sits in.

Links and the time rule
-----------------------
A link p → q says how p relates to an earlier paper q:

* ``builds_on``, ``generalizes``, ``corrects``, ``challenges``, ``tests``,
  ``rediscovers``, ``fails_to_replicate``: directed, from later to earlier;
* ``independent``: simultaneous discovery (Black–Scholes and Merton), so
  undirected.

Let :math:`t(p)` be the year p first circulated, or its publication year
when that's all we know. A paper can only build on work that existed, so
every directed link needs

.. math::  t(q) \le t(p).

The rule allows a cycle only among papers sharing a year, so the checks
also require the directed links to form a DAG (Kahn's algorithm: repeatedly
remove nodes with no remaining outgoing links; anything left is on a cycle).

Labels
------
A paper's short label is its first author's surname, a second surname or
"et al.", and :math:`t(p)`: "Black–Scholes 1973", "Fama et al. 1969".
Surnames keep lower-case particles and capitalized "De"/"Van" ("De Bondt");
generational suffixes ("Jr.", "3rd") are dropped. An entry may set
``label`` where the rule can't know (a double surname: "López de Prado").

Verification
------------
``verified.via`` is ``crossref`` (the DOI's record matched title, first
author and year), ``arxiv`` (the same against arXiv), or ``none`` for a
book, magazine piece or talk with no registered identifier; ``none``
carries a note saying why. Nothing here touches the network: the checks
were run when the canon was written and are recorded, not repeated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

LINK_KINDS = {
    "builds_on": "builds on",
    "generalizes": "generalizes",
    "corrects": "corrects",
    "challenges": "challenges",
    "tests": "tests",
    "rediscovers": "rediscovers",
    "fails_to_replicate": "fails to replicate",
    "independent": "found independently with",
}
# How each kind reads from the other end ("Mandelbrot 1963 challenges
# Bachelier 1900" -> Bachelier's card lists "challenged by Mandelbrot 1963").
INCOMING = {
    "builds_on": "built on by",
    "generalizes": "generalized by",
    "corrects": "corrected by",
    "challenges": "challenged by",
    "tests": "tested by",
    "rediscovers": "rediscovered by",
    "fails_to_replicate": "failed to replicate in",
    "independent": "found independently with",
}
CRITIQUES = frozenset({"corrects", "challenges", "fails_to_replicate"})
VERIFIED_VIA = frozenset({"crossref", "arxiv", "none"})

_ID = re.compile(r"[a-z0-9-]+")
_PARTICLES = {"de", "da", "di", "du", "van", "von", "der", "den", "la", "le", "De", "Van", "Von"}
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "3rd"}


class CanonError(ValueError):
    """The canon breaks one or more rules; ``problems`` lists them all."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class Link:
    to: str
    kind: str

    @property
    def directed(self) -> bool:
        return self.kind != "independent"


@dataclass(frozen=True)
class Verification:
    via: str                        # crossref | arxiv | none
    checked: str = ""               # ISO date the check ran
    matched: tuple[str, ...] = ()   # fields that matched the record
    note: str = ""                  # why "none"


@dataclass(frozen=True)
class CanonPaper:
    id: str
    title: str
    authors: tuple[str, ...]
    year: int
    venue: str
    strand: str
    changed: str
    verified: Verification
    question: str = ""
    assumed: str = ""
    lives_in: tuple[str, ...] = ()
    links: tuple[Link, ...] = ()
    circulated: int | None = None
    doi: str = ""
    arxiv: str = ""
    url: str = ""
    label_override: str = ""

    @property
    def when(self) -> int:
        """t(p): the year it first circulated, else its publication year."""
        return self.circulated if self.circulated is not None else self.year

    @property
    def implemented(self) -> bool:
        return bool(self.lives_in)

    @property
    def source_url(self) -> str:
        if self.doi:
            return f"https://doi.org/{self.doi}"
        if self.arxiv:
            return f"https://arxiv.org/abs/{self.arxiv}"
        return self.url

    @property
    def label(self) -> str:
        if self.label_override:
            return self.label_override
        names = [surname(a) for a in self.authors]
        if len(names) == 1:
            who = names[0]
        elif len(names) == 2:
            who = f"{names[0]}–{names[1]}"
        else:
            who = f"{names[0]} et al."
        return f"{who} {self.when}"


@dataclass(frozen=True)
class Strand:
    id: str
    name: str
    blurb: str = ""


@dataclass(frozen=True)
class Era:
    start: int
    end: int
    name: str          # short, for a narrow band
    long: str = ""     # shown when the band has room


@dataclass(frozen=True)
class Canon:
    strands: tuple[Strand, ...]
    eras: tuple[Era, ...]
    papers: tuple[CanonPaper, ...]
    _by_id: dict[str, CanonPaper] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_id", {p.id: p for p in self.papers})

    def paper(self, paper_id: str) -> CanonPaper:
        return self._by_id[paper_id]

    def __contains__(self, paper_id: object) -> bool:
        return paper_id in self._by_id

    def cited_by(self, paper_id: str) -> list[tuple[CanonPaper, Link]]:
        """Later papers linking to ``paper_id``, each with its link."""
        return [(p, link) for p in self.papers for link in p.links if link.to == paper_id]


def surname(author: str) -> str:
    """The surname an author is cited by (module docstring, Labels)."""
    tokens = [t for t in author.replace(",", " ").split() if t.lower() not in _SUFFIXES]
    if not tokens:
        return author
    i = len(tokens) - 1
    while i > 0 and tokens[i - 1] in _PARTICLES:
        i -= 1
    return " ".join(tokens[i:])


def _on_cycles(papers: Iterable[CanonPaper]) -> set[str]:
    """Ids left on a directed cycle (empty when the links form a DAG).
    Links to missing papers are reported elsewhere and ignored here."""
    papers = list(papers)
    ids = {p.id for p in papers}
    out = {p.id: {l.to for l in p.links if l.directed} & ids for p in papers}
    incoming = {pid: 0 for pid in out}
    for targets in out.values():
        for q in targets:
            incoming[q] += 1
    ready = [pid for pid, n in incoming.items() if n == 0]
    removed = set()
    while ready:
        pid = ready.pop()
        removed.add(pid)
        for q in out[pid]:
            incoming[q] -= 1
            if incoming[q] == 0:
                ready.append(q)
    return set(out) - removed


def validate(canon: Canon) -> list[str]:
    """Every rule the canon breaks, as readable problems (empty: valid)."""
    problems = []
    strands = {s.id for s in canon.strands}
    if len(strands) != len(canon.strands):
        problems.append("two strands share an id")
    seen: set[str] = set()
    for p in canon.papers:
        if p.id in seen:
            problems.append(f"{p.id}: duplicate id")
        seen.add(p.id)
    by_id = {p.id: p for p in canon.papers}
    for p in canon.papers:
        where = f"{p.id}"
        if not _ID.fullmatch(p.id):
            problems.append(f"{where}: id must be lower-case letters, digits and hyphens")
        if p.strand not in strands:
            problems.append(f"{where}: unknown strand {p.strand!r}")
        if not (p.title and p.authors and p.venue and p.changed):
            problems.append(f"{where}: title, authors, venue and 'changed' are required")
        if p.circulated is not None and p.circulated > p.year:
            problems.append(f"{where}: circulated after it was published")
        if p.verified.via not in VERIFIED_VIA:
            problems.append(f"{where}: unknown verification {p.verified.via!r}")
        if p.verified.via == "none" and not p.verified.note:
            problems.append(f"{where}: an unverified entry needs a note saying why")
        if not (p.doi or p.arxiv or p.url) and p.verified.via != "none":
            problems.append(f"{where}: no DOI, arXiv id or URL")
        for link in p.links:
            if link.kind not in LINK_KINDS:
                problems.append(f"{where}: unknown link kind {link.kind!r}")
            target = by_id.get(link.to)
            if target is None:
                problems.append(f"{where}: links to missing paper {link.to!r}")
            elif link.to == p.id:
                problems.append(f"{where}: links to itself")
            elif link.directed and target.when > p.when:
                problems.append(f"{where} ({p.when}) {link.kind} {link.to} ({target.when}): "
                                "a link must point back in time")
    for era in canon.eras:
        if era.end < era.start:
            problems.append(f"era {era.name!r} ends before it starts")
    cycle = _on_cycles(canon.papers)
    if cycle:
        problems.append(f"directed links form a cycle among {sorted(cycle)}")
    return problems


def _paper(raw: Mapping, strand: str) -> CanonPaper:
    verified = raw.get("verified") or {}
    return CanonPaper(
        id=raw["id"], title=raw["title"], authors=tuple(raw["authors"]), year=int(raw["year"]),
        venue=raw.get("venue", ""), strand=strand, changed=raw.get("changed", ""),
        verified=Verification(verified.get("via", ""), verified.get("checked", ""),
                              tuple(verified.get("matched", ())), verified.get("note", "")),
        question=raw.get("question", ""), assumed=raw.get("assumed", ""),
        lives_in=tuple(raw.get("lives_in", ())),
        links=tuple(Link(l["to"], l["kind"]) for l in raw.get("links", ())),
        circulated=int(raw["circulated"]) if raw.get("circulated") is not None else None,
        doi=raw.get("doi", ""), arxiv=raw.get("arxiv", ""), url=raw.get("url", ""),
        label_override=raw.get("label", ""))


def canon_from_dicts(timeline: Mapping, papers_by_strand: Mapping[str, Sequence[Mapping]]) -> Canon:
    """Build and check a canon from parsed JSON. Raises ``CanonError``
    listing every problem, so one fix-and-retry sees them all."""
    try:
        strands = tuple(Strand(s["id"], s["name"], s.get("blurb", "")) for s in timeline["strands"])
        eras = tuple(Era(int(e["start"]), int(e["end"]), e["name"], e.get("long", ""))
                     for e in timeline.get("eras", ()))
        papers = tuple(_paper(raw, strand) for strand, entries in papers_by_strand.items()
                       for raw in entries)
    except (KeyError, TypeError, ValueError) as exc:
        raise CanonError([f"malformed entry: {exc!r}"]) from exc
    canon = Canon(strands, eras, papers)
    problems = validate(canon)
    if problems:
        raise CanonError(problems)
    return canon
