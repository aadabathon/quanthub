# TARGET_PLAN.md — a beta target from valuation and volatility

Proposal, 2026-10-24. Planning only: no app code changes, nothing
committed. It adds a regime-dependent beta target to W7 (D49): Shiller's
data as a new source, a valuation level from the excess CAPE yield, and a
vol-target scale. Each milestone needs your approval before it starts, as
with [WORKSTATION_PLAN.md](WORKSTATION_PLAN.md) and [LENS_PLAN.md](LENS_PLAN.md).

## What you asked

You described your regime as: hedge in full when volatility is low, and
accumulate beta as volatility rises and multiples get cheaper, above a
beta floor. (Your own values stay in your private REGIME.md, not here; the
0.7 below is the backtest's.) You then asked for that to be tested, then for a
valuation trigger, then for the two combined, and then for this plan:
"adding Shiller/ECY as a data source and a valuation level plus vol-target
scale to W7".

W7 can't express any of it today:
- Its gates close when the market leaves calm, and their only effects are
  *block adds*, *suggest hedge* and *note* (`data/regime.py`).
- Its beta rule (4.beta) is a fixed band.
- Nothing reads valuation at the market level. W6 values single names.

## What the evidence says (scratch runs, 2026-10-24)

Three backtests, kept in `spikes/target/` (gitignored), with their
results files:
- **The setup:** daily US market and T-bill returns (Ken French,
  1926-07 → 2026-08) and Shiller's monthly data (1871 →).
- **The rules:** β between 0.7 and 1.0, the rest in T-bills.
- **No look-ahead:** β is decided at the close of t and earns t+1.

Reproduce them:

    cd spikes/target
    py -3.12 -I -X utf8 beta_timing.py data/ff_daily.csv
    py -3.12 -I -X utf8 valuation_timing.py data/ff_daily.csv data/ie_data.csv
    py -3.12 -I -X utf8 combo_timing.py data/ff_daily.csv data/ie_data.csv

| 1926–2026 | CAGR | Vol | Sharpe | Worst DD | α on the market ± NW SE |
|---|---|---|---|---|---|
| Constant 0.7 | 8.45% | 12.0% | 0.46 | −71% | — |
| Add β as vol rises (your first rule) | 8.54% | 14.8% | 0.40 | −81% | −0.75% ± 0.24 |
| Add β on a dip from the 12-month high | 8.46% | 14.2% | 0.41 | −82% | −0.64% ± 0.23 |
| Add β when CAPE is cheap | 9.32% | 13.7% | 0.47 | −77% | +0.28% ± 0.23 |
| Add β when the excess CAPE yield (ECY) is cheap | 9.77% | 13.3% | 0.51 | −74% | +0.76% ± 0.20 |
| Vol target, capped at 1 | 9.57% | 11.1% | 0.57 | −58% | +1.82% ± 0.52 |
| **ECY level × vol scale** | 8.82% | 8.7% | **0.63** | **−46%** | **+1.96% ± 0.44** |
| ECY × vol, floored at 0.7 | 9.19% | 12.4% | 0.50 | −71% | +0.52% ± 0.14 |

What it means:
- **Valuation level, yes. Changes (vol up, prices down), no.**
  - Adding beta because vol rose was worse than constant 0.7 at every
    threshold, in every era, and in all nine crises from 1929 to 2025.
  - The excess yield, which nets out real bond yields, worked where plain
    CAPE didn't. CAPE sat in its top fifth for most of the last 30 years.
- **The two layers add to each other.** The combination's α is
  +0.52% ± 0.15 beyond vol targeting, and +1.42% ± 0.41 beyond ECY.
- **Since 1993:**
  - Its worst drawdown was −26%, against −37% to −52% for the other
    rules at their default settings.
  - It got there with less beta: mean β 0.58, CAGR 7.5% against 8.8% for
    constant 0.7, at 8.3% vol against 12.9%.
- **A 0.7 floor switches the vol layer off.** The floored combination's α
  since 1993 is +0.04%. The benefit comes from cutting below 0.7 in
  storms.
- **Multiple testing.**
  - 31 rules were tried across the three runs, about 10–22 of them
    independent.
  - A circular-shift family-wise test shifts every β path by the same k
    days. That keeps each rule's persistence and the rules' correlation,
    and destroys only the timing.
  - It keeps the combination and ECY at its main thresholds (p
    0.006–0.03). Two of ECY's threshold variants fall just short (0.07,
    0.09), and so does vol targeting alone (0.09).
