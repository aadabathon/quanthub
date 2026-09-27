# ARCHITECTURE.md

How Quant Hub is put together. For *why*, see [DECISIONS.md](DECISIONS.md).

## Layers

Imports point strictly downward. `tests/test_layering.py` scans source files
and fails the build on violations.

```
gui/            Tkinter/ttk presentation. Renders state, forwards user intent.
                No math, no data munging, no broker calls beyond interfaces.
services/       Application facade + caching. May import data/, storage/, and
                broker interfaces (broker/base.py, broker/types.py). Never
                tkinter, never ibapi.
data/           Pure domain: types, generators, statistics, validation,
                interval math. NumPy/pandas only. Imports nothing from any
                other quant_hub package.
models/         Pure quantitative models. May consume data/ contracts; never
                GUI, services, broker, or storage.

broker/         Infrastructure. The ONLY package allowed to import ibapi.
                Never storage.
storage/        SQLite persistence (stdlib sqlite3). Knows data/ only —
                no broker, no GUI, no services.
visualization/  Matplotlib figure construction. No tkinter (GUI embeds).
```

Cross-cutting modules:

- `config.py` — one frozen `AppConfig` dataclass (IBKR host/port, timeouts,
  defaults). No config files yet, deliberately.
- `events.py` — `EventBus` + `LogEvent` + `EventBusLogHandler`.
- `app.py` — the composition root: the only module that knows all layers.
  Every class takes dependencies as constructor arguments; `app.py` is the
  single place that wires them. That is the entire DI story.
  `create_app()` builds everything without entering the mainloop (used by
  smoke tests); `main()` calls it and runs `mainloop()`.

## File map

```
quant_hub/
├── app.py                     composition root, logging setup, view registry
├── config.py                  AppConfig
├── events.py                  EventBus (thread-safe), LogEvent, log handler
├── gui/
│   ├── main_window.py         shell: header/nav (titled sections)/container/
│   │                          log + THE PUMP
│   ├── theme.py               all colors, fonts, ttk styles (dark terminal)
│   ├── task_runner.py         worker thread + result queue (tkinter-free)
│   ├── views/base.py          View base class (on_show/on_hide lifecycle)
│   ├── views/dashboard.py     workstation overview: connection + store cards
│   ├── views/simulator.py     the GBM/Monte Carlo laboratory (synthetic only)
│   ├── views/markets.py       watchlist: page-owned source, provenance
│   │                          columns, per-row unavailable reasons, history sync
│   ├── views/placeholder.py   ONE class for all 7 future modules
│   └── widgets/               log_panel.py (collapsible), status_bar.py
├── broker/
│   ├── base.py                Broker protocol, ConnectionState, ConnectionEvent
│   ├── types.py               ContractSpec, HistoricalBar, BrokerError tree
│   │                          (incl. InvalidContract/NoHistoricalData/Pacing)
│   ├── ibkr.py                IBKRBroker: callbacks → blocking calls;
│   │                          bar-size whitelist, error-code classification
│   └── market_source.py       IBKRSource: Broker → DataSource + BarSource
│                              adapter (volume lots→shares per D21)
├── data/
│   ├── types.py               PriceHistory (validated), Quote
│   ├── sources.py             DataSource + BarSource protocols, SyntheticSource
│   ├── bars.py                canonical OHLCV validation for ACQUIRED data
│   │                          (structural raise vs row-drop + report)
│   ├── ranges.py              DateRange (closed session-date intervals),
│   │                          merge/subtract — coverage arithmetic
│   ├── synthetic.py           GBM generator (the quant-module exemplar)
│   ├── simulation.py          GBMSimulationResult, run_gbm_simulation,
│   │                          monte_carlo_summary (closed forms documented)
│   └── stats.py               full analytics layer: returns/CAGR/rolling vol/
│                              drawdown/moments, GBM estimation (μ̂, σ̂),
│                              ReturnStats + SeriesAnalysis bundles
├── models/
│   ├── garch.py               stationary GARCH(1,1): simulation, Gaussian
│                              QMLE fit, conditional variance diagnostics

├── services/
│   ├── market_service.py      source registry + get_history/get_quote facade
│   ├── sync.py                SyncStatus/SyncPlan: FULL_HIT/PARTIAL_HIT/MISS
│   └── cached_source.py       CachedSource: BarSource + BarStore → DataSource
├── storage/bar_store.py       SQLite schema (instruments/bars/coverage),
│                              upsert with counts, range queries, coverage
└── visualization/charts.py    ChartStyle + figure-level compositions:
                               plot_price_history, plot_lab_overview (price/
                               drawdown/distribution/rolling vol),
                               plot_monte_carlo (fan + terminal dist)
```

