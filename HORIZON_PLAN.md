# HORIZON_PLAN.md — what the write-up promises, mapped and planned

Proposal, 2026-10-25. Planning only: no app code changes, nothing
committed. It doesn't reorder or replace [LENS_PLAN.md](LENS_PLAN.md),
[TARGET_PLAN.md](TARGET_PLAN.md) or [EXECUTION_PLAN.md](EXECUTION_PLAN.md).
Each milestone needs your approval before it starts, as with those plans,
and **do not build ahead** (AGENTS.md hard rule 5) still holds.

## Why this exists

Your public QuantHub write-up (the portfolio site's
`frontend/src/content/blog/quanthub.md`) describes where the app is
going. Some of that is already planned here and some isn't planned
anywhere. This file maps each promise to its status and proposes tracks
for the gaps, so the write-up and the repo tell the same story.

## The promises, and where each stands

| # | The write-up says | Today | Already planned in | This plan |
|---|---|---|---|---|
| 1 | Deploy real algorithms through trading APIs | No order code (hard rule 1, D12) | EXECUTION_PLAN E0–E6, awaiting your approval | Nothing new |
| 2 | REST endpoints on your home server | Desktop only. Flask/FastAPI and cloud infra are banned (AGENTS.md). ROADMAP: "possible web deployment … out of scope until the owner raises it" | — | **Track H** |
| 3 | Graduate-level portfolio optimization | Kelly ± SE, the minimum-variance hedge per sleeve, `scale_to_risk`. The optimizers, frontier, Ledoit–Wolf and rebalance ticket were deferred (D39) | Half of LENS_PLAN's literature queue (Black–Litterman, Ledoit–Wolf, risk parity) | **Track P2** |
| 4 | How the macro environment ties in | No macro data | FRED's T-bill for RF (W2b). VIX and the term spread "later" (LENS data track). Shiller CAPE (TARGET T1) | **Track M** |
| 5 | Exotic derivative pricing | European Black–Scholes–Merton and Monte Carlo. American exercise is a documented limit (D30, `models/carry.py`, `models/vol_surface.py`) | — | **Track X** |
| 6 | Models run on your portfolio | The Portfolio page | LENS_PLAN L (the `portfolio` source) | Nothing new |
| 7 | Useful whatever the trader's stack | IBKR, CSV bars, Fidelity and generic positions (D34) | Ken French, FRED and SEC providers | **Track S** |
| 8 | All of the quant finance literature | The Papers catalog | LENS_PLAN's literature track | Nothing new |
| 9 | Open source | MIT-licensed since 2026-10-25 | — | ~~Decision 0~~ Done |

Rows 1, 6 and 8 need nothing here: approving the existing plans covers
them. Row 9 is done (Decision 0). The rest of this file is rows 2–5 and
7.

## Fit with the hard rules

- **X, P2, M and S** are read-only and need no new dependencies: numpy,
  pandas and the stdlib. Each new online provider gets a terms check
  first (D34). They live in `providers/`, which the layering test already
  limits to `data/` plus the stdlib.
- **H conflicts with AGENTS.md's ban list** (Flask/FastAPI, cloud infra,
  Docker) and with ROADMAP's "out of scope" line. Like EXECUTION_PLAN's
  E0, it starts with a decision entry, and nothing changes until you
  approve it. The recommended design (below) needs no banned dependency.
- **Execution** is EXECUTION_PLAN's business. Nothing here touches
  orders.

## ~~Decision 0. The license~~

**Done 2026-10-25: MIT** (`96c163f` on `main`).
- A `LICENSE` file (MIT, © 2026 Adam Shebani) and a License section at
  the end of the README. GitHub detects the repository as MIT.
- **No `license` field in `pyproject.toml`.**
  - The current SPDX form (`license = "MIT"`) needs setuptools ≥ 77, and
    the build pins `setuptools>=68`. The older table form is deprecated.
  - setuptools includes `LICENSE*` files in builds by default, so
    nothing is lost.
  - Revisit if the build pin is ever raised.
- **The write-up:** the post's "Try it" section now says open source
  under MIT.