- **What the test can't remove:** CAPE, ECY and vol-managing were each
  found on largely this same US history.
  - α after publication is positive but short and noisy: vol target since
    2017 +2.1% ± 1.8%, ECY since 2020 +0.5% ± 0.2%, CAPE since 1998 ≈ 0.
  - McLean & Pontiff (2016) found published predictors earn about 58% less
    after publication. Expect something like half the backtested α.

So the evidence supports the **direction**: contrarian on valuation level,
de-risk on rising vol. It doesn't support the **size** of the α. This plan
makes the rule visible and checkable. It doesn't make it right.

## Fit with the hard rules

- **Read-only (D12).** The target is a number on the checklist, like every
  W7 rule. Nothing is sent anywhere. [EXECUTION_PLAN.md](EXECUTION_PLAN.md)
  (a separate, unapproved proposal) isn't needed or assumed.
- **No new dependency** on the recommended path (decision 2).
- **A new data source, by file import (D34).** It's stored with
  provenance, usable offline, and versioned like the rest (D36, D37).
- **Layering:**
  - `importers/` reads the file (data/ + pandas + stdlib)
  - `storage/` keeps it (data/ + sqlite3)
  - `models/` holds the math (data/ only)
  - `services/review.py` wires it, and `gui/pages/review.py` shows it
- **Derive before code.** T2's math below goes into its module docstring
  for your review before any implementation, as K1 and W2 did.

## The plan

Order: **T1 → T2 → T3**, and **T4** only after W2b, if at all. T1–T2 need
nothing from LENS_PLAN. T3 could wait for V1 so the card is built once in
the new design language (decision 1).

### T1. Shiller's data: import and store

**The file.** `ie_data.xls` from shillerdata.com: the S&P Composite's
monthly price, dividend and earnings, CPI, and the 10-year yield, from
1871.
- **Its format is a problem.** It's a legacy `.xls`, and no allowed
  package reads it. pandas needs `xlrd` for `.xls`; `openpyxl` reads only
  `.xlsx`.
- **The recommended path is a manual CSV,** like the Fidelity export.
  Monthly data makes a monthly manual step reasonable. You open the file
  in Excel and Save As CSV (the Data sheet), and the app imports that.
- **Measured cost:** Excel writes the 2 decimals it displays. Against a
  full-precision copy, ECY moves by ≤ 0.014 percentage points, its
  percentile by ≤ 1.6 points, and the level by ≤ 0.02 β.

**`importers/shiller_csv.py`** (name settled at T1):
- **Columns by header label, not position.** It reads the header row that
  starts with `Date`, then `P`, `D`, `E`, `CPI` and `Rate GS10` (and `CAPE`,
  only for the cross-check). Shiller adds columns over the years, and a
  missing label is an error that names it.
- **Dates.** Shiller writes October as `1871.1`, so the month is
  round(100·(x − ⌊x⌋)). Months must be consecutive and unique; a gap or
  duplicate names its line.
- **Blank and `NA` cells** are missing values. Earnings end months before
  prices do, and the import records "earnings through …".
- **The last month is provisional and dropped.** The file's own footnote
  says so ("Oct price is Oct 2nd close", "Sept/Oct CPI estimated"), the
  same treatment V0 gives a same-day bar. The footnotes are kept with the
  import, for the page.
- Percent- and thousands-formatted columns are never read.

**Storage** (schema v7, with the D36 backup; if W2b lands first, the next
number):
- `valuation_months`: month, price, dividend, earnings, CPI, GS10.
- `valuation_imports`: the file's SHA-256, imported at, first and last
  month, earnings through, footnotes.
- Shiller revises history (CPI, recent earnings), so an import is a whole
  snapshot. The newest replaces the rows, and every review records the
  import hash it used.

**Tests** (a synthetic fixture in Excel's Save-As layout; Shiller's data is
never committed, see decision 3):
- `1871.1` is October; a blank-earnings tail; `NA` cells; the footnote row
- a missing header names its label; a skipped or repeated month names its
  line
