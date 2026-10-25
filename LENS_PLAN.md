# LENS_PLAN.md — your portfolio, through every lab

Proposal, 2026-10-24. Planning only: no app code changes, nothing committed.
It reshapes the personal workstation phase (D43, [WORKSTATION_PLAN.md](WORKSTATION_PLAN.md))
around your direction of the same day. Each milestone still needs your
approval before it starts.

**Status, later on 2026-10-24.**
- V0 landed (D50).
- W2–W7 landed separately, through PR #2 (D44–D49): regressions, growth,
  the DCF, SEC fundamentals, Valuation and Review. That happened in another
  session, in the original order, so this plan's order is history.
- What it still adds:
  - V1
  - the CAPM workshop's extras on top of the landed W2: the security
    market line, GRS, the Kalman β
  - L, W2b and O

## Your direction, restated

- Quant Hub becomes a workstation personalized to *your* portfolio. It's
  data-driven, and it shows the portfolio in the light of every lab.
- Its purpose is decisions: informed expectations about the future.
- A CAPM and Fama–French workshop, and over time most of the quant finance
  literature, each piece applied to your holdings.
- A UI that is "sexy and human".
- Data accumulated from several APIs.

Nothing in this conflicts with the hard rules. It's still read-only (D12),
there are no new dependencies, and every new provider is stdlib plus terms
checked (D34). It changes the *order* of D43's plan and adds four
milestones (V0, V1, L, O), plus a second online provider.

## What the Portfolio page shows today (2026-10-24, your latest import)

The page was screenshotted headless on a copy of the store. Holdings aren't
named here.

1. **One day decides most of the numbers, and nothing says so.**
   - A small position carries most of the risk, because of one session in
     which it rose many times over.
   - That single day is most of the replayed mix's variance.
   - Without it, the invested mix's mean return falls by more than half.
     That mean also feeds Kelly.
   - It sets the P&L histogram's x-axis, so the body of the distribution
     is squeezed into a fifth of the chart.
   - It inflates the Gaussian VaR above the historical one, because a gain
     is counted as risk.
   - It bends the existing models when they're pointed at the mix:
     - GARCH's next-month forecast comes out equal to its long-run vol.
     - A two-regime Markov fit spends its second regime on that one day.
   - The bars look real, not a split error: adjusted and traded agree, and
     volume jumped by orders of magnitude. So it's a true event, but it's
     one observation.
2. **The page can't run with TWS closed.**
   - The store holds every bar through Oct 23. But with the source set to
     `ibkr` and no connection, the cache insists on fetching today's tail
     (a Saturday).
   - Every symbol then fails, and the page reports "0 common days of
     returns". Data that has been accumulated should be usable offline,
     labelled with its as-of date.
3. **The visuals present numbers; they don't explain them.**
   - Weight and risk share sit in two percentage columns.
   - The replay has no annotation: not the defining day, not the trough.
   - The panel titles fall back to pyqtgraph's own font.
   - The six tiles are equal in weight, so nothing leads.

## The visual direction (a spike, for you to react to)

`spikes/portfolio_v2.py` (gitignored) redraws the Portfolio page from the
same `PortfolioView`, plus the existing GARCH and Markov fits. Run it on a
copy of the store:

    QT_QPA_FONTDIR=C:/Windows/Fonts QT_QPA_PLATFORM=offscreen py -3.12 spikes/portfolio_v2.py <copy.db> <out.png>

What it tries:
- **A hero that says what the portfolio is.** Equity, then one sentence
  with its numbers: how much of the variance the benchmark explains, and
  where the risk sits. The replay is drawn faintly behind it.
- **Findings in plain language.** Three ranked cards. Each has a
  headline, the number behind it, and why it matters: concentration, the
  one day, Kelly's error.
- **Value beside risk, drawn.** Paired bars on one scale, amber where risk
  outruns weight, and a two-year sparkline per holding.
- **An annotated replay.**
  - a gradient area in data coordinates (a fill gradient in bounding-box
    coordinates showed vertical bands in the spike's first render;
    `charts._vertical_gradient` uses that mode, so V1 checks the
    Dashboard for the same thing)
  - end labels on the lines
  - the defining day and the drawdown trough marked on the chart
- **Losses said as sentences:** "On 1 day in 20, expect to lose more than
  $X." The histogram is clipped to its body, with an edge count of the
  days beyond, and the tail past VaR is shaded.
- **Through the labs:** the same portfolio, read by GARCH and Markov
  (live), with CAPM/FF, the forward fan and the DCF as planned rows.
- **The data behind it:** source, basis, days, coverage, the typed cash
  rate, and flagged days.

It's built only with QPainter and pyqtgraph; no new dependency.

## The plan

Order: **V0 → V1 → W2 → L → W2b → O → W3 → W4–W6 → W7.** W1 (IBKR
positions) lands whenever the account exists, as before. Each milestone is
a few files and is reviewed before the next.

