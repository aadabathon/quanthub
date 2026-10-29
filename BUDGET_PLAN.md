# BUDGET_PLAN.md — a risk budget from what you believe

Proposal, 2026-10-25. Planning only: no app code changes, nothing
committed. It adds a risk-budget card to the Review page (W7, D49). You
write a believed α for each stream in REGIME.md, and the card shows:
- the Treynor–Black size of each stream
- the core β left over
- each stream's break-even α
- the α your current sizes imply

Each milestone needs your approval before it starts, as with the other
plans.

## What you asked

"A risk-budget card: believed α per stream in REGIME.md, Treynor–Black
sizes, the netted core β, break-even α, and what my current sizes imply."

It answers your creed's own question: "a core beta stream, plus hedge and
return streams orthogonal to it".
- **Today** W3 grades each stream's hurdle on its sample mean. Over two
  years that mean is known only to about ±20–40% a year.
- **The budget** puts your stated belief where the sample mean was, and
  separates each stream's β from its own risk. The data supplies only
  what it can estimate well: β, and the residual covariance.

Nothing here recommends a number. The beliefs are yours, and the card
does arithmetic on them.

## The math

The module docstring carries this for review before any code (B1), as W2
and W3 did.

**Streams.** For each stream s, x_s is its excess return per unit of its
value share: W3's series over its share, as `_sleeve_returns` builds it
for section 6. The benchmark's excess return is x_m. W2's OLS gives:

    x_s = a_s + β_s x_m + ε_s

with annualized σ_m² = 252·Var(x_m) and residual covariance
Σ_ε = 252·Cov(ε). In sample, OLS makes ε uncorrelated with x_m. Out of
sample that's an assumption, and the card says so.

**The portfolio.**
- **A core** is held at weight w_c, with β = 1, α = 0 and ε = 0 (the
  benchmark).
- **The streams** are held at weights w, with your believed α in place
  of 252·â_s.