- the provisional last month is dropped
- store round trip; a re-import replaces the rows and records the new hash

### T2. The target, derived

A new module, `models/target.py` (name settled at T2), holds the math in
its docstring and is reviewed before any code.

**Monthly valuation.** m indexes months. P_m is the month's average
close and E_m trailing 12-month earnings (Shiller's columns).
- Real values: RP_m = P_m / CPI_m and RE_m = E_m / CPI_m. The deflator's
  base cancels in every ratio below.
- Ten-year earnings with a reporting lag ℓ:

      E10_m = (1/120) Σ_{j=0}^{119} RE_{m−ℓ−j},   ℓ = 3

  Shiller's own column uses ℓ = 0, which a real-time reader can't. With
  ℓ = 3, the result tracks his column at ρ = 0.9999 (median |diff| 0.46%).
- CAPE_m = RP_m / E10_m.
- Ten-year inflation: π10_m = (CPI_m / CPI_{m−120})^{1/10} − 1.
- **The excess CAPE yield:**

      ECY_m = 1/CAPE_m − (GS10_m/100 − π10_m)

  This is the earnings yield over the real bond yield, Shiller, Black &
  Jivraj (2020).
- **Its expanding percentile:**

      p_m = #{k < m : ECY_k < ECY_m} / #{k < m}

  This uses earlier months only, so appending a month never changes a
  past p. It's undefined until 120 earlier months exist (1891).

**The level.** With your percentiles a < b and your level range
λ_lo ≤ λ_hi:

    L_m = λ_lo + (λ_hi − λ_lo) · clip((p_m − a) / (b − a), 0, 1)

**The vol scale.** On the benchmark's daily simple returns r:

    σ̂_t = √(252/n · Σ_{s=t−n+1}^{t} r_s²),  n = 21
    v_t = min(1, σ*/σ̂_t),   with v_t = 1 when σ̂_t = 0

- σ̂ is not demeaned: over 21 days the mean is noise, and it's what the
  backtest used.
- σ* is your vol target. A blank σ* means v_t = 1, the level alone.
- Decision 4 asks whether to use W7's existing GARCH forecast instead.

**The target.** With your bounds β_min ≤ β_max, and m(t) the newest
stored month no later than month(t) − 1:

    β*_t = clip(L_{m(t)} · v_t, β_min, β_max)

- Shiller's P_m is the month's average, so month m is known only once it
  ends. Day t can use at most month(t) − 1.
- **Staleness.** If m(t) is older than month(t) − 1 by more than
  `STALE_MONTHS` = 3 (a named constant, decision 6), the target reports
  no data, naming the month it has.

**What the review reads.** It reads a gap, not the target itself, so the
existing grading works unchanged:

    gap_t = β̂_p − β*_t,   SE(gap) = SE(β̂_p)

- β̂_p is W2's portfolio β (today's mix, HAC).
- β* is a rule's output, not an estimate, so it adds no error.
- A rule like `−0.1 – 0.1 by 1 SE` then grades it through `check()`, with
  D49's evidence levels, near zone and persistence as they are.
- **The sign says which way:** above the target is more beta than the
  rule wants; below is room the rule allows. It's a number, never an order.

**REGIME.md** (the template; your copy stays yours, and absent rows leave
the target off):
- **Section 4** gains a rule: `| Beta vs its target | ___ – ___ | β |
  β̂ − β*, ± SE (T2) | ___ |`.
- **Section 7** gains a small *Beta target* table (decision 5), all blank
  in the template:
  - Level from valuation: λ_lo – λ_hi, in β
  - ECY percentiles: a – b, in %
  - Volatility target: σ*, in % a year
  - Target bounds: β_min – β_max, in β (a floor lives here)
- **The backtest's values, for reference only:** λ 0.7–1.0, a = 50%,
  b = 80%, σ* = 12%, bounds none or 0.7–1.0. Section 10's change policy
  applies to whatever you choose.
- **If your target bounds reach below your 4.beta band,** the review notes
  that two of your rules disagree (decision 7).
- **The parser learns the new rows.**
  - `importers/regime_md.py` gets the section 4 rule in `_RULES`, and
    `_market` gets the section 7 table.
  - In `data/regime.py`, `MarketSettings` gains a target record.
  - An unknown label stays an error (D49), so a mistyped row can't
    silently turn the target off.

