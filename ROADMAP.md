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

## Current phase — Qt UI migration (set 2026-10-10, D31)

Owner direction: a modern, fast, appealing workstation UI (a target mockup
was provided); heavy dependencies accepted. Tk and Qt run side by side
(Qt is the default; `--classic` opens Tk) until parity. Each milestone is approved before the next.

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

   A GPU 3-D surface (pyqtgraph.opengl or Plotly in a QWebEngineView)
   remains a separate, later step.
6. **Q6 — Retire Tk.** Delete `gui/` (keeping `task_runner`) and `app.py`;
   make Qt the default; collapse the layering rules.

## Previous phase — synthetic research workflows (set 2026-09-27, done)

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

## Next phase — data side (after the synthetic workflows)

- Live-verify the Options Lab and Volatility Lab `ibkr` loads against TWS.
- Options chains via IBKR (read-only, D12): a `broker/` adapter emitting
  `data/options.OptionChain`, feeding the unchanged Volatility Lab
  pipeline. Then a joint SSVI (whole-surface) fit, to regularize noisy real
  chains (D30).
- Real portfolio/positions (read-only) and the real-time trading dashboard.
- **Pinned historical end-times** (lifts D11) — minimal backfill/interior-gap
  fetches; prerequisite for deep backfills without over-fetch.
- Corporate-action adjustment policy (see `DATA_SYSTEM_IMPROVEMENTS.md`)
  before real multi-year histories feed models.

## Model candidates after the current phase

- **Kalman filtering** (local-level/trend on log prices), derived and
  validated on a synthetic state-space world first.
- **Synthetic-data expansion** — mean-reverting (OU), correlated assets,
  regime-switching generators, to feed model validation before real data.

## Later (unordered)

- Markov regime models over returns/vol.
- Portfolio analytics: CAGR/geometric-growth optimization, Kelly-style
  sizing, risk metrics beyond drawdown.
- Strategy research + backtesting engine (consumes the same `PriceHistory`
  and future model outputs; no GUI coupling — the SQLite store now provides
  its reproducible data foundation).
- Frequency-aware annualization in `data/stats.py`, then expose the already-
  plumbed weekly/monthly (and eventually intraday, per D19) bar sizes.
- Paper trading; execution only after that, as its own guarded design phase.
- Streaming (non-snapshot) market data via the broker's existing
  callback-confinement pattern.
- Possible web deployment of selected research tools (explicitly out of scope
  until the owner raises it).

## Intended stack flow (architecture already accommodates this)

```
Market data → feature/model layer (Kalman, GARCH, regimes, IV, factors)
           → strategy research → portfolio construction → risk → execution
```

Every model must remain usable from the GUI, tests, scripts, notebooks, and
a future backtester alike — i.e., keep obeying the layering rules.
