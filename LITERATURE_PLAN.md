# LITERATURE_PLAN.md — a timeline of the literature, tied to the code

Proposal, 2026-10-25. Planning only: no app code changes. It builds on
the Papers catalog (D42) and doesn't reorder or replace
[LENS_PLAN.md](LENS_PLAN.md), [HORIZON_PLAN.md](HORIZON_PLAN.md) or
[EXECUTION_PLAN.md](EXECUTION_PLAN.md). Each milestone needs your
approval before it starts, and **do not build ahead** (AGENTS.md hard
rule 5) still holds.

## Why this exists

Your direction of 2026-10-25: every open-ended, teaching project you run
(Quant Hub, ML Cookbook, more later) gets a literature vault. It's a
timeline of the field in its papers. For quant finance, it starts with
Bachelier's *Théorie de la spéculation* (1900) and runs up to what the
journals publish now.

What exists today:
- **The Papers catalog (D42)** is a flat list. It holds metadata, local
  PDF copies, notes, reading status and links to experiments. Five
  foundations are seeded (`_FOUNDATIONS` in `services/research.py`), and
  Bachelier is one of them.
- **LENS_PLAN's workshop pattern** has four parts: the paper, the
  derivation, the result on your holdings, and a test against a planted
  truth.
- **HORIZON_PLAN row 8** ("all of the quant finance literature") points
  at those two and plans nothing new.

What's missing is the order and the links: how the field got from
Bachelier to now, and where each step lives in your code.

## What makes it worth using over Zotero

Zotero, Connected Papers and Semantic Scholar already store papers and
draw citation graphs, and they do it better than this app will. Don't
compete with them. The vault earns its place with three things they
can't do:

1. **It's tied to your code.** Every paper names the modules that
   implement it.
2. **It records replications.** It shows whether you've reproduced a
   result, and where.
3. **It holds your narrative.** For each paper: the question, what it
   changed, what it assumed, and what later broke it.

If a proposed feature uses none of the three, use the existing tool
instead.

## The design in three ideas

**1. Tied to code: the start date falls out.** Each strand starts from a
module and is traced back to its root. The timeline is all those
lineages combined, so nobody has to pick a start date. Quant Hub's
earliest root is Bachelier, 1900. ML Cookbook's is least squares,
1805/1809.

**2. Two tiers: a canon and an inbox.**
- The canon is curated and small, at most a couple of hundred papers.
  They're the papers that changed what people do, or that your code
  implements.
- The inbox holds what's new, from journals and arXiv, or what an LLM
  proposed. Most of it gets dismissed.
- Nothing moves from the inbox to the canon without you.

**3. The links are the point.** A list sorted by year is a bibliography.
The links make it a history: Mandelbrot *challenges* Bachelier, Roll
*challenges* CAPM tests, Harvey–Liu–Zhu *challenges* the factor zoo.

## The format

### An entry

One JSON file per canon paper, named by its id:

```json
{
  "id": "bachelier1900",
  "title": "Théorie de la spéculation",
  "authors": ["Louis Bachelier"],
  "year": 1900,
  "venue": "Annales scientifiques de l'École normale supérieure",
  "doi": "10.24033/asens.476",
  "url": "https://archive.numdam.org/articles/10.24033/asens.476/",
  "strand": "random-prices",
  "question": "If the market is fair, how must a price move?",
  "changed": "Prices as a driftless random walk: Brownian motion five years before Einstein, and the first option prices.",
  "assumed": "Arithmetic Gaussian steps, so a price can go negative.",
  "lives_in": ["quant_hub/data/synthetic.py"],
  "links": [],
  "verified": {"via": "doi", "checked": "2026-10-25", "matched": ["title", "year", "first_author"]}
}
```

`verified` here only shows the shape of the record. Lit-1 writes the
real one. A later entry links back to it. For example, Osborne (1959)
carries `{"to": "bachelier1900", "kind": "corrects"}`.