- **The checks held:**
  - `plotly.min.js` comes from the installed plotly package (MIT) and
    isn't vendored into the repository.
  - The seeded paper references are metadata only.
  - `REGIME.md` values stay private (D49).

## Track X. Derivatives beyond the vanilla European

**Why this comes first among the new tracks.** SPY's options are
American. Today the Options Lab prices them as European, and the
Volatility Lab fits only out-of-the-money quotes with early exercise as a
documented limit (D30). Fixing that limit is the most decision-relevant
"exotic", and it improves pages that already exist.

### X1. American exercise (`models/lattice.py`, name settled at X1)

**The model: Cox–Ross–Rubinstein (1979).** With n steps, Δt = T/n and a
continuous dividend yield q:

    u = e^{σ√Δt},  d = 1/u,  p = (e^{(r−q)Δt} − d) / (u − d)

Terminal payoffs are rolled back with early exercise at every node:

    V = max( intrinsic, e^{−rΔt} (p V_up + (1 − p) V_down) )

- **Convergence:** error is O(1/n) and oscillates. The binomial
  Black–Scholes refinement (Broadie–Detemple 1996) replaces the last step
  with the closed form and Richardson-extrapolates two step counts. That
  gets a cent-accurate SPY price in a few hundred steps.
- **The docstring derives:**
  - p from risk-neutral drift matching
  - the no-arbitrage condition d < e^{(r−q)Δt} < u, which validates the
    inputs
  - why an American call with q = 0 is never exercised early

**Tests:**
- The European lattice converges to `black_scholes.price`, with error
  shrinking like 1/n.
- An American call with q = 0 equals the European call.
- An American put is at least the European put and at least intrinsic.
- The early-exercise boundary is monotone in time to expiry.
- Vectorized prices equal a scalar reference implementation.

**Then wire it in:**
- **Implied vol for American quotes.** A bracketed solver like
  `implied_volatility`'s, valid because the American price is monotone
  in σ.
- **Options Lab:** an Exercise toggle (European / American) on the
  underlying card. The default for SPY is your call.
- **Volatility Lab:** an option to de-Americanize quotes, inverting each
  one through the lattice to a European-equivalent IV before the SVI fit.
  The fit stays European, so its arbitrage checks stay valid.

### X2. Discrete dividends

- **The escrowed-dividend model:** price on S − PV(known dividends), not
  a continuous q.
- **Dividend amounts are already in the store.** The ratio of the
  adjusted to the traded series (F3, D38) steps on each ex-date, and each
  step gives that dividend's amount. A pure function in `data/` extracts
  them.
- **Tests:**
  - the extracted dividends reproduce a planted schedule
  - many small, evenly spaced dividends converge to the continuous-q
    price
  - a lattice ex-date lands on a node

### X3. Path-dependent payoffs, each with a closed-form check

These go in a new Options Lab tab, priced by antithetic Monte Carlo off a
flat σ or the saved surface's ATM vol. Following the GBM exemplar, every
payoff gets a closed form to test against:

| Payoff | The closed-form check |
|---|---|
| Barrier (up/down, in/out) | Reiner–Rubinstein (1991), continuous monitoring. Daily monitoring is matched with the Broadie–Glasserman–Kou (1997) shift of the barrier by e^{±0.5826 σ√Δt} |
| Asian, arithmetic average | No closed form, so the geometric Asian (Kemna–Vorst 1990) serves as both the test and a control variate that cuts the MC variance |
| Lookback, floating strike | Goldman–Sosin–Gatto (1979) |
| In–out parity | knock-in + knock-out = vanilla, exactly, path by path |

- IBKR quotes none of these on SPY, so this is a learning and research
  lab, not a market comparison. The page says so.
- Synthetic: TRUE (the closed form) against the MC estimate ± SE, as the
  existing Monte Carlo tab does.

### X4. Stochastic volatility (a candidate, your call)

- **Heston (1993):** priced through its characteristic function (Lewis
  2001 single-integral form) with numpy quadrature, and calibrated to a
  saved chain with `models/optimize.nelder_mead`.
- **The question it answers:** does a dynamic model fit SPY's smile as
  well as SVI's static one? The page compares the two.
- **Size:** larger than X1–X3. Decide after X3.

## Track P2. Portfolio construction (the deferred P1 items)