### V0. Data you can trust (small; first)

**Landed 2026-10-24 (D50).** One change from this proposal:
- **The flag threshold is k = 10, not 6.** Measured, 6 flags 1.4–3.2 days
  per two years of fat-tailed noise, and four days in SPY's own last two
  years. 10 flags 0.2–0.7 days, and SPY's one, Apr 9, 2025.
- The proposal below is kept as written.

**Offline-first reads.** When the provider can't be reached, `CachedSource`
serves the stored range and says how stale it is ("as of Oct 23"). It no
longer fails the symbol. Non-session days at the tail don't count as
missing. A D18 amendment.

**Influence diagnostics** go in `models/portfolio.py`. For the mix's daily
returns rₜ:
- Day t's share of the sample variance is (rₜ − r̄)² / Σₛ(rₛ − r̄)².
- The robust z is zₜ = (rₜ − median) / (1.4826 · MAD). The factor makes
  the MAD estimate σ under normality.
- A day is flagged when |zₜ| > k. k is set at review; 6 is a starting
  point.
- Leave-one-out mean: r̄₋ₜ = (n r̄ − rₜ)/(n − 1). The page shows the
  headline numbers (mean, Kelly, VaR, vol) with and without the flagged
  days, side by side.
- Nothing is ever dropped silently: the full-sample numbers stay the
  headline.

Tests:
- a planted jump is flagged and its share is recovered
- clean GBM flags nothing at k = 6 (about 2e-9 per day under normality)
- the leave-one-out identity

**Histogram clipping.** The display range is clipped to its body, with an
edge count. This is presentation only; the statistics use every day.

### V1. The design language, Portfolio first

The spike's look goes into `theme.py`, `charts.py` and `components.py`,
then Portfolio is rebuilt on it. After that it rolls out page by page:
Dashboard, then the labs.
- **Charts:**
  - data-coordinate gradients
  - `Panel` gains `events` (date, label, tone), end labels and an extremum
    marker
  - the histogram gains a clip range with edge counts
  - panel titles use theme fonts
- **Components:** Hero, Finding, ValueRiskBars (custom-painted rows),
  LensRow, ProvenanceCard.
- **Findings are computed, not written in the page.**
  - A pure module maps a `RiskReport` to ranked findings: tone, headline,
    figure and sentence.
  - Its thresholds are named constants with their rationale, and each rule
    has a test.
  - The module name is settled at V1.
  - The page only renders them.
- **The palette stays** (D13/D31 validation). Amber and red still mean
  state, never decoration.

### W2. The CAPM workshop (the derivation is already written)

`models/regression.py` is waiting for your review. It becomes the CAPM
workshop:
- α and β ± HAC standard errors, R², and rolling β per holding and for the
  mix.
- **Systematic vs. own risk.** R² = β²σₘ²/σₚ² is the hero's "the
  benchmark explains X% of the daily variance".
- **The security market line.** Each holding's β against its mean excess
  return, with the line r_f + β(μₘ − r_f). Jensen's α is the vertical
  distance to it; Treynor and the information ratio go beside it.
- **Joint test of α = 0 across holdings: GRS (1989).**
  - With N assets, T days and L factors:

        W = (T−N−L)/N · (1 + f̄′Ω̂⁻¹f̄)⁻¹ · α̂′Σ̂⁻¹α̂ ~ F(N, T−N−L)

    under i.i.d. normal errors.
  - Daily returns aren't i.i.d., so the page also shows the HAC Wald
    statistic α̂′V̂_α⁻¹α̂ ~ χ²_N.
  - Both get derived before code.
- **Time-varying β, a lens on the Kalman Lab.**
  - Make β a random walk: βₜ = βₜ₋₁ + ηₜ, observed through
    yₜ = α + βₜ fₜ + εₜ.
  - That's `models/kalman.py`'s state space, except that its
    observation row Hₜ = (1, fₜ) changes daily. Today H is fixed, so this
    is a small, tested extension. The filtered β gets a band; rolling OLS
    has none.

### L. Lenses: the portfolio as a series in every lab

- **A `portfolio` source** in `MarketService`. It serves today's mix,
  replayed, as a price history (the value of $1), one symbol per account.
- **Every lab already takes a source and a symbol,** so GARCH, Kalman,
  Markov, the Simulator and Research all gain your portfolio with no page
  changes.
- **The Portfolio page's "Through the labs" card** shows one live number
  per lab and opens that lab on the portfolio.
- **Caveat, stated on every lens: this is a constructed series, not your
  account's history.**
  - It applies today's weights backwards.
  - A position that just jumped enters the past at its post-jump size,
    which is the effect in finding 1.
  - The account's real history needs transaction or position history. A
    Fidelity history export is a later importer.

### W2b. The Fama–French workshop, and its provider