**Tests:**
- **E10 in closed form.** For RE_m = e·(1+g)^m:
  E10_m = e(1+g)^{m−ℓ−119} · ((1+g)^{120} − 1) / (120 g).
- **Constant inflation** i gives π10 = i exactly.
- **No look-ahead:** appending months leaves every earlier p unchanged.
  Ties use strict "<".
- **Shape of the target:** L is non-decreasing in p, v is non-increasing
  in σ̂, v = 1 for σ̂ ≤ σ*, and the bounds hold.
- **Months:** day t maps to month(t) − 1, and the stale cutoff applies.
- **The gap** grades through `check()` exactly as 4.beta does.

### T3. The Review page: a Beta target card

- **The decomposition:**
  - β* and its parts: ECY, its percentile and month → L; σ̂ against σ* → v
  - the bounds, β̂_p ± SE, and the gap in SE
- **A chart** (`gui/charts.py`): β*_t over the lookback, with W2's causal
  rolling β of the mix beside it. The past is drawn from stored data only.
- **The side panel:** the import (file, months, earnings through,
  footnotes, staleness) and an *Import Shiller CSV* button, through the
  TaskRunner.
- **A citation in the app:** Shiller, *Irrational Exuberance*, data from
  shillerdata.com.
- **Checks per AGENTS.md:** the geometry at 1280 px (each card against its
  minimum, the tables' columns), a headless smoke test, and a screenshot on
  a copy of the store.

### T4. The evidence, in-app (optional; after W2b)

The scratch runs become a reproducible Research run, so section 10's "a
research run supporting it" can name one. Today's Research can't host it:
- **Strategies take prices only** (`models/strategies.py`). The rule needs
  a monthly series beside them.
- **The backtest earns a constant cash rate** (`models/backtest.py`).
  Across 1926–2026 that has to be a time-varying T-bill series, which is
  W2b's RF.
- **The long history** comes from W2b's French market series, or IBKR's
  SPY from 1993.
- **Optional:** report the circular-shift family-wise p across the saved
  runs on the same data, a "how many things did you try" number. It's new
  research machinery, so it's your call.

## The data track, one row added

| Provider | What | Milestone | Terms |
|---|---|---|---|
| Shiller's `ie_data.xls`, as a CSV you save | monthly price, dividend, earnings, CPI, GS10 from 1871 | T1 | no license stated (decision 3) |

## Decisions for you

1. **The milestones and their place.** Are T1 → T2 → T3 approved? Do they
   go before LENS_PLAN's V1, or does T3 wait for V1's design language? Is
   T4 in, after W2b, or out?
2. **Reading the file:**
   - **(a) A manual CSV,** recommended. No dependency, a monthly step, and
     the rounding measured above.
   - **(b) `xlrd`** (pure Python, BSD). The app fetches and reads the
     `.xls` itself, exactly. It's a new dependency, and the app's second
     online provider after the SEC.
3. **Shiller's terms.** Neither the page nor the workbook states a license.
   The workbook's Disclaimer sheet covers accuracy and investment advice
   only. The plan treats the data as local-only: never committed, tests on
   a synthetic fixture, cited in the app. Is that acceptable?
4. **The vol estimator.**
   - **21-day realized vol** is what the backtest tested.
   - **W7's GARCH h-day forecast** is already computed, smoother and
     forward-looking, but untested in this rule.
5. **Where the parameters live:** a *Beta target* table in section 7, plus
   a row in section 4. Or a new section; that would renumber sections 8–10
   and every key that names them, so the plan avoids it.
6. **Staleness:** the target goes silent when the newest month is more
   than 3 months behind. Should that be a different number, or your own
   REGIME.md setting?
7. **Disagreeing rules:** when the target's bounds go below your Beta band,
   should the review only note it (proposed), or should one rule take
   precedence?

## Doc changes on approval

- **ROADMAP.md:** T1–T4 and where they sit.
- **DECISIONS.md:** one entry per milestone when it lands, at the next free
  number (LENS_PLAN reserves D51 for its phase if approved).
- **CURRENT_STATE.md:** the plan, and later each milestone.
- **REGIME.md (template):** section 4's new row and section 7's table, at
  T2.
- **ARCHITECTURE.md:** the importer, the two tables, the target module.
