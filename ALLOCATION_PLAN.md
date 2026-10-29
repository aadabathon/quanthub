# ALLOCATION_PLAN.md — compare allocations, and the rules a core needs

Proposal, 2026-10-29. Planning only: no app code changes. It comes out of a
session spent putting the owner's real book and creed through Quant Hub.
That work used scratch scripts, kept local and gitignored, because they
name holdings. None of the owner's holdings, values or rule settings appear
here. Each milestone needs your approval before it starts, as with the
other plans.

## What the session did

- **The real book, graded.** REGIME.md v2 was filled in row by row and the
  Review ran on it. A private v3 draft followed. It moves toward an index
  core, with a capped picks sleeve whose β is netted into the core, and cash
  that's spent by a valuation rule.
- **Eleven weight vectors, through the app's own analysis.** Each was
  written as a generic-layout positions CSV and run through
  `PortfolioService.analyse`, the Portfolio page's job, then checked
  against the owner's section 4 limits:
  - index and cash mixes
  - core plus satellite, and a barbell
  - equal risk, minimum variance and max Sharpe (with `models/optimize.py`'s
    Nelder–Mead)
  - a Treynor–Black budget ([BUDGET_PLAN.md](BUDGET_PLAN.md))
- **A century of crashes.** Each vector's β was replayed, as a constant-β
  mix, through 1926–2026 daily US market data (Ken French). The same data
  tested a cash-spending rule.

## What it found about the app

1. **Section 4 grades an index fund like a single stock.** Max holding
   weight, max risk share and the effective-holdings floor flagged every
   vector built on an index core. N_eff came out between 1.0 and 2.3 for
   core-heavy mixes, though the core holds hundreds of names. A creed
   built on "a core beta stream" is punished by its own checklist.
2. **Sizes are per holding only.** A sleeve's total ("picks between x and
   y % of the book") can't be graded. Per-name ranges scaled to the total
   stand in for it, and they break as soon as the mix inside the sleeve
   drifts.
3. **The drawdown check never sees a crash.** It reads the worst drawdown
   of today's mix replayed over the lookback, which here was two calm
   years. A book that passed its tolerance comfortably lost about half in a
   constant-β replay of 2007–09.
4. **A spend-the-cash rule can't be written.** The gates only block adds
   or suggest hedges, and section 4's ranges are static. So a rule like
   "hold cash in a set range while markets are calm, and spend it when vol
   spikes and valuations are cheap" breaches its own cash range the day it
   works. TARGET_PLAN's T2
   (a regime-dependent β target) is where that belongs.
5. **Comparing vectors needs a script.**
   - Importing a what-if CSV on the Portfolio page leaves a permanent
     snapshot; there's no delete.
   - The same export imported twice under different layouts (Generic, then
     Fidelity) made the Review draft a trade that never happened: a cash
     line read as a stock.
6. **In-sample optimizers are traps, and the page should show it.**
   - Max Sharpe fitted on two years put 17 % into a recent winner the
     owner had called a "cope" position.
   - Minimum variance came out about 86 % index.
   - Both are lessons worth seeing beside the vectors you'd actually
     consider.

**The research behind the cash rule** (public data, 1926–2026, β between
a base level and 1.0):
- **Spending when 21-day vol ≥ 30 % and the excess CAPE yield is in its
  cheapest 30 %** fired 4 times in 100 years: 1932, 1946, 1974 and 2009.
  α +0.85 % ± 0.35 % a year.
- **The valuation condition alone** did as well: +1.14 % ± 0.37 %.
- **Volatility alone** lost: −0.81 % ± 0.42 %.
- **Spending all the cash at once deepened the 2007–09 low by about 9
  points,** then recovered faster.

The scripts are `spikes/target/deploy_rule.py` and
`spikes/whatif/compare.py`, both local. TARGET_PLAN's evidence section
holds the earlier timing results and the multiple-testing check.

## The plan: A1 → A4

### A1. Core-aware limits, and sleeve totals

A W7 change, through `importers/regime_md.py`, `data/regime.py` and
`services/review.py`.

**A `core` sleeve isn't graded as a single name:**
- It's exempt from max holding weight and max risk share. Its weight and
  its risk share are still shown: a core carries market risk by design.
- N_eff is computed over the non-core holdings, with the core's weight
  beside it. That's decision 2.
- **Netting is shown, never applied.** The review reports the β the
  non-core holdings supply (Σ wᵢβᵢ) and the core weight that would bring
  the book to a stated target: target − Σ wᵢβᵢ. That's how a pick's β is
  "shorted out" without a short. An IRA can't short, and netting needs no
  margin.

**Sleeve totals.** Section 3 gains a small table, blank in the template:

| Sleeve | Intended total |
|---|---|
| ___ | ___ – ___ % of equity |

- It's graded like a holding's intended size, under section 3's
  sleeve-grade line.
- An unknown sleeve name is an error naming its line (D49).
- Per-name ranges become optional rather than the only way to cap a
  sleeve.

**Tests:**
- a core exempt from the three rules, and still shown
- N_eff without the core
- the netting arithmetic
- sleeve totals graded at the boundary
- parsing errors

### A2. Crash stress, measured

**A new section 4 row:**

| Rule | Your value | Unit | Measured by | On breach |
|---|---|---|---|---|
| Crash stress | ___ | % | The worst loss of a constant-β mix through the benchmark's stored crashes (A2) | ___ |