**What exists:**
- `models/portfolio.py`: the risk report and `scale_to_risk`
- `models/growth.py`: Kelly ± SE and the minimum-variance hedge per
  sleeve
- the synthetic generator `correlated_returns`

**What D39 deferred:** the optimizers, the frontier, Ledoit–Wolf, long-only
allocation and the rebalance ticket. This track builds them, and with
them the construction half of LENS_PLAN's literature queue.
Volatility-managed portfolios, time-series momentum and betting against
beta are strategies, so they stay in Research.

**The theme, from the write-up:** μ is the input you can measure worst
(its SE is about σ/√T). Every allocation shows how far its weights move
under that uncertainty, the same way Kelly already shows its SE.

### P2a. Covariance estimation

- **Sample covariance, plus Ledoit–Wolf shrinkage** toward a
  constant-correlation target (Ledoit–Wolf 2004, "Honey, I shrunk…"). The
  optimal intensity has a closed form, which the docstring derives.
- **Tests:**
  - the intensity lies in [0, 1]
  - it goes to 0 as T/N grows for a well-conditioned truth
  - on synthetic correlated returns, the out-of-sample variance of the
    minimum-variance portfolio is lower with shrinkage than without

### P2b. Unconstrained allocations

- **Minimum variance:** w ∝ Σ⁻¹1.
- **Tangency:** w ∝ Σ⁻¹(μ − r), with the two-fund efficient frontier
  between the two.
- **Estimation error, shown:**
  - the weights' sampling distribution, by a block bootstrap of the
    return history, since daily returns are dependent
  - shown as bands on the weights and as a cloud of resampled frontiers
- **Tests:**
  - on a planted μ and Σ, the closed forms match a brute-force grid for
    N = 3
  - the frontier's minimum is the minimum-variance portfolio

### P2c. Long-only and budget constraints (no SciPy)

- **Method:** projected gradient descent, projecting onto the simplex
  each step (Duchi et al. 2008: sort, then threshold, in O(n log n)).
- **Why not an active-set QP:** it's more code to own, and long-only
  minimum variance is a smooth convex problem, which the projected
  method solves reliably at this size.
- **The docstring derives:** the projection, and the step size from Σ's
  largest eigenvalue.
- **Tests:**
  - the projection's KKT conditions
  - agreement with a brute-force simplex grid for N = 3
  - an interior optimum reproduces P2b exactly

### P2d. Risk parity

- **Equal risk contributions:** wᵢ(Σw)ᵢ is the same for every i.
- **Solver:** Spinu's (2013) convex reformulation, solved by cyclical
  coordinate descent (Griveau-Billion, Richard and Roncalli 2013), each
  step a scalar quadratic with a closed-form root.
- **Tests:**
  - the contributions are equal to 1e-10
  - a diagonal Σ gives inverse-volatility weights

### P2e. Black–Litterman

- **Equilibrium returns:** π = δ Σ w_mkt.
- **Views:** P μ = Q + ε, with ε ~ N(0, Ω).
- **The posterior:**

      μ_BL = [(τΣ)⁻¹ + P′Ω⁻¹P]⁻¹ [(τΣ)⁻¹π + P′Ω⁻¹Q]

- **On the page:** views are typed in. Ω is chosen with He–Litterman's
  (1999) default, derived in the docstring.
- **Tests:**
  - no views returns π
  - a view with Ω → 0 is met exactly
  - the posterior mean lies between the prior and the view

### P2f. The rebalance ticket (read-only)

- Targets go to shares, then to orders, as a printed ticket you place by
  hand.
- **One module with EXECUTION_PLAN:** its `models/rebalance.py`, with
  the no-trade band and rounding math. Whichever lands first builds it,
  and the other reuses it.

**The page.** A Construction card on Portfolio, or its own page, is your
call. It reads the same `RiskReport` inputs and, after LENS L, the same
account selector.

## Track M. The macro environment

**Scope:** published market and economic series, their relationships with
returns and regimes, measured. No forecasts of the economy, and no
narrative.

