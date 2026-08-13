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

## Near-term candidates (order decided with the owner)

- **IBKR shakedown** — first live test against paper TWS (port 7497): connect,
  pull SPY history into the dashboard, snapshot quotes; fix whatever the old
  9.81 ibapi build surfaces. Small, high-value, de-risks everything broker-shaped.
- **First real model module** (creates `models/`, per D6): likely **GARCH(1,1)**
  (fits the synthetic-validation story: simulate → fit → recover parameters)
  or **Kalman filtering** (local-level/trend on prices). Full math derivation
  first, following the GBM exemplar conventions.
- **Synthetic-data expansion** — mean-reverting (OU), correlated assets,
  regime-switching generators. These feed model validation before real data.

## Later (unordered)

- Options Lab: chains via IBKR, Black-Scholes pricing, greeks.
- Implied-volatility estimation; smiles and surfaces (needs options data).
- Markov regime models over returns/vol.
- Portfolio analytics: CAGR/geometric-growth optimization, Kelly-style
  sizing, risk metrics beyond drawdown.
- Strategy research + backtesting engine (consumes the same `PriceHistory`
  and future model outputs; no GUI coupling).
- Caching/local persistence of market data (a `CachedSource` behind the same
  `DataSource` protocol — this is why the protocol exists).
- Paper trading; execution only after that, as its own guarded design phase.
- Streaming (non-snapshot) market data via the broker's existing
  callback-confinement pattern.
- Pinned historical end-times for IBKR requests (lifts D11).
- Possible web deployment of selected research tools (explicitly out of scope
  until the owner raises it).

## Intended stack flow (architecture already accommodates this)

```
Market data → feature/model layer (Kalman, GARCH, regimes, IV, factors)
           → strategy research → portfolio construction → risk → execution
```

Every model must remain usable from the GUI, tests, scripts, notebooks, and
a future backtester alike — i.e., keep obeying the layering rules.
