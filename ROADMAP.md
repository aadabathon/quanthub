# ROADMAP.md

Long-term vision: a personal quantitative research and trading workstation —
market data, options analytics, volatility modeling, state-space and regime
models, portfolio construction, backtesting, paper trading, and (eventually,
as a separate deliberate step) algorithmic execution.

**Standing rule: do not build ahead.** Placeholders exist so the navigation
and layering don't have to change when modules land; they are not an
invitation to fill them in. Each phase below is scoped and approved with the
owner before implementation begins.

## Done

- **v0.1 — Foundation** (2026-08-13): app shell + navigation, dark theme,
  event bus + task runner concurrency, synthetic GBM data source, dashboard
  (chart + return stats), quote watchlist, read-only IBKR connectivity
  (connect/disconnect/daily history/snapshot), 68 tests incl. layering
  enforcement. See [CURRENT_STATE.md](CURRENT_STATE.md).
- **v0.2 — Synthetic laboratory (Milestone 3)** (2026-08-13): full analytics
  layer in `data/stats.py` (simple/log returns, CAGR, rolling vol, drawdown,
  moments, GBM μ̂/σ̂ estimation), `data/simulation.py` (one-call GBM
  experiments + Monte Carlo terminal stats vs closed forms), reusable chart
  compositions (path/drawdown/distribution/rolling-vol overview; MC fan +
  terminal distribution), dashboard rebuilt as an interactive GBM lab with
  TRUE-vs-ESTIMATE diagnostics, card-based control system, tabbed charts.
  114 tests. Decisions D15–D16.
- **v0.3 — IBKR ingestion + persistence (Milestone 4)** (2026-08-14):
  IBKR → validation (`data/bars.py`) → SQLite (`storage/bar_store.py`:
  instruments/bars/coverage, DB-enforced uniqueness) → coverage-based
  incremental sync (`services/sync.py`, FULL_HIT/PARTIAL_HIT/MISS logged) →
  `CachedSource` behind the unchanged `DataSource` protocol. **First live
  TWS contact passed** (port 7496, read-only): SPY daily bars fetched,
  persisted, and rendered through the existing dashboard; relaunch serves
  from disk. Volume lots→shares; typed IBKR errors; 210 tests. D17–D21.

- **v0.4 — Product architecture + quote provenance (Milestone 6, reduced)**
  (2026-08-14): Dashboard/Markets/Simulator responsibility split (D24);
  `MarketService.active_source` deleted — sources are explicit per call
  (D23); IBKR quote path fixed live: entitlement advisories no longer fail
  requests, delayed ticks + `marketDataType` normalize into
  `Quote.mode: MarketDataMode`, unavailable = typed error with the TWS
  reason shown per row (D22); nav sections, collapsible log. 223 tests.
  Deferred consciously: dashboard widget framework/persistence,
  SettingsStore, watchlist persistence, Markets historical detail pane.
- **GARCH domain foundation** (2026-09-20): stationary GARCH(1,1)
  simulation, Gaussian-QMLE fitting, conditional-variance diagnostics, and
  synthetic parameter-recovery tests in the new pure `models/` layer. The
  interactive Synthetic/SQLite research surface is now available as GARCH Lab.
- **Model-layer housekeeping** (2026-09-27): Black-Scholes moved to pure
  `models/black_scholes.py` with no-arbitrage bounds and a bracketed-Newton
  IV solver (D26); shared `models/optimize.py` Nelder–Mead, fixing a
  degenerate-simplex bug that confined GARCH fits to a line (D27);
  snake_case module names; bytecode untracked; doc paths corrected. 410
  tests.
- **Milestone A — Options Lab** (2026-09-27): Black-Scholes-Merton with
  dividend yield (vectorized, typed `Greeks`), IV input, multi-leg
  strategies with exact expiry analysis, greek profiles, and antithetic
  Monte Carlo vs the closed form. Built on a source-agnostic `MarketInputs`
  that is filled manually, synthetically, or from IBKR/SQLite via
  `services/underlying.py` (D28/D29). 480 tests.
- **Milestone B — Volatility Lab** (2026-09-27): `OptionChain` contract;
  synthetic arbitrage-free SSVI world → noisy OTM chain → IV inversion →
  spread-weighted raw-SVI fit per expiry → butterfly/calendar diagnostics;
  smiles, term structure, and surface views; GARCH-forecast and
  realized-vol overlay from any source (D30). 529 tests.