**Provider: Ken French's Data Library.**
- Daily FF3, FF5 and momentum, each with its daily risk-free rate RF.
- Fetched with stdlib `urllib` and `zipfile`, and parsed with pandas.
- Stored as factor series in SQLite, one more schema version under D36's
  migration path. Each series keeps its release date.
- Refreshed on demand.
- The library publishes with a lag of about one to two months. That lag
  bounds the regression window, and the page shows it.
- This needs a terms check (D34) and a citation in the app.

**RF replaces the typed "Rate %"** wherever it covers the dates. A FRED
T-bill series (DTB3) can fill the recent tail; it's the third provider,
with its own terms check.

**Results:**
- Loadings per holding and for the mix, ± HAC.
- **Return attribution.** Each factor's part, β̂ₖ f̄ₖ, plus α. They sum to
  the mean excess return.
- **Variance attribution.** b′Σ_f b against residual variance.
- **An honest expectation.**
  - The expected excess return is E[R − r_f] = Σₖ βₖ λₖ, with each factor
    premium λₖ estimated from the library's full history: from 1926 for
    the three factors and momentum, from 1963 for the five-factor set.
  - By the delta method, assuming the two estimation errors independent:

        Var ≈ β′V_λβ + λ′V_βλ

  - Shown beside CAPM's β·λₘ, so you see how much the extra factors change
    the expectation, and how sure either one is.

**Tests:**
- planted loadings are recovered
- the attributions sum exactly
- parsing runs offline on recorded files

### O. Outlook: expectations of the future, in one place

After W2b and L. This is the page your phrase "informed decisions about
expectations of the future" asks for. Each number carries its error.
- **Expected return:** CAPM and FF, ± SE.
- **Volatility:** the GARCH term structure over 1, 3 and 12 months.
- **Regime odds:** today, and 20 days ahead (Markov).
- **The forward fan, with parameter uncertainty.**
  - The log value at horizon h is N((μ − σ²/2)h, σ²h).
  - μ itself is only known to ±σ/√T, so the predictive variance is

        σ²h (1 + h/T)

  - With two years of data, the two-year-ahead fan is √2 wider than the
    model-only fan. Saying so is the feature, as Kelly's error is in D39.
- **Probabilities:** the chance of a drawdown beyond your REGIME.md
  tolerance within a year, and of ending below today's value.

### The literature track: one workshop at a time

**The workshop pattern.** Each one has:
- the paper, in the Research page's catalog (which exists)
- the derivation, in the module docstring
- the result on your holdings
- statistical tests against a planted truth

**A proposed queue, after CAPM and FF.** You choose the order:

- Black–Litterman (1992): your views blended with the market's implied
  returns.
- Minimum variance with Ledoit–Wolf shrinkage (2004): the D39 deferral.
- Risk parity.
- Volatility-managed portfolios (Moreira–Muir 2017). This is the closest
  to your thesis, "minimize vol to maximize CAGR", so it pairs with W3.
- Time-series momentum (Moskowitz–Ooi–Pedersen 2012).
- Betting against beta (Frazzini–Pedersen 2014).

### Unchanged

- **W3**, streams and growth. Its hurdle needs W2's covariances.
- **W4–W6**, the DCF track, with SEC as the first online provider.
- **W7**, the regime and its daily review.

## The data track, in one view

| Provider | What | Milestone | Terms |
|---|---|---|---|
| IBKR (exists) | bars, quotes, chains, positions | — | read-only API |
| CSV import (exists) | positions, bars | — | — |
| Ken French Data Library | FF3/FF5/momentum, RF, daily | W2b | to check |
| FRED | T-bill tail; later VIX and the term spread for regimes | W2b | to check |
| SEC companyfacts | fundamentals | W5 | checked (D43) |

They follow the same rules as D34 and D43:
- stdlib only
- stored as fetched, with provenance
- usable offline from the store (V0)

## Decisions for you

1. **The visual direction.** Is the spike the look you want, or what would
   you change (density, color, tone of the words)?
2. **The order.** V0 → V1 → W2 → L → W2b → O, then W3 and the DCF track.
   Or put W2 (CAPM) before V1, since its derivation is ready.
3. **Review the W2 derivation** (`models/regression.py`), now as the CAPM
   workshop. It adds GRS and the Kalman β to the original scope.
4. **Ken French's library as the second online provider,** and FRED as
   the third, each subject to a terms check.
5. ~~**V0's offline-first change to the cache** (a D18 amendment), and the
   flag threshold k.~~ Approved and landed (D50), with k = 10.

## Doc changes on approval

- **ROADMAP.md:** the reordered phase and the new milestones.
- **DECISIONS.md:**
  - D51, for the phase as a whole.
  - Entries written when their milestone lands: offline reads, influence
    flags, the findings module, the portfolio source, the factor provider,
    the outlook's predictive fan.
- **CURRENT_STATE.md:** the two findings (offline failure and the
  unflagged outlier day) as known limits until V0 lands.
- **AGENTS.md:** V1's new components and chart options.