- **The rest is cash** at r.
- Total β is b = w_c + w′β. With λ the market premium:

      μ_p − r = bλ + w′α,      σ_p² = b²σ_m² + w′Σ_ε w

  So growth (W3's quadratic) is

      g = r + [bλ − ½b²σ_m²] + [w′α − ½w′Σ_ε w]

**It separates.** The first bracket depends only on b, and the second
only on w.
- **Stream sizes don't depend on λ,** which nobody knows well. For b
  fixed, the second bracket is maximized at w* = Σ_ε⁻¹α (Treynor & Black
  1973).
- **Total β is your setting, not an estimate.** The first bracket's
  maximum, b* = λ/σ_m², needs λ, so the budget never uses it. b comes
  from REGIME.md (decision 2).

**What the card computes, with f your Kelly fraction (decision 3):**

| Quantity | Formula |
|---|---|
| **Stream size** (Treynor–Black at f) | w_f = f · Σ_ε⁻¹ α |
| **Core β left over** | w_c = b − w_f′β. If negative, the streams alone carry more β than your target; that's reported, never shorted or clipped |
| **Growth from the streams** | G(w) = w′α − ½w′Σ_ε w, so G(w_f) = ½f(2 − f)·IR², with IR² = α′Σ_ε⁻¹α |
| **Break-even α** (others held) | Stream s adds growth iff α_s > (Σ_ε w)_s − ½w_sσ_s²; with no cross-correlation, α_s > ½w_sσ_s² |
| **Implied α at today's sizes** | α_implied = (1/f) · Σ_ε w_now: the beliefs for which today's sizes are exactly f-Kelly |
| **The margin under your belief** | α_s − (Σ_ε w_now)_s: positive means the stream is small for your belief, negative means oversized |
| **Sensitivity** | ∂w_f/∂α = fΣ_ε⁻¹. With no cross-correlation, 1% a year of belief moves the size by f/σ_s² |

**How it ties to W3's hurdle.** Under your belief, W3's margin splits as:

    (μ_s − r) − Cov(R_s, R_p) = [α_s − (Σ_ε w)_s] + β_s·[λ − bσ_m²]

- The second term is the core's own margin, per unit of β. β is worth
  the same wherever it comes from.
- What distinguishes a stream is the first term: its α net of the own
  risk it adds.
- With α = 0, a stream's distinct margin is −(Σ_ε w)_s. It can then pay
  its way only by hedging the other streams' own risk; otherwise the same
  β taken from the core does better.

**Why sensitivity is on the card.** Treynor–Black sizes are notoriously
large and fragile. With 34% own volatility and f = ½, 1% a year of
belief moves a stream by about 4.3% of equity. Showing that number is
the feature, as Kelly's error is in D39.

**What the data does and doesn't supply.**
- **Σ_ε is well estimated.** A variance over T days has a relative SE of
  about √(2/T) under normality, roughly 6% at T = 500, and more with fat
  tails. That's small next to any belief's uncertainty.
- **α isn't.** The card shows the data's α̂ ± HAC SE beside your belief,
  labelled as a reality check that can rarely confirm or refute anything
  on two years.

**Out of scope:**
- **Hedges and options.** The regression captures only a linear β, and
  convex payoffs are W3's job (c_mv, the replay). Hedge sleeves keep W3's
  sizes.
- **The core itself.** Core holdings, if section 3 names any, count
  toward b and aren't sized here.

## Fit with the hard rules

- **No orders.** The budget is a number on the Review page, like every W7
  check. Nothing on the execution path (D52, [EXECUTION_PLAN.md](EXECUTION_PLAN.md))
  reads it, unless a later decision says so.
- **No new dependency, no new data source.** Everything comes from the
  W2/W3 series the review already builds, under D53's flagged-day choice.
- **Layering:**
  - `models/` holds the math, pure, knowing only `data/`
  - `data/regime.py` holds the beliefs record
  - `importers/regime_md.py` reads the rows
  - `services/review.py` wires it, and `gui/pages/review.py` shows it
- **Privacy.** Your beliefs live in your private `~/.quant_hub/REGIME.md`
  (D49); the committed template stays blank. Review payloads stay in your
  local store.

## The plan

Order: **B1 → B2 → B3**, each a few files, reviewed before the next.

### B1. The budget, derived and tested

`models/budget.py` (name settled at B1): the docstring above, then pure
functions over (β, Σ_ε, α, f, b, w_now).

**Validated inputs:**
- Σ_ε symmetric positive definite. Two identical streams make it
  singular, and the error names them.
- α finite, 0 < f ≤ 1, b finite.
- One entry per stream, in the same order.

**Tests:**
- **Separability.** On a simulated panel with OLS residuals, the sample g
  equals bλ + w′α − ½(b²σ_m² + w′Σ_ε w) to rounding.
- **Optimum.** w* = Σ_ε⁻¹α has zero gradient, and random perturbations
  lower G.
- **Fraction.** G(f·w*) = ½f(2 − f)·IR² exactly.
- **Round trip.** Sizing at α_implied(w_now, f) returns w_now.
- **Break-even.** G(w) − G(w without s) changes sign exactly at the
  break-even α.
- **No cross-correlation.** A diagonal Σ_ε gives w_s = fα_s/σ_s².
- **Core.** w_c = b − w′β, a negative w_c is reported, and nothing is
  clipped.
- **Statistical.** With a planted β, Σ_ε and α, sizes from the sample
  Σ_ε land within their simulated error of fΣ_ε⁻¹α.

### B2. Your beliefs in REGIME.md, wired into the review

**The template** gains a *Stream beliefs* table at the end of section 6.
It's blank, since stream names are yours:

| Stream | Believed α | Why |
|---|---|---|
| ___ | ___ % a year | ___ |

**Parsing** (`importers/regime_md.py`, `data/regime.py`):
- A stream name must match a `stream: <name>` from section 3; an unknown
  or repeated one is an error naming its line (D49's loud failures).
- **A blank α leaves that stream out of the budget,** and the card says
  so. A stream you can't put a number on isn't sized.
- `RuleSet` gains the beliefs, so a review records the beliefs it used.

**The review** (`services/review.py`):
- **Inputs:** the stream series and W2 fits that section 6 already
  computes, so there's no new regression code. Σ_ε comes from their
  residuals, w_now from W3's value shares, and b and f from decisions 2
  and 3.
- **Outputs:** a `BudgetReport` on `Review`, stored in the payload like
  the checks.
- **No new grade** in this milestone (decision 6).

**Tests:**
- parsing the table; an unknown stream; a blank α
- the report built from a synthetic book with planted beliefs
- the payload round trip

### B3. The card on the Review page

**One row per stream:**
- weight now, β, own vol σ_ε
- **your α**, the **size at f**, and the change from now
- the **break-even α**, the **α your size implies**, and the data's
  α̂ ± SE (greyed, labelled)

**The totals:**
- β from the streams, and the **core β left over**, or by how much the
  streams exceed your target
- growth from the streams today and at the budget, under your beliefs
- one line of sensitivity per stream: "+1% a year of belief = +x% of
  equity"

**Where a budget size would break another of your rules,** the row names
the rule: max weight, risk share, stream β or the vol band. It's shown,
never clipped.

**Checks** as in AGENTS.md: built from `gui/components.py`, the geometry
checked at 1280 px, a headless smoke test, and a screenshot on a copy of
the store.

## Relation to the other plans

- **TARGET_PLAN T2.** If it lands, its β\* can be the budget's b
  (decision 2).
- **W3 is unchanged.** Its c\* stays as the data's view, scaling β and
  own risk together. The budget is the beliefs' view, with β netted to a
  core. The card says which is which.
- **LENS_PLAN O (Outlook).** It could show the beliefs' expected excess
  return, bλ + w′α, beside CAPM's. That's for O to decide.
- **LITERATURE_PLAN.** Treynor & Black (1973) joins the *Sizing & growth*
  strand, with `lives_in: models/budget.py` and links to Sharpe's
  single-index model (1963) and Kelly (1956). Lit-1 verifies the
  citation before it's canon.

## Decisions for you

1. **The milestones and their place:** B1 → B2 → B3, against TARGET_PLAN
   T1–T3, LENS_PLAN V1 and EXECUTION_PLAN E2.
2. **Where the budget's total β, b, comes from:**
   - (a) its own REGIME.md row, proposed
   - (b) T2's β\* once it lands
   - (c) today's measured β, which shows only what's left for a core
3. **The fraction f:** reuse section 6's *Fractional sizing* value
   (proposed: "never more than f of the optimum" is the same number), or
   give it its own row.
4. **Σ_ε full or diagonal:**
   - **Full** counts residual correlations between streams; most are
     within noise, and one pair can matter.
   - **Diagonal** is classic Treynor–Black: simpler, and robust when the
     correlations are noise.
   - Proposed: full, with the diagonal sizes shown beside it.
5. **A negative core** (the streams carry more β than the target):
   reported only (proposed), or graded.
6. **Grading:** informational in B2–B3 (proposed). A later rule could
   grade "stream size vs its budget, ± a tolerance".
7. **The data's α̂ beside your belief:** shown, greyed and labelled
   (proposed), or hidden so it doesn't anchor you.

## Doc changes on approval

- **ROADMAP.md:** B1–B3 and where they sit.
- **DECISIONS.md:** one entry per milestone, at the next free number.
- **CURRENT_STATE.md:** the plan, then each milestone.
- **REGIME.md (template):** section 6's *Stream beliefs* table, at B2.
- **ARCHITECTURE.md:** the budget module and its place in the review.
- **The literature canon:** the Treynor–Black entry, at Lit-1 or when B1
  lands, whichever is later.