## Done — Qt UI migration (2026-10-10, D31)

Released as **v0.5**. Owner direction: a modern, fast, appealing
workstation UI (a target mockup was provided); heavy dependencies accepted.
Tk and Qt ran side by side until parity, then Tk was retired. Each
milestone was approved before the next.

1. ~~**Q1 — Foundation + Dashboard.**~~ Done 2026-10-10. Delivered:
   - `core.py` split
   - `gui_qt/` design system, components and pyqtgraph charts
   - shell: sidebar, Ctrl+K search, status bar, log drawer
   - Dashboard rebuilt on `services/workstation.py`
2. ~~**Q2 — Markets + GARCH Lab.**~~ Done 2026-10-10. Both pages ported,
   with shared form, stats, badge, stacked-chart and histogram components.
   Markets gains a selection inspector (reason, stored-history sparkline,
   sync, open in explorer). GARCH synthetic mode overlays the true σₜ and
   adds a TRUE column.
3. ~~**Q3 — Simulator.**~~ Done 2026-10-10:
   - path / drawdown / rolling-vol stack and a Monte Carlo fan with bands
     (`data.simulation.path_percentiles`), the E[Sₜ] curve and the sample
     path highlighted
   - return and terminal-price histograms; the terminal one marks median,
     mean, E[S(T)], p5 and p95
   - TRUE | ESTIMATE table
   - one job → one state (D15)
4. ~~**Q4 — Options Lab.**~~ Done 2026-10-10:
   - underlying card with a provenance badge
   - Pricer (live recalculation, greek tiles, profiles across expiries)
   - Strategy (P&L with signed shading and breakevens, days-forward slider,
     legs editor, templates)
   - Monte Carlo (log-x convergence funnel, verdict badge, terminal
     histogram)
5. ~~**Q5 — Volatility Lab.**~~ Done 2026-10-10:
   - experiment card
   - smile small multiples, term structure with the GARCH/realized overlay,
     and a surface heatmap
   - fit-by-expiry table and checks panel

   The 3D surface followed as its own step (S3D, 2026-10-14, D32): Plotly
   in Qt WebEngine, a Heatmap / 3D toggle in the Surface view.
6. ~~**Q6 — Retire Tk.**~~ Done 2026-10-10:
   - Tk `gui/` and `app.py` deleted
   - `gui_qt/` → `gui/`, `app_qt.py` → `app.py`, `TaskRunner` →
     `tasks.py`
   - layering rules collapsed, and tkinter forbidden
   - GUI smoke tests 5× faster: logging and theming are now idempotent, and
     test windows are really destroyed

## Done — synthetic research workflows (set 2026-09-27)

Owner direction: finish the quant-research side on **synthetic processes
first** (a known true world → estimate → compare, as GBM and GARCH already
do), then turn to the data side. Each milestone is approved before the next.

1. ~~**Milestone A — Options Lab.**~~ Done (see above). Vectorized Black-Scholes over
   strikes/expiries; continuous dividend yield `q` (Merton); IV input in the
   calculator; multi-leg strategy payoff and P&L (at and before expiry);
   greek profiles vs spot and time; Monte Carlo pricing under GBM checked
   against the closed form. Tests: put-call parity with `q`, greeks vs
   finite differences, MC vs closed form within standard error.
2. ~~**Milestone B — Volatility Lab**~~ Done (see above). (Replaced the placeholder.) A synthetic
   SVI (Gatheral raw) smile per expiry plus an ATM term structure as the
   true surface → priced chain with bid/ask noise → IV inversion → SVI
   refit through `models/optimize.py` → true-vs-fitted smile, term
   structure, and surface views. Realized-vol panel from GBM/GARCH paths
   (OHLC range estimators wait for real bars, D4). Candidate tie-in: the
   GARCH variance-forecast term structure vs the ATM IV term structure.

## Done — data side, first pass (2026-10-15)

- Kalman K1/K2 and the 3D vol surface (S3D, D32) landed 2026-10-14.
- Live TWS verification of the Qt UI (2026-10-15, 9/9): connect, quotes,
  Sync, the labs' `ibkr` loads, a clean disconnect. It found and fixed
  the D11 duration bug that left leading-gap holes, and the owner's store
  was repaired.

## Current phase — the road to v1.0 (planned 2026-10-15)