| Field | Meaning | Required |
|---|---|---|
| `id` | First author + year, a stable key. It matches the seeded ids, so your existing notes carry over | yes |
| `title`, `authors`, `year`, `venue` | As published | yes (`venue` may be a book or a talk) |
| `circulated` | Year of the first public version, if earlier than `year` (working papers, Lloyd 1957/1982) | no |
| `doi`, `arxiv`, `url` | At least one. Books and talks may have only a URL or ISBN | one of them |
| `strand` | Its lane on the timeline (below) | yes |
| `question`, `changed`, `assumed` | Your words, one sentence each. No quotations | `changed` yes |
| `lives_in` | Repository paths that implement it. Empty means "not in the code yet" | yes (may be empty) |
| `links` | Typed links to earlier canon papers | yes (may be empty) |
| `verified` | How the metadata was checked, and when | yes |

### Link kinds

Every link points from a paper to an **earlier** one: a paper can only
build on work that existed when it was written.

| Kind | Example |
|---|---|
| `builds_on` | Bollerslev 1986 → Engle 1982 |
| `generalizes` | Carhart 1997 → Fama–French 1993 |
| `corrects` | Osborne 1959 → Bachelier 1900 (log prices) |
| `challenges` | Mandelbrot 1963 → Bachelier 1900 (fat tails) |
| `tests` | Fama–MacBeth 1973 → Sharpe 1964 |
| `rediscovers` | Samuelson 1965 → Bachelier 1900 (found again through Savage in the 1950s) |
| `fails_to_replicate` | Reserved for the replication literature |
| `independent` | Undirected, for simultaneous discovery: Black–Scholes and Merton 1973 |

**The time rule.** Let t(p) = `circulated` if present, else `year`. Every
directed link p → q needs t(q) ≤ t(p). The rule doesn't rule out cycles
on its own, because two papers from the same year can point at each
other. So the validator also checks that the directed links form a DAG.

### Strands (the timeline's lanes)

Proposed for Quant Hub, settled at Lit-0:
- random prices
- options and volatility
- portfolios and asset pricing
- time series and regimes
- sizing and growth
- valuation
- efficiency and methodology
- machine learning in finance

Era bands (for example "the option-pricing revolution, 1973–1981") are
optional background labels, kept in a separate small file. They're
interpretation, not data.

### Where it's kept

