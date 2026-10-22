# ARCHITECTURE.md

How Quant Hub is put together. For *why*, see [DECISIONS.md](DECISIONS.md).

## Layers

Imports point strictly downward. `tests/test_layering.py` scans source files
and fails the build on violations.

```
gui/            Qt (PySide6 + pyqtgraph) presentation (D31). Renders state,
                forwards user intent. No math, no data munging, no broker
                calls beyond interfaces. May call pure models/ directly (D26).
services/       Application facade + caching. May import data/, storage/, and
                broker interfaces (broker/base.py, broker/types.py). Never
                the GUI toolkit, never ibapi.
data/           Pure domain: types, generators, statistics, validation,
                interval math. NumPy/pandas only. Imports nothing from any
                other quant_hub package.
models/         Pure quantitative models. May consume data/ contracts; never
                GUI, services, broker, or storage.

broker/         Infrastructure. The ONLY package allowed to import ibapi.
                Never storage.
importers/      File readers (CSV bars and positions): files into data/ types with
                pandas + stdlib. No other layer; services store what they
                read, and the GUI reaches them only through services.
storage/        SQLite persistence (stdlib sqlite3). Knows data/ only —
                no broker, no GUI, no services.
visualization/  Matplotlib figures for scripts and notebooks. Never GUI.
```

Cross-cutting modules:

- `config.py` — one frozen `AppConfig` dataclass (IBKR on/off, host, port,
  client id, timeouts, store path, defaults), and `load_config`: the user's
  `~/.quant_hub/settings.toml` over those defaults, every problem reported,
  none fatal (D34, D36). Only `build_core()` without a config reads the
  file; scripts and tests pass an `AppConfig`.
- `events.py` — `EventBus` + `LogEvent` + `EventBusLogHandler`.
- `tasks.py` — `TaskRunner`: one worker thread + a result queue drained on
  the GUI thread. Toolkit-free, so it lives outside `gui/`.
- `core.py` — the GUI-independent half of the composition root:
  `build_core()` wires bus, logging, broker, stores, services and the task
  runner into a `Core`; `Core.shutdown()` tears them down. A script can call
  it alone to get the wired services without any widgets.