Owner direction: v1.0 is the option-chain adapter, Markov regimes, Portfolio
and Research on top of what exists. After that, the work is refining and
mastering the workflows to trade with. **v1.0 stays read-only (D12).** The
app informs decisions, and trades are placed by hand in TWS. Paper trading
and execution come after v1.0, as their own guarded design phase.

How every milestone runs, as so far:
- derive the math in the module docstring, and get the owner's review
- implement it with statistical tests on a synthetic world with a known
  answer
- build the page (TRUE vs FIT where there is a truth)
- live-verify against TWS where IBKR is involved

Each milestone is approved before the next starts.

**Order: C → M → F → P → R.** Each module ends in a version bump:
- **C, option chains (v0.6):** finishes the data side the Volatility Lab
  was built for.
- **M, Markov (v0.7):** a self-contained model, and an input Research needs.
- **F, data foundations:** small, but must land before P and R put
  multi-year, multi-asset returns to work.
- **P, Portfolio (v0.8):** needs F. It supplies the position sizing R
  uses.
- **R, Research (v0.9):** comes last because it consumes everything
  (Kalman, GARCH, Markov signals; Portfolio sizing; adjusted histories).
- **v1.0:** a stabilization pass.

### Decisions (approved 2026-10-15: D33, D34)

The owner approved the recommendations:
- option quotes measured in C0; no paid regulatory snapshots
- SPY and SPX
- parity-implied forwards per expiry
- chain snapshots persisted
- both price bases stored
- daily vectorized backtester with an explicit lag

**Amended:** positions come from the owner's Fidelity CSV export first;
IBKR's read-only positions are an optional second adapter.

**Provider-agnostic (D34).** Anyone who clones the repo should find it
useful, whatever broker they use.
- Every acquired data kind is a port whose shape lives in `data/`:
  bars, quotes, option chains, positions.
- v1.0 adapters:
  - synthetic
  - file import (CSV, with presets such as Fidelity positions and a
    documented generic layout)
  - IBKR (optional, live)
- The app is fully usable with no broker connection.
- A user settings file replaces the hard-coded owner defaults.
- A free online provider is a later candidate, once its terms of use are
  checked.

### C — Option chains (v0.6)

Chains arrive through an `OptionChainSource` port (D34). IBKR is the first
adapter. A non-IBKR live chain provider is a candidate once its terms are
checked; until then, users without IBKR keep the synthetic chain.

- ~~**C0. Live spike (throwaway, against TWS 7496)**~~ Done 2026-10-15.
  Delayed option quotes work for free; findings are in D33.
  - `reqSecDefOptParams` for SPY/SPX: expiries, strikes, trading classes
  - option quote modes (live, delayed, frozen, unavailable)
  - IBKR's own model IV and greeks (`tickOptionComputation`)
  - quotes per second, and the market-data line limit (usually 100
    concurrent)
  - **Output:** findings that settle decisions 1–3.
- ~~**C1. Broker and adapter**~~ Done 2026-10-15, with the
  parity-implied carry (C1b, `models/carry.py`). Live, implied carry cut
  SPY's χ²/pt from 8–122 to 0.3–12, and SPX's from 3.6–43 to 0.0–3.8.
  - Broker side:
    - `IBKRBroker` gains read-only `get_option_params` and a batched
      `get_option_quotes`
    - batches stay under the line limit
    - each quote streams briefly and self-cancels
    - typed errors, and the D22 advisory handling
  - Adapter side: `broker/option_chain.py` turns a selection into an
    `OptionChain`.
  - Selection policy (pure, unit-tested):
    - listed expiries nearest to target day-counts (7/30/60/91/182/365,
      as the synthetic chain)
    - strikes in a moneyness band of about −2.5 to +1.5 ATM standard
      deviations
    - out-of-the-money sides, plus calls and puts at a few near-ATM
      strikes for parity
  - Tests run against a fake broker.
- ~~**C2. Volatility Lab on real chains**~~ Done 2026-10-15, live on SPY
  and SPX (about 42 s per chain).
  - The Experiment card gains a chain source: synthetic or `ibkr`.
  - Real chains have no TRUE column.
  - Chain quality stats: fetched, no bid, wide spreads, skipped expiries.
  - Per-expiry implied forwards, if decision 3 is approved.
  - Snapshot persistence, if decision 4 is approved.
  - The 3D view, checks and GARCH overlay work unchanged.
  - Live-verified.
