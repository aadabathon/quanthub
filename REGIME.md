# REGIME.md — your trading regime, written down

A template (W0, D43). You fill in the `___` values, and W7 turns each line
into a stored, versioned rule set and a daily check on the Review page.
Until then it's the spec that W2–W6 are built to answer.

- **The values are yours.** The app measures and reports against them. It
  doesn't choose them, and nothing here recommends a number.
- **Keep your filled-in copy private.** The repository is public, so the
  filled-in copy lives at `~/.quant_hub/REGIME.md`, beside
  `settings.toml`, and W7 reads it from there. This file stays the blank
  template.
- **"Measured by"** names the number the app compares against. A tag says
  when it exists: *today* (v1.0), or the milestone that adds it (W2, W3,
  W6, W7).
- **"On breach"** is one of:
  - **fail:** the review says stop and look
  - **warn:** worth a look
  - **note:** recorded only
- **How W7 reads a value** (`importers/regime_md.py`):
  - A blank (`___`) leaves its rule off. One side of a range can stay
    blank.
  - Percent rows take percent numbers: `15` means 15%.
  - A rule that reads an estimate (± SE) breaches once the estimate is
    past its limit. To require evidence first, add `by k SE` to the value,
    e.g. `0.8 – 1.2 by 1.645 SE` (`models/regime.py`).
  - Rows are recognized by their first column. Change the values, not
    the labels.

Version: ___ · Written: ___ · Reason for this version: ___

## 1. Thesis

In your words (2026-10-20); edit freely:

> A core beta stream, plus hedge and return streams orthogonal to it, to
> minimize volatility and maximize CAGR. Compound until the portfolio's
> convexity is meaningful: when the assets pay for the lifestyle.

What would prove the thesis wrong for a given stream: any of these tests,
with the limits set in section 6. One failing review is a **warn**;
failing ___ reviews in a row is a **fail**.
- **Growth hurdle (W3):** the stream's margin (μᵢ − r) − Cov(Rᵢ, R_p) is
  below zero.
- **Orthogonality (W2):** its residual correlation with the core, or with
  another stream, is above the limit.
- **Beta creep (W2):** its β to the benchmark is above the limit.

## 2. Accounts and data

| Setting | Your value | Notes |
|---|---|---|
| Accounts in scope | ___ | Fidelity names as in the export (Individual, ROTH IRA, …); IBKR once W1 lands |
| Positions refresh | ___ | Fidelity is a manual CSV download; how often you'll export |
| History source | ___ | `ibkr` (needs TWS open for windows ending today) or `file` (CSV imports) |
| Benchmark (the core's yardstick) | ___ | Portfolio page *Benchmark*; SPY by default |
| Cash rate | ___ % | Portfolio page *Rate %*; used for excess returns, the hurdle and option pricing. Usually the 3-month Treasury yield (FRED `DGS3MO`) |
| Lookback for risk numbers | ___ calendar days | Portfolio page *Lookback*; 730 by default, at least 120 |
| Regression window | ___ | W2 fits over the lookback; rolling β uses 126 trading days. Longer means smaller standard errors, slower to notice change |

## 3. Sleeves

Every holding gets one sleeve. W3 reports growth, hurdles and sizing per
sleeve, as well as per holding. Fill this in after the first import: the
dry run lists your holdings.

| Symbol | Sleeve: core / hedge / stream / other | Why it's held (one line) | Intended size or range |
|---|---|---|---|
| ___ | ___ | ___ | ___ % of equity |

- **Sleeve names.** Write `core`, `hedge`, `other`, or `stream: <name>`.
  Each stream needs its own name: two streams with one name are one
  sleeve, and the orthogonality test can't run between them.
- **Intended size.** A range (`8 – 12`) is checked. A single number is
  shown as the target, not graded.
- **Options** are listed under their underlying unless they form a sleeve
  of their own (e.g. a put hedge on the core).

Sleeve size outside its intended range: ___ (fail / warn / note)

## 4. Portfolio limits

| Rule | Your value | Unit | Measured by | On breach |
|---|---|---|---|---|
| Max single holding weight | ___ | % of equity | Holding weight, options counted by delta (*today*) | ___ |
| Max single holding risk share | ___ | % of portfolio vol | Euler risk share (*today*) | ___ |
| Portfolio volatility band | ___ – ___ | % a year | *Volatility · annual* tile (*today*) | ___ |
| Beta band | ___ – ___ | vs the benchmark | *Beta* tile (*today*); ± SE (W2) | ___ |
| 1-day VaR limit (95%) | ___ | % of equity | Historical VaR, options repriced in full (*today*) | ___ |
| 1-day ES limit (99%) | ___ | % of equity | Historical ES (*today*) | ___ |
| Drawdown tolerance | ___ | % | Worst drawdown of today's mix, replayed (*today*) | ___ |
| Cash range | ___ – ___ | % of equity | Cash and money-market lines (*today*) | ___ |
| Minimum effective holdings | ___ | count | *Effective holdings* N_eff (*today*) | ___ |
| Sizing cap | ___ | fraction f of estimated Kelly | Kelly f\* and its SE (*today*; per sleeve in W3) | ___ |

When one day carries most of a holding's variance (the review flags it,
`models/regime.py`), grade the return-based numbers: ___ (as measured /
without that day; blank is as measured)

