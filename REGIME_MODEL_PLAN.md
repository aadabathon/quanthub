# REGIME_MODEL_PLAN.md — a probabilistic regime that sets the rules

Proposal, 2026-10-29. Planning only: no app code changes, nothing
committed. Each milestone needs your approval before it starts, as with
the other plans.

## What you asked

> "taking all the data available and trying to model a regime, the regime
> itself is what defines the creed, it is not black and white"

Today the creed's numbers are fixed bands in REGIME.md: a β band, a cash
range, a drawdown limit. The market's state reaches them only through W7's
gates, which can block adds or suggest a hedge and nothing else. This
plan turns the regime into an estimate with probabilities, built from
every input that earns its place. The rule values become continuous
functions of that estimate.

It generalizes four things that exist or are planned:
- **W7's calm gate** (D49): the HMM's filtered probability becomes one
  input among several.
- **TARGET_PLAN T2:** "ECY level × vol scale" becomes one special case of
  the β rule below.
- **ALLOCATION_PLAN A2:** its fixed crash stress gets a probabilistic
  sibling, a chance constraint.
- **BUDGET_PLAN:** its total β, b, can come from this model (its
  decision 2).

## What the inputs said on 2026-10-28 (the worked example)

These are market readings from the app's own models, with data through
Oct 28. None of them describe a portfolio.

| Input | Reading | Clock |
|---|---|---|
| Realized vol, 21 days | 9.6% (two-year: 16.6%) | fast |
| GARCH average vol ahead | 13.8% over a month, 15.2% over a year | fast |
| HMM, filtered | K = 2: calm 99%, 90% in 20 days. K = 3: calm 85%, 62% in 20 days | fast |
| Trend | SPY 0.9% below its two-year high, 6.5% above its 200-day average, +13.9% over 12 months | medium |
| Valuation | CAPE 40.8 (99th percentile since 1881); excess CAPE yield 0.81% (17th) and falling as 10-year yields rose | slow |

**Why fixed thresholds fail here.**
- **The clocks disagree.** The fast signals say risk is cheap to hold; the
  slow one says it pays little.
- **A threshold rule sees only one corner of that.** Take a rule that
  waits for both high vol and valuation in its cheapest 30%. From today's
  valuations, it needs about a 56–68% market fall before it fires.
  - In March 2009 it would have fired, but only from a much cheaper start
    (the excess CAPE yield reached its 76th percentile).
  - In 2020 it wouldn't have fired (61st percentile).
- **A continuous model reads both clocks at once,** and says how much each
  one moves the numbers.

## The model

The derivation goes in a module docstring for review before any code
(Q2 and Q4), as W2, W3 and B1 did.

### 1. Inputs, each in real time

Each input is known at the close of day t, so monthly ones carry their
publication lag:

| Input | Series | Source | Evidence so far |
|---|---|---|---|
| Volatility | GARCH h-day forecast σ̂_{t,h}; 21-day realized | the store (exists) | vol targeting α +1.8% ± 0.5% (1926–2026 scratch run) |
| Turbulence | HMM filtered calm probability π_t, K yours | the store (exists, W7) | not yet tested as a sizing input |
| Valuation | excess CAPE yield, real-time percentile p_t | Shiller (TARGET T1) | α +0.76% ± 0.20%, but ≈ 0 since 1993 |
| Trend | 12-month excess return; price against its 200-day average | the store / Ken French | 200-day trend α 4.6% (t 2.1) since 2008, from two episodes |
| Stock–bond correlation | rolling correlation of stock and Treasury returns | Shiller's monthly bond returns from 1871 (in T1's file); daily needs FRED or IBKR | untested. It decides whether Treasuries hedge, and they didn't in 2022 |
| Credit spread | Baa minus the 10-year yield | FRED (a new provider, terms check) | untested; candidate only |

All inputs are put on a real-time scale (an expanding percentile or z),
so nothing uses the full sample's distribution.

### 2. What the model estimates: the next horizon's distribution

The "regime" is the conditional distribution of the market's excess
return over a horizon h, not a label. Three parts, from best to least
well estimated:

- **Variance.** σ²_{t,h} from GARCH (W7 has the closed form). This is the
  part the data knows best.
- **Mean.** A combination forecast (Rapach, Strauss & Zhou 2010): the
  average of univariate predictive regressions, each fitted walk-forward
  on data before t:

      μ̂_{t,h} = μ̄_t + (1/J) Σ_j θ̂_{j,t} x_{j,t}

  Each x_{j,t} is demeaned in real time, with its mean up to t, so that μ̄_t
  is the forecast when every input sits at its average.

  With Campbell & Thompson's (2008) restrictions: a coefficient with the
  wrong sign is set to 0, and a negative forecast is floored at 0. The
  benchmark is the historical mean μ̄_t. Goyal & Welch (2008) showed most
  predictors lose to it out of sample, so it's the bar every input must
  clear.