- ~~**C3. Joint SSVI fit**~~ Done 2026-10-15 (`models/ssvi_fit.py`).
  - It wins on noisy synthetic chains.
  - Live SPY/SPX smiles lie outside the SSVI family, so it's an option and
    independent SVI stays the default (D35).
  - Possible follow-up: constrained SVI slices started from SSVI.
  - `models/svi.py` fits θ(T) per expiry plus global ρ, η.
  - No static arbitrage by construction when η(1+|ρ|) ≤ 2: that is the
    regularizer noisy real chains need (D30).
  - Validated on the synthetic world, whose truth *is* SSVI.
  - The page compares it with independent slices.
- ~~**C4. Market vol into the Options Lab**~~ Done 2026-10-15
  (`models/market_surface.py`).
  - A Flat σ / Surface toggle on the underlying card. Surface prices every
    contract from the newest saved chain: its own implied vol, and its
    expiry's parity carry.
  - Strategy legs keep their surface inputs as spot moves (sticky strike,
    stated in the docstring).
  - Provenance stays visible (D23): a SURFACE badge, the chain's source,
    as-of time and snapshot, and an extrapolation flag on any vol not
    backed by quotes.

### M — Markov regimes (v0.7)

- ~~**M1. `models/markov.py`**~~ Done 2026-10-15. As planned below, plus a
  closed-form clustering check (squared returns decay as λʰ) and regime
  forecasts α_n Pʰ.
  - Gaussian hidden Markov model on daily log returns: regime s,
    r | s ~ N(μₛ, σₛ²).
  - Algorithms:
    - scaled forward–backward
    - Viterbi path
    - Baum–Welch (EM) with multi-start
    - a variance floor against degenerate states
    - states ordered by σ, so labels are stable
    - BIC for choosing 2 or 3 regimes
  - Filtered probabilities are tradable; smoothed ones are hindsight (as
    in the Kalman Lab).
  - Expected regime duration is 1/(1 − pᵢᵢ).
  - Synthetic regime-switching generator.
  - Tests:
    - forward–backward and Viterbi vs brute-force enumeration on short
      series
    - EM never decreases the likelihood
    - parameter recovery
    - BIC picks the true K
- ~~**M2. Markov Lab page**~~ Done 2026-10-15 (`gui/pages/markov_lab.py`),
  live-verified on SPY. Also: a Filtered / Smoothed / Viterbi toggle,
  regime odds 1–20 days ahead, and the BIC of every count tried.
  - synthetic or market history
  - price shaded by filtered regime probability, plus a probability panel
  - transition matrix, per-regime stats, expected durations
  - TRUE | FIT
  - Live-verified.

### F — Data foundations (before P and R)