**The math.** The rule goes in a module docstring, reviewed first:
- With b the book's β to the benchmark (W2) and r the cash rate, the
  replay is

      Vₜ = Π (1 + b·R_m,s + (1 − b)·r_s),  rebalanced daily

- The measure is the largest peak-to-trough fall of V over the stored
  history, with its dates.
- With W2's error, it's also reported at b + k·SE (decision 4).
- **The card says it's a floor, not an estimate.** Single stocks carry
  their own risk, and β tends to rise in a crash, so a stock book loses
  more than its β replay.

**The data** needs a long benchmark history (decision 3):
- IBKR's daily SPY, as far back as IBKR serves it. The store holds 2021
  onward today, and the depth is to be checked.
- W2b's Ken French market series, from 1926.
- Or the longest of the two.

**Tests:**
- b = 1 reproduces the benchmark's own drawdown
- b = 0 has none
- the loss is monotone in b
- the dates and the SE variant

### A3. A What-if page

**A dock** (D55), with cards on the 12-column grid (D56).
- **Vectors.** Type or paste them (symbol → % of equity; the rest is
  cash), or start from a builder:
  - today
  - today scaled into a picks total
  - an index and cash mix
  - picks of x % with their β netted into a core at a target β
- **Transient by default.** A vector never becomes a positions snapshot,
  which fixes finding 5. Named sets can be saved in the store's view
  state (decision 5).
- **One TaskRunner job runs, for each vector:**
  - `PortfolioService.analyse`, the same analysis Portfolio runs
  - the owner's section 4 rules, aware of A1
  - A2's crash stress
- **The result:** one comparison table (β ± SE, vol, VaR, ES, drawdown,
  crash stress, N_eff, top risk share, growth, breaches), and the risk
  shares per vector.
- **References, labelled:**
  - minimum variance and equal risk, long-only through a softmax over
    `models/optimize.nelder_mead`
  - max Sharpe shown only with its in-sample warning (decision 6)
- **Missing bars** are named, never fetched behind your back (V0's
  offline rule).

**Tests:**
- vector-to-snapshot conversion
- equality with Portfolio's analysis for the same vector
- the optimizers' constraints (long-only, sum to the invested share)
- the netting builder
- page geometry and a smoke test, per AGENTS.md

### A4. Snapshot hygiene

- **Delete a snapshot** from the Portfolio page, after a backup in D36's
  style, or hide it (decision 7). Always the owner's action, never
  automatic.
- **Flag a second import of the same file** (same SHA-256) under a
  different layout before it's stored. That was the cause of the phantom
  trade draft.
- **Tests:** the backup before a delete; the review's drafts unaffected
  by a deleted or hidden snapshot; the duplicate warning.

## Relation to the other plans

- **TARGET_PLAN T2.** The spend-the-cash rule is a β target that rises
  when valuations are cheap. The owner's version adds a vol condition, and
  spends more as prices fall. Those go to T2's decisions, not here.
- **BUDGET_PLAN.** Its card's sizes can become one more vector on the
  What-if page later.
- **EXECUTION_PLAN (D52).** Nothing here sends orders. A what-if vector is
  never an order, and the execution path doesn't read it.
- **LITERATURE_PLAN.** Candidates for the canon, verified at Lit-1:
  - Michaud (1989), the "error maximizer"
  - DeMiguel, Garlappi & Uppal (2009), 1/N
  - Maillard, Roncalli & Teïletche (2010), equal risk contribution
  - Moreira & Muir (2017)
  - Campbell & Shiller (1998)

## Fit with the hard rules

- **No orders, and no new dependency:** Nelder–Mead and pyqtgraph are
  already here.
- **Data:** stored history only. A2's long series is decision 3, and may
  wait for W2b's provider.
- **Layering:**
  - the stress math and the vector conversion in `models/`
  - the what-if job in `services/`
  - the page in `gui/`
  - the rule rows in `importers/` and `data/`
- **Privacy:** vectors live in the owner's local store, and rule values in
  the private REGIME.md (D49). The template's new rows are blank.

## Decisions for you

1. **The order:** A1 → A2 → A3 → A4, against TARGET_PLAN, BUDGET_PLAN,
   LENS_PLAN V1 and EXECUTION_PLAN E2. A1 is small, and is what lets a
   core pass your own checklist.
2. **N_eff with a core:** count only the non-core holdings (proposed), or
   look through the fund. Looking through needs its constituents, which
   is a new data source.
3. **Crash-stress data:** IBKR's SPY depth, W2b's French series, or the
   longest of the two.
4. **Crash-stress evidence:** grade at β̂, or at β̂ + k·SE like the other
   estimated rules.
5. **What-if vectors:** kept as named sets in view state (proposed), or
   never stored.
6. **Max Sharpe on the page:** shown with an in-sample warning
   (proposed), because seeing it pick last year's winner is the lesson;
   or left out.
7. **Snapshots:** delete with a backup, or hide.

## Doc changes on approval

- **ROADMAP.md:** A1–A4 and where they sit.
- **DECISIONS.md:** one entry per milestone, at the next free number.
- **CURRENT_STATE.md:** each milestone as it lands.
- **REGIME.md (template):** A1's sleeve-totals table, and A2's row.
- **ARCHITECTURE.md:** the what-if job and page.
