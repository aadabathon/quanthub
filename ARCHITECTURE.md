# ARCHITECTURE.md

How Quant Hub is put together. Design decisions are cited by number (D17,
D31, …) throughout the code and these notes.

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
providers/      Online providers (the SEC's companyfacts): fetches into data/
                types with the stdlib only. No other layer; services cache
                and store what they fetch (D47).
storage/        SQLite persistence (stdlib sqlite3). Knows data/ only —
                no broker, no GUI, no services.
visualization/  Matplotlib figures for scripts and notebooks. Never GUI.
```

**CI.** `.github/workflows/tests.yml` runs the whole suite on every pull
request and every push to main: Windows, Python 3.12, offscreen Qt, no
network (D108).

**No orders.** Brokerage access is read-only, and there is no order code.
`tests/test_layering.py` fails the build if `placeOrder`, `cancelOrder` or
`reqGlobalCancel` appears anywhere outside `broker/orders.py`, a module
that doesn't exist.

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
│   │                          multiples), Heatmap (sequential ramp + color bar),
│   │                          QuantileBands (rows of 5–95% bands: the desk's
│   │                          voices side by side, D101)
│   ├── timeline.py            TimelineChart: the literature on strand lanes
│   │                          over a squeezed year axis, chips by level of
│   │                          detail, links on selection; the one chart that
│   │                          pans and zooms, along time only (Lit-2, D58)
│   ├── surface3d.py           3D vol surface (D32): SurfaceMesh → Plotly figure
│   │                          dict → local plotly.js in Qt WebEngine; draws
│   │                          only when shown, warm-up on load, Plotly.react
│   ├── app_icon.py            the α app icon: painted from the palette, packed
│   │                          into assets/quanthub.ico (window, taskbar and
│   │                          desktop shortcut), taskbar AppUserModelID
│   ├── main_window.py         shell + THE PUMP (QTimer, 16 ms)
│   ├── workspace.py           pages as docks: tabs, splits, floating; the
│   │                          saved layout (D55)
│   ├── sidebar.py / top_bar.py / status_bar.py   shell pieces (nav, Ctrl+K
│   │                          search + IBKR control, status + log drawer)
│   └── pages/                 base.py (Page, ScrollPage, Shell, debounce),
│                              dashboard.py, desk.py (D101, record D105), markets.py, returns.py (HRH),
│                              data.py (D64), trades.py (D95, timing D100, as held D107), whatif.py (D73),
│                              hedge_lab.py (D71),
│                              garch_lab.py,
│                              simulator.py, options_lab/ (page, underlying,
│                              pricer, strategy, monte_carlo), volatility_lab.py,
│                              kalman_lab.py, markov_lab.py, valuation.py,
│                              research/ (page, history, timeline, library)
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
│   ├── literature.py          the literature canon: CanonPaper/Link/Strand/
│   │                          Era, the time rule, DAG and source checks,
│   │                          short labels (Lit-1, D58)
│   ├── canon/                 the canon itself, package data: timeline.json
│   │                          (strands, eras) + one JSON file per strand
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
│   ├── dcf.py                 unlevered-FCF DCF (an earlier modeler, ported):
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
│   │                          N_eff, Kelly with its error (D39); the days
│   │                          that decide the numbers (D50, D53)
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
│   ├── accounts.py            AccountService: IBKR accounts, positions and
│   │                          balances, read-only, as a snapshot (E1, D54)
│   ├── portfolio.py           PortfolioService: positions imports kept,
│   │                          holdings valued, options priced, risk run,
│   │                          regressions on the benchmark (D44), growth
│   │                          and book greeks (D45); gaps
│   │                          named; the example portfolio
│   ├── research.py            exact experiment snapshots/replay, matched
│   │                          tests, sweep manifests and paper imports (D42)
│   ├── valuation.py           the Valuation page's data: companies, prices
│   │                          (quote else last close), the screen ranked
│   │                          cheapest first, saved records, CSV (D48)
│   ├── fundamentals.py        ticker/CIK → FinancialHistory: SEC facts kept a
│   │                          day, refetched on demand, mapped offline (D47)
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
├── providers/sec_edgar.py     SEC companyfacts through the stdlib: the client
│                              (identity, gzip, 10/s, typed errors), facts as
│                              filed, and the mapping to FinancialHistory (D47)
├── storage/valuation_store.py  saved valuations, immutable records with a
│                              filing-data fingerprint (schema v5, D48)
├── storage/fundamentals_store.py  SEC facts as filed, filers, the ticker map
│                              (schema v4, D47)
├── storage/position_store.py  imported position snapshots, kept (D39)
├── storage/research_store.py  immutable research payloads, mutable notes
│                              and paper metadata; local PDF copies (D42)
├── storage/canon_store.py     load_canon: reads and checks data/canon/; your
│                              fields on canon papers stay in research_store,
│                              merged by id in services/research.py (D58)
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

## Added since 1.0

Layer by layer. Each module's docstring has the detail.

- `extensions.py` — installed extensions' pages and Ctrl+K commands: load, place, shut down (D86, D87); an extension's book and grader, the first of each kept (`merge_book`, D89)
- `data/series.py` — SeriesMeta/FetchedSeries/StoredSeries: outside series as fetched, with unit, citation and vintage; fractions for returns (D60)
- `data/holdings.py` — Holding/FundHoldings: one N-PORT report as filed; weights as fractions of net assets (D62)
- `data/trading_calendar.py` — NYSE holidays by rule from 1998 (Easter, observed days, special closures); trading_days (D63)
- `data/return_distribution.py` — HRH's math: period returns and windows, tails, the latest move's rank, A-D and JB on a non-overlapping sample, FD bins, the Q-Q band with fitted parameters (H1, D84)
- `models/implied_variance.py` — model-free implied variance: the fitted smile's K_var (quoted range + wings), the VIX-style quote sum and its bid/ask band, constant- maturity tenors, forward variances (D75)
- `models/variance_premium.py` — realized variance over NYSE trading-day windows, GARCH's expected total, the three premiums, ex-post series + Newey–West mean (D76)
- `models/hedge.py` — hedges on the book: cash, streams, trend, vol targeting, rolled puts / spreads / collars at VIX·ρ·smile; the yardstick: crash windows by rule, cost, Δ log growth ± NW, convexity (U1)
- `models/stress.py` — crash stress: a constant-β replay through French's market from 1926, its worst falls (A2, D67)
- `models/whatif.py` — what-if vectors: parse, a transient snapshot, the builders, min variance / equal risk / equal weight / max Sharpe by softmax (A3, D72)
- `services/external_data.py` — outside series: Ken French's files refreshed when a week old, each failure keeping its stored copy; Shiller CSV imports (D61); reads served offline (D60)
- `services/fund_holdings.py` — ticker → latest N-PORT report: checked monthly, new filings kept beside old, offline (D62)
- `services/security_ids.py` — held symbols' ISINs: stored, else IBKR's when connected (D66)
- `services/chain_collector.py` — the watchlist's chains once a trading day after 09:45 NY, wide band, one symbol per task (D63)
- `services/book.py` — today's book for What-if and the Hedge Lab: `Book` (the analysed snapshot, its settings, sleeves and core), the `BookSource` and `Grader` protocols, `Grades`, and `PortfolioBook`: the newest Portfolio snapshot with the Portfolio page's own settings record, the example portfolio before any import (D89)
- `services/whatif.py` — the What-if job: today's context from the book source, builders, references, each vector analysed, crash-replayed and (with a grader) graded; named sets (A3, D72, D89)
- `services/massive_source.py` — Massive as a BarSource under CachedSource, like IBKR: traded = split-adjusted bars; adjusted = traded × Π f_e over later ex-dates, f = 1 − D/P_before, both on one split scale; unreachable is offline (D97)
- `services/hedge.py` — hedges on stored data: the β proxy and a mix's book, today's shape (ρ, smile, spread), every candidate at every size, today's prices; offline (D70)
- `importers/shiller_csv.py` — Shiller's ie_data Data sheet saved as CSV → monthly series by header label; Oct = 1871.1, provisional last month dropped (T1, D61)
- `providers/ken_french.py` — Ken French's daily FF3, FF5 and momentum zips through the stdlib: client + pure parser, percent → fraction, terms in the docstring (D60)
- `providers/massive.py` — Massive's (formerly Polygon's) daily bars, split-adjusted, and dividends through the stdlib: the key only in an Authorization header, requests paced for the free plan, pages followed, typed errors, a session's date from its New York-midnight timestamp (D97)
- `providers/fred.py` — FRED's API: VIXCLS, DTB3, DGS3MO as published; the key never logged, the credit line cited (D69)
- `providers/sec_nport.py` — funds' N-PORT: the fund ticker map, the EDGAR Atom feed, primary_doc.xml → FundHoldings (D62)
- `storage/series_store.py` — outside series as fetched: ext_series (meta, fetched_at, file SHA-256) + ext_values, replaced whole per fetch (schema v7, D60)
- `storage/holdings_store.py` — fund ticker map + every N-PORT report date kept (schema v8, D62); held symbols' ISINs (v9, D66) (position_store: each import's file hash, v10, and delete after a backup, D74)

### The desk (D93)

Models as voices: each states scorable claims about today's book and the
market, every day, and is scored once their day has passed. Nothing
trades.

- `data/desk.py` — the record: `Claim` (quantiles at 5/25/50/75/95%, or an event's probability, about a target's `.return`, `.vol` or `.fall` over a horizon in NYSE sessions), `Book` (priced holdings, cash, what was left out, the market's close), `Proposal`, `VoiceOutput`, `Snapshot` (canonical JSON, SHA-256), `Decision` (cites a snapshot's hash, states what it expects as claims), `Outcome` (D96, D102, D104)
- `models/baseline.py` — the no-edge voice every other is scored against: a driftless random walk at the last 63 sessions' volatility; return quantiles, ending-down odds, realized-vol quantiles from the exact χ², the deepest fall simulated on daily closes (`deepest_falls`) (D96, D102)
- `models/voices.py` — garch, regimes and bootstrap, each the baseline's claims with one assumption changed, about the book and the market; garch and regimes propose volatility-managed risk (D99, D102)
- `models/tail.py` — the tail voice: the book's β and own risk beside SPY, applied to 20,000 windows of French's market since 1926 at today's bill rate (D103)
- `models/comparison.py` — the voices side by side: the book's claims in dollars, every shared claim lowest to highest, nothing averaged; proposals as trades, and a uniform-scale proposal read under every voice's law (D101)
- `models/scoring.py` — what happened to each claim's target, from one adjusted series fetched at resolution; quantile, Brier and log scores; skill against the baseline, tested on non-overlapping claims only; calibration; regimes by the century's volatility terciles (D104)
- `services/desk.py` — `take` (the book rolled forward and priced on one day, SPY beside it, every voice, stored once per data day, JSON beside the store), `resolve`, `record`, `view` for the page, `record_decision`, `record_run` and `runs`; refreshes French's files before a take when a week old (D96, D101–D105, D109)
- `services/desk_report.py` — the desk as plain data, every unit named: `status` (the last run, the snapshot's age in sessions, the book's export and history, the backup, the record), `view`, `changes` between two snapshots, `record`, `decisions`; a snapshot cited by its stored hash (D109)
- `storage/desk_store.py` — desk_snapshots, desk_claims (each with its resolve day), desk_decisions, desk_outcomes, desk_runs; append only (schema v12, v13, v14)
- `gui/pages/desk.py` — the Desk page: the voices' bands in dollars, where they disagree, what each would do and its effect under every voice's law, each voice's record, the snapshot and its book, each voice's reasoning and papers (D101, D105)
- `snapshot.py` — `python -m quant_hub.snapshot`: resolves what's due, takes the day's snapshot, then copies the store (`storage/backup.py`), each step going ahead when an earlier one fails; then records the run; for a scheduled daily run (D96, D104, D108, D109)
- `decide.py` — `python -m quant_hub.decide`: records a decision, its trades, reasoning and claims, on the newest snapshot, from flags or an agent's JSON; `--list`, with `--json` for an agent (D108, D109)
- `desk.py` — `python -m quant_hub.desk status / view / changes / record / whatif`, text or `--json`; only reads (D109, D111)
- `models/weighing.py` — a trade weighed on the desk: the book after it at the snapshot's close (value unchanged, cash the other side), shorts and margin flagged (D111). `DeskService.whatif` runs every voice on both books from the returns `take` built (D111)
- `trades.py` — `python -m quant_hub.trades import-activity / import-positions / trips / timing / held`, text or `--json` (`services/trades_report.py`); the imports only append (D110)
- `storage/backup.py` — the store copied with SQLite's online backup to dated files, the newest `[backup] keep` kept (D108)

### Your trades

A broker's own history, replayed against its exports to the cent, as
round trips, as timing against luck, and as the returns the account
really had.

- `data/activity.py` — `Transaction` (quantity = change in shares, amount = change in cash, each kind's rules, its account type from its wording), `ActivityFile`, the key and occurrence numbers that let overlapping exports count a row once (D90, D91, D106)
- `importers/activity_csv.py` — the generic layout, and Fidelity's history download by its header: an action map holding only wording seen in real exports, everything else kept as other; a bad row named with its line (D90–D92)
- `models/ledger.py` — the history replayed against the positions exports: each holding's residual, same-day trades placed either way, opening gaps; each export's cash checked to the cent (`cash_lines`: settled cash and margin types, short cash aside, no line for a margin loan) (D92, D106)
- `models/lots.py` — FIFO lots and round trips flat to flat, P&L from the cash, income beside it; the open lots' basis against an export's (D94)
- `models/timing.py` — how well each trade was timed: the exact P&L split, better days in ±5/±10 sessions against the exact null (the arcsine law convolved), markouts net of β with a sign test, MFE/MAE/capture (D100)
- `models/held.py` — returns as held: units and all the cash day by day, value at traded closes, external flows, time- and money-weighted returns, the same flows in SPY, growth of $1 and its drawdown (D106, D107)
- `services/activity.py` — imports (every file read first, then all stored or none), `reconcile`, `round_trips`, `basis_checks`, `timing`, `held`, and `current_holdings` (the newest export rolled forward, its cash from the history when it reconciles) (D90–D107)
- `storage/activity_store.py` — activity_imports + activity, keyed by (row key, occurrence) with INSERT OR IGNORE (schema v11). The table isn't `trades`: an extension may own that name in the same file
- `gui/pages/trades.py` — the Trades page: imports, round trips and their pieces, the check against the exports, timing, and returns as held (D95, D100, D107)

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
Returns    = one stored security's return histogram (HRH): the latest move's
             rank, tails, A-D and JB, a Q-Q view (D84, D85)
Data       = what the store holds, by kind: outside series with a preview and
             citation, fund holdings, chain snapshots, price history (D64)
Trades     = your broker's history imported (Fidelity's download or the generic
             layout), replayed against every positions export, cost basis checked,
             round trips and their FIFO pieces, every transaction (TR5a, D95)
What-if    = weight vectors (typed, built from today's book, or the four references)
             through Portfolio's analysis side by side, with the crash replay and
             risk shares; saved sets; nothing trades (A3, D72–D73, D89)
Hedge Lab  = every hedge on the book (β proxy since 1926, or today's mix): the
             frontier, growth and drawdown, the crash table, the selected hedge's
             card with today's price (HEDGE U1–U4, D71, D89)
Simulator  = controlled stochastic experiments (the GBM/Monte Carlo lab)
Options Lab= pricing/IV, multi-leg strategies, MC check over one MarketInputs
GARCH Lab  = conditional-volatility fit: synthetic GARCH or explicit source history
Vol Lab    = implied-vol surface from an OptionChain; GARCH/realized overlay;
             heatmap or rotatable 3D surface (D32); the variance premium (D77)
Kalman Lab = local level / local linear trend on log prices: synthetic or source history
Markov Lab = hidden volatility regimes in returns (Gaussian HMM, BIC over 1–3):
             synthetic worlds or source history
Portfolio  = imported positions (or the example): risk first: VaR/ES, risk
             contributions, beta, the replayed drawdown, Kelly with its error;
             then growth by sleeve with hedge sizes and the options book
             (D45), α/β regressions, rolling β and residual links (D44)
Valuation  = a DCF from SEC filings: seeded assumptions,
             EV bridge, projection, WACC × g grid, reverse DCF with every
             root, a screen, saved valuations (D46–D48)
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
  It can't see a split IBKR left unadjusted in both bases, as it did in
  much of its pre-2006 history. `PriceHistory.split_gaps`
  (`data/splits.py`, D57) names those overnight gaps, and pages show them.
  Nothing repairs them.
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
- **Offline (D50)**: the exception is `SourceOfflineError` (TWS closed, no
  network; `NotConnectedError` is one).
  - The cache then serves the stored, covered bars, as long as they form
    one stretch inside the request. A real interior gap still fails.
  - The history carries `offline_reason`, and every page that shows the
    result says so.
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
  crosshair + readout as the hover layer. The one exception is
  `timeline.py` (D58), which pans and zooms along time because 221 papers
  don't fit one view. Matplotlib is not used in pages
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
- **Card grid (D56).** A page lays its cards on `CardGrid` (`gui/card_grid.py`,
  imported from `components`): `add(name, card, x, y, w, h)` or
  `add_column(...)` with default places on 12 columns, then
  `pages.base.saved_grid(grid, store, "<key>.cards")`. The user drags
  and resizes cards; the grid keeps each at least its minimum size,
  collapses hidden cards, and saves the layout.
- **Docks (D55).** Each page sits in one dock of `gui/workspace.py`, a
  `QMainWindow` nested in the shell. Pages tab together, split side by
  side, or float, and cards stay inside their page. `on_show`/`on_hide`
  follow whether a page can be seen, so several can be shown at once.
  Navigation opens a closed page as a tab of the active one. At shutdown
  every shown page gets `on_hide`, and the layout plus the window's
  geometry are saved under the view-state key `workspace`. Ctrl+K's "Reset
  layout" goes back to the first page alone.
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
  which would otherwise size pages to their *preferred* height). It never
  scrolls sideways, so it asks for its content's minimum width instead,
  and the window's minimum width is the sidebar plus the widest page built.
  Only the window's minimum height is fixed. Tabbed content uses
  `PageStack`, which sizes to the visible page, not the tallest.

## Quant-module conventions (follow the GBM exemplar)

Every future quantitative module (`data/synthetic.py` is the template):
mathematical description + discretization in the module docstring;
assumptions and units stated; inputs validated in `__post_init__`/entry;
no unexplained numerical constants; diagnostics where meaningful; tests
covering determinism (seeded), shape, domain invariants, invalid-input
rejection, and statistical validation against closed forms when they exist.
Models never import GUI, broker, or service code.