**What's already planned:**
- FRED's T-bill rate (DTB3) for RF (W2b)
- VIX and the term spread "later" (LENS_PLAN's data track)
- Shiller's CAPE and the excess CAPE yield (TARGET_PLAN T1)

### M1. The FRED provider, extended

W2b builds the provider. M1 adds the series:

| Series | What | Frequency |
|---|---|---|
| T10Y3M | 10-year minus 3-month Treasury | daily |
| DGS1MO … DGS30 | the Treasury curve | daily |
| BAA10Y | Baa corporate minus 10-year | daily |
| VIXCLS | VIX close | daily |
| CPIAUCSL, UNRATE, INDPRO | inflation, unemployment, industrial production | monthly |

**Three cautions, each in the docstring:**
1. **Terms.** FRED marks some series as third-party copyrighted (VIXCLS
   is Cboe's), so the terms check is per series. The fetch uses FRED's
   keyless CSV download through stdlib `urllib`; that endpoint and its
   terms are confirmed at M1.
2. **Revisions.** Monthly data gets revised, and a backtest on today's
   vintage uses numbers nobody had at the time. ALFRED keeps the
   vintages: store the release date with each value and serve
   point-in-time.
3. **Publication lag.** A March CPI print lands in April. Every series
   gets a "known as of" date, and research may only read values known by
   the decision date. The look-ahead test is the same idea as D41's
   (scrambling the future can't change the past).

**Storage:** a macro series table, under a new schema version with D36's
backup.

### M2. A Macro view

- current levels and their percentile in the stored history
- the Treasury curve today, against one month and one year ago
- each series' chart, with its source, release date and vintage

### M3. Macro and returns, tested honestly

- **Predictive regressions** of forward market returns on the term
  spread, the credit spread and the excess CAPE yield:
  - HAC standard errors for overlapping horizons (Hodrick 1992)
  - the Stambaugh (1999) small-sample bias, stated
  - the out-of-sample R² against the historical mean (Campbell–Thompson
    2008)
- **The point is evidence, not a signal.** Goyal–Welch (2008) found most
  such predictors fail out of sample. The page reports that finding as
  readily as a success.
- **Optional:** macro covariates in the Markov transition probabilities
  (time-varying transition probabilities, Diebold–Lee–Weinbach 1994),
  extending `models/markov.py` with the same synthetic recovery tests.

**Order:** after W2b, which builds the FRED provider, and T1, which
brings in Shiller's data.

## Track S. More of the trader's stack (D34, continued)

- **S1. More positions presets.**
  - Schwab, Vanguard and E\*TRADE positions exports, each recognized by
    its header like Fidelity's.
  - Each needs a real export, anonymized and recorded as a test fixture:
    from you, or from users who open an issue.
  - No preset ships without a fixture.
- **S2. Transaction history.**
  - An activity-history importer, Fidelity first, giving the account's
    real history.
  - LENS L's caveat names this as the fix for "today's weights applied
    backwards".
  - It also feeds EXECUTION_PLAN's reconciliation if you trade by hand
    alongside an algo.
- **S3. A free online daily-bars provider** (D34's open candidate).
  - Stooq's CSVs already parse with the generic layout.
  - Keyed providers (Tiingo, Alpha Vantage) need a key in
    `settings.toml`.
  - The terms check decides which, if any, ship.
- **S4. Option chains without IBKR.** Only if a provider's terms allow
  storing quotes. Until then, users without IBKR keep the synthetic chain
  (as now).

## Track H. QuantHub over HTTP, on your home server

**The conflict, first:**
- AGENTS.md bans Flask/FastAPI, cloud infra and Docker.
- ROADMAP puts web deployment out of scope until you raise it. The
  write-up raises it.
- H0 is that decision. Nothing below is built before it.

**Why the architecture is ready:**
- `services/` never imports the GUI, and `models/` and `data/` are pure.
- So a second front end is a second composition root beside `app.py`,
  and nothing underneath changes.

**The recommended design: stdlib only.**
- **Server:** `http.server.ThreadingHTTPServer` plus `json`, in a new
  `server/` package. The layering test lets it import `services/`,
  `models/` and `data/`, and never `gui/` or `broker/`.
  - It's enough for read-only JSON at personal traffic.
  - It needs no banned dependency.
  - Revisit FastAPI only if this stops being enough, as its own
    decision.
- **No broker on the server.** It reads the store only, which V0's
  offline reads make possible, and never connects to IBKR. That avoids
  client ID clashes, keeps the read-only posture trivially true, and
  leaves nothing to reconnect.
- **Market-data terms.** IBKR's market data is licensed for your own
  use. The server returns computations, such as fits, prices and
  statistics, on synthetic data or on inputs the caller sends. It never
  returns stored IBKR bars or quotes. H0 confirms this reading of the
  terms.
- **Nothing private.** Positions, `REGIME.md`, the journal and saved
  valuations stay off the server.
- **Limits:**
  - caps on request size and computation: Monte Carlo paths, lattice
    steps, history length
  - per-client rate limits
  - a timeout on every request
- **Exposure:**
  - bound to localhost or the LAN by default
  - making it public (a tunnel, port forwarding) is your infrastructure
    choice, named in H0
- **Threads:**
  - The GUI's TaskRunner and pump don't apply here; each request runs on
    its server thread.
  - SQLite gets a connection per thread, read-only. H1 checks how
    `storage/` opens connections.

**Milestones:**
- **H0. The decision.**
  - a D-entry amending AGENTS.md's ban list and ROADMAP's line
  - the endpoint list
  - the terms reading
- **H1. The server.**
  - `/health`, a Black–Scholes price and greeks endpoint, and a GBM
    simulation endpoint
  - JSON shapes derived from the `data/` types
  - tests with `urllib` against a server on port 0
  - layering rules for `server/`
- **H2. More endpoints:** the lattice (X1), the GARCH fit on
  caller-supplied returns, and the synthetic SVI world.
- **H3. Running it.** A Windows Task Scheduler entry or a service on
  your home machine, its logs and its settings section.
- **Optional:** the portfolio website calls it for a live demo, which
  needs CORS for that one origin.

## Suggested order

This doesn't override the existing plans' queues: LENS (V1, the W2
extras, L, W2b, O), TARGET (T1–T4) and EXECUTION (E0–E6), each still
awaiting your approval.

1. ~~**Decision 0 (the license):** any time; no code.~~ Done
   2026-10-25 (MIT).
2. **X1 (American exercise):** early. It's small, self-contained, and
   fixes a documented limit in existing pages. It touches different files
   from V1, so the two can run side by side.
3. **P2a–P2c:** after L, so the portfolio lens and its covariances exist.
   Then P2d–P2e. P2f shares `models/rebalance.py` with E2/E4.
4. **M1–M3:** after W2b and T1.
5. **S1–S2:** whenever a real export exists. S3 and S4 follow a terms
   check.
6. **X2–X3:** after X1. X4 is your call after X3.
7. **H:** last, and only after H0.

## Decisions for you

1. ~~**The license:** MIT, Apache-2.0, GPL-3.0, or keep all rights
   reserved and change the write-up's wording.~~ Decided: MIT
   (2026-10-25).
2. **X1's default exercise style for SPY** in the Options Lab, and whether
   the Volatility Lab de-Americanizes by default.
3. **Is X4 (Heston) in scope**, or is the track done at X3?
4. **Where construction lives:** a card on Portfolio, or its own page.
5. **Track M's series list**, and whether M3's time-varying Markov
   extension is in.
6. **Which brokers S1 targets,** and whether you can supply the exports.
7. **Track H:**
   - whether to lift the ban at all
   - the stdlib-server recommendation
   - the endpoint list
   - whether the server is ever public

## Doc changes on approval

- **ROADMAP.md:**
  - the approved tracks join the phase list
  - "Possible web deployment" moves from "After v1.0" into Track H, if
    approved
- **DECISIONS.md:**
  - an entry per approved track, numbered after LENS_PLAN's reserved
    D51 and whatever has landed by then
  - entries for the lattice, the shrinkage estimator, the long-only
    method, the macro point-in-time rule and the server, each written as
    its milestone lands
- **AGENTS.md:**
  - the ban list and the `server/` layering rule, if H is approved
  - the `LICENSE` note under the repo layout
- **CURRENT_STATE.md:** American exercise stays listed as a known limit
  until X1 lands.
- **README.md:** each track's user-facing section as it lands. (The
  License section landed with Decision 0.)