- **Tail.** The probability of a large fall:

      P_t(D_{t,h} ≥ d),  D = the market's largest peak-to-trough fall within h

  This comes from a walk-forward logistic regression on the inputs, at a
  few thresholds d (10, 20, 30, 40%), made monotone in d. It's fitted by
  IRLS in numpy, with no new dependency.
- **A readable label,** for the card only: the HMM's state × valuation
  tercile × trend sign, e.g. "calm · dear · up", with the HMM's
  probability. The rules never read the label; they read the three
  estimates above.

### 3. From the regime to the creed: continuous rules

**The growth term** (Merton; Kelly with a fraction f):

    β^g_t = f · μ̂_{t,h} / σ̂²_{t,h}

- With μ̂ constant, this is vol targeting (Moreira & Muir 2017).
- With μ̂ from the valuation band and σ̂ realized, it's TARGET T2.
- So the century tests already run are its special cases.

**The drawdown term, made probabilistic** (a chance constraint):

    β^d_t = D / q_{t,h}(p)

- D is the loss you'll accept, and p the probability you'll accept of
  exceeding it.
- q_{t,h}(p) is the market fall that the tail model gives a probability p
  of being exceeded within h.
- A fixed spec of the form "lose at most D when the market falls X" is
  this with q pinned at X. Here q moves with the state.
- A book with hedges loses less than β·D. ALLOCATION A2's replay measures
  the book's crash β, and that replaces β in the constraint (decision 4).

**The target:**

    β*_t = clip(min(β^g_t, β^d_t), β_min, β_max)

**Cash and the hedge mix:**
- **Cash** is what's left after the core at β*, BUDGET's streams, and the
  hedges.
- **The hedge mix** follows the stock–bond correlation ρ_t. A Treasury
  sleeve's weight scales with max(0, −ρ_t). When stocks and bonds fall
  together (ρ > 0, as in 2022), its weight shifts to trend and cash. The
  mapping is yours, and Q3 tests it on 1871+ monthly data.

**A no-trade band.** The targets move every day, but the review grades
the book against a band δ around them (decision 9). A rule that churns at
this book's size pays its trading costs and its taxes.

**An attribution on every number.** Each target shows how much each input
moved it, relative to the model with that input at its median. "Valuation
took β from 0.70 to 0.55" is something you can argue with. "β 0.55" alone
isn't.

### 4. Evidence: each input earns its place

The protocol is fixed before any run, so it isn't tuned to the answer:
- **The data.**
  - Walk-forward on Ken French daily, 1926–2026, with cash at RF.
  - Shiller monthly for valuation (percentiles from 1881) and for the
    stock–bond correlation.
  - Every series real-time: earnings lagged, nothing from the future.
- **The baselines:** constant β at your chosen level, vol targeting alone,
  ECY × vol (T2), and the full model.
- **The measures:**
  - out-of-sample R² of μ̂ against the historical mean (Campbell–Thompson)
  - growth (CER) gain ± Newey–West SE
  - worst drawdown and each crash, as on 2026-10-24
  - for the tail model: the Brier score against the base rate, and a
    calibration table
- **Inclusion.**
  - An input stays only if adding it raises out-of-sample growth, with a
    family-wise p below your threshold (decision 6).
  - The test is the circular shift used on 2026-10-25, across every
    variant ever tried.
- **A ledger of variants.** Every specification run is recorded, kept or
  not, and the family-wise test counts them all. Thirty-one rules in one
  afternoon was the lesson.
- **After publication and by era.** Each input is reported after its
  paper's publication (expect about half: McLean & Pontiff 2016) and for
  1926–49, 1950–92 and 1993 on.

**What the evidence can't settle:**
- **The tail model rests on a few independent episodes,** roughly 10–15
  large falls since 1926, so its intervals will be wide. The card shows
  them.
- **Structure shifts.** Valuation's edge was about 0 after 1993.
- **Many funds are younger than the test.** Trend funds and
  most ETFs have no 2008. Their sleeve behavior is measured where it
  exists and labelled where it doesn't.

## Fit with the hard rules

- **No orders.** The model's numbers are review targets, like every W7
  check. The execution path (D52) doesn't read them unless a later
  decision says so.
- **No new dependency.** GARCH and the HMM exist. The combination forecast
  and the logistic fit are numpy, with tests against planted truths.
- **Data sources:**
  - Shiller via TARGET T1 (a manual CSV, or its decision 2)
  - Ken French via W2b's provider, or a manual file import as T1 does
    (decision 2)
  - FRED only if credit or daily yields are kept, a third provider with a
    terms check (D34)
- **Layering:**
  - `models/` holds the math, knowing only `data/`
  - `services/` wires it
  - the GUI shows it in a dock (D55/D56)
- **Privacy.** No holdings in the plan or the code. Your parameters live in
  your private REGIME.md (D49).

## The plan: Q1 → Q5

Each milestone is a few files, reviewed before the next. **Prerequisite:
TARGET T1,** Shiller's data in the store.