| Layer | Holds | Store | Shared? |
|---|---|---|---|
| **Canon** | The entries above | JSON files, committed (recommended: package data under `quant_hub/literature/`, so an installed copy has it) | Yes: the format moves between projects |
| **Personal** | Reading status, notes, PDFs, experiment links | The existing `research_papers` table and the `.papers` folder (D42), keyed by the same id | No: local, never committed |
| **Inbox** | Proposed and fetched papers awaiting triage | SQLite, with provenance (D34's "stored as fetched") | No |

The Papers page merges the layers. Canon fields come from the canon
files, and personal fields from SQLite. `research_papers` already stores
each row as a JSON blob, so this needs **no schema migration**.

Promoting a paper writes its JSON file into the canon directory. In your
editable install that's the repo, so it shows up as a diff for you to
review and commit. Nothing is committed automatically.

## The spine: a first canon (Lit-1's draft)

These papers are listed by author and year only. **None is canon until
Lit-1 verifies it.** That includes the five D42 seeds, whose links are
already in the code. Papers marked "not yet" have no code: they
show as hollow nodes on the timeline, and HORIZON or LENS milestones may
fill them in.

| Strand | Papers | Lives in |
|---|---|---|
| Random prices | Bachelier 1900 → Osborne 1959 (log prices) → Mandelbrot 1963 (fat tails) → Samuelson 1965 (properly anticipated prices) | `data/synthetic.py` |
| Options & vol | Black–Scholes 1973 ∥ Merton 1973 → Boyle 1977 (Monte Carlo) → Cox–Ross–Rubinstein 1979 → Harrison–Kreps 1979, Harrison–Pliska 1981 → Heston 1993, Dupire 1994 → Gatheral 2004 (SVI) → Gatheral–Jacquier 2014 (SSVI) | `black_scholes.py`, `carry.py`, `option_monte_carlo.py`, `vol_surface.py`, `market_surface.py`, `svi.py`, `ssvi_fit.py`. CRR not yet (HORIZON X1) |
| Portfolios & pricing | Markowitz 1952 → Tobin 1958 → Sharpe 1964 → Fama–MacBeth 1973 → Ross 1976 → Roll 1977 → Gibbons–Ross–Shanken 1989 → Fama–French 1993 → Carhart 1997. Black–Litterman 1992, Ledoit–Wolf 2004 | `optimize.py`, `portfolio.py`, `regression.py`. GRS, BL and LW not yet (LENS W2, HORIZON P2) |
| Time series & regimes | Kalman 1960 → Baum et al. 1970 → Engle 1982 → Bollerslev 1986 → Hamilton 1989 | `kalman.py`, `garch.py`, `markov.py` |
| Sizing & growth | Kelly 1956 → Breiman 1961; Moreira–Muir 2017 | `growth.py`. Moreira–Muir not yet (LENS queue) |
| Valuation | Williams 1938 → Modigliani–Miller 1958 → Gordon 1959 | `dcf.py` |
| Efficiency & methodology | Cowles 1933 → Kendall 1953 → Fama 1970 → Shiller 1981 → Poterba–Summers 1988 → Lo–MacKinlay 1990 → Jegadeesh–Titman 1993 → White 2000 → Lo 2002 → Moskowitz–Ooi–Pedersen 2012 → Bailey–López de Prado 2014 → Harvey–Liu–Zhu 2016 | `strategies.py`, `backtest.py`, `backtest_stats.py` |
| ML in finance | Gu–Kelly–Xiu 2020 | not yet |

That's about 45 papers. The arrows show the reading order within each
strand. The typed links are written at Lit-1.

## LLMs, without invented citations

You'll want an LLM's help, mostly for ML Cookbook, where the lineages
are wider. LLMs invent plausible citations. A real DOI attached to the
wrong title is the worst case, because it looks verified. So an LLM
only ever **proposes**:

1. **Propose.** The LLM writes a *proposal file*: papers with title,
   authors, year, DOI or arXiv id, strand, links into the canon by id,
   and one line on why. The file records the model, the prompt and the
   date. The prompt starts from a module ("trace this module's lineage
   back to its root") and lists the canon's ids, so the LLM links into
   existing papers instead of re-proposing them.
2. **Check.** The resolver looks each id up. It requires the
   normalized title, first-author surname and year (±1, for
   online-first dates) to match. A mismatch is rejected, and the reason
   is recorded.
3. **Queue.** Papers that pass go to the inbox with their provenance.
4. **Approve.** You promote a paper to the canon, or dismiss it.

The proposal file is plain JSON, so any LLM can produce one: Claude in a
session, or a local model through ML Cookbook's Ollama recipe. The app
never has to call a model.

## Milestones

Lit-0 and Lit-1 come first, in that order. After Lit-1, Lit-2 and Lit-3
can come in either order. Lit-4 and Lit-5 need Lit-3, and Lit-6 needs
Lit-2. Module names are settled at each milestone's start, as in
HORIZON_PLAN.

### Lit-0. The spec (documents only)

The format above, finalized: fields, link kinds, the time rule, strands,
the verification record, where the canon lives, and the merge rule
with the personal layer. It's written once, project-neutral, so ML
Cookbook can copy it.

**Done when:** you approve the spec.

### Lit-1. The spine, by hand

- About 45 entries (the table above), each written in your words, each
  `verified` against its DOI, arXiv id or URL. Books and talks get a
  manual check, and the record says so.
- The canon replaces the five `_FOUNDATIONS` seeds. Their ids are kept,
  so your notes and PDFs attach to the same entries.
- The pure checks go in `data/` (types, validation, graph), and loading
  goes in `storage/`, consistent with the layering test.

Tests:
- Every link target exists.
- Directed links obey the time rule and form a DAG.
- Every entry has a `verified` record.
- Every `lives_in` path exists, so renaming a module breaks the test,
  not the vault.
- **Coverage:** every module in `models/` is implemented by at least one
  canon paper, or appears on a short, explicit exemption list (plumbing
  such as `strategy.py`, and your own rules such as `regime.py`).
  Adding a model without its literature fails the suite.

**Done when:** the suite passes and the Papers page shows the canon
merged with your notes.

### Lit-2. The timeline view

- A Timeline view in Research, drawn with `gui/charts.py` (pyqtgraph).
- One lane per strand, with year on the x axis. The axis is compressed
  before 1950, where papers are sparse (piecewise-linear, settled at
  Lit-2).
- Implemented papers are filled; "not yet" papers are hollow.
- Links are drawn as curves, styled by kind, so a `challenges` link
  stands out from a `builds_on` link.
- Clicking a paper opens a card in the side panel with its question,
  what it changed, what it assumed, what challenged it later, its
  modules, its PDF and its linked runs. Mind the wrapped-text traps
  (D48, D49): give each `Note` its width.

**Done when:** the smoke test passes and screenshots at 1280 width are
reviewed.

### Lit-3. The resolver

- Providers in `providers/` (stdlib `urllib`, `data/` only), each behind
  a terms check first (D34):
  - **Crossref** for DOIs
  - **arXiv** for preprints
  - **OpenAlex** for reference lists and "cited by"
- It fills in metadata for a DOI or arXiv id, runs the match rule, and
  suggests links from a paper's reference list. Suggestions only.
- Tests against planted cases:
  - A made-up DOI fails.
  - A real DOI with the wrong title fails.
  - A right paper with a one-year online-first offset passes.
  - Diacritics fold ("Bachelier" vs "Bachélier").

**Done when:** those tests pass, offline, against recorded responses.

### Lit-4. LLM proposals

- The proposal-file format and the lineage prompt (above).
- Importing a proposal file runs every entry through the resolver into
  the inbox, recording which model, prompt and date proposed it.
- Inbox triage on the Papers page: promote, dismiss, or edit then
  promote.

**Done when:** a planted proposal file with mixed real and invented
papers comes out sorted correctly, with reasons.

### Lit-5. What's new

- On demand (a button on the TaskRunner, no scheduler): fetch recent
  works that cite canon papers, from chosen venues (JF, JFE, RFS,
  Mathematical Finance, Finance and Stochastics, Quantitative Finance)
  and arXiv's q-fin categories.
- **Ranking, to derive at Lit-5:** a candidate paper p scores

      s(p) = Σ_{j ∈ canon ∩ refs(p)} log(C / c_j)

  where C is the number of candidates fetched and c_j is how many of
  them cite canon paper j. Citing Black–Scholes says little; citing
  Moreira–Muir says a lot. This is the same weighting as tf-idf.
- Candidates above a threshold go to the inbox and appear at the
  timeline's right edge in their strand, until you promote or dismiss
  them.
- **A known limit:** reference lists for brand-new preprints may be
  missing from the index. How well OpenAlex covers them is checked at
  Lit-5's start, and the page says so.

**Done when:** a recorded fetch reproduces the same ranked inbox.

### Lit-6. Reading paths

- **A reading path to any paper:** its ancestors through the directed
  links, in topological order with ties broken by year. A depth limit
  keeps late papers readable, because Gu–Kelly–Xiu's ancestors are half
  the canon.
- **What broke it:** the incoming `challenges`, `corrects` and
  `fails_to_replicate` links, shown on the card.
- **Replication status,** derived, not typed:
  - *implemented:* `lives_in` is set and the module has its tests
  - *replicated:* a saved Research run on real data links to the paper
  - This closes LENS's workshop loop.

**Done when:** paths and statuses are tested on a small planted graph.

## Carrying it to ML Cookbook and later projects

- **Share the format from Lit-0.** That's what makes a vault portable.
- **Share code only when a third project arrives.** Quant Hub builds it
  first, because the catalog exists and the start date is clear. ML
  Cookbook copies it after Lit-2. A shared package gets extracted only
  once three copies show what's actually common to them. Extracting it
  earlier means designing for differences you can't see yet.
- **ML Cookbook's lineages start from its recipes.** It follows the same
  rule as here, with one difference: the LLM path (Lit-4) carries more
  of the work, because the field is wider. Sketch:
  - linear regression: Legendre 1805 / Gauss 1809 → Hoerl–Kennard 1970 →
    Tibshirani 1996
  - logistic regression: Cox 1958
  - trees: Breiman et al. 1984 → Quinlan 1986 → Breiman 2001
  - SVM: Cortes–Vapnik 1995
  - neural nets: McCulloch–Pitts 1943 → Rosenblatt 1958 →
    Rumelhart–Hinton–Williams 1986
  - PCA and clustering: Pearson 1901 → Hotelling 1933; Lloyd 1957/1982
    → MacQueen 1967
  - tests: Pearson 1900 (χ²) → Fisher 1922 (its degrees of freedom) →
    Benjamini–Hochberg 1995
  - LLMs: Vaswani et al. 2017 → Radford et al. 2019 (GPT-2)
- ML Cookbook's own stack and layering rules decide where its code
  lives. This plan doesn't touch that repository.

## Fit with the hard rules

- **Read-only and no new dependencies.** It uses JSON, sqlite3, urllib,
  numpy for the layout, and pyqtgraph. Nothing goes near the brokerage.
- **Layering:** pure types, checks and graph code in `data/`, loading in
  `storage/`, providers in `providers/`, merging in
  `services/research.py`, the view in `gui/pages/research/`. Every
  network fetch runs on the TaskRunner.
- **Copyright:** no PDFs are bundled (D42 still holds). Canon text is
  written in your own words, with no quotations.
- **Privacy:** the canon holds nothing personal. Reading status, notes
  and PDFs stay local and are never committed.

## Order against the other plans

Lit-0 and Lit-1 touch only the Papers catalog, new files and one test.
They can run alongside E2+, LENS V1/L/W2b and the HORIZON tracks.
Lit-2 onwards is new UI and new providers, so it's better after
whichever track you're on finishes. Several LENS and HORIZON milestones
(GRS, Black–Litterman, Ledoit–Wolf, Moreira–Muir, American exercise)
turn hollow nodes filled as they land. That's the vault doing its job.

## Decisions for you

1. **Approve the track,** Lit-0 → Lit-6, with each milestone approved
   separately as usual.
2. **Where the canon lives.** I recommend JSON files committed as
   package data, which are reviewable, portable and readable by an LLM.
   The alternative is everything in SQLite, consistent with D42, but
   then the canon can't be shared or reviewed as diffs.
3. **The first spine.** The table above has about 45 papers. Add,
   cut or re-strand before Lit-1. What earns a place in the canon? I
   propose: it changed practice, or your code implements it, or it's
   the standard critique of something that does.
4. **The strands.** The eight lanes above, or your own split.
5. **How LLMs take part.** I recommend proposal files only, so any model
   works and the app calls none. The alternative is the app calling a
   local Ollama server over stdlib HTTP, as ML Cookbook does.
6. **Online providers.** Crossref, arXiv and OpenAlex, each subject to a
   terms check (D34) at Lit-3. Or start with Crossref alone.
7. **Where this sits against the other plans:** Lit-0 and Lit-1
   alongside the current track, or after it.
8. **ML Cookbook:** copy the format after Lit-2 (recommended), or start
   there in parallel.

## Doc changes on approval

- **ROADMAP.md:** the literature track, Lit-0 → Lit-6.
- **DECISIONS.md:**
  - an entry for the track, numbered at approval (D54 is the latest on
    main; numbers are contended across sessions)
  - entries for the format, the coverage rule, the resolver's match rule
    and the ranking, each written as its milestone lands
- **AGENTS.md, hard rule 4:** "and the papers it implements are in the
  canon", enforced by Lit-1's coverage test.
- **HORIZON_PLAN.md row 8:** "Nothing new" becomes this plan.
- **RESEARCH_DESIGN.md:** its "citation graphs, automatic metadata
  retrieval, and bulk literature acquisition are possible later
  increments" line points here.
- **ARCHITECTURE.md:** the canon directory, the merge with the personal
  layer, and the new modules.
- **CURRENT_STATE.md:** each milestone as it lands.
