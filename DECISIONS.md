# DECISIONS.md — architectural decision log

Numbered, append-only. If you want to change one, add a superseding entry
explaining why — don't silently drift. Format: decision, rationale,
consequences.

## D1 — Tkinter + threads + queues, not asyncio (2026-08-13)

Tkinter has no native asyncio integration and the official `ibapi` is
thread-based (the asyncio route is third-party `ib_async`, excluded by the
owner's "official TWS API" requirement). Threads + queues is the smallest
correct model. Consequence: exactly two thread-crossing mechanisms exist
(EventBus for pushes, TaskRunner for request/response), both drained by one
`root.after` pump.

## D2 — Broker exposes blocking calls, not callbacks/futures (2026-08-13)

`IBKRBroker` converts ibapi's callback model into blocking-with-timeout
methods internally (request id → `threading.Event` + buffer). Chosen over a
future/callback API because callers are already off the GUI thread via the
TaskRunner, and blocking APIs are dramatically easier to read and test.
Consequence: broker methods must never be called on the GUI thread.

## D3 — One `PriceHistory` wrapper instead of raw DataFrames (2026-08-13)

Every future consumer needs the same invariants (sorted unique DatetimeIndex,
positive finite closes). A frozen dataclass wrapper gives those invariants one
home plus symbol/source metadata. Trade-off accepted: slightly more ceremony
than passing DataFrames around.

## D4 — `close` is the only required column (2026-08-13)

The plan sketched full-OHLCV as canonical; implementation relaxed it. The
synthetic source generates only closes — fabricating OHLC microstructure adds
columns, not information — while IBKR bars pass real OHLCV through. Models
that genuinely need OHLC (e.g., range-based vol estimators) should check for
the columns and say so.

## D5 — `IBKRSource` lives in `broker/`, not `data/sources.py` (2026-08-13)

The plan originally placed it in `data/`. Moved: `data/` importing broker
code points the dependency arrow outward. In ports-and-adapters terms the
domain defines the port (`DataSource` protocol, structural via
`typing.Protocol`); infrastructure implements it. Enforced by the layering
test.

## D6 — No `models/` package until the first real model (2026-08-13)

A base class with zero concrete models is premature abstraction (owner
concurred). The GBM module carries the conventions instead. When the first
real model (GARCH/Kalman) lands, create `models/` and shape any shared base
on two concrete examples, not zero.

## D7 — One `PlaceholderView` class, not seven files (2026-08-13)

Parameterized by title + description. Seven near-identical files would be
noise. Registered per-module in `app.py`.

## D8 — Dropped `services/app_state.py` and `services/status.py` (2026-08-13)

An "app state" object is a god-object magnet. Split into transport
(`EventBus`) plus broker-owned state (`IBKRBroker.state`); a service caching
connection state would have been duplicated state. `EventBusLogHandler` lives
in `events.py` as logging↔bus glue.

## D9 — Single TaskRunner worker thread (2026-08-13)

v0.1 requests are user-initiated and serial; one worker gives FIFO ordering
for free and avoids concurrent-request handling in the broker. Revisit
(worker pool or per-domain workers) only when something genuinely needs
parallel fetches.

## D10 — `ibapi` is an optional dependency; app degrades gracefully (2026-08-13)

`broker/ibkr.py` guards its import; `connect()` raises
`BrokerUnavailableError` with install instructions if absent. PyPI ships old
9.81; the official 10.x comes from interactivebrokers.github.io. Callback
signatures differ across versions, so `_IBApp` methods take `*extra`.
Delayed market data (`reqMarketDataType(3)`) is requested so snapshots work
without paid subscriptions.

## D11 — IBKR history always ends "now"; durations round up, adapter slices (2026-08-13)

Pinning historical end times requires per-version timezone formatting, so
v0.1 anchors every request at now. Long windows round UP to whole years
(`duration_string`), and `IBKRSource` slices the frame back to [start, end],
making over-fetch harmless. Lift when pinned historical windows are needed.

**Amended 2026-10-15 (live finding).** Because a request ends now, its
duration must reach from `start` to *now*, not to `end`. `get_history` had
measured `duration_string(start, end)`, so a window ending in the past (a
cached series' leading or interior gap, D18) came back anchored at now and
missed its own beginning, while `CachedSource` recorded the whole gap as
covered: a silent, permanent hole. It now measures to `max(end, now)`.
The owner's store had one such hole (SPY, 2021-08-14 → 2022-08-15), created
when a 1Y sync preceded a 5Y load on 2026-08-14. It was repaired the same
day: the coverage row was narrowed to the stored bars, then the missing year
was fetched.

## D12 — Read-only brokerage, no auto-reconnect (2026-08-13)

Safety posture, not a TODO: no order code exists anywhere, and a dropped
connection flips the indicator and logs — reconnecting is a deliberate user
click. Any future execution work is a separate, owner-initiated design
conversation with its own safeguards.

## D13 — Single committed dark theme; palette from a validated set (2026-08-13)

One dark "terminal" look, centralized in `gui/theme.py`. Colors are from a
palette validated for colorblind-safe adjacency and 3:1 contrast on the dark
surface (`#1a1a19`); series-blue `#3987e5` is the chart line; status colors
are reserved for state and never reused as series colors. No per-widget hex
constants outside theme.py (charts receive a `ChartStyle` mapped once).

## D14 — Synthetic symbols are deterministic worlds via CRC32 seeds (2026-08-13)

Per-symbol GBM parameters and seed derive from `zlib.crc32(symbol)` (stable
across processes, unlike salted `hash()`); each symbol's full path is
simulated from a fixed base date (2015-01-02) and sliced, so different
requested windows return consistent overlapping prices — the vendor-like
property research code relies on.

## D15 — Dashboard = synthetic laboratory with ONE simulation state (2026-08-13)

Milestone 3 rebuilt the dashboard around a single state triple
`(GBMSimulationResult | None, SeriesAnalysis, MonteCarloSummary | None)`
produced by one TaskRunner job — charts and stats never regenerate data
independently, so everything on screen always describes the same draw. Two
entry paths feed it: "Run Simulation" (custom GBM, true parameters known →
TRUE vs ESTIMATE panel, σ reference line, Monte Carlo tab) and "Load" (any
`DataSource` → same analytics/charts, TRUE column shows "—", no Monte
Carlo). The Load path was deliberately kept so the IBKR/CSV future flows
through the identical pipeline. Consequence: `SeriesAnalysis` (in
`data/stats.py`) is the one bundle the GUI consumes; new metrics get added
there, not in widget code.

## D16 — Estimator conventions: μ̂ inverts Ito; plain-moment g1/g2 (2026-08-13)

Under GBM, log returns have mean :math:`(\mu - \sigma^2/2)\Delta t`, so the
annualized mean log return estimates the *log drift*, not μ. The dashboard's
μ̂ therefore adds the Ito correction back: `mu_hat = r̄·p + sigma_hat²/2`
(derivation in `data/stats.py`). Both the log-drift estimate and μ̂ are shown
so the two "drifts" people conflate stay visibly distinct. Skewness/excess
kurtosis use the plain moment estimators (g1 = m3/m2^1.5, g2 = m4/m2² − 3)
rather than pandas' bias-adjusted versions, because hand-computable values
make the tests verifiable; small-sample bias is acceptable in a diagnostics
panel and is itself part of the estimation-uncertainty story. Annualization
lives in one place: every stats function takes `periods_per_year` defaulting
to `TRADING_DAYS_PER_YEAR = 252` in `data/stats.py`.

## D17 — SQLite (stdlib `sqlite3`) is the approved persistence mechanism (2026-08-14)

Supersedes the ambiguity in the old "no databases" rule, with explicit owner
approval: local SQLite persistence via Python's standard-library ``sqlite3``
is allowed and is the *preferred* store for historical market data (no new
dependency). What stays banned is external database infrastructure —
PostgreSQL, MySQL, Redis, Dockerized or cloud databases — unless a future
milestone explicitly requires it. Default location: ``~/.quant_hub/
market_data.db`` (``AppConfig.db_path``); acquired market data is a local
asset, not source code, so it lives outside the repository.

## D18 — Coverage ranges, not bar existence, drive cache decisions (2026-08-14)

Weekends/holidays make "no bar stored" ambiguous, so the store keeps an
explicit ``coverage`` table of successfully-synced date intervals per
(instrument, source, bar size), merged into maximal disjoint ranges on
write. Sync classification (FULL_HIT / PARTIAL_HIT / MISS) is pure interval
arithmetic (``data/ranges.py`` + ``services/sync.py``) and every decision is
logged. Two deliberate consequences: (a) D11 stands — fetches still end
"now"; a leading/interior gap degrades to fetching from the gap to now,
harmless because inserts upsert; (b) **today is never marked covered**: a
same-day daily bar is provisional until the session closes, so a request
ending "now" always re-fetches exactly today's bar, which refreshes it
(verified live: the partial bar's close moved between passes). Failed
syncs record nothing and fail the whole request — no silently partial data.

## D19 — Daily bars are naive session dates; intraday deferred (2026-08-14)

A daily bar's "timestamp" is an exchange session *date* — inherently
timezone-free. Storing it as "UTC midnight" would manufacture off-by-one-day
bugs while adding no information, so daily (and weekly/monthly) bars are
naive dates end-to-end (matching the codebase's existing naive
``DatetimeIndex`` convention), stored as ISO text which orders
lexicographically = chronologically. Intraday bar sizes are *rejected* at
the broker boundary until a future milestone defines the timezone policy
(most likely UTC internally, converted at ingestion). Verified live: ibapi
9.81 daily bars arrive as ``YYYYMMDD`` strings.

## D20 — Cache is a decorator over a raw-bars port (2026-08-14)

``CachedSource`` implements ``DataSource`` (so ``MarketService`` and the GUI
are unchanged) but consumes a second, lower-level port: ``BarSource.
fetch_bars(...) -> DataFrame`` (possibly empty, unvalidated). Rationale:
``get_history -> PriceHistory`` *cannot represent* an empty response
(``PriceHistory`` rejects emptiness by design), yet "this window had no
sessions" is a legal, expected sync outcome. ``IBKRSource`` implements both
ports. Validation happens exactly once, in the cache layer via
``data/bars.py``, before persistence. Synthetic data deliberately bypasses
the cache: generated experiments are reproducible from parameters;
persistence is for acquired observations.

## D21 — IBKR US-stock volume is stored as shares, not broker lots (2026-08-14)

Live shakedown (2026-08-14, TWS 7496, ibapi 9.81) confirmed IBKR reports
historical US-stock volume in lots of 100 (SPY daily ≈ 850k lots ≈ 85M
shares). The IBKR adapter multiplies by 100 so the canonical frame stores
shares — the broker-independent unit. Revisit per security type if non-STK
contracts arrive.

## D22 — Entitlement advisories are not failures; quotes stream briefly (2026-08-14)

Raw-callback probe against live TWS proved: with ``reqMarketDataType(3)``,
error 10167 is an *advisory* followed ~300ms later by delayed ticks
(68/75/76 + tickString 88 = last-trade epoch) and a ``marketDataType=3``
callback; 10089 (for this account, e.g. AAPL) is an advisory followed by
*silence*; snapshot mode holds the line ~11s while streaming delivers the
same burst in <0.6s. Therefore: quote requests stream and self-cancel,
completing on the first usable price tick plus a short burst grace;
advisory codes (10167/10089/10090) accumulate on the pending slot and
never fail it or touch connection state; a priceless timeout raises
``NoMarketDataError`` carrying the advisory text. ``quote_timeout_s`` (5s)
bounds unavailable instruments. The old bug — failing the request on the
advisory before the promised delayed ticks arrived — is what broke the
watchlist.

## D23 — Quote provenance is first-class; no global active source (2026-08-14)

``Quote`` carries ``MarketDataMode`` (live/frozen/delayed/delayed-frozen/
synthetic/unknown — IBKR's own market-data-type vocabulary plus ours);
"unavailable" is deliberately NOT a mode: it is the absence of a quote,
represented by a typed error whose reason the Markets UI shows per row.
``MarketService.active_source`` is deleted — a process-global mutable
source meant the Dashboard's combobox silently changed what Markets rows
meant. Every call now names its source; pages own and display their choice
(Markets' selector, Simulator = synthetic by construction). Tick codes and
callback shapes still never escape ``broker/``.

## D24 — Dashboard = overview; the GBM lab moved to Simulator (2026-08-14)

Supersedes D15's *page placement* only: the laboratory (with its
single-state-triple invariant, TRUE-vs-ESTIMATE panel, and Monte Carlo tab
— all intact) now lives on a dedicated Simulator page, synthetic-only. The
Dashboard is a deliberately *static* workstation overview (connection card,
persisted-store summary, page guide) — the configurable-widget system,
drag-and-drop, layout persistence, and a SettingsStore were consciously
deferred, not forgotten. Markets keeps a persist-only "Sync 1Y history"
button so the M4 ingestion pipeline remains reachable from the GUI until a
richer historical-research page exists. Navigation gained titled sections
(core pages / MODELS placeholders).

## D25 — Options greeks use trader-convention scaling, not raw derivatives (2026-08-19)

`OptionService.greeks()` returns the closed-form Black-Scholes partials
(correct by construction — same `d1`/`d2` as `price()`), but the raw
calculus values don't match what a broker ticket shows: raw vega/rho are
per 100%-move in `sigma`/`rate` (~100x a real quote), and raw theta is
annual decay rather than the daily decay traders read. Values are now
scaled at the `greeks()` boundary — `vega`/`rho` divided by 100 (per
1-percentage-point move), `theta` divided by `DAYS_PER_YEAR` (per calendar
day) — while `delta`/`gamma` are left as raw derivatives (no convention gap
for either). Consequence: `greeks()`'s dict keys are *reported*, not raw,
values; any future consumer computing off them (e.g. a hedging calculation)
must know the scaling or re-derive from `d1`/`d2` directly.


## D26 — Black-Scholes is a pure model; the options service is deleted (2026-09-27)

Supersedes the *location* in D25 (its greeks scaling stands unchanged):
`services/Options/options_service.py` → `models/black_scholes.py`, as
module-level functions (`price`, `greeks`, `no_arbitrage_bounds`,
`implied_volatility`). The `OptionService` class held no state and no I/O
— it was a namespace, and pricing math is exactly what `models/` exists
for (D6). Both upcoming labs (Options, Volatility) need the same pricer and
IV solver, so it had to live below either of them. GUI views call pure
models directly, as GARCH Lab already did; a service earns its place only
when it adds sources, caching, or I/O. Consequence: the capitalized
`services/Options/` package and `gui/views/Options_lab.py` are gone
(renamed `options_lab.py`); every package and module name is snake_case.

The IV solver is a bracketed Newton iteration (Newton step if it lands
inside the root bracket, else bisection), derived in the module docstring:
monotonicity in σ plus the σ→0/∞ limits make a root exist iff the price is
strictly inside the no-arbitrage bounds, so out-of-bounds prices *raise*
rather than being clamped to a meaningless volatility.

## D27 — One shared optimizer in `models/optimize.py`; degenerate-simplex fix (2026-09-27)

GARCH's private Nelder–Mead moved to `models/optimize.py`
(`nelder_mead` → `MinimizeResult`) now that a second consumer (SVI smile
fitting) is imminent — shaping shared code on the second concrete need,
per D6. The move exposed a real bug: the initial simplex was built with
`simplex[1:, range(n)] += step`, which NumPy's mixed slice/advanced
indexing applies to the *whole* block, making all n non-start vertices the
same point. Every Nelder–Mead move is an affine combination of vertices,
so the search was confined to one line through the start and fits stalled
near their starting point while reporting convergence (true α=0.12 fitted
as 0.078). The simplex is now `start + step·I` (spans ℝⁿ). GARCH fits
reach strictly lower negative log-likelihoods, and regression tests pin
this: an off-diagonal quadratic in `test_optimize.py` and a GARCH truth
that is not one of the fitter's starting points.

## D28 — Options workflows run on `MarketInputs` + plain position data (2026-09-27)

Milestone A (Options Lab) is built so a synthetic run and a live run differ
only in *who fills two value objects*:

- **`MarketInputs(spot, rate, sigma, dividend_yield)`** is the only market
  state any options model consumes. The lab's UNDERLYING strip fills it by
  hand or through `services/underlying.load_underlying(market, symbol,
  source)`. That service gets spot from the source's quote, falling back to
  the last daily close with the basis recorded (D22/D23), and σ from
  realized daily-log-return vol over a lookback. Realized vol is the
  historical stand-in for Black-Scholes' forward-looking σ, and implied vol
  from chains replaces it in the data phase. Rate and dividend yield stay
  manual until a source exists.
- **`Strategy` / `OptionLeg` are plain data**: signed contract counts, a
  signed stock position, and an optional *recorded* entry premium (None =
  model fair value at entry). A future IBKR positions adapter only has to
  emit `OptionLeg`s with fill prices. The P&L, exact expiry profile, and
  greeks code does not change.
- **Strikes re-centre only when the loaded symbol changes** from the one
  they were built around. Reloading the same symbol never moves them, and
  neither does editing by hand.

Chains, the vol surface, American exercise, and live positions are
deliberately out of scope here (Milestone B / data phase).

## D29 — Greeks are a typed, vectorized `Greeks`; Φ via `np.vectorize(math.erf)` (2026-09-27)

Supersedes the dict return in D25; the D25 *scaling* is unchanged. `greeks()` returns a frozen
`Greeks(delta, gamma, vega, theta, rho)` (floats for scalar inputs), and
`price_array`/`greeks_array` broadcast NumPy inputs so one call prices a
strike × expiry × spot grid. A typed result catches misspelled keys and
lets strategy greeks be summed field-wise. NumPy has no `erf` and SciPy is
not a dependency. So Φ uses stdlib `math.erf` through `np.vectorize`: it is
exact to double precision (a rational approximation, e.g. A&S 7.1.26 at
~1e-7, would break the 1e-10 IV round trips), at the cost of a Python-level
loop. That costs ~1 ms for a 4×241 profile grid and ~0.2 ms per IV solve,
which is fine for the labs. Revisit only if chain-wide IV inversion in
Milestone B needs more; a vectorized bracketed Newton over the chain is the
natural next step there.

## D30 — Volatility Lab: chain contract in `data/`, SSVI truth, independent SVI slices (2026-09-27)

- **`OptionChain`/`OptionQuote` live in `data/options.py`.** A chain is an
  acquired observation like `PriceHistory`, so the domain defines its shape
  and a future IBKR adapter in `broker/` fills it (D5). The whole pipeline
  (`models/vol_surface.fit_surface`) consumes only this type: the
  synthetic generator today, real chains later, with no code changes.
- **The synthetic truth is SSVI with a mean-reverting ATM term structure**
  (Gatheral–Jacquier power law, γ = 1/2). It is free of static arbitrage
  when η(1+|ρ|) ≤ 2, and its slices are *exactly* raw SVI with
  closed-form parameters, so per-expiry fits have a known answer. The
  no-arbitrage claims are verified numerically on grids in the tests, not
  taken on citation. The `density ⇔ g(k)` formula is checked against
  Breeden–Litzenberger.
- **Slices are fitted independently** (raw SVI; validity and Lee's wing
  bound enforced by the reparameterization). Weights come from each quote's
  half-spread propagated into total variance by the delta method, so χ²/pt
  is interpretable (≈ noise² for synthetic quotes). Independence is
  deliberate: the butterfly/calendar checks then *test* the fitted surface
  instead of holding by construction. Under heavy noise the slices overfit
  and the ATM errors grow. A joint SSVI (whole-surface) fit is the natural
  next step when real chains arrive.
- **The heatmap uses standardized moneyness `k/√θ(T)`**: raw `k` ranges
  scale like √T, so a raw-`k` intersection would shrink to the 7-day window.
- **The model side already runs on real data.** `services/vol_context.py`
  loads any source's history and overlays trailing realized vols plus the
  GARCH average-variance forecast (derived in `models/garch.py`; the same
  functional form as the synthetic θ(T) with κ = −252 ln p) on the ATM IV
  term structure. With real chains, that overlay shows the variance risk
  premium.

## D31 — The GUI moves to Qt (PySide6 + pyqtgraph); Tk retired at parity (2026-10-10)

Supersedes D1's *toolkit* choice (its threads-and-queues model stands) and
D13's *palette surface* (its validation method stands). Owner direction:
a modern, fast workstation UI, explicitly accepting heavy dependencies.

- **Why Qt.** Profiling the Tk app found ~100–130 ms per matplotlib redraw
  (≈60% of it the constrained-layout solve) and every hidden view
  redrawing on resize. Fixable, but Tk's ceiling stays: no antialiased
  shapes, no animation, no GPU surface. Qt gives styled, antialiased
  widgets; pyqtgraph redraws a chart in milliseconds, so crosshair hover is
  ~1 ms per mouse move. Qt also hosts OpenGL and web views, which is the
  path to GPU 3-D surfaces later (explicitly deferred).
- **Dependencies:** PySide6, pyqtgraph, qtawesome (Phosphor/MDI icons).
  matplotlib stays for `visualization/` (tests and research figures).
- **Threading is unchanged.** `TaskRunner` and `EventBus` were already
  toolkit-free. A `QTimer` pump at 16 ms (one 60 Hz frame) replaces
  `root.after(50)`.
- **Composition root split.** `core.py` wires everything below the GUI
  once. `app.py` (Tk) and `app_qt.py` (Qt) each build their widgets on
  that core, so both UIs run the same services during the migration.
- **Migration is page by page, side by side.** The Qt UI is the default
  (`python -m quant_hub`, and the `quant-hub` launcher a desktop shortcut
  targets, so the shortcut always opens the current UI). The Tk UI stays
  reachable via `--classic` / `quant-hub-classic`. Unported pages show a
  placeholder pointing to it. When every page is ported, `gui/` (except the shared
  `task_runner`) and `app.py` are deleted. Tk and Qt cannot share a
  process, so a hybrid window is not possible.
- **Design system.** All tokens and the generated stylesheet live in
  `gui_qt/theme.py`. Widgets set a `role`/`variant` property, never colors
  or sizes. Pages are assembled from `gui_qt/components.py`. The navy
  surface was re-validated: series blue, orange and aqua clear all-pairs
  CVD ΔE ≥ 9.4 and contrast ≥ 3:1. Text and status inks were chosen by
  WCAG contrast (≥ 4.5:1).
- **Dashboard rebuilt around the local store** (`services/workstation.py`):
  KPIs, a market explorer, a sparkline watchlist, the store table, and a
  GBM forecast fan fitted to the explorer's symbol. The fan is labeled
  "illustrative" because one year of data gives μ̂ a standard error about
  as large as σ̂. Windows are anchored at the last stored bar, not today.
- **Layering:** the GUI never imports ibapi, the concrete broker, or the
  bar store. Qt libraries appear only in the GUI package and its
  composition root. Both rules are enforced by `tests/test_layering.py`.
- **Completed the same day (Q6).** Every page reached parity, so the Tk
  front end was deleted:
  - `gui_qt/` became `gui/`, and `app_qt.py` became `app.py`
  - the toolkit-free `TaskRunner` moved to `tasks.py`
  - `--classic` and `quant-hub-classic` are gone
  - the layering test now forbids tkinter everywhere

  `visualization/` (matplotlib) stays, for scripts, notebooks and tests;
  the app itself draws only with pyqtgraph. The Tk version remains in git
  history (`f7e4d33`). The bullets above use the names from during the
  migration.

## D32 — 3D implied-vol surface: Plotly in Qt WebEngine (2026-10-14)

Owner choice after a side-by-side prototype (Qt Graphs 3D vs Plotly; the
throwaway script is kept locally in the gitignored `spikes/`).

- **Why Plotly.** Hover readouts anywhere on the surface; quote markers and
  bid–ask whiskers are easy; a colour bar; the same figure format works in
  notebooks. Qt Graphs 3D was native and faster to first frame, but it
  needed Python to hold references to every object (the graph, axes,
  series and custom items, or the app segfaults), all render-to-texture
  widgets in a window to share one graphics API, and window flags set
  before show. The fragility wasn't worth it.
- **Dependency:** `plotly` is declared for its bundled `plotly.min.js`
  only. The Python side of plotly is never imported: `gui/surface3d.py`
  builds the figure as a plain dict and loads the local JS file, so nothing
  touches the network. Qt WebEngine ships with PySide6.
- **Cost, and how it's hidden.** Constructing the first web view costs
  ~0.7 s of GUI-thread time; Chromium then loads in other processes
  (~1.3 s) and the first WebGL draw takes ~0.5 s. The Volatility Lab
  builds the view when first shown, while its fit runs in the background.
  Later updates use `Plotly.react` (in place; the camera is kept through
  `uirevision`), not a reload. Chromium's harmless GPU log lines are
  silenced with `QTWEBENGINE_CHROMIUM_FLAGS=--log-level=3`
  (`gui/__init__.py`).
- **Math lives in `models/`.** `vol_surface.surface_mesh` interpolates
  total variance linearly in T at fixed k. That adds no calendar arbitrage
  of its own and holds ATM forward variance constant between expiries.
  It also checks g on every interpolated row, since a mix of
  butterfly-free smiles need not be butterfly-free. It never extrapolates
  in T, and it marks points where a nearer expiry's SVI wing is
  extrapolated in k. Derivation in the module docstring.
- **Scope:** the 3D view is a toggle inside the Volatility Lab's Surface
  view; the heatmap stays the default. Pyqtgraph remains the 2D charting
  layer (D31).

## D33 — v1.0 scope and its seven data/design calls (2026-10-15)

v1.0 adds four modules, in order C → M → F → P → R:
- option chains (C)
- Markov regimes (M)
- data foundations (F)
- Portfolio (P)
- Research and backtesting (R)

It stays read-only (D12); trades are placed by hand. Plan: ROADMAP.md. The
owner approved the recommendations:

1. **Option quotes:** C0 measures what the API returns. Never use
   regulatory snapshots (charged per request). Quotes stream briefly and
   self-cancel (D22). The owner decides on IBKR's OPRA bundle if delayed
   quotes are missing.
2. **Underlyings:** SPY and SPX. SPX options are European, so IV inversion
   is exact. For SPY (American), only out-of-the-money quotes are fit, with
   early exercise a documented limit.
3. **Carry:** each expiry's forward and discount factor come from
   put–call parity at near-the-money strikes (C − P = D(F − K)).
   `OptionChain` gains optional per-expiry forwards and discount factors.
4. **Chain snapshots are persisted** in SQLite. They're the only route to
   option history, since IBKR has none for expired contracts.
5. **Both price bases are stored:** TRADES (display, levels) and adjusted
   (return math), with the basis named on every call like the source (D23).
6. **Positions come from the owner's broker export first.** Amended by the
   owner: the portfolio is at Fidelity, so positions arrive by CSV import
   (D34); IBKR's read-only positions are an optional second adapter. Still
   no order code.
7. **Backtester:** daily bars, vectorized, with an explicit one-bar lag. A
   position decided at close t earns t → t+1, and that rule is tested.

**C0 findings (2026-10-15, TWS 7496, `spikes/spike_options.py`).**
- Delayed option quotes work with no option-data subscription (market
  data type 3, ticks 66/67, plus 10167/10090/10091 advisories, which D22
  already treats as non-failures). OPRA is only needed for real-time.
- SPY quotes: 40 contracts filled bid and ask in 2.5 s (median 1.5 s).
- SPX quotes: 20 contracts in 0.7 s.
- Options parameters: SPY has class SPY (34 expiries, 491 strikes); SPX
  has classes SPX (monthly, AM-settled) and SPXW (weeklies, 41 expiries).
  Both list a 0-DTE expiry, so selection needs a minimum DTE.
- IBKR's own model IV and greeks also arrive delayed (ticks 80–83): a
  free cross-check for our inversion.
- The delayed spot is a last-trade print whose time can differ from the
  option quotes'. The parity-implied forward (point 3) sidesteps spot
  entirely for moneyness.

## D34 — Provider-agnostic data: useful whatever you trade on (2026-10-15)

Owner direction: anyone should be able to clone the repo and find it useful,
whatever broker they use. So IBKR becomes one optional provider, not the
backbone.

- **Every acquired data kind is a port** whose shape lives in `data/`:
  - daily bars (already: `BarSource`, D20)
  - quotes
  - option chains (`OptionChain`, D30)
  - positions (new)

  Adapters fill the ports, and pages name their provider per call, as they
  already name sources (D23).
- **v1.0 adapters:**
  - synthetic (always available)
  - file import (CSV): universal, since every broker and data vendor
    exports CSV, with presets per format (Fidelity positions first) and a
    documented generic layout
  - IBKR (optional, live)

  A free online provider is a candidate only once its terms of use are
  checked.
- **No broker connection required.** The app is fully usable on synthetic
  data and imported files. Live-only features, like streaming quotes and
  live chains, say what they need instead of failing.
- **Settings, not hard-coded owner defaults.** A user settings file covers
  providers, IBKR host, port and client id, and the store path. It
  replaces `AppConfig`'s frozen defaults (currently the owner's live TWS
  port) as the source of truth.
- **Where adapters live:** file importers are infrastructure like
  `broker/`, so they get their own package, which may import only `data/`
  plus pandas and the stdlib. The layering test enforces it; the name is
  settled in F.

## D35 — Joint SSVI is an option, not the default (2026-10-15)

C3 implemented the reviewed joint SSVI fit (`models/ssvi_fit.py`): N + 3
parameters, and free of static arbitrage by construction for γ ∈ (0, ½]
and η(1+|ρ|) ≤ 2 (derived, and checked numerically).

- **Synthetic results (8 seeds):**
  - It recovers the SSVI truth exactly.
  - Under the D30 heavy-noise case it beats independent SVI on every seed:
    median IV error 1.12 → 0.69 vol points, worst ATM 4.62 → 1.55.
  - Arbitrage-free 8 of 8 times, against SVI's 3.
- **The search:** it profiles θ (golden section per expiry, pooling
  adjacent violators for calendar order) under a 3-D Nelder–Mead over
  (ρ, η, γ).
  - The first version, one Nelder–Mead over all N + 3 coordinates, stalled
    when a θ increment collapsed. It ended at χ² 430 when the truth scored
    331.
  - A test now requires the fit never to score worse than the truth.
- **Live SPY and SPX chains: it fits far worse than independent SVI.**
  - χ²/pt: SPY 1,015 against 7, SPX 28 against 0.6. SPY's 7-day ATM vol
    is 9.5% against 10.6%.
  - Fitting SSVI to each expiry alone barely helps, so the limit is the
    SSVI slice family (vertex position and curvature tied together), not
    the shared shape.
- **Decision:**
  - The Volatility Lab defaults to independent SVI, which passes the
    arbitrage checks on these chains.
  - SSVI is a one-click option (refitting the loaded chain) for noisy or
    thin chains.
  - If real chains turn out noisy enough to need regularized but flexible
    fits, the next step is SVI slices fitted under explicit calendar and
    butterfly constraints, started from the SSVI fit (Gatheral–Jacquier's
    own recommendation). That isn't built.

## D36 — Settings file, schema versions and backups (2026-10-15)

F1 puts D34's settings and the store's safety in place.

- **Settings:** `~/.quant_hub/settings.toml`, read with stdlib `tomllib`
  over `AppConfig`'s defaults (`config.load_config`).
  - `QUANT_HUB_SETTINGS` can name another file, for example a paper-trading
    setup.
  - The first run writes a commented template, so the file is easy to find.
  - It covers the store path and IBKR (enabled, host, port, client id). IBKR
    turned off registers no IBKR sources and hides its controls.
  - A bad file never stops the app. Each problem is logged (it shows in the
    status bar and log drawer), and that setting keeps its default. A typo
    shouldn't make a double-clicked app vanish.
  - The default port stays 7496, TWS's own live default (read-only API, no
    order code, D12). It isn't owner-specific; paper and Gateway users set
    theirs.
- **Schema versions:** one version for the whole store file, in a
  `schema_migrations` table (version, time, backup), checked by
  `storage/schema.prepare_store` before any store opens the file.
  - A table rather than `PRAGMA user_version`, because it keeps a history:
    when each step ran, and which backup preceded it.
  - Version 0 is every file from before versioning. 0 → 1 changes no table.
  - Steps run in order, each in its own transaction (SQLite's DDL is
    transactional). A failure rolls back and stops the app with the file
    at its last good version.
  - A file from a newer app version is refused, never written.
- **Backups:** before any migration, the whole file is copied into
  `backups/` beside it with SQLite's online backup API, named for its
  version and time. Backups are never deleted automatically: the store
  holds history IBKR won't serve again. The first run after F1 adopts
  every existing store at v1, so it leaves a backup of each.
- **Daily-only cache:** `CachedSource` rejects any bar size but "1 day".
  Its coverage rule (complete through yesterday) is right for daily bars
  only (DATA_SYSTEM_IMPROVEMENTS #7).

## D37 — File import stores once; "file" serves it (2026-10-15)

F2 makes CSV the universal provider (D34). Calls made:

- **Import, then serve from the store.** A file isn't fetched by date
  range, so it doesn't sit behind `CachedSource`: that would record
  "no sessions after the file ends" as covered, a lie its sync planning
  would then trust. `services/file_import.import_bar_file` validates each
  symbol's bars with the same `validate_ohlcv_frame` as IBKR's and upserts
  them. `FileSource` (the `file` source) serves them, with no coverage
  table. A window past a file's end holds fewer bars, and one with none
  says to import first.
  - The roadmap said "a CSV `BarSource`". The importer yields the same raw
    frame a `BarSource` returns, but no `fetch_bars`, which would be
    ceremony for a file.
- **One provider name, `file`**, for every import. A re-import replaces
  bars on the same dates (a corrected file wins). Imported SPY sits beside
  IBKR's SPY: a different source, never mixed.
- **All or nothing per file:** every symbol in it must validate before any
  is stored.
- **Layouts:** a documented generic layout (`date,open,high,low,close,
  volume`, optional `symbol`; Stooq's own), and presets detected from the
  header (Nasdaq.com, Yahoo Finance). Prices are stored as the vendor gives
  them. Basis is F3's.
- **Layering:** `importers/` reads files into `data/` types with pandas and
  stdlib only, and the GUI reaches it only through services.
- **No live quote** from a file: `FileSource.get_quote` raises
  `NoMarketDataError`, so the Options Lab falls back to the last close.
- **Explorer keys:** a symbol stored from more than one source shows as
  "SPY · file" on the Dashboard, so neither hides the other.

## D38 — Two price bases, traded and adjusted (2026-10-16)

F3 settles D33 point 5. A spike against TWS (`spikes/spike_adjusted.py`,
2026-10-15) changed the plan's premise:

- **IBKR's TRADES is already split-adjusted.** NVDA runs through its 10:1
  split. No IBKR series is raw, so "a split shows as a gap in raw bars"
  can't be tested on IBKR data, and DATA_SYSTEM_IMPROVEMENTS #1 had it
  wrong.
- **ADJUSTED_LAST is TRADES times a step factor.** It moves only on
  ex-dividend dates, by f = 1 − D/C_prev. The implied dividends match the
  real ones: SPY March 2025, $1.6959 against $1.6956. All four prices carry
  the factor; volume doesn't. The latest prices are equal in both.
- **Both rewrite history.** A dividend rescales every earlier adjusted
  price, and a split every earlier traded one. An append-only cache would
  mix scales and invent jumps.

Decided (the owner approved the proposal):

- **`PriceBasis.TRADED` / `ADJUSTED`.** `MarketService.get_history`
  requires the basis, as it does the source (D23). Every `PriceHistory`
  carries it, and adapters default to traded.
- **Storage:** the bars key includes the basis (schema v2, the first
  migration that changes a table; the backup runs first). Existing bars
  became traded.
- **Adjusted, cached whole:** the first adjusted request of a calendar day
  refetches the whole stored span and replaces it. Later syncs that day,
  traded or adjusted, fetch only what they add.
- **The split guard:** over completed sessions, adjusted/traded must be at
  most 1, never falling, and rise by at most 20% a day.
  - A stored traded series that fails straddles a split, so it is
    refetched whole and replaced, and logged.
  - A false alarm (a special dividend over 20%) costs one refetch.
- **Files declare their basis by column:** `close` is traded and
  `adj_close` adjusted (Yahoo's `Adj Close`). With both, the adjusted open,
  high and low use the day's adj/close factor. A request for a basis a file
  lacks says so.
- **Pages:**
  - GARCH, Kalman and Markov have a Prices choice (Adjusted by default).
  - The Options Lab's realized vol and the Volatility Lab's overlay use
    `get_return_history`: adjusted, or traded where an import has no
    adjusted prices, the history saying which.
  - Markets and the Dashboard show traded levels.

## D39 — Portfolio for v1: risk first, lean (2026-10-16)

The owner holds stocks and ETFs at Fidelity and uses options sparingly. They
asked for a fundamental-style portfolio page that leads with risk, broad
enough for any trader, shipped soon. So P is cut lean.

- **In v1:**
  - Positions from CSV: a generic layout, and a Fidelity preset, kept as
    dated snapshots.
  - A risk model (`models/portfolio.py`, derivation in its docstring):
    - vol, and Euler risk contributions (each holding's share of the risk
      beside its share of the value);
    - beta;
    - historical and Gaussian one-day VaR and ES;
    - the drawdown of today's mix over the lookback;
    - the effective number of holdings;
    - Kelly leverage with its standard error.
  - The Portfolio page, led by those numbers, and an example portfolio
    before any import (D34).
- **Deferred to v1.1:**
  - allocation optimizers (minimum variance, tangency, long-only) and the
    efficient frontier;
  - Ledoit–Wolf shrinkage;
  - IBKR read-only positions;
  - the what-if rebalance ticket.
- **Calls:**
  - **Weights come from the export's own values,** so they're the account's
    as of the file. The source's last close is the fallback.
  - **Options count by delta** in the covariance, beta, Gaussian VaR and
    drawdown, and are repriced in full in the one-day historical replay. That
    way a protective put's convexity shows where it matters most.
  - **An option's spot is the file's price for its underlying** when the
    file has one, so a snapshot is self-consistent.
  - **Holdings whose history won't load are left out of the risk model and
    named,** with their share of equity. Expired options are named too.
  - **Kelly is shown with its standard error,** 1/(σ√T): from a year or two
    of history it's noise, and saying so is the feature.
  - **Returns are adjusted** (D38), with traded as the fallback where an
    import has no adjusted prices, which is noted.

## D40 — Prepare WebEngine before showing the main window (2026-10-18)

Supersedes D32's timing of browser construction. On Windows, attaching the
first WebEngine widget to an already-visible window made Qt hide, destroy
and recreate the native window (~110 ms in the diagnostic run). The process
kept running, but the first Volatility Lab visit looked like a restart.

- `create_app` prepares the Volatility Lab, including `Surface3D`, before
  returning the main window for its first `show()`.
- `MainWindow.prepare_page` only constructs and attaches. It does not
  select the page or call `on_show`: chain acquisition and fitting still
  begin on the first visit, through the existing TaskRunner.
- The browser's construction cost moves to launch. Other pages still
  build on navigation; no extra browser or dummy graphics widget is needed.
- A separate first-visit flash came from `Card` showing its subtitle before
  parenting it. The layout now attaches the label before setting visibility.

## D41 — R1: self-financing daily research accounting (2026-10-18)

The owner authorized the next Research increment. R1 implements the engine
and its statistics; R2's strategies and R3's page remain later milestones.
Derivations precede the implementations in `models/backtest.py` and
`models/backtest_stats.py`.

- Targets are decisions at close t, held for t → t+1 (D33). The supplied
  target matrix is indexed by every close except the last; realized
  returns are indexed by the following closes. Closing-price execution is
  an idealized research convention. The final portfolio is marked, not
  automatically liquidated.
- Targets are fractions of equity **after transaction costs**. The exact
  self-financing scalar fee equation is solved across all dates together.
  Pre-trade weights include market drift, so maintaining a constant target
  can require a rebalance. Entry costs count; cash finances the remainder.
- Costs are half-spread plus commission in basis points per absolute
  notional traded. Turnover counts buys and sells, with no factor of 1/2.
  Cash uses an effective annual rate; borrowing uses that same rate.
  Default targets are nonnegative with gross exposure at most one. Shorts
  and higher gross exposure require explicit configuration. Insolvency and
  unaffordable liquidation fail the run; there are no margin/borrow models.
- Walk-forward fitting receives only its training slice. Predictions
  receive history through their decision close, with no future bars.
  The model callbacks are plain Python functions. Training/test block
  lengths count close observations; test blocks are disjoint and warm-up
  exposure is zero.
- Performance uses realized net simple returns. Sharpe SE/PSR/DSR use an
  asymptotic independent-observation approximation and say so in the API;
  they do not correct for serial dependence. Sortino uses the lower
  partial second moment over all observations.
- DSR requires all recorded trial Sharpes plus an explicit integer count
  of effectively independent trials. The engine does not infer independent
  trials from grid size or manufacture missing trial history.
- No new dependencies. The modules are source-agnostic, pure, and usable
  from scripts and tests. Data provenance and immutable run snapshots are
  R3's persistence responsibility.

## D42 — v1 Research workspace and local evidence (2026-10-19)

The owner's “ship v1” authorized finishing R2/R3, the lean paper catalog
and stabilization without further milestone stops. No new dependencies.

- Daily long/cash hypotheses: momentum, rolling log-price reversion,
  Kalman slope/level deviation, GARCH risk targeting and Markov calm-state
  filtering. Each model fits training prices strictly before its test
  block. Kalman test filters continue the training terminal state; Markov
  uses a fixed-parameter forward pass, never smoothed/Viterbi positions.
- Portfolio sizing uses homogeneity: scale a fixed equal-budget signal to
  the daily volatility target, bounded by gross exposure. Zero estimated
  variance means cash. GARCH log-return forecasts are transformed using
  the conditional lognormal variance before simple-return risk sizing.
- Test-only reporting, with a matched equal-weight buy-and-hold basket
  entered on the same close and charged the same costs. Training is for
  parameter estimation, not a second claimed performance sample. The
  initial entry is charged, and final holdings are marked without an exit.
- Immutable SQLite run payloads retain exact prices, decisions, fits,
  costs, results, hypothesis, seeds, source/basis and SHA-256 of source
  implementation (including uncommitted changes). Reopening verifies the
  evidence hash and replays accounting without data fetches or refitting;
  changed source is disclosed. Notes are separately editable. Schema v3
  adds two tables and uses the existing backup-before-migration mechanism.
- Grids vary lookback/risk window and threshold on common test dates;
  GARCH is run individually because it has no signal threshold. Every
  completed trial is stored, including losers; a manifest records the
  chosen trial and explicit effective independent-trial assumption. DSR
  is conditional on that recorded grid, assumes IID, and does not claim
  an untouched holdout or account for omitted earlier searches.
- A local paper catalog holds references and imported content-hash PDF
  copies beside the store, reading notes/status and experiment links.
  Five verified foundational source references seed it; no copyrighted PDF
  collection is bundled. SQLite and the PDF directory must both be backed
  up; schema backups intentionally remain database-only.
- Data loading, fitting, sweeps, replay, substantive saves and PDF import
  all run on the existing single TaskRunner. Widgets and launch-to-reader
  callbacks stay on the GUI thread. Per-view form state follows the
  existing small synchronous hide-time convention.
- Version 1.0.0 identifies the implemented research foundation. External
  acceptance of a real Fidelity export and a fresh adjusted-cache TWS run
  remains explicitly unverified; TWS was not open during this session.

## D43 — The personal workstation phase (2026-10-20)

The owner asked to bring their own trading regime and their DCF modeler
into the app, scoped to "something I would personally use over other
software tooling". They approved
[WORKSTATION_PLAN.md](WORKSTATION_PLAN.md) as proposed: milestones W0 →
W7 (ROADMAP.md).

- **The regime is a portfolio thesis:**
  - a core beta stream plus hedge and return streams orthogonal to it,
    minimizing vol to maximize CAGR
  - expressed through options and a long-term portfolio, on daily data,
    so D19 stands
  - the owner's numbers live in `REGIME.md`, a template in the repo; their
    own copy can stay in the gitignored `personal/`
- **Still read-only (D12, unchanged).** The owner raised deploying algos,
  then scoped it out. Execution would need its own decision amending D12,
  with paper trading first. This phase only reads accounts.
- **The DCF modeler is ported, not wrapped.**
  - edgartools, yfinance and openpyxl are dropped.
  - SEC data comes through the stdlib from SEC's `companyfacts` API. SEC's
    fair-access terms apply: a declared User-Agent from the settings file,
    at most 10 requests a second. That makes SEC the first online provider
    D34 allowed once terms were checked.
  - Prices come from the existing named sources (D23). Export is CSV only.
- **Amends D34:** fundamentals become a fifth port, with its shape in
  `data/`.
- **Amends D39:** IBKR read-only positions and aggregate option greeks
  move into this phase (W1, W3). Optimizers, the frontier, Ledoit–Wolf and
  the rebalance ticket stay deferred.
- **W0 finding (2026-10-21): the owner's real exports are another
  Fidelity layout.** They come from the Positions view, not the Portfolio
  Positions download the D39 preset was built for:
  - The layout: a preamble with the view's name and "as of" date; `Last` /
    `Value` / `Basis` columns; cash as `Cash (SPAXX)`; sometimes a `Totals`
    row.
  - There's no account column, so the rows of two accounts can't be told
    apart. Every row belongs to the view's account ("All Accounts"), and
    the risk model sums a symbol's rows, which is right for one combined
    portfolio.
  - A second preset reads it. Per-account risk needs the Portfolio
    Positions download, or one export per account.
  - One import may now hold several files as one snapshot
    (`data.positions.combine_snapshots`). The rules keep it one moment and
    count nothing twice:
    - the same as-of day
    - each account from one file only
    - every row with an account
    - no All Accounts export beside the accounts it already holds
  - The snapshot keeps the date only, as the other preset does (D19's
    naive dates).
  - The layout's option rows are unverified: no export so far held an
    option.
- **Settled at their milestones:**
  - regression conventions (W2)
  - growth conventions (W3)
  - the DCF conventions the plan raised (W4): reverse-DCF roots, the
    terminal cash flow, the valuation date, net debt
  - the name and layering rule of the online-provider package (W5)
  - rule-set versioning (W7)

## D44 — W2: regression conventions (2026-10-21)

The owner approved the derivation in `models/regression.py` ("continue
on"). The implementation and the Portfolio page's regressions follow it.
Calls made:

- **Returns:** daily simple returns of adjusted closes, on the risk model's
  own common days.
- **Excess over cash at r/252 a day.** That's the Portfolio page's
  arithmetic convention, the one its Kelly uses. The backtester compounds
  instead, (1+r)^(1/252) − 1. At 4% the two differ by about 0.03
  percentage points of α a year.
- **Errors:**
  - Newey–West is the default: Bartlett weights, the plug-in lag
    ⌊4(T/100)^{2/9}⌋, and the T/(T − p) small-sample factor (also used
    for White's).
  - Classical and White are kept beside it.
- **The portfolio row:**
  - It's the replay's P&L over equity, with cash earning the rate.
  - For stocks, its coefficients are then exactly the weighted
    holdings', and its β equals the Beta tile's (tested).
- **Stale prices:** a Lagged β choice (Off / 1 day) turns β into the
  Dimson sum, and shows the lag's own coefficient, amber when
  significant.
- **Rolling β:**
  - 126 sessions, causal, with ±1.96 Newey–West bands, for the clicked
    row.
  - No β = 1 reference line: it pulled the axis away from the data.
- **Residual links:**
  - A table of pairs, strongest first, amber where the Fisher interval
    excludes zero, with the raw correlation beside each.
  - This replaces the heatmap the derivation sketched. A correlation
    matrix is categories on both axes with signed values, and
    `charts.Heatmap` has a numeric x and a sequential ramp. A table
    shows the numbers and needs no new design token.
  - A holding that is the benchmark itself fits exactly (R² = 1 to
    rounding). Its residual is rounding noise, so its links are "—".
- **Deferred within W2:** extra factor ETFs on the page (the model
  already takes them), and factor spreads as tilts.
- **Amended the same day: extra factors landed.**
  - An *Extra factors* field takes up to three ETFs. The benchmark and
    repeats are dropped, and pasted text is uppercased.
  - Their betas are partial: each holds the others fixed.
  - Factor returns stay apart from the risk model's. A factor with
    shorter history shortens the regressions' sample, never the VaR's,
    and a factor without history is named and left out.
  - The table split in two, so neither outgrows 1280 px:
    - the main table: α, β to the benchmark, R², residual vol
    - "Other exposures": each extra factor's β, and the lag β, shown only
      when there are any
  - The Days column became the subtitle's day count, since every row
    shares it.
  - `DataTable.set_columns` lets a table's columns follow the data.
  - Factor spreads stay deferred. By the invariance in the derivation,
    they'd change only what the coefficients mean, not the fit.

## D45 — W3: growth and streams (2026-10-21)

The owner approved the derivation in `models/growth.py`. The model and the
Portfolio page's Growth and Options book cards follow it. Calls made:

- **From the replay, position by position.** `risk_report` keeps each
  position's daily P&L (options repriced in full, as before), its value
  and its underlying. Those series sum to the replay.
  - The excess series nets cash at r/252 on each position's value share.
  - An option's value share is the account's own premium from the export,
    when there is one, not the model's. That keeps weights consistent with
    equity: a file's premium and the model's can differ widely.
- **Growth:** g = r + 252(mean − ½ var) of the portfolio's excess series,
  with realized log growth shown beside it. Variances use ddof = 1, as
  `np.cov` does in the risk model.
  - For stocks alone, g equals the Sizing card's delta-based growth
    exactly (tested).
  - With options the two differ, so the Sizing card now shows W3's g when
    there is one.
- **Attribution:** exact, the mean less half the covariance with the
  portfolio.
- **Verdicts:** contribution and margin give four outcomes, not the
  derivation's three:
  - *adds*
  - *oversized*
  - *undersized*: costs growth today, but more would help, e.g. a cheap
    hedge held too small
  - *costs*

  A verdict is grey when its margin is within 1.96 Newey–West errors of
  zero.
- **Sleeves:** each underlying, i.e. its shares plus its options, until
  W7 stores the owner's own. `growth_report` takes any grouping.
- **Sizes:** both are shown in multiples of today's size.
  - The min-variance size comes with its regression's Newey–West error,
    and the page lists it first.
  - The growth-optimal size is shown muted, with its error. In the tested
    hedge world its error is more than ten times the min-variance size's,
    and the derivation's simulation found about 140 times.
  - A sleeve with no variance has no sizes (NaN), rather than failing the
    report.
- **What-if:** an exact quadratic within the replay. It's computed on the
  GUI thread as a pure model call (D26), and is read-only: no ticket, no
  order (D12).
- **Book greeks:** signed sums in D25 units (dollar delta, gamma P&L of a
  1% move, vega per vol point, theta per day), plus annual carry = 365 ×
  theta.
  - The Options book card appears only when the book holds options.
  - Its five tiles sit in two rows: in one row they truncated at 1280 px.
- **Not included:** transaction costs and taxes in the what-if; a smile
  for the greeks (realized vol, as in the risk model).

## D46 — W4: the DCF port (2026-10-21)

The owner's `dcfmodeler` is ported into the layers.
- `schema.py` becomes `data/fundamentals.py`: the contract, with its
  loud-failure rule, plus a `FundamentalsSource` port (D34) and a
  `marketable_securities` line.
- The engine, assumptions, sensitivity, analysis and the batch screener's
  seeding become `models/dcf.py`.

The math is the plan's (D43). Calls made:

- **Conventions the plan left open are options, defaulting to the owner's
  original behavior.** The golden 18.784 holds, and nothing changes meaning
  until the owner picks.
  - `terminal_method`: *last-year*, UFCF_N(1+g_T) (the default), or
    *steady-state*, year N's ratios at g_T. They're equal iff g_N = g_T.
    At 15% growth the steady-state flow is 7.9% higher, with the TV at 78%
    of EV.
  - `valuation_years` s ∈ [0, 1): discount over t − s, so EV is multiplied
    by (1+W)^s. s = 0 values as of the fiscal year end, the original. s ≥ 1
    is refused: use a newer filing.
  - `net_marketable_securities`: off by default. When on and the filing
    lacks the line, it's a loud `ValuationError`, never a silent zero.
  - Recommended, for the owner to decide at W6:
    - steady-state as the page's default: a 78% TV share is too much
      weight to leave on a working-capital artifact
    - s from the filing's period end to the price date, so value and price
      are compared at the same time
    - netting securities per company, for cash-rich filers
- **Reverse DCF returns every root** (a tuple, empty when none is in
  range), instead of one float or None.
  - `growth_monotone` (k_t ≥ n_t ≥ 0 every year) says when the implied
    growth is unique.
  - `cash_flows_positive` says when the implied WACC is.
  - The working-capital-heavy case's two roots (−14.9% and +15.9% at $18)
    are tested.
- **A bug in the original, fixed.** `FinancialHistory.latest()` returned the
  last element appended. The edgartools adapter appended the newest year
  first, so live runs valued the company off its *oldest* fetched year.
  - Debt and cash were attached to that year too.
  - Example: three years at 800 → 1,000 gave $18.12 a share instead of
    $23.15 (22% low).
  - The original tests built histories oldest-first, so they missed it.
  - `latest()` now sorts by fiscal year, with a regression test. Past
    valuations of growing companies from the original tool were likely
    biased low.
- **Typing:**
  - Dicts became frozen dataclasses (`ForwardMultiples`, `Calibration`,
    results).
  - The sensitivity grid uses NaN, not None.
  - Missing data raises once instead of blanking every cell.
  - A market price must be positive.
- **Moved:**
  - The edgartools adapter is replaced in W5, and its tests' intent goes
    with it.
  - `serialize`, `rank_screen` and their tests go to W6 (CSV only).
  - The CLI and text renderers aren't ported.
- **Defaults** (WACC 9%, g_T 2.5%, margins) are the owner's illustrative
  numbers, not recommendations.

## D47 — W5: SEC fundamentals, stdlib only (2026-10-21)

D43 approved SEC's `companyfacts` API through the stdlib, in place of
edgartools. W5 builds it. Calls made:

- **A new package, `providers/`,** mirrors `importers/`: it fetches into
  `data/` types with the stdlib only.
  - The layering test enforces it. The GUI may not import it or the fact
    store: it goes through `services/fundamentals.py`.
  - `providers/sec_edgar.py` has three parts:
    - a client that only fetches
    - a flattener to `data.fundamentals.Fact`
    - a pure mapping to `FinancialHistory`
- **The fair-access policy:**
  - Every request declares `[sec] identity`, "Name email", from the
    settings file. The template documents it, and no identity ships with
    the code.
  - Requests are spaced at least 0.1 s apart, gzip is accepted, and
    timeouts are 30 s.
  - A 403 says to set the identity, and a 404 says the filer has no XBRL
    facts.
- **Facts are stored as filed** (schema v4, after D36's backup): concept,
  unit, period, value, form, filing date, accession.
  - Only the concepts the mapping reads are kept, so the store stays
    small.
  - A refetch replaces a filer's facts whole.
  - The mapping runs on stored facts, so a rule fix applies without
    refetching.
  - The ticker map is stored too, so tickers resolve offline.
- **Mapping rules** (in the module docstring):
  - **Which facts:** annual forms (10-K, 20-F, 40-F, and amendments);
    flows spanning 350 to 380 days; instants at the year end.
  - **Restatements:** the latest-filed fact per concept and period wins.
  - **Tags:** priority lists followed year by year, so a filer's tag
    change is tracked.
  - **Fiscal years:** labelled by the year they end in, with first-week-of-
    January ends labelled the year before so 52/53-week filers keep unique
    labels.
  - **Debt:** non-overlapping parts.
    - The current part is `DebtCurrent`, else commercial paper +
      short-term borrowings + current long-term debt.
    - The noncurrent part is `LongTermDebtNoncurrent`, else `LongTermDebt`
      less its current portion.
    - The original summed every tag present, which double-counts current
      maturities beside a `LongTermDebt` total.
  - **Shares:** weighted-average diluted shares, else the cover page's,
    labelled basic.
  - **Currency:** revenue's unit, USD preferred.
  - **Gaps:** every one is a reason in `missing`.
- **Refresh:** stored facts serve for a day, and Refresh fetches at once.
  The plan said "when a newer filing exists". Checking that would cost a
  second request per lookup (the submissions API), while filings arrive
  only a few times a quarter. A failed refresh serves the stored copy and
  says so. With stored facts, no identity is needed.
- **Not yet:**
  - IFRS filers (`ifrs-full`, many 20-Fs) come back with no annual facts,
    named as such.
  - The live check against real 10-Ks (AAPL and two others, the original
    README's checklist) needs the owner's identity. Until then the mapping
    is verified only on fixtures in the API's shape.

## D48 — W6: the Valuation page (2026-10-21)

The owner's DCF modeler is usable in the app: a Valuation page in the core
section, after Portfolio, on `services/valuation.py`. Calls made:

- **The flow:**
  - a ticker or CIK loads the filings (W5); Refresh refetches
  - assumptions seed from the company's own trailing averages (the
    original screener's base case) and stay editable, as one number or
    one per year
  - the DCF (W4) recalculates as you type: a debounced, pure model call on
    the GUI thread (D26)
  - fetches run on the TaskRunner: filings, prices, the screen, saves
- **Conventions on the page** (D46's options):
  - Terminal flow: Last year (original) or Steady state
  - Value as of: Fiscal year end or Today. Today uses s from the period
    end, and a filing over a year old falls back to its year end, with
    the reason.
  - Marketable securities: Not netted or Netted
  - The defaults stay the original's until the owner picks.
- **What it shows:**
  - tiles: value per share, price, margin of safety, and the terminal
    share of EV, red above 75%
  - the filings table, with a Trailing row that seeds the assumptions
  - the EV bridge and the projection, with its terminal row
  - the WACC × terminal-growth grid, ±2 and ±1 points, as the original
    CLI had
  - what the price implies: every root of the reverse DCF, and whether
    it's unique
- **Price:**
  - From the named source: its quote, else its last close (the Options
    Lab's rule, D23), or typed by hand.
  - The basis is shown and saved.
- **The screen:**
  - Holdings (the latest positions import) or typed tickers, each seeded
    from its own filings.
  - The page's WACC, terminal growth, working capital and conventions
    apply. Working capital joined because filings can't give it; the
    original screener assumed none.
  - Ranked cheapest first, with failures kept as rows giving their reason.
  - Exported as CSV only. The original `rank_screen` test is ported.
- **Saved valuations** (schema v5, after the backup):
  - Immutable records of the assumptions, the price and its basis, the
    result, the implied roots, and a SHA-256 fingerprint of the filing
    data.
  - Reopening restores the assumptions against today's filings and says
    whether they changed since (a restatement, a new 10-K). Notes are
    editable in the store; not yet on the page.
- **Layout:**
  - At 1280 px the first version overflowed. The Screen card's field and
    three buttons on one row set a 1,178 px minimum; two rows bring it to
    871 px in a 1,038 px viewport.
  - Wrapped text in a ScrollPage's main column squeezed the cards below
    it. The body opts out of height-for-width, so a wrapped note's real
    height isn't in the page minimum.
    - The Filings sources note took 68 px from the Valuation card and hid
      the projection's terminal row.
    - The provenance and gap notes moved to the fixed-width side panel,
      and the reverse-DCF sentences became a table. AGENTS.md records the
      trap.
- **Not live yet:** real companies need `[sec] identity` (D47). The page
  and its tests run on a made-up filer in the store.

## D49 — W7: the regime, checked; the Review page (2026-10-23)

The owner's `REGIME.md` became rules the app checks at every review. The
derivation (`models/regime.py`) was reviewed and approved before the code.
Calls made:

- **The checks** (`models/regime.py`):
  - An estimated measure breaches when it's past its limit by more than
    k standard errors, with k the owner's per rule (`by k SE`; k = 0 is
    the estimate alone). Past the limit by less is *near*: shown, not
    graded. On the boundary, noise breaches 1 − Φ(k) of reviews; the tests
    check that by simulation.
  - A correlation limit applies on Fisher's scale, so at k = 1.96 it
    agrees with W2's interval exactly.
  - Persistence (section 1) counts reviews in a row, one per day, and is
    a delay, not a filter. Daily reviews of a two-year window share
    499/500 of their data, so noise persists; k is what filters it.
  - One day dominating a holding's variance: D = max r² / Σ r², flagged
    above the 99.9% point of an iid t(3) null. The calibration
    (`DOMINANCE_NULL`) is 100,000 runs per T on a grid from 60 to 2,500
    days, seed 20261023; `calibrate_dominance()` reproduces it, and a test
    reproduces its first row exactly. The Gaussian null (0.045 at T = 500)
    is far too thin: SPY's own D over two years is 0.20.
  - Gates: the HMM's filtered calm probability, whose history is never
    smoothed or Viterbi (both look ahead), its odds αP^h, and GARCH's
    closed-form average variance. A test checks the GARCH number against
    a Monte Carlo run of the recursion.
  - A reverse DCF with several roots is *ambiguous*, never chosen between.
    A non-positive trailing CAGR makes "c × CAGR" no limit, so that check
    reports no data.
- **The rules' source.** The repository is public, so the filled-in copy
  lives at `~/.quant_hub/REGIME.md` (`[regime] path`). The template stays
  blank.
  - `importers/regime_md.py` recognizes rows by their first column. An
    unknown label is an error, so a renamed row can't silently turn its
    rule off. Every unreadable value names its line.
  - Each distinct text is stored once, by SHA-256, as a version, and a
    review names the version it ran under.
  - The template gained what parsing needed: units in section 7, the
    `by k SE` convention, a grade line for sleeve sizes, the dominant-day
    choice, and section 1's N.
- **The review** (`services/review.py`), one TaskRunner job:
  - **Positions.** The newest export in `[positions] folder`, by the date
    inside each file. Files that aren't positions are named, not fatal.
    Without a folder, the newest imported snapshot. The export before
    drafts the journal's trade entries.
  - **Settings.** Section 2's documented defaults apply (SPY, 730 days).
    A blank cash rate or history source stops the run, naming the row.
  - **The book.** `PortfolioService.analyse` gained `sleeves` (growth by
    the owner's sleeves) and `exclude` (days dropped from every return
    series), and returns its return frame.
  - **Streams.** Each sleeve's own return is W3's excess over its value
    share, plus cash. Stream β and the residual correlations (stream with
    stream, stream with core) come from W2's OLS on the benchmark.
  - **Section 8** re-runs the owner's newest saved valuation of each name
    on today's filings and price. A name without one is reported, not
    guessed: assumptions are the owner's to set.
  - **Missing history.** It's fetched through the named source. The review
    never connects a broker on the owner's behalf.
  - **Cost.** Only measures some rule reads are computed.
  - **No orders.** Trades are recorded, never sent (D12).
- **Storage**, schema v6 after the backup: `regime_versions`, `reviews`
  (immutable payloads; notes editable) and `trades`.
- **The page** sits after Dashboard, as the plan's front door.
  - The tiles, then the cards: checklist, thesis by stream, market state
    with the calm probability's filtered history, one-day dominance, the
    journal, and past reviews.
  - The side panel holds the rules and folder, the owner's indicator tick
    boxes, and the trade form.
- **Layout at 1280 px:**
  - The first checklist (six columns) overflowed its card:
    `DataTable.balance_columns` never shrinks a column below its natural
    width. It became four: a short name with its limit, what it's on, the
    value, and a result that carries how far past it is in SE, and why
    when there's no data. Long columns are pinned with `fix_column`.
  - Wrapped notes in the side panel lost their last line, because the page
    body ignores height-for-width. `Note` now takes its width and sets its
    own minimum height. AGENTS.md records both.
  - The content's minimum is 775 px in a 1,038 px viewport, and no card is
    below its minimum height.
- **Not yet:**
  - The owner's values for sections 3–10. The review runs with whatever
    is filled in.
  - A live run on the owner's real book.
  - Fetching the cash rate from FRED, which would be a new provider (for
    discussion).
  - Per-account rules: the All Accounts export can't tell accounts apart.

## D50 — V0: data you can trust: offline reads and influence flags (2026-10-24)

The owner approved V0 of [LENS_PLAN.md](LENS_PLAN.md), a proposal of the
same day. It followed two findings on the Portfolio page:
- With TWS closed, the page failed, though the store held every bar
  through the day before.
- One session of one small holding carried most of the replayed mix's
  variance, and nothing flagged it.

The rest of LENS_PLAN (its order, V1's design, the new providers) still
waits on the owner.

**Offline reads (amends D18's failure policy).**
- A port-level `SourceOfflineError` (`data/sources.py`) means "can't be
  reached". It says nothing about the data. The broker's
  `NotConnectedError` is one; future providers raise it for "no network".
- On it, `CachedSource` serves the stored bars instead of failing, under
  three rules:
  - **One stretch.** The covered ranges inside the request must form one
    stretch. A gap that holds no weekday doesn't count, but a real
    interior gap still fails: serving across it would turn several
    sessions into one return.
  - **Covered dates only.** A provisional same-day bar that an earlier sync
    stored is left out.
  - **Adjusted bars** are served through the day before their last
    refresh, since that day's own bar was provisional. If none are stored,
    it raises `BasisUnavailableError`, and return math falls back to
    traded.
- Every other failure still fails the whole request, as D18 says.

**Never silent.**
- `PriceHistory.offline_reason` says why, and the frame ends where the
  store does.
- Portfolio says through which day it has data, and how many weekday
  sessions are missing since (`data.ranges.weekdays_missed`). Weekends
  don't count; holidays do, which errs toward "stale". With none missing
  it's an info note, otherwise a warning.
- The GARCH, Kalman and Markov labs add "offline, stored bars through …"
  to their run labels.
- Markets' Sync 1Y reports an offline sync as failed, with what the store
  holds.
- Research runs record `offline` in their provenance.

**Influence** (`models/portfolio.py`, derivation in its docstring):
- **Each day's share** of the mix's variance.
- **A robust z** from the median and 1.4826·MAD, which one huge day can't
  inflate. The sample sd stands in when the MAD is 0.
- **A flag at |z| > 10** (`INFLUENCE_Z`). The plan's starting point was 6,
  but measured, 6 is too many to read:
  - On two years of Student-t noise (ν = 4 and 3), 6 flags 1.4–3.2 days,
    and it flags four SPY days in SPY's last two years.
  - 10 flags 0.2–0.7 days on the same noise, and one SPY day (Apr 9, 2025).
- **Each flagged day names its driver:** the holding with the largest
  dollar move.
- **A "without" report.** The whole report is recomputed without the
  flagged days. It sits beside the headline, never instead of it, as a
  sensitivity, not a better estimate.

**Histogram clip.**
- `Histogram.set_data(clip=…)` draws a range and counts the values beyond
  it in the readout. Densities stay per all values.
- Portfolio clips to the range of the unflagged days.

**On the owner's portfolio (2026-10-24, TWS closed):**
- The page runs on stored bars through the last completed session.
- It flags one day. Without that day, the annual vol falls by almost half,
  and the largest risk share moves to a different holding.
- VaR and ES don't change: the day was a gain.

## D52 — Execution, paper first: amends D12 and hard rule 1 (2026-10-25)

The owner approved [EXECUTION_PLAN.md](EXECUTION_PLAN.md) on 2026-10-25:
- the execution track E0–E6
- this amendment
- timing (b)
- a dedicated account

This entry is E0. It contains no order code. Its parts are this decision,
the rewritten hard rule, the settings and journal designs in the plan, and
two layering checks that enforce the rule from today.

**What changes:**
- D12 said no order code anywhere. Now there is one guarded path for
  orders, and nowhere else:
  - `broker/orders.py` is the only module that calls `placeOrder`,
    `cancelOrder` or `reqGlobalCancel`.
  - Only `services/execution.py` (and the composition root) import it.
  - The runner and the GUI's Orders page reach orders through that
    service.
- `tests/test_layering.py` checks both statically, comments and docstrings
  included. Neither module exists yet, so today it asserts that no order
  call exists anywhere.
- AGENTS.md and CLAUDE.md carry the new hard rule 1.

**What stays from D12:**
- Everything outside that path is read-only: the pages, the models, the
  Review page and every other service.
- Nothing reconnects automatically. A dropped connection stops a run, and
  the next run reconciles.
- Nothing retries a send. A send that may have reached IBKR is matched by
  its `orderRef` before anything else happens.
- There is no login automation, and no stored IBKR credentials. TWS or IB
  Gateway is logged in by hand, and IBKR's weekly re-login is the owner's.

**Settled choices:**
- **Timing (b).**
  - Decide after the close of session t, from complete stored bars.
  - Send market-on-close orders for session t+1.
  - Every live decision can be replayed exactly from the store.
  - Research gains an `execution_lag` option, at E4, so backtests show
    what the lag costs.
- **Dedicated accounts:** a paper account, then a live account, both
  separate from hand-held positions. A run refuses any connected account
  but the configured one.
- **Scope:** US stocks and ETFs, long or cash. No shorts, options or
  leverage.
- **Every deployed algo is a saved Research run,** matched by its
  fingerprint.
- **Fail closed:**
  - an unset money limit means nothing is sent
  - so does an unreachable account, an unexpected account, or a failed
    check
  - a halt is cleared only by hand
- **Modes:** `off` by default. `paper` from E2. `live` needs its own
  decision entry at E6.

**Still the owner's:**
- the money limits (the plan proposes the rest of the defaults)
- the paper length
- where E0–E1 sit against LENS_PLAN
- confirming a paper login linked to the account (E1 needs it)
- the `ibapi` 10.x upgrade, confirmed at E1's start

## D53 — One rule for "one day decides the numbers" (2026-10-25; amends D49 and D50)

D49 (W7, Review) and D50 (V0, Portfolio) landed a day apart, with two
answers to the same question.
- W7 used D = max r² / Σ r² per holding, flagged above the 99.9% point of
  an iid t(3) null.
- V0 used the mix's robust z, flagged at |z| > 10.

The owner compared them on the same data and approved one rule
(2026-10-25). (D51 is reserved for LENS_PLAN's phase; D52 and D54 are
the execution track's.)

**What each flagged on the same data:**
- SPY's Apr 9, 2025 (+10.5%, z = 14.8, 20% of the variance): V0 yes, W7 no.
- A +60% day in a 30% holding: V0 yes; W7 no (D = 0.67).
- Two +500% days in one holding: V0 both; W7 neither. Each carries half
  the sum of squares, so the two hide each other.
- The owner's book: both flagged the same single day, V0 in the mix and
  W7 in the small holding behind it.
- Noise: on SPY-like GARCH-t(5) series of 500 days, V0 flagged something
  in 17%. W7 never fired: it needs a 59σ day, and V0 a 10σ one.
- D49 called SPY's D of 0.20 normal for equities. D50 cited the same day
  as a correct flag. One definition can't keep both.

**The rule** (`models/portfolio.py`, derivation in its docstring):
- **The robust share.** ŝ_t = z_t² / (z_t² + n − 1) is the share of the
  variance a day would carry if every other day were ordinary. It's W7's
  closed form x² / (x² + (n−1)σ²), with V0's robust x and σ (median and
  1.4826·MAD). Unlike a share of the sum of squares, other big days can't
  dilute it.
- **The flag is a size, not a surprise.** A day decides the series when
  ŝ_t > ½ ⇔ |z_t| > √(n − 1) (`INFLUENCE_SHARE`): its squared move
  outweighs the ordinary variance of all the other days together. That's
  REGIME.md's "most of the variance".
  - When the other days are ordinary, removing a flagged day at least
    halves the variance and moves the mean by about one standard error.
  - The z it takes grows with the sample: 15.8 at 250 days, 22.3 at 500,
    31.6 at 1,000. A longer sample is harder to outweigh.
- **Gone:** `INFLUENCE_Z` and W7's calibrated null (`DOMINANCE_NULL`,
  `calibrate_dominance`, `Dominance`).
- **Noise rates are measured, not set.** At 500 days a series flags with
  these probabilities:
  - t(4) 0.7%, t(3) 6.8%
  - SPY-like GARCH-t(5) 0.9%
  - the same with a quarterly earnings move of 4 (6) daily sds:
    1.4% (8.4%)

  A test reproduces the t(3) rate.
- **The reported share stays V0's,** about the mean, which is exactly
  what leaves with the day:
  Σ_{s≠t}(v_s − v̄_{−t})² = Σd² − n/(n−1)·d_t². W7's raw share differs
  by under 0.3 points in every case measured.
- **Both levels.** A day is flagged when it decides the mix or any
  holding's own series, and it records which (`InfluentialDay.holdings`).
  The mix decides σ_p, VaR, ES, Kelly and β_p. A holding's series decides
  its own vol, β and risk share, and its sleeve's stream numbers. A +60%
  day in a 30% holding carries 67% of the holding's variance but only 38%
  of the mix's.

**One function, read by both pages:**
- `risk_report(...).influence` computes the flags. Portfolio shows them
  and the WITHOUT report beside the headline.
- The Review reads the same object from its analysis, and removes those
  days for the second analysis. A test checks that the graded numbers
  equal Portfolio's WITHOUT. Section 4 still chooses which analysis
  grades.
- The Review's card became "Days that decide the numbers": one row per
  series a day decides (the mix, then each holding), with the share and
  the robust share.
- The journal payload keeps `influence` in place of `dominance`. Older
  payloads are left as they were.
- REGIME.md section 4's sentence names the mix as well as holdings, and
  `models/portfolio.py`. The parser reads only the choice.

**What changes for the owner:**
- SPY's Apr 9, 2025 is no longer flagged by itself (ŝ = 0.31). The
  Portfolio card still names it as the largest day, with its share.
- The owner's real day stays flagged (the mix's ŝ = 0.78, the holding's
  0.996). It's the only flag across the mix and every holding. V0's
  z > 10 applied to each holding would have raised several more.

## D54 — E1: accounts read from IBKR, read-only (2026-10-25)

The execution track's first code milestone (D52). It absorbs W1 (D43). The
number skips D53, which the unify-day-flags work holds.

**The broker reads accounts.** `IBKRBroker` gains three read-only methods,
on the `Broker` protocol too:
- `get_managed_accounts`: the list TWS sends on connect, asked for again if
  it's missing.
- `get_positions`: `reqPositions`, read once.
- `get_account_summary`: `reqAccountSummary` over "All", for net
  liquidation, cash and position value.

Both requests are subscriptions in the API, and each is cancelled as soon
as its first full answer is in, including after a timeout or an error.
Positions carry no request id, so:
- one positions read runs at a time
- its slot sits in the pending map under a key no request id can take,
  so a dropped connection fails it like any other
- updates that arrive after the read are ignored

**`ibapi` 9.81 and 10.x.**
- TWS API 10.33 inserted `errorTime` before `errorCode` in `error()`. The
  old wrapper read the first argument as the code, so on 10.33 it would
  have read a timestamp instead. That would have broken every rule keyed on
  codes: not-connected, no-data, pacing and the market-data advisories.
- The installed wrapper's own signature now decides (`_ERROR_HAS_TIME`),
  with tests for both forms.
- Quantities, which are `Decimal` in 10.x, go through `float()`.
- The connect log names the `ibapi` version.

**The snapshot** (`services/accounts.py`):
- Stocks and ETFs become equity positions. Their cost basis is IBKR's
  per-share average cost times the shares.
- US equity options (multiplier 100) become option positions. Their cost
  basis is the per-contract average cost times the contracts.
- Cash comes from each account's `TotalCashValue`.
- Futures, forex, bonds, funds, other option multipliers, and anything not
  in USD are left out and named. The risk model prices US instruments in
  dollars.
- Closed positions (zero) are dropped.
- The snapshot carries no prices. The portfolio service values each
  holding at the source's last close, as it does for imports without
  prices.
- It's dated today (D19), source "IBKR", and is kept by
  `PortfolioService.keep` like a file import.
- The notes on what was left out show on the Portfolio page for that read.
  They aren't stored.

**The page.**
- Portfolio's **From IBKR** button appears only when IBKR is on in
  settings.
- It reads, keeps and analyses the snapshot, and states the accounts' net
  liquidation.
- Without a connection, it says to press Connect.

**Not done here:**
- Installing `ibapi` 10.x needs IBKR's license accepted on their site, so
  the owner does it. README has the steps.
- The live read-only check, on paper and then live: `spikes/e1_check.py`,
  local, masks ids and amounts by default. It waits on the owner's paper
  login and TWS.

## D55 — G1: pages dock; cards stay in their page (2026-10-27; amends D24)

Proposed 2026-10-27. Approved 2026-10-29, when the owner said to carry on
until done, and G1 landed the same day. What building it settled is at the
end, under "As built".

The owner asked whether everything could be widget-based: drag and drop,
and snappy. D24 deferred the configurable-widget system, drag-and-drop and
layout persistence on purpose. This lifts the deferral for whole pages.
The Dashboard's widget system stays deferred.

**The unit is a page, not a card.**
- Each page becomes one dock. Pages can sit side by side, tab together or
  float onto another monitor, and the layout is saved.
- Cards stay where their page puts them, for two reasons:
  - a page reads top to bottom as one argument (Sizing's subtitle is
    "read the error first", and the error is there)
  - one TaskRunner job fills a page, so every number on it describes the
    same picture (Portfolio's rule). Cards scattered across the screen
    would each need an owner for the inputs that job uses.
- G2 (the Dashboard as panels) and G3 (panels from every page, with linked
  inputs) are not part of this.

**Qt's own docks; no new dependency.**
- A nested `QMainWindow` (a new `gui/workspace.py`) replaces the page
  `QStackedWidget`. Each page sits in a `QDockWidget` named after it. The
  sidebar, top bar, log drawer and status bar stay where they are.
- Rejected:
  - Qt Advanced Docking System: better drop targets, but a new dependency
    whose Python bindings are built for specific PySide6 versions
  - `QMdiArea`: overlapping windows inside the window
- Dock title bars are a custom widget from `gui/components.py`, styled
  through `gui/theme.py`. QSS reaches only part of Qt's own title bar.

**`on_show`/`on_hide` mean visible, not selected.**
- They follow each dock's visibility, so several pages can be shown at
  once. Switching tabs hides one page and shows another, as navigation
  does today.
- Dragging a dock can hide and show it more than once. The workspace
  settles visibility one event-loop turn later, so a re-dock doesn't rerun
  the Dashboard's refresh or the Review's rule load.
- At shutdown every visible page gets `on_hide`, not only the active one,
  so each saves its state.
- The active page (the top bar's title, the sidebar's highlight) is the
  one that last had focus. The sidebar raises a page's dock, and opens a
  closed one tabbed with the active page.
- `Shell.navigate` and `MainWindow.show_page` keep their signatures.

**A page is never narrower than its content.**
- `ScrollPage` turns horizontal scrolling off, so a page narrower than its
  body would clip its right edge. Since 2026-10-29 a page asks for its
  content's width and the window's minimum follows (before, seven pages
  clipped at the old fixed 1100 px minimum). Docks keep that: a dock
  never squeezes a page below its content.
- The minimums run from 702 px (Research) to 1,014 px (Options Lab), with
  Windows fonts.
- On the owner's 2560 px monitor, maximized, any two pages fit side by
  side: the widest pair, Options Lab and the Dashboard, needs about
  2,010 of the 2,328 px beside the sidebar. At the default 1920 px window, mostly pairs
  with Research or Review fit. The 1440 px portrait monitor holds any one
  floating page.
- What Qt does with a drop that doesn't fit (refuses it, or grows the
  window) is checked in G1. The window must not grow past its screen.

**The layout is saved in the view-state store.**
- `QMainWindow.saveState` and the window's geometry go into
  `ViewStateStore` under "workspace", base64, with a layout version.
- Pages stay lazy. Restoring makes an empty dock for each saved page, and
  a page's content is built the first time its dock is visible.
- A saved page that no longer exists is dropped. A layout that fails to
  restore falls back to the default: the first page alone, with each page
  the sidebar opens tabbed beside it. That's today's app plus a tab strip.
- Ctrl+K gains "Reset layout".
- With a monitor unplugged, the restored window must land on a screen
  that's there (tested).
- The first launch, and a reset, keep today's sizing: 88% of the screen,
  capped at 1920×1240, centered.

**WebEngine (D40) floats only if it passes a check.**
- Floating a dock makes it a new native window, which is the case where
  D40 found Qt destroys and recreates a window.
- The Volatility Lab may float only if, after floating and docking back,
  the surface still draws and the main window isn't recreated. Otherwise
  its dock tabs and splits but doesn't float.
- It passed, on the owner's display (2026-10-29): it redraws floating and
  docked again, and the main window keeps its native window. So no page
  is fixed (`main_window.FIXED_PAGES` is empty).
- It's still prepared before the window's first show (D40).

**One worker stays (D9).**
- Visible pages queue their jobs on the one FIFO TaskRunner worker, so a
  slow Portfolio run delays the next page's refresh. Each page already
  shows its own loading state.
- More workers would be their own decision, made from a measurement.

**Tests.**
- Smoke tests keep `show_page`. The one check on `window._stack` moves to
  the workspace.
- New tests:
  - two pages side by side are both shown, once each
  - a tab switch hides a page and saves its state
  - re-docking doesn't refresh a page twice
  - the layout round-trips through the store, and an unknown saved page
    is dropped
  - Reset layout restores the default
  - shutdown saves every visible page
  - a page can't be squeezed below its body's width
- The Volatility Lab float check runs in a subprocess and may skip, like
  the WebGL test.
- The layering test is unchanged: everything stays in `gui/` and `app.py`.

**Not done here:** cards that move, several named layouts, more workers.
`CURRENT_STATE.md` records G1 when it lands.

**As built (2026-10-29).** `gui/workspace.py`, the `DockTitleBar`
component, and the docked-page styles in `gui/theme.py`.
- **Qt doesn't hide back tabs.** It parks them outside the workspace,
  still "visible", and `visibilityChanged` misfires when a dock floats.
  So the workspace decides: a page is shown when its dock is open and it
  floats or something of it is on screen. A dock's show, hide, move or
  resize events schedule the settle. Check `Workspace.shown()`, not a
  page's `isVisible()`.
- **A drop that doesn't fit becomes a tab.** Qt grows the window for a
  split, even past the screen (1600 → 2100 px in a test). When pages side
  by side need more than the screen's width, the page dropped last joins
  the active page's tabs. After a restore onto a smaller screen, every
  docked page joins one group.
- **A page docked back comes to the front.** Qt files it back behind its
  old group's front tab, where it would be hidden.
- **Snappy:** drops aren't animated, and pages build the first time
  they're shown, so a restored layout doesn't build them all at launch.
- **Small rules:** the last open page has no close button. The
  workspace has no context menu, since Qt's would show docks outside
  these rules. Tabs sit under a group, so the title bar stays the drag
  handle.
- **Tested:** the D55 list, plus a page docked back comes to the front,
  and a layout from another version falls back to the default. The 1280
  px check now opens every page as a tab, since a closed dock adds no
  width. Dragging with a mouse is Qt's own, and the tests drive the
  same moves through Qt's API.