## Concurrency model

Threads that exist: **GUI/main** (Tkinter mainloop), **task-worker** (one,
in `TaskRunner`), **ibkr-network** (created per connect, runs `EClient.run()`).

Two seams confine all threading:

1. **Inside `broker/ibkr.py`** — ibapi is callback-based on the network
   thread. Each request registers a `_Pending` slot (a `threading.Event` +
   buffers) keyed by request id; the caller blocks on `event.wait(timeout)`;
   callbacks fill the buffer and set the event. Callers therefore see plain
   blocking methods that raise `BrokerError` subclasses. Nothing
   callback-shaped escapes this file.
2. **`gui/task_runner.py` + the pump** — the GUI never calls blocking methods
   directly; it calls `tasks.submit(fn, on_done, on_error)`. The worker runs
   `fn`, queues the result, and `MainWindow._pump` (a `root.after` loop, ~50ms)
   invokes the callback **on the GUI thread**. The same pump drains the
   EventBus, which is how broker state changes (`ConnectionEvent`) and log
   records (`LogEvent`) reach widgets. Push events = bus; request/response =
   TaskRunner.

Why not asyncio: Tkinter has no native asyncio integration and the official
ibapi is thread-based. Threads + queues is the smallest correct model.

## Page responsibilities and data flow (M6, D23/D24)

```
Dashboard  = workstation overview (connection card, persisted-store summary)
Markets    = real market observations: explicit source, provenance columns
Simulator  = controlled stochastic experiments (the GBM/Monte Carlo lab)
GARCH Lab  = conditional-volatility fit: synthetic GARCH or explicit source history
MODELS…    = GARCH Lab plus placeholders for future specialized workflows
```