## 5. Hedges and options

| Rule | Your value | Unit | Measured by | On breach |
|---|---|---|---|---|
| Hedge budget | ___ | % of equity a year | Hedge sleeve's carry: option theta × 365, plus other hedges' excess mean (W3) | ___ |
| Max premium at risk | ___ | % of equity | Market value of long options (*today*, from the import) | ___ |
| Net delta band | ___ – ___ | % of equity | Aggregate delta dollars across positions over equity (W3) | ___ |
| Vega limit | ___ | $ per vol point | Aggregate vega (W3) | ___ |
| Short options allowed | ___ | yes / covered only / no | Option positions with negative quantity (*today*) | ___ |
| Minimum days to expiry held | ___ | calendar days | Days to expiry from the snapshot date (*today*) | ___ |

## 6. The thesis, checked

| Rule | Your value | Unit | Measured by | On breach |
|---|---|---|---|---|
| A stream must clear its growth hurdle | ___ | e.g. estimate above the hurdle, or by ___ SE | (μᵢ − r) − Cov(Rᵢ, R_p), ± SE (W3) | ___ |
| Max correlation between streams | ___ | \|ρ\| of regression residuals | Residual correlation matrix (W2) | ___ |
| Max beta of a "stream" to the core | ___ | β ± SE | Regression on the benchmark (W2) | ___ |
| Fractional sizing | ___ | f (share of the estimated optimum) | h\* and f(2 − f) of max growth (W3) | ___ |

## 7. Market-state gates

What the market's state allows. W7 evaluates these on the benchmark (or
another symbol you name) each review.

| Gate | Your value | Measured by | Effect when closed: block adds / suggest hedge / note |
|---|---|---|---|
| Calm-regime probability | ≥ ___ % | Markov filtered probability of the lowest-vol regime, K = ___ (*today*: Markov Lab; never smoothed or Viterbi) | ___ |
| Volatility forecast | ≤ ___ % a year | GARCH expected vol over ___ days (*today*: GARCH Lab, vol context) | ___ |
| Regime odds ahead | ≥ ___ % calm in ___ days | Markov odds of the calm regime that many days ahead (*today*) | ___ |
| Your own indicators | ___ | Anything else you watch, separated by `;`. Each is a manual tick box on the Review page | ___ |

## 8. Universe and screens

| Rule | Your value | Measured by | On breach |
|---|---|---|---|
| Universe | ___ | Holdings + Markets watchlist + this list | — |
| Excluded kinds | ___ | e.g. financials, insurers, REITs (unlevered DCF is the wrong model, per the DCF README) | — |
| DCF margin of safety to add a name | ≥ ___ % | 1 − price / value per share (W6) | ___ |
| Max terminal share of EV | ≤ ___ % | PV(terminal) / EV (W6) | ___ |
| Implied growth vs history | ≤ ___ × trailing revenue CAGR | Reverse DCF, every root shown (W6) | ___ |
| Missing filing fields allowed | ___ | The loud-failure list: missing fields named, never filled in (W5) | ___ |
| Valuation refresh | ___ | W5 refetches filings after a day or on Refresh; a saved valuation's fingerprint says when its filings changed | — |

## 9. Routine

| When | Steps | Kept in the journal (W7) |
|---|---|---|
| Daily | ___ (e.g. export positions → Review: sync, risk, regressions, growth, gates, checklist) | ___ |
| Weekly | ___ | ___ |
| Monthly / quarterly | ___ (e.g. sleeve sizes, DCF refresh) | ___ |
| After a trade (placed by hand) | ___ | symbol, quantity, price, the reason, which rule it serves |

## 10. Changing the rules

A change makes a new version with a reason. W7 keeps every version, so a
review always says which rules it ran under. Anything you want to require
before a change (e.g. a research run supporting it): ___
