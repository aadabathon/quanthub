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


## Near-term candidates (order decided with the owner)

- **Kalman filtering** (local-level/trend on log prices): the next distinct
  model candidate. Derive and validate the filter/smoother on a controlled
  synthetic state-space world before exposing it to persisted history.
- **Synthetic-data expansion** — mean-reverting (OU), correlated assets,
  regime-switching generators. These feed model validation before real data.
- **Pinned historical end-times** (lifts D11) — makes backfill/interior-gap
  fetches minimal instead of gap-to-now; prerequisite for deep backfills
  without over-fetch.

## Later (unordered)

- Options Lab: Black-Scholes pricing + greeks done (manual-entry only, see
  CURRENT_STATE.md); chains via IBKR still open (no free chain-data source
  wired in yet).
- Implied-volatility estimation; smiles and surfaces (needs options data).
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