**No global source state.** ``MarketService.get_history/get_quote`` take a
required ``source`` argument; each page owns and displays its choice
(Markets' selector; Simulator is synthetic by construction). GARCH Lab names
its market source per fit; `ibkr` therefore traverses the existing
SQLite-first `CachedSource` path. Every fit runs as one TaskRunner job, so
both the fitted parameters and every chart panel describe the same returns.
The Simulator keeps the D15 invariant: one TaskRunner job → one immutable
``(sim, analysis, mc)`` triple → every chart/stat renders from it.

**Quotes carry provenance** (``Quote.mode: MarketDataMode`` — live/frozen/
delayed/delayed-frozen/synthetic/unknown; timestamp = trade time when the
source provides it). Inside ``broker/ibkr.py`` (D22): quote requests
stream briefly and self-cancel; live+delayed tick ids and the
``marketDataType`` callback are normalized; entitlement advisories
(10167/10089) never fail the request or touch connection state; no usable
price within ``quote_timeout_s`` raises ``NoMarketDataError`` with the
advisory as the reason, which Markets renders as an UNAVAILABLE row with
the reason in its detail line. Connection health and per-instrument
entitlement are therefore fully independent states.

Historical series still flow ``MarketService → CachedSource → SeriesAnalysis
→ charts`` (Markets' "Sync 1Y history" persists without charting; a richer
historical-research surface is a later milestone).

`PriceHistory` (sorted unique DatetimeIndex; `close` required, finite, > 0;
OHLCV passed through when present) is the contract that makes every consumer
source-agnostic. Validation lives there once — future models must not
re-validate defensively.

## Market-data persistence (Milestone 4)

Real (IBKR) history flows through a caching pipeline that synthetic data
deliberately bypasses (experiments are reproducible from parameters;
persistence is for acquired observations):

```
IBKRBroker ──► IBKRSource.fetch_bars (raw frame, volume lots→shares)
                  ▲ only when the sync plan says fetch
CachedSource.get_history            (services/cached_source.py, task-worker)
  1  BarStore.coverage ──► plan_sync ──► FULL_HIT | PARTIAL_HIT | MISS
  2  fetch only missing ranges  ──► validate_ohlcv_frame (data/bars.py)
  3  BarStore.insert_bars (upsert; inserted/replaced counted + logged)
  4  record coverage, capped at YESTERDAY (today's bar is provisional)
  5  BarStore.query_bars over the full window ──► PriceHistory
```

Key properties (decisions D17–D21):

- **Store**: SQLite via stdlib `sqlite3`, default `~/.quant_hub/
  market_data.db` (`AppConfig.db_path`). Three tables — `instruments`
  (identity: symbol/sec_type/exchange/currency), `bars` (PK: instrument,
  source, bar_size, ts; CHECK constraints on OHLC sanity; re-insert
  replaces), `coverage` (synced date intervals, merged on write).
  Uniqueness is database-enforced, never Python-only.
- **Coverage ≠ bar existence**: weekends/holidays make "no bar" ambiguous,
  so cache decisions come from explicit coverage intervals; the arithmetic
  is pure (`data/ranges.py`, `services/sync.py`) and every decision logs as
  full-hit / partial-hit / miss with fetch ranges and insert counts.
- **Time**: daily/weekly/monthly bars are naive exchange session dates
  stored as ISO text (lexicographic = chronological). Intraday bar sizes
  are rejected at the broker boundary until a timezone policy exists (D19),
  and the analytics annualization (252/yr) assumes daily anyway.
- **Same-day bars**: never marked covered; a request ending "now" refetches
  exactly today's bar each time, upserting the provisional value.
- **Failure**: a failed fetch fails the whole request (no silently partial
  series); `BrokerError` subclasses distinguish not-connected / invalid
  contract / no data / pacing / timeout, and the GUI shows the message.
- **Fetch semantics**: D11 stands — IBKR requests end "now" with a duration;
  the adapter slices. A leading/interior coverage gap therefore fetches
  from the gap to now; harmless, because inserts upsert and coverage merges.

## GUI conventions

- All styling lives in `gui/theme.py` (single committed dark palette,
  validated for CVD-safety/contrast on the dark surface). No hex constants in
  views. Charts get colors via `ChartStyle`, mapped from the theme once in
  `dashboard.py`.
- Status colors (good/warning/serious/critical) mean *state* — connection
  health, signed values — and are never used as chart series colors. State is
  always shown as color + text, never color alone.
- Views subclass `gui/views/base.View`, are created lazily on first
  navigation, and stay alive (state survives switching). Register new views
  in `app.py`'s `view_specs`.
- Long-lived matplotlib: one Figure per embedded canvas for the life of the
  view (the dashboard has two — Path Analysis and Monte Carlo tabs). All
  redraws go through `visualization.charts.plot_*(fig, ...)` functions that
  clear and repopulate in place — never create a new Figure/canvas per
  refresh. Chart functions render only; every plotted quantity arrives
  precomputed from `data/`.
- Spacing uses the `SPACE_*` scale from `theme.py`; control groups sit in
  `Card.TFrame` panels with `CardHeading.TLabel` captions.

## Quant-module conventions (follow the GBM exemplar)

Every future quantitative module (`data/synthetic.py` is the template):
mathematical description + discretization in the module docstring;
assumptions and units stated; inputs validated in `__post_init__`/entry;
no unexplained numerical constants; diagnostics where meaningful; tests
covering determinism (seeded), shape, domain invariants, invalid-input
rejection, and statistical validation against closed forms when they exist.
Models never import GUI, broker, or service code.