- `app.py` — the composition root proper: `build_core()` plus the window and
  its page registry. Every class takes dependencies as constructor
  arguments; `core.py` and `app.py` are the only places that wire them.
  That is the entire DI story. `create_app()` builds everything without
  entering the event loop (smoke tests, screenshots); `main()` runs it.
  Launch: `python -m quant_hub` or the `quant-hub` launcher (the desktop
  shortcut's target).

## File map

```
quant_hub/
├── core.py                    build_core(): non-GUI wiring + logging; Core
├── app.py                     composition root: window + page registry, main()
├── tasks.py                   TaskRunner: worker thread + result queue
├── gui/
│   ├── theme.py               design tokens (navy palette, fonts, spacing),
│   │                          generated stylesheet, icon(), pyqtgraph config
│   ├── components.py          Card, KpiTile, MetricTile, PageHeader (+ status
│   │                          line), SegmentedControl, Select, Field (inline
│   │                          or captioned)/SelectField + form_grid, StatTable
│   │                          (TRUE | FIT, signed tones), Badge, Note
│   │                          (critical or info tone),
│   │                          DataTable (balanced columns), PageStack (sizes
│   │                          to the visible page), NavRow, EmptyState
│   ├── charts.py              pyqtgraph, render only: PriceVolumeChart,
│   │                          Sparkline, FanChart (optional history and
│   │                          overlays), StackedTimeSeries (Panel/Series/Band,
│   │                          one crosshair, optional fill, fixed y range,
│   │                          per-panel readout lines, stacked areas, Shade
│   │                          background tints), Histogram (markers),
│   │                          CurveChart (y vs numeric/log x: profiles, P&L
│   │                          with signed shading, MC convergence + band,
│   │                          markers / point-only series), SmileGrid (small
│   │                          multiples), Heatmap (sequential ramp + color bar)
│   ├── surface3d.py           3D vol surface (D32): SurfaceMesh → Plotly figure
│   │                          dict → local plotly.js in Qt WebEngine; draws
│   │                          only when shown, warm-up on load, Plotly.react
│   ├── main_window.py         shell + THE PUMP (QTimer, 16 ms)
│   ├── sidebar.py / top_bar.py / status_bar.py   shell pieces (nav, Ctrl+K
│   │                          search + IBKR control, status + log drawer)
│   └── pages/                 base.py (Page, ScrollPage, Shell, debounce),
│                              dashboard.py, markets.py, garch_lab.py,
│                              simulator.py, options_lab/ (page, underlying,
│                              pricer, strategy, monte_carlo), volatility_lab.py,
│                              kalman_lab.py, markov_lab.py,
│                              research/ (page, history, library)
├── config.py                  AppConfig
├── events.py                  EventBus (thread-safe), LogEvent, log handler
├── broker/
│   ├── base.py                Broker protocol, ConnectionState, ConnectionEvent
│   ├── types.py               ContractSpec, HistoricalBar, BrokerError tree
│   │                          (incl. InvalidContract/NoHistoricalData/Pacing)
│   ├── ibkr.py                IBKRBroker: callbacks → blocking calls;
│   │                          bar-size whitelist, error-code classification
│   ├── market_source.py       IBKRSource: Broker → DataSource + BarSource
│   │                          adapter (volume lots→shares per D21)
│   └── option_chain.py        IBKROptionChainSource: two-pass chain fetch
│                              (parity pairs, then OTM wings), per-day strike
│                              cache, dte to 16:00 New York (C1)
├── data/
│   ├── types.py               PriceHistory (validated), Quote
│   ├── sources.py             DataSource + BarSource protocols, SyntheticSource
│   ├── bars.py                canonical OHLCV validation for ACQUIRED data
│   │                          (structural raise vs row-drop + report)
│   ├── fundamentals.py        YearData/FinancialHistory: a filer's statements
│   │                          with provenance and null reasons (the loud-
│   │                          failure contract); FundamentalsSource (D46)
│   ├── positions.py           Position/OptionContract/PositionSnapshot: what an
│   │                          account holds, broker-agnostic (D39);
│   │                          combine_snapshots: one export per account, one
│   │                          snapshot (D43)
│   ├── options.py             OptionQuote/OptionChain: the chain contract
│   │                          (synthetic today, IBKR adapter later)
│   ├── ranges.py              DateRange (closed session-date intervals),
│   │                          merge/subtract — coverage arithmetic
│   ├── synthetic.py           GBM generator (the quant-module exemplar)
│   ├── simulation.py          GBMSimulationResult, run_gbm_simulation,
│   │                          monte_carlo_summary, path_percentiles (fan
│   │                          bands; closed forms documented)
│   └── stats.py               full analytics layer: returns/CAGR/rolling vol/
│                              drawdown/moments, GBM estimation (μ̂, σ̂),
│                              ReturnStats + SeriesAnalysis bundles
├── models/
│   ├── backtest.py            daily self-financing portfolio accounting,
│   │                          explicit decision/earning dates, drift-aware
│   │                          turnover, causal walk-forward callbacks (R1)
│   ├── backtest_stats.py      net-return metrics, Sharpe + IID standard error,
│   │                          probabilistic and deflated Sharpe (D41)
│   ├── optimize.py            shared NumPy-only Nelder–Mead minimizer
│   │                          (MinimizeResult); callers reparameterize
│   ├── garch.py               stationary GARCH(1,1): simulation, Gaussian
│   │                          QMLE fit, conditional variance diagnostics
│   ├── dcf.py                 unlevered-FCF DCF (the owner's, ported):
│   │                          projection, terminal conventions, valuation
│   │                          date, sensitivity, multiples, margin of safety,
│   │                          reverse DCF with every root, seeding (D46)
│   ├── black_scholes.py       Black-Scholes-Merton (dividend yield q):
│   │                          MarketInputs, vectorized price/Greeks (D25
│   │                          scaling), no-arbitrage bounds, bracketed-Newton IV
│   ├── strategy.py            OptionLeg/Strategy, value/P&L/greeks at a
│   │                          horizon, exact expiry profile, templates
│   ├── option_monte_carlo.py  risk-neutral MC pricing (antithetic), running
│   │                          estimate + SE vs the closed form
│   ├── svi.py                 raw SVI (derivatives, density g(k)), SSVI
│   │                          surface, spread-weighted SVI fit
│   ├── carry.py               per-expiry forward and discount factor from
│   │                          put–call parity (weighted regression, fixed-rate
│   │                          fallback, Student-t dof) (C1b)
│   ├── vol_surface.py         chain → IVs + weights → per-expiry SVI →
│   │                          butterfly/calendar checks; synthetic chains;
│   │                          surface_mesh (total variance linear in T at
│   │                          fixed k, checked between expiries)
│   ├── market_surface.py      MarketSurface: a fit → (σ, r, q) at any (K, T),
│   │                          extrapolation flagged; apply_surface fills
│   │                          strategy legs (sticky strike) (C4)
│   ├── kalman.py              linear Gaussian state space: Joseph-form filter,
│   │                          RTS smoother, simulation; local level / local
│   │                          linear trend MLE (zero-variance reporting)
│   ├── portfolio.py           portfolio risk: vol, Euler risk contributions,
│   │                          beta, historical (options repriced) and
│   │                          Gaussian VaR/ES, drawdown of today's mix,
│   │                          N_eff, Kelly with its error (D39)
│   ├── growth.py              growth from the replay's per-position series:
│   │                          exact attribution, hurdles and verdicts,
│   │                          min-variance and growth-optimal sleeve sizes
│   │                          with errors, what-if, book greeks (W3, D45)
│   ├── regression.py          OLS of daily excess returns: classical, White
│   │                          and Newey–West errors, Dimson sum beta, causal
│   │                          rolling fits, Fisher-z residual correlations
│   │                          (W2, D44)
│   ├── strategies.py          six causal daily rules, rolling fits and
│   │                          capped portfolio risk sizing (D42)
│   ├── research_worlds.py     seeded null, trend and reversion controls
│   └── markov.py              Gaussian HMM on returns: scaled filter, Kim
│                              smoother, Viterbi, multi-start EM with a
│                              variance floor, BIC over K, forecasts (M1)
├── services/
│   ├── market_service.py      source registry + get_history/get_quote facade
│   ├── underlying.py          any source → spot (quote, else last close) +
│   │                          realized vol, with provenance (D28)
│   ├── vol_context.py         any source → trailing realized vols + GARCH
│   │                          expected-vol term structure (D30)
│   ├── workstation.py         local-store overview, windowed history, GBM
│   │                          forecast fan (the Dashboard's data, D31)
│   ├── option_chains.py       ChainService: chains by named source, every
│   │                          live fetch saved; "stored" = latest snapshot
│   ├── portfolio.py           PortfolioService: positions imports kept,
│   │                          holdings valued, options priced, risk run,
│   │                          regressions on the benchmark (D44), growth
│   │                          and book greeks (D45); gaps
│   │                          named; the example portfolio
│   ├── research.py            exact experiment snapshots/replay, matched
│   │                          tests, sweep manifests and paper imports (D42)
│   ├── file_import.py         CSV bars validated into the store under
│   │                          "file" (all or nothing per file); FileSource
│   │                          serves them; FileImportService for the GUI
│   ├── sync.py                SyncStatus/SyncPlan: FULL_HIT/PARTIAL_HIT/MISS
│   └── cached_source.py       CachedSource: BarSource + BarStore → DataSource
├── importers/bar_csv.py       CSV daily bars: the generic layout, Nasdaq.com
│                              and Yahoo presets detected by header, raw
│                              frames by symbol (D37)
├── importers/positions_csv.py positions CSV: generic layout + Fidelity
│                              presets (Portfolio Positions; Positions view,
│                              no account column, D43), option symbols parsed
│                              (D39)
├── storage/position_store.py  imported position snapshots, kept (D39)
├── storage/research_store.py  immutable research payloads, mutable notes
│                              and paper metadata; local PDF copies (D42)
├── storage/bar_store.py       SQLite schema (instruments/bars/coverage),
│                              upsert with counts, range queries, coverage
├── storage/chain_store.py     option-chain snapshots (option_chains /
│                              option_quotes, order kept), same database
├── storage/schema.py          prepare_store: the file's schema version
│                              (schema_migrations), an online backup before
│                              any migration, newer files refused (D36)
└── visualization/charts.py    matplotlib, for scripts/notebooks (the app
                               uses gui/charts.py): ChartStyle + compositions:
                               plot_price_history, plot_lab_overview (price/
                               drawdown/distribution/rolling vol),
                               plot_monte_carlo (fan + terminal dist),
                               plot_garch_overview, plot_option_profile,
                               plot_strategy_pnl, plot_monte_carlo_pricing,
                               plot_smiles, plot_vol_term_structure,
                               plot_vol_heatmap
```

## Concurrency model

Threads that exist: **GUI/main** (the Qt event loop), **task-worker** (one,
in `TaskRunner`), **ibkr-network** (created per connect, runs
`EClient.run()`).

Two seams confine all threading:

1. **Inside `broker/ibkr.py`** — ibapi is callback-based on the network
   thread. Each request registers a `_Pending` slot (a `threading.Event` +
   buffers) keyed by request id; the caller blocks on `event.wait(timeout)`;
   callbacks fill the buffer and set the event. Callers therefore see plain
   blocking methods that raise `BrokerError` subclasses. Nothing
   callback-shaped escapes this file.
2. **`tasks.py` + the pump** — the GUI never calls blocking methods
   directly; it calls `tasks.submit(fn, on_done, on_error)`. The worker runs
   `fn`, queues the result, and `MainWindow._pump` (a 16 ms `QTimer`, one
   60 Hz frame) invokes the callback **on the GUI thread**. The same pump drains the
   EventBus, which is how broker state changes (`ConnectionEvent`) and log
   records (`LogEvent`) reach widgets. Push events = bus; request/response =
   TaskRunner.

Why not asyncio (D1): the official ibapi is thread-based, and threads +
queues is the smallest correct model. It carried over unchanged from the
original Tk front end to Qt (D31).

## Page responsibilities and data flow (M6, D23/D24)

```
Dashboard  = workstation overview: store KPIs, market explorer, watchlist,
             GBM forecast sandbox, store table, research shortcuts
Markets    = real market observations: explicit source, provenance columns
Simulator  = controlled stochastic experiments (the GBM/Monte Carlo lab)
Options Lab= pricing/IV, multi-leg strategies, MC check over one MarketInputs
GARCH Lab  = conditional-volatility fit: synthetic GARCH or explicit source history
Vol Lab    = implied-vol surface from an OptionChain; GARCH/realized overlay;
             heatmap or rotatable 3D surface (D32)
Kalman Lab = local level / local linear trend on log prices: synthetic or source history
Markov Lab = hidden volatility regimes in returns (Gaussian HMM, BIC over 1–3):
             synthetic worlds or source history
Portfolio  = imported positions (or the example): risk first: VaR/ES, risk
             contributions, beta, the replayed drawdown, Kelly with its error;
             then growth by sleeve with hedge sizes and the options book
             (D45), α/β regressions, rolling β and residual links (D44)
RESEARCH…  = GARCH Lab, Vol Lab, Kalman Lab, Markov Lab, Research
```

**No global source state.** ``MarketService.get_history/get_quote`` take a
required ``source`` argument; each page owns and displays its choice
(Markets' selector; Simulator is synthetic by construction). GARCH Lab names
its market source per fit; `ibkr` therefore traverses the existing
SQLite-first `CachedSource` path. Every fit runs as one TaskRunner job, so
both the fitted parameters and every chart panel describe the same returns.
The Options Lab (D28) owns a source selector in its underlying card:
`manual` inputs, or `services/underlying.load_underlying` on a named source
(one TaskRunner job: history → realized vol, quote → spot, falling back to
the last close when the quote is unavailable). Every tab then prices off the
single resulting ``MarketInputs``. The models never know the source.
In Surface mode (C4) the card also holds a ``MarketSurface``, fitted from
the newest saved chain on one TaskRunner job. The Pricer and Strategy tabs
take each contract's σ, r and q from it (``OptionLeg.vol/rate/
dividend_yield``), and spot is the chain's own. The
Simulator keeps the D15 invariant: one TaskRunner job → one immutable
``(sim, analysis, mc)`` triple → every chart/stat renders from it.

**Quotes carry provenance** (``Quote.mode: MarketDataMode`` — live/frozen/
delayed/delayed-frozen/synthetic/unknown; timestamp = trade time when the
source provides it). Inside ``broker/ibkr.py`` (D22): quote requests
stream briefly and self-cancel; live+delayed tick ids and the
``marketDataType`` callback are normalized; entitlement advisories
(10167/10089) never fail the request or touch connection state; no usable
price within ``quote_timeout_s`` raises ``NoMarketDataError`` with the
advisory as the reason, which Markets renders as an UNAVAILABLE row with
the reason in its inspector. Connection health and per-instrument
entitlement are therefore fully independent states.

Historical series flow ``MarketService → CachedSource`` into the store;
the Dashboard's market explorer and watchlist read it back through
``services/workstation.py`` (Markets' "Sync 1Y history" fills it). A richer
historical-research surface is a later milestone.

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
  `CachedSource` accepts daily bars only: its coverage rule is a daily one.
- **Price bases** (D38): every request names traded or adjusted. Traded
  bars cache as above. Adjusted ones are refetched whole on the first
  adjusted request of a day, since every ex-date rescales the past. That
  daily refetch runs the split guard (adjusted/traded must be a dividend
  step function), which refetches traded history that straddles a split.
  `bars` keys on the basis (schema v2), and `refreshes` records each
  series' last full refetch.
- **Schema and backups** (D36): `build_core` runs
  `storage/schema.prepare_store` before any store opens the file. It reads
  the version from `schema_migrations`, copies the whole file into
  `backups/` with SQLite's online backup API before any migration step,
  applies the steps in order (each one transactional), and refuses a file
  from a newer app. Each store's `CREATE TABLE IF NOT EXISTS` still defines
  the current schema for a new file; migration steps change old ones.
- **Same-day bars**: never marked covered; a request ending "now" refetches
  exactly today's bar each time, upserting the provisional value.
- **Failure**: a failed fetch fails the whole request (no silently partial
  series); `BrokerError` subclasses distinguish not-connected / invalid
  contract / no data / pacing / timeout, and the GUI shows the message.
- **Fetch semantics**: D11 stands — IBKR requests end "now" with a duration;
  the adapter slices. A leading/interior coverage gap therefore fetches
  from the gap to now; harmless, because inserts upsert and coverage merges.

## GUI conventions (`gui/`, D31)

- **Design system.** `theme.py` holds every token (navy surfaces, ink,
  accent, validated series and status colors, fonts, spacing, radii) and
  generates the one application stylesheet. Widgets declare *what* they
  are via `role`/`variant` dynamic properties (`card`, `pageTitle`,
  `primary`, `segment`, …); the stylesheet decides how that looks. No hex
  constants, font sizes, or `setStyleSheet` calls in pages (a `StatusDot`
  coloring itself from a palette token is the one documented exception).
- **Pages are assembled from `components.py`**, never from raw styled
  widgets; a new visual pattern becomes a component first. Long text that
  could widen a card uses `ElidedLabel`. Pages that can outgrow the window
  subclass `ScrollPage` (scroll, never clip) and reflow on resize.
- **Charts** are pyqtgraph widgets in `charts.py`: render-only, precomputed
  inputs, view-only ranges driven by page controls, and a snapped
  crosshair + readout as the hover layer. Matplotlib is not used in pages
  (a future research figure may embed it via the QtAgg backend).
- **Status colors** (good/warning/critical) mean *state* — connection
  health, signed values, check results — and are never chart series
  colors. State is always color + text (or sign), never color alone.
- **Lifecycle.** Pages normally build on first navigation and stay alive.
  The Volatility Lab and its WebEngine widget are prepared before the main
  window is shown (D40), so Qt establishes its graphics surface up front.
  Preparation does not run jobs: `on_show` refreshes on visits, and
  `on_hide` persists via `ViewStateStore`. Pages reach the shell
  only through `Shell` (`navigate`, `publish_commands` for Ctrl+K).
- **Async results carry a token** when the user can supersede a request
  (e.g. switching symbols); stale results are dropped, not painted.
- **Consistent sizing.** Every control is `CONTROL_HEIGHT` tall; fields use
  `FIELD_S/M/L`; every page's right-hand inspector is `SIDE_PANEL_WIDTH`
  (inputs + stats on labs, selection details on Markets). Forms put the
  caption above the input; `Field.number()` parses, flags the field red and
  raises a message the page shows in its header status line.
- **Header status vs. card subtitle.** The `PageHeader` status line is for
  transient messages and errors; what a chart *shows* (source, sample,
  seed) lives in its card subtitle, so an error never leaves a chart
  unlabeled.
- **Replacing widgets.** Hide a widget before replacing it (`DataTable`
  does this for cell widgets): Qt deletes replaced widgets only on the next
  event-loop pass, and until then they paint over their replacement.
- **Showing widgets.** Attach children to their parent before calling
  `show` or `setVisible(True)`. A parentless widget is a desktop window;
  even a brief show during construction flashes a taskbar entry.
- **Live recalculation.** Closed-form pages recompute as you type through
  `pages.base.debounce` (150 ms after the last keystroke); Enter commits at
  once. Anything that blocks or simulates stays on the TaskRunner and is
  only *marked stale* by input edits, never re-run automatically.
- **Sizing rules.** `ScrollPage` fills the viewport and scrolls only below
  the content's minimum (its body layout opts out of height-for-width,
  which would otherwise size pages to their *preferred* height). Tabbed
  content uses `PageStack`, which sizes to the visible page, not the tallest.

## Quant-module conventions (follow the GBM exemplar)

Every future quantitative module (`data/synthetic.py` is the template):
mathematical description + discretization in the module docstring;
assumptions and units stated; inputs validated in `__post_init__`/entry;
no unexplained numerical constants; diagnostics where meaningful; tests
covering determinism (seeded), shape, domain invariants, invalid-input
rejection, and statistical validation against closed forms when they exist.
Models never import GUI, broker, or service code.
