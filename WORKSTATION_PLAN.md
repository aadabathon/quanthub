# WORKSTATION_PLAN.md — your regime and the DCF modeler, inside Quant Hub

Proposal, 2026-10-20. Planning only: no app code, nothing committed. Each
milestone below needs your approval before it starts.

## What the two things are

**Your regime** (from your answers). It's a portfolio thesis more than a
signal: hold a core beta stream, add hedge and return streams orthogonal to
it, and keep volatility down so CAGR compounds until the portfolio's
convexity matters. It covers rules, a market-state view, a daily routine,
and a universe with screens. It trades options and a long-term portfolio
(Fidelity) on daily data. Scope, in your words: see your Fidelity or IBKR
portfolio, run workflows on it ("regressions and such"), and absorb the DCF
modeler. The test is whether you'd use it instead of other tools.

**The DCF modeler.** It lives at `C:\Users\adams\Projects\Fin-SW\DCF_proj`
as package `dcfmodeler` 0.2.0, committed as "DCF modeler 1.0.0" in the Fin-SW
repo. About 1,100 lines and 28 tests, which pass offline in 2 s.
- **Shape:** it's already ports and adapters.
  - `adapter_edgar.py` is the only module that imports edgartools. It
    feeds `schema.FinancialHistory`, where every field is present with a
    source tag or absent with a reason.
  - `engine.value` is pure.
  - `sensitivity` builds the WACC × g grid.
  - `analysis` covers forward multiples, margin of safety, reverse DCF by
    bisection, and historical calibration.
  - `batch` seeds assumptions from trailing averages and ranks a screen by
    margin of safety.
- **Inputs:**
  - a ticker, CIK or EDGAR URL, plus your SEC identity
  - assumptions: growth, EBIT margin, tax, D&A, capex and ΔNWC, each a
    scalar or a per-year path
  - years, WACC and terminal growth
  - overrides for revenue, shares and net debt
  - an optional price
- **Outputs:**
  - EV split into explicit and terminal parts
  - equity, value per share, and the projection
  - multiples and margin of safety
  - implied growth and WACC
  - the WACC × g grid
  - a ranked screen (CSV/xlsx)
- **Dependencies:** edgartools (21 direct requirements: httpx, pyarrow,
  pydantic, lxml, rich, …) and pandas; yfinance and openpyxl for the
  screen.
- **Storage: none.** Every run refetches from SEC; edgartools keeps only
  an HTTP cache in `~/.edgar`. CURRENT_STATE's "stores it in SQLite" was the
  plan, not the code. You confirmed Fin-SW is the current copy.
- **Leftovers:**
  - The pip editable install points at `Learning\Python-Projects\DCF_proj`,
    which no longer exists.
  - `#DCF model.py` and a Java `sec-dcf-scraper` jar in
    `Learning\Java Projects\Sandbox` are earlier sketches, now superseded.

One correction to the brief: Markov, Portfolio and Research aren't
placeholders any more. All three shipped in v1.0 (M2, D39, D42). This plan
extends Portfolio and adds two pages.

## Conflicts with the hard rules

1. **"Deploy algos through Quant Hub."** Order placement breaks hard rule 1
   (D12). ROADMAP already places paper trading, then execution, after v1.0
   as their own guarded phase, so neither is in this plan. Reading your
   accounts (positions, balances, quotes, chains) is in. If you want
   execution, it needs a separate decision to amend D12, with paper trading
   first and after this phase.
2. **Fidelity has no retail API.** "Plug in Fidelity" means its CSV export,
   which is built but not yet checked on a real file. No stored credentials,
   no scraping.