- ~~**F1. Settings and store safety**~~ Done 2026-10-15 (D36):
  `config.load_config`, `storage/schema.py`, the daily-only `CachedSource`.
  - A user settings file (TOML, read with stdlib `tomllib`): providers,
    IBKR host/port/client id, store path. It replaces the hard-coded owner
    defaults (D34).
  - A schema version table, and an automatic copy of the database before
    any migration (SQLite online backup API).
  - Non-daily bar sizes rejected at the `CachedSource` boundary
    (DATA_SYSTEM_IMPROVEMENTS #6, #7).
- ~~**F2. File import: the universal provider (D34)**~~ Done 2026-10-15 (D37):
  `importers/bar_csv.py`, `services/file_import.py`, Import CSV… on the
  Dashboard.
  - New infrastructure package (layering-tested: `data/` + pandas + stdlib
    only).
  - A CSV `BarSource` with a documented generic layout and column-mapping
    presets (DATA_SYSTEM_IMPROVEMENTS #5).
  - Imported bars go through the same validation and store as IBKR's, under
    their own provider name.
- ~~**F3. Adjusted prices (D33)**~~ Done 2026-10-16 (D38).
  - IBKR `ADJUSTED_LAST` fetched and stored, whole, once a day. A split
    guard repairs traded history that straddles a split.
  - A file declares its basis by column (`close`, `adj_close`).
  - The basis is named on every call, like the source (D23).
  - Tests: total return over a dividend matches IBKR's convention; a split
    after caching is caught and repaired, continuous. IBKR's TRADES is
    split-adjusted already, so the planned "gap in raw bars" test doesn't
    apply.
  - Data foundations (F) are complete.

### P — Portfolio (v0.8)

Shipped lean on 2026-10-16 (D39): positions import (Fidelity and generic),
the risk model, and the Portfolio page, risk first. Below is the full plan;
the optimizers, frontier, Ledoit–Wolf, IBKR positions and the rebalance
ticket wait for v1.1.

- **P1. `models/portfolio.py`**
  - Alignment and estimation:
    - multi-asset return alignment
    - covariance estimation: sample, and Ledoit–Wolf shrinkage
  - Allocation rules:
    - minimum variance
    - tangency
    - growth-optimal (Kelly) w* = Σ⁻¹(μ − r), with growth
      r + ½·SR²max, and fractional Kelly's growth/variance trade-off
    - long-only allocations: method chosen in the derivation, since
      there's no SciPy
  - Risk:
    - historical and Gaussian VaR and expected shortfall
    - drawdown statistics
    - risk contributions
  - The docstring covers volatility drag and why estimation error in μ
    makes full Kelly dangerous; one year of data gives μ̂ a standard error
    about the size of σ.
  - Synthetic correlated-GBM generator. Weights recovered on known μ and
    Σ; closed forms checked.
- **P2. Positions, broker-agnostic (D33, D34)**
  - A `Position` shape in `data/`: stocks, ETFs, options as `OptionLeg`s,
    and cash.
  - A positions port with two adapters:
    - CSV import, with a Fidelity preset first and the generic layout
    - IBKR read-only positions, account summary and P&L (optional)
  - Imports are snapshots stamped with their as-of date, and kept.
  - Live-verified with a real Fidelity export.
- **P3. Portfolio page**
  - holdings: imported positions, the IBKR account, or a hypothetical
    basket
  - allocation and risk (vol, beta, VaR/ES, drawdown)
  - efficient frontier and the Kelly/fractional-Kelly views
  - what-if rebalance, with a hand-placed ticket
  - aggregate option greeks across positions (the "trading dashboard"
    item)

### R — Research and backtesting (v0.9)

- **R1. `models/backtest.py` (decision 7)**
  - Positions come from information up to t and earn returns t → t+1.
  - Explicit cost model: half-spread plus commission, charged per unit
    turnover.
  - Metrics: CAGR, vol, Sharpe with its standard error, Sortino, max
    drawdown, turnover.
  - Walk-forward / out-of-sample splits.
  - Deflated Sharpe ratio for many trials.
  - Tests:
    - look-ahead: scrambling the future can't change past positions
    - cost identities
    - hand-computed cases
- **R2. Strategy library and synthetic worlds**
  - Strategies are pure `signal(history, params) → positions` functions:
    - Kalman trend / level deviation
    - GARCH vol targeting
    - Markov regime filter
    - momentum and mean reversion baselines
  - Sizing comes from Portfolio.
  - Validation worlds:
    - a world with a planted edge, which a strategy should find
    - a null random walk, where the deflated Sharpe must flag the best of
      many strategies as noise
- **R3. Research page and research log**
  - universe, strategy and parameters, run on the TaskRunner
  - equity vs benchmark, drawdown, rolling Sharpe
  - a trades table
  - an in-sample / out-of-sample split
  - a parameter-sweep heatmap with deflated Sharpe
  - every run saved to SQLite (parameters, data range, results), so
    research is reproducible

### v1.0 — stabilization

- Live-verify the end-to-end workflows as a routine:
  - sync
  - regimes
  - vol surface
  - portfolio risk
  - research checks
- Close the known limits that affect trading use.
- The clone-and-use path (D34): a README "getting started without a
  broker" covering synthetic labs, CSV imports and the settings file.
  IBKR setup becomes an optional section.
- Docs and the version bump.

### After v1.0 (unordered)

- Paper trading, then execution, as their own guarded design phase (D12).
- Option-strategy backtests on the stored chain snapshots.
- Streaming market data, and intraday bars (needs a timezone policy, D19).
- Pinned historical end-times (lifts D11).
- A CSV/Parquet `BarSource` (DATA_SYSTEM_IMPROVEMENTS #5).
- Frequency-aware annualization.
- Parallel universe backfill (revisits D9).
- Possible web deployment of selected tools (out of scope until the owner
  raises it).
- After the roadmap: the owner's DCF modeler port (SEC EDGAR + SQLite).

## Intended stack flow (architecture already accommodates this)

```
Market data → feature/model layer (Kalman, GARCH, regimes, IV, factors)
           → strategy research → portfolio construction → risk → execution
```

Every model must remain usable from the GUI, tests, scripts, notebooks, and
a future backtester alike — i.e., keep obeying the layering rules.