### Q1. The inputs, stored and real-time
- The series above, from the store and T1. The Ken French history comes
  from W2b's provider or a file import (decision 2).
- Each input's real-time transform (expanding percentile or z), with its
  as-of date and lag.
- **Tests:** no look-ahead (appending data never changes a past value),
  the lags, and the stock–bond correlation against a closed form.

### Q2. The three estimates, derived and tested
- A new module (`models/regime_model.py`, name settled at Q2) holds the
  docstring and pure functions:
  - σ̂ from GARCH
  - μ̂ from the combination forecast
  - P(D ≥ d) from the logistic tail model
- **Tests:**
  - **Planted truths:** a planted predictor is recovered, and a useless one
    gets out-of-sample R² ≤ 0 on average.
  - **The restrictions** hold.
  - **IRLS** matches the logistic MLE in closed-form cases.
  - **The tail probabilities** are monotone in d.
  - **Calibration** holds on simulated data with a known event rate.

### Q3. The evidence run
- The walk-forward harness, the baselines, the ledger and the family-wise
  test, as a saved Research run (D42).
  - Then section 10's "a research run supporting it" can name it.
- **Research needs two things it doesn't have** (as in TARGET T4):
  - an exogenous series beside prices
  - a time-varying cash rate
- **Output:** which inputs stay. The model that ships is the one the
  evidence keeps, not the one this plan lists.

### Q4. The rules from the regime
- β^g, β^d and β\*, cash, the hedge mix, the no-trade band, and the
  attribution, derived in the docstring and reviewed first.
- **REGIME.md gains a *Regime model* table:**
  - the horizon h
  - f
  - D and p
  - β_min and β_max
  - the hedge mapping
  - the input set
  - the model version
- **Existing rows can read the model.** For example, the beta band becomes
  `model ± 0.1 by 1.645 SE` and the cash range `model ± 5`.
- **The review grades gaps,** as TARGET T2 proposed: book minus target,
  with W2's SE.

### Q5. The regime card
- **Today's estimate:**
  - the label with its probability
  - σ̂'s term structure, μ̂ ± its error, and P(market −20% within 12
    months) with its interval
- **The rule values** each input implies, as an attribution waterfall.
- **What would change it:**
  - the distance to each input's next decile (e.g. "trend flips 6% lower")
  - the history of β\*_t against the market
- **Where it lives** is decision 7: a dock on Review, or LENS_PLAN O's
  Outlook page.

## Relation to the other plans

- **TARGET_PLAN:**
  - T1 is a prerequisite.
  - T2 becomes a baseline in Q3 rather than a rule of its own (decision 8).
  - T3's card merges into Q5.
- **BUDGET_PLAN:** its b can be Q4's β\*.
- **ALLOCATION_PLAN:**
  - A2's fixed stress stays as the floor check.
  - β^d is its probabilistic sibling, and both are shown.
  - A3's What-if page can preview Q4's targets.
- **W7:** the calm gate becomes an input; gates can stay for anything not
  yet modelled.
- **LENS_PLAN:** W2b supplies the history, and O can host Q5.
- **SLEEVE_PLAN:** a theme can read the regime later; nothing here
  depends on it.
- **LITERATURE_PLAN:** the plan's papers join the canon at Lit-1:
  - Hamilton 1989 and Moreira–Muir 2017 are already listed.
  - New: Goyal–Welch 2008, Campbell–Thompson 2008, Rapach–Strauss–Zhou
    2010, Ang–Bekaert 2002 and Guidolin–Timmermann 2007 on
    regime-switching allocation.
  - Each is verified before it's canon.

## Decisions for you

1. **The milestones and their place.** Q1 → Q5 after TARGET T1. Against
   ALLOCATION A1–A4, BUDGET B1–B3, LENS V1 and EXECUTION E2.
2. **The century's history:** W2b's Ken French provider first, or a manual
   file import now.
3. **The horizons:** for example, 21 days for σ̂ and 12 months for μ̂ and the
   tail.
4. **The chance constraint:** your D and p, and whether the book's crash β
   comes from A2's replay or from β alone.
5. **The fraction f** for the growth term. Section 4's sizing cap could
   serve.
6. **The inclusion bar:** the family-wise p, and whether an input with no
   post-publication evidence can stay.
7. **Where the card lives:** Review or LENS O.
8. **TARGET T2:** superseded (proposed) or built first as the simple
   version.
9. **The no-trade band δ** around each target.
10. **FRED as a provider,** for credit spreads and daily yields, or leave
    both out until the monthly evidence asks for them.

## Doc changes on approval

- **ROADMAP.md:** Q1–Q5, after T1.
- **DECISIONS.md:** one entry per milestone, at the next free number.
- **CURRENT_STATE.md:** the plan, then each milestone.
- **REGIME.md (template):** the *Regime model* table, and model-valued
  rows, at Q4.
- **ARCHITECTURE.md:** the regime model's modules and data flow.
- **TARGET_PLAN.md:** a note that T2 is superseded, if decision 8 says so.