3. **Dependencies.** edgartools, yfinance and openpyxl aren't on the allowed
   list. The proposal uses none of them (W5):
   - SEC data comes through the stdlib (`urllib`, `json`) from SEC's
     `companyfacts` API. Your adapter already bypasses edgartools'
     standardized getters for debt, cash, tax, pretax income and D&A, and
     reads raw us-gaap tags. `companyfacts` serves exactly those tags.
   - Prices come from sources Quant Hub already has (the IBKR quote, stored
     or imported closes), with provenance (D23). That also drops yfinance,
     whose terms D34 never cleared.
   - Export is CSV only, through pandas.
   - The alternative is edgartools as an optional extra, like ibapi (D10).
     It's less porting, but a heavy dependency tree with a cache outside the
     store. Not recommended.
4. **A new online provider.** D34 allows a free one once its terms are
   checked. SEC's fair-access policy asks for a declared User-Agent (name
   and email) and at most 10 requests a second. Your identity goes in the
   settings file; none ships with the code.
5. **Roadmap order.** Several items here are scheduled later:
   - The DCF port is listed "after the roadmap".
   - IBKR positions, aggregate greeks and growth-optimal sizing are P items
     deferred to v1.1 (D39).
   - v1.0's external acceptance checks are still open.

   This plan makes them the next phase, after acceptance. That's a ROADMAP
   change, and approving this plan approves it. Optimizers, the frontier,
   Ledoit–Wolf and the rebalance ticket stay deferred.
6. **No conflict:** daily bars suffice for options and a long-term
   portfolio, so D19 stands. All new storage goes in the same SQLite file
   (D17), through D36's migration and backup path.

## Where each piece lands

| Piece | Module | Storage | Page |
|---|---|---|---|
| Regressions | `models/regression.py` | — | Portfolio: Regressions view |
| Growth and streams | `models/growth.py` | — | Portfolio: Growth view |
| IBKR positions | `broker/` adapter, `IBKRBroker.get_positions` | existing position snapshots | Portfolio account selector |
| Fundamentals contract | `data/fundamentals.py` | — | — |
| DCF math | `models/dcf.py` | — | Valuation |
| SEC fetch | `providers/sec_edgar.py` (new package) | — | — |
| SEC facts | `storage/fundamentals_store.py` | schema v4 | — |
| Fundamentals service, saved valuations | `services/fundamentals.py`, `services/valuation.py` | v5 | Valuation |
| Regime rule sets | `data/regime.py`, `storage/regime_store.py` | v6 | Review |
| Regime checks | `models/regime.py` | — | Review |
| Daily review job, journal | `services/review.py` | v6 | Review |

`providers/` mirrors `importers/`: it may import `data/` and the stdlib,
nothing else, and the GUI reaches it only through services. The rule goes
into `tests/test_layering.py`, and the package name is settled at W5. The
models follow the `data/synthetic.py` exemplar: the DCF has a closed-form
check (a), the regressions a coverage test, and growth a Monte Carlo test.

## Milestones

Math is derived in each module's docstring and reviewed before the code,
as before.

**W0. Your real accounts** (closes v1.0)
- Import a real Fidelity positions export, and fix the preset where it
  differs.
- Run the fresh adjusted-cache check with TWS open.
- Write `REGIME.md` together: you supply the numbers, I supply the
  template. No code. This makes W2–W7 answer your questions rather than
  generic ones. It covers:
  - your benchmark and core
  - a sleeve per holding: core, hedge or stream
  - limits: max weight, vol band, beta band, drawdown tolerance, hedge
    budget, option premium at risk
  - market-state gates, e.g. the Markov calm probability on the benchmark,
    or the GARCH forecast against your vol band
  - screens, e.g. a margin-of-safety floor
  - review cadence

**W1. IBKR read-only positions** (P2's second adapter), once the IBKR
account exists
- `reqPositions` and `reqAccountSummary` (net liquidation, cash) fill a
  `PositionSnapshot`, kept like Fidelity imports. `ibkr` joins the account
  selector.
- Tested against the fake broker, then live-verified. Read-only calls
  only.

**W2. Regressions on the portfolio** (v1.1, with W1 and W3)
- OLS of each holding's and the portfolio's excess returns on the
  benchmark. Optionally add ETF proxies synced into the store (e.g. QQQ,
  IWM, TLT, GLD); a factor-return CSV can follow later.
- Results: α (annualized) ± SE, β ± SE, R², residual vol, rolling β, and
  the residual correlation matrix, which answers "are my streams
  orthogonal?"
- Tests:
  - a planted α and β are recovered
  - 95% intervals cover the truth about 95% of the time under GARCH errors
    with HAC errors, and visibly less often with classical ones

**W3. Streams and growth** (your thesis as numbers)
- For each holding and sleeve:
  - its growth contribution and hurdle, ± SE
  - the vol drag
  - the optimal size, and fractional sizing
  - the minimum-variance hedge ratio
- A read-only what-if, e.g. "this sleeve 5% bigger".
- Options get aggregate greeks across positions (P3's deferred item), and
  their growth is measured on the existing full-repricing replay, because
  the formula below misses convexity.
- Tests: the closed forms below, and Monte Carlo realized log growth against
  g(w) under correlated GBM.
- **Amended 2026-10-21** by the simulations behind the derivation
  (`models/growth.py`):
  - The quadratic doesn't miss convexity on daily data: it matches
    realized log growth to about 0.003%. What misses it is counting
    options by delta, so W3 works from the replay's per-position series.
  - The growth-optimal size is about 140 times noisier than the
    minimum-variance hedge ratio, so the page leads with the latter.

**W4. DCF core, pure** (v1.2, with W5 and W6)
- `data/fundamentals.py` holds your `FinancialHistory` and its loud-failure
  contract. `models/dcf.py` holds the engine, sensitivity, reverse DCF,
  multiples, margin of safety, calibration and seeding.
- Your 28 tests are ported. The golden 18.784 holds unless you change
  convention (c).
- New tests: the Gordon identity, the two-root reverse DCF, and
  terminal-cash-flow consistency.
- Conventions (c)–(e) below are settled at review.

**W5. SEC fundamentals and their store**
- Ticker → CIK through SEC's `company_tickers.json`, then `companyfacts`.
  - Your tag lists carry over.
  - New tags cover what edgartools' getters used to supply: revenue,
    operating income, capex, current assets and liabilities, and diluted
    shares.
  - A missing tag records a reason; no number is invented.
- Facts are stored as filed: concept, period, form, filed date, accession
  and unit. Restatements keep both versions: the latest filing wins per
  period, and its accession is shown.
- `[sec] identity` goes in the settings file. At most 10 requests a second.
  Refresh on demand or when a newer filing exists.
- Tests run offline on recorded JSON. Live check: AAPL and two others
  against their 10-Ks, using your README's first-run checklist.

**W6. Valuation page**
- The flow:
  - company, then history and calibration
  - assumptions, seeded and editable per year
  - the EV bridge, with the terminal share flagged above ~75%
  - the projection and a WACC × g heatmap
  - reverse DCF, showing every root
  - a price from a named source, then the margin of safety
- A Screen tab ranks holdings, the watchlist or a typed list by margin of
  safety. Failures stay as rows. CSV export.
- Valuations are saved immutably, like research runs: assumptions, a
  fingerprint of the facts, and the result.

**W7. The regime: rule sets and the daily review** (v1.3)
- Rule sets come from `REGIME.md`, versioned and immutable in SQLite, so
  you can see which rules held when.
- The Review page is the workflow's front door. It runs one TaskRunner job
  chain:
  1. newest positions
  2. history sync
  3. risk, regressions and growth
  4. market state
  5. valuations of holdings
  6. a checklist: pass, warn or fail, each with its number
  7. a journal entry: notes, and any trades you placed by hand
- Decision support only. No orders.

**Order**
- W0 comes first because everything runs on your real holdings.
- W2–W3 come next: they serve your thesis using data already stored.
- Then the DCF, math before provider, as M1 came before M2.
- The regime comes last because it consumes everything, as R did.
- W1 can land whenever the IBKR account exists.
- The DCF track (W4–W6) needs nothing from W1–W3, so it can go first if
  you'd rather.

## The math

### Growth and streams (W3)

Notation, all annual: weights w (fractions of equity), mean returns μ,
covariance Σ, cash rate r. With continuous rebalancing (the GBM
approximation), the portfolio's log growth is

    g(w) = r + w′(μ − r𝟏) − ½ w′Σw,    CAGR = e^g − 1.

The term ½ w′Σw is the volatility drag. A daily-rebalanced simple-return
portfolio differs by third-moment terms. That's why options are judged on
the replay.

- **The hurdle.** ∂g/∂wᵢ = (μᵢ − r) − (Σw)ᵢ = (μᵢ − r) − Cov(Rᵢ, R_p).
  Adding to stream i, financed from cash, raises growth if and only if its
  excess mean beats its covariance with the portfolio, Cov(Rᵢ, R_p) =
  β_{i|p} σ_p².
- **Hedges.** If β_{i|p} < 0, the hurdle is negative. A hedge with a
  negative excess mean still adds growth as long as its cost satisfies
  |μᵢ − r| < |β_{i|p}| σ_p². Example: at σ_p = 16%, a stream with
  β_{i|p} = −1 can cost up to 2.6% a year.
- **One stream h on a fixed core b.** Growth is quadratic in the stream's
  weight h:

      h* = (μ_h − r − σ_hb)/σ_h²,    Δg* = ½ (μ_h − r − σ_hb)²/σ_h².

  For an orthogonal stream (σ_hb = 0), h* = (μ_h − r)/σ_h² and
  Δg* = ½ SR_h². With mutually orthogonal streams, each sized at its own
  optimum, the gains add: g* = r + ½ Σᵢ SRᵢ².
- **Your thesis in one line:** growth is bought with Sharpe ratio, and
  orthogonality is what makes the purchases add up.
- **Fractional sizing.** Scaling the optimum by f yields f(2 − f) of the
  maximum growth at f² of the variance. Half size keeps 75% of the growth
  for 25% of the variance.
- **Noise.** SE(μ̂) ≈ σ/√T, so SE(h*) ≈ 1/(σ_h√T), the same as D39's
  Kelly error. A year of data says little about h*, so the page shows every
  growth number ± SE.
- **Hedge ratio.** Minimizing Var(R_b + h R_h) gives
  h = −Cov(R_b, R_h)/Var(R_h): minus the slope from regressing the core on
  the hedge (W2).

### Regressions (W2)

The model is y = Xb + e on daily excess returns (cash at r/252), with
b̂ = (X′X)⁻¹X′y.
- **Classical:** V = s²(X′X)⁻¹.
- **Newey–West HAC:** V = (X′X)⁻¹ S (X′X)⁻¹, where
  - S = Γ₀ + Σ_{ℓ=1}^{L} (1 − ℓ/(L+1)) (Γ_ℓ + Γ_ℓ′)
  - Γ_ℓ = Σₜ eₜ e_{t−ℓ} xₜ x′_{t−ℓ}
  - L = 0 gives White's estimator.
  - The Bartlett weights keep S positive semidefinite.
  - The default lag is L = ⌊4(T/100)^{2/9}⌋ (Newey–West 1994), shown on the
    page.
- **Why HAC:** daily returns cluster in volatility (GARCH), so classical
  SEs come out too small. The synthetic test demonstrates it.
- **Reported:** α annualized ×252, R², and residual vol ×√252.

### DCF (W4): your engine, checked

    Rev_t  = Rev_{t−1} (1 + g_t)
    UFCF_t = m_t Rev_t (1 − τ_t) + d_t Rev_t − c_t Rev_t − n_t (Rev_t − Rev_{t−1})
    EV     = Σ_{t=1..N} UFCF_t (1+W)^−t  +  TV (1+W)^−N
    TV     = UFCF_N (1 + g_T)/(W − g_T),   W > g_T
    value per share = (EV − debt + cash) / diluted shares

**(a) Gordon identity: a new closed-form test**
- With constant ratios and g_t = g_T = g, UFCF_t = UFCF₁(1+g)^{t−1}.
- Let ρ = (1+g)/(1+W). The explicit sum is UFCF₁/(W−g)·(1 − ρ^N), and the
  discounted TV is UFCF₁/(W−g)·ρ^N.
- So EV = UFCF₁/(W − g) for every N. Your engine matches it exactly at
  N = 1, 5 and 10.

**(b) Reverse DCF can have two answers**
- With flat growth g and cash margin k = m(1−τ) + d − c, the cash flow is
  UFCF_t = Rev₀(1+g)^{t−1}[k + g(k − n)].
- If k ≥ n ≥ 0, every term and the TV are nondecreasing in g (for
  g > −1). There's one root, and bisection finds it.
- If n > k, value can rise and then fall. Example: m = 20%, τ = 21%,
  d = 4%, c = 5% (so k = 14.8%) and n = 60%.
  - Value peaks near g = 5%.
  - A $18 price has two implied growth rates, −14.9% and +15.9%.
  - Your `implied_growth` returns "out of range", because the endpoints
    don't bracket a root.
- Implied WACC is unique whenever every UFCF_t > 0.
- The port states the condition, scans a grid for sign changes, and
  returns every root.

**(c) Terminal cash flow**
- The TV uses UFCF_N(1+g_T), which still carries year N's working-capital
  build at growth g_N.
- The steady-state year N+1 cash flow is Rev_N(1+g_T)k − n g_T Rev_N. The
  two agree only if g_N = g_T.
- At 15% explicit growth and g_T = 2.5%, the steady-state flow is 7.9%
  higher, and the TV is 78% of EV.
- Choice: keep yours (conservative; the golden value holds) or normalize
  (a new golden value).

**(d) Valuation date**
- Cash flows are discounted to the last fiscal year end, but the price is
  today's.
- τ years later, EV_today ≈ EV_FYE (1+W)^τ, less cash paid out in between.
  Thirteen months after the year end at W = 9%, that's +9.8%, about the
  size of a typical margin of safety.
- Choice: roll forward to today, or show the as-of date beside the margin.

**(e) Net debt**
- Net debt nets strict cash only. Marketable securities aren't netted
  (your choice).
- For cash-rich filers the difference is large, so the page labels it and
  offers a toggle.

## Doc changes on approval

- **ROADMAP.md:**
  - a new phase, "The personal workstation (v1.1–v1.3)", with W0–W7
  - the DCF bullet leaves "After v1.0"
  - P's deferral list loses IBKR positions, aggregate greeks and growth
    sizing
  - paper trading and execution stay where they are
- **DECISIONS.md:**
  - New entries from D43, each written when its milestone lands:
    - phase scope: D12 reaffirmed, execution excluded
    - regression conventions
    - growth and stream conventions
    - the DCF port: conventions (b)–(e) and the dropped dependencies
    - the SEC provider and `providers/`
    - versioned rule sets and the review journal
  - Amended:
    - D34: fundamentals becomes the fifth port, and SEC the first online
      provider, terms checked
    - D39: a shorter deferral list
- **AGENTS.md, ARCHITECTURE.md, `tests/test_layering.py`:** the `providers/`
  package and its rule.
- **CURRENT_STATE.md:** correct the "stores it in SQLite" claim, and replace
  the DCF open decision with this plan.

## Decisions for you

1. Approve the phase and its order, or put the DCF track first.
2. Drop edgartools, yfinance and openpyxl (recommended), or keep edgartools
   as an optional extra.
3. Schedule W0's `REGIME.md` session with you to fill in the numbers.
4. Do W1 now, or once the IBKR account exists.
