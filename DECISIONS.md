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

## D56 — G2: every card moves and resizes within its page (2026-10-29; amends D55 and D24)

The owner tried G1. Their direction: "every card in every module should be
snappy, resizable, and draggable", and "a good dashboard should be utterly
customizable". Saved templates come later. Floated pages should also look
like the panels they are.

**What changes.** D55 kept cards where their page put them. Now every page
lays its cards on a snapping grid (`gui/card_grid.py`, re-exported from
`gui/components.py`):
- Drag a card by any part that isn't a control. Drag its corner grip to
  resize it. It snaps to `GRID_COLUMNS` (12) columns and `GRID_ROW` (24 px)
  rows, with 16 px between cards.
- A card lands at once, with no animation. While it's dragged, a dashed
  outline shows where it will land, and the other cards make room.
- Each page's arrangement is saved in the view-state store under
  `<page key>.cards`, after every drop and resize (`pages.base.saved_grid`).
- Ctrl+K's "Reset layout" also resets the cards of every page opened in
  the session. "Reset this page's cards" resets the active page's cards.
  A page not opened since keeps its saved cards until it's reset.
- D24 deferred the Dashboard's configurable widgets. This is that, for
  every page.

**What stays.**
- A card stays in its own page, and one job still fills the page, so every
  number on it describes the same picture. Moving a card changes where it
  sits, not what feeds it.
- Cards don't move between pages and don't float on their own: pages do
  (D55). G3, panels from every page with linked inputs, is still not
  proposed.
- A page's argument is its default layout, and a reset brings it back.

**Never smaller than its content.**
- Each card is at least its minimum size, and its height-for-width when it
  holds wrapped text.
- At any width the grid works out a *shown* layout from the places the user
  chose. Each card is widened and heightened to its minimum and moved left
  to fit. Then every card falls as high as it can without overlapping one
  placed before it.
- The user's places are kept, so a card widened by a narrow window shrinks
  back when the window grows.
- Hidden cards take no room, so a note that appears pushes the cards below
  it down.
- Side-panel cards keep `SIDE_PANEL_WIDTH` as their minimum, because D49's
  wrapped notes are sized for it. On a narrow window, the side cards drop
  below the main column.

**Pages.**
- Every page's main column becomes columns 0–8 and its side panel columns
  9–11. Review and Valuation use 8 + 4, so that their four tiles fit.
- Tiles are cards of their own, except the Pricer's five greeks, which
  move as one strip because five don't share nine columns evenly.
- Options Lab's three tabs each have a grid. The Underlying card stays
  fixed above them, because it feeds all three. Research's Experiments tab
  has a grid. Its History and Library tabs don't, since they are single
  views.
- The Dashboard and the Simulator lose their resize reflows: the grid does
  that now.
- A `Card` now leaves height it can't use at its bottom, so a card taller
  than its content keeps its title and subtitle together.

**Floating (G1 polish).**
- The float button and a double-click float a page 48 px down and right of
  where it was, at 80% of its size. Its title bar is tinted and its edge
  drawn, so it reads as a panel of its own.
- The dock is hidden while Qt makes it a window and shown once it's in
  place. That removes the see-through flash the owner saw.
- Toggles closer together than 0.35 s are ignored.
- While a mouse button is held, page refreshes wait for its release, so a
  drag doesn't refresh the pages it crosses.

**Not done here:**
- Templates (named layouts).
- Cards across pages.
- Scrolling the page while dragging a card past its edge.

## D57 — Split-sized overnight gaps are named, never repaired (2026-11-02)

**Found (2026-11-01, on a copy of the store).** Some of IBKR's older daily
history isn't split-adjusted in either basis. MSFT, CSCO, INTC, ORCL, NVDA,
AAPL and others keep their 2:1 splits from 1999 through May 2005 as a
one-day fall of about 50%. Their later splits (NVDA's 2006 and 2007,
AAPL's 2014 and 2020) are adjusted. TRADES and ADJUSTED_LAST carry the same
step, so F3's split guard (adjusted/traded must be a dividend step
function, D38) can't see it. One fake −50% day poisons every number built
on the series.

**Decision.** `data/splits.py` finds overnight gaps the size of a split:
the log gap x_t = ln(O_t / C_{t−1}) within τ = 0.07 of −ln k for k ∈ {3/2,
2, 3, 4}, of +ln k for k ∈ {2, 3, 4}, or beyond ×5 either way. A day whose close
is more than 0.25 (log) from its open is skipped: that's a bad print or a
news day. A day without an open is tested on its close. The derivation and
the calibration are in the module docstring.
- Every `PriceHistory` has `split_gaps`.
- The GARCH, Kalman and Markov labs append it to the line that names their
  data (`history_note`, formerly `offline_note`).
- Portfolio carries the gaps per holding (`PortfolioView.split_gaps`) and
  names them among its warnings.
- Research saves them in a run's provenance and shows them under the
  chart.

**Why name and not repair.** A genuine overnight fall inside a band can't
be told from a split by open, high, low, close and volume. AAPL's of
2000-09-29 opened at ×0.527. A repair would erase a real crash, the worst
failure a risk tool can have, while a false note costs a glance. On the
store copy (41 symbols, 162,666 overnight gaps) the bands found all 16
unadjusted 2:1 splits and a 3:1, plus two days that aren't known splits.
Refusing the request was rejected too: it would block every long history
of a stock with one real crash in a band.

**Not done:**
- Real adjustment, which needs a source with a split calendar (a new
  provider, for discussion).
- The Review page, Markets, the Options Lab's realized vol and the
  Volatility Lab's overlay don't show the note yet. The Dashboard charts
  the step as it is.
- A 5:4 split or a stock dividend under about 28% isn't named, and neither
  is a split on a day that moved more than 25% after the open.

## D58 — Lit-1 and Lit-2: the literature canon and its timeline (2026-11-02)

The owner's direction: "construct the entire timeline of quant finance in
literature and make a cool graphic on the research page … a nice timeline
and you can open the papers with little widgets on the timeline." That
approves LITERATURE_PLAN's Lit-1 (the spine) and Lit-2 (the view) together,
and takes the plan's recommended answers to its open decisions.

**The canon (Lit-1).**
- 221 papers in 12 strands, from Bernoulli (1738, translated 1954) and
  Bachelier (1900) to 2024, with 320 typed links. It's package data in
  `quant_hub/data/canon/`: `timeline.json` (the strands' order, the eras)
  and one file per strand.
- **One file per strand, not per paper** (the plan said per paper). A
  strand reads as a story in one file, and 221 tiny files would bury the
  diffs. Promotion from an inbox (Lit-4/5) can still append to a file.
- **12 lanes, not 8.** The plan's eight left out rates and credit,
  microstructure, risk measurement and machine learning, which the
  "entire timeline" needs.
- `data/literature.py` holds the records and the rules: unique ids, known
  strands and link kinds, every directed link pointing back in time by the
  year a paper *circulated* (Gatheral–Jacquier circulated 2012, published
  2014), no directed cycles (Kahn's algorithm), and a source or a reason
  there isn't one. `storage/canon_store.py` loads and checks it.
- **Written by Claude, checked by machine.** Each entry was proposed by
  Claude in the session and its DOI checked against Crossref, or its id
  against arXiv (through DataCite, when arXiv rate-limited the check).
  Title, first author and year all had to match. 211 matched. One wrong
  DOI was caught and corrected (Hutchinson–Lo–Poggio). Ten have no
  registered identifier: a book, a talk, two magazine pieces, a symposium
  paper, two Industrial Management Review papers and three AER papers from
  1958–81. They say so on their card ("Not machine-checked: …"). The check
  ran once,
  at writing time, and is recorded per entry; the app doesn't call
  Crossref. That's still Lit-3, with its terms check (D34).
- **Coverage.** `tests/test_literature.py` fails if a module in `models/`
  has no canon paper in its `lives_in`, except an explicit list of three
  modules that aren't from a paper: `backtest.py` (accounting),
  `optimize.py` (Nelder–Mead) and `research_worlds.py` (synthetic
  controls). The mapping came from what each module's docstring cites.
  36 papers are implemented; the rest show as "not yet".

**The merge with your catalog.** `ResearchService.papers()` lists the
canon papers with your fields merged in by id, followed by your own
papers. The canon owns title, authors, year, strand and source; the store
keeps status, notes, PDF and linked runs (D42's table, unchanged, no
migration). The five hard-coded D42 seeds are gone: their ids are canon
ids, so existing notes and PDFs stay attached, as do the owner's imports
(`markowitz1952`, `blackscholes1973`, …). The Papers tab shows canon
metadata read-only. A canon file that fails its checks doesn't stop the
app: the catalog falls back to your own papers and the Timeline tab says
what's wrong.

**The timeline (Lit-2).** Research gets a Timeline tab, between Saved runs
and Papers: `gui/timeline.py` (the chart) and
`gui/pages/research/timeline.py` (the tab and its inspector).
- **Axis.** Years before 1950 are squeezed by 0.3 and years before 1900 by
  0.05: piecewise linear, continuous and strictly increasing, derived in
  the module docstring. Without it, 1738–1950 would take most of the
  width for a dozen papers.
- **Dots and chips.** Each paper is a dot on its strand's lane: filled
  when it lives in your code, hollow when it doesn't, with a ✓ on its chip
  when you've read it. Chips (the "little widgets") appear where they fit:
  two rows per lane when lanes are 48 px or taller, placed greedily by
  priority (the selection, its links, search matches, then the most-cited
  papers). Zooming in reveals more. Same-year papers spread within their
  year.
- **Links.** All 320 links are drawn faintly. Picking a paper draws its
  own links: builds-on kinds in light blue, critiques (corrects,
  challenges) in orange and dashed, independent discoveries dotted.
- **Interaction.** This is the one chart that pans and zooms (along time
  only; the lanes stay put). The others stay view-only. Clicking a dot or
  a chip selects the paper; double-clicking opens its PDF if you have one,
  otherwise its source. Find dims everything but the matches.
- **The inspector** shows the question, what changed, what it assumed, its
  links both ways (each one click away), the modules it lives in, your
  reading status (saved on change), Open PDF, Open source and Attach PDF,
  and how the metadata was checked. It scrolls inside its card, using a
  new `cardScroll` theme role (transparent, theme-only).
- Like History and Library, the tab is a single view with no card grid
  (D56).

**Not done here:** Lit-3 (the in-app resolver), Lit-4 (LLM proposal files
and the inbox), Lit-5 (the feed of new papers), Lit-6 (reading paths and
replication status). The canon is quant-only; ML Cookbook gets the format
after this lands.

## D59 — The hedge-and-crash track next; free data accumulates (2026-11-03)

On 2026-11-03 the owner worked through the open plans' decisions in eight
batches, each with a recommended answer. 58 were settled, each marked in
its plan's decision list. That includes a few that earlier work had
already overtaken, and four that became moot once the regime model
superseded TARGET's T2.

**The owner's objective, in their words:** "I WANT A CORE BETA PROFILE ON
MY PORT AND INSTEAD OF LOADING UP ON MORE EQUITY I HAVE DIVERSIFIED
ORTHOGONALLY SLEEVES TO MY PORTFOLIO. … only survival, only minimizing
volatility and maximizing cagr". In the math that's W3's growth hurdle:
a sleeve earns its place when its excess return beats its covariance with
the book. HEDGE ranks candidates by Δ log growth for this reason.

**The order** (ROADMAP's "What's next"):
1. The data batch.
2. ALLOCATION A1 → A2.
3. HEDGE U1, then U2–U5.
4. ALLOCATION A3–A4.
5. VARIANCE, then REGIME_MODEL.

**Data: accumulate as much free data as possible.** The owner: "data is
power." Each provider gets its own terms check (D34), is stdlib only,
stores what it fetches with provenance, and works offline (V0):
- **Ken French's library**
- **FRED:** rates and curve, credit and stress, growth and inflation, the
  dollar and commodities
- **CBOE's VIX history**
- **Shiller's data:** a manual CSV, local only
- **SEC N-PORT fund holdings,** through the existing SEC provider (D47)
- **Crossref, arXiv and OpenAlex,** for the literature

Option chains snapshot daily, the first time the app runs with TWS
connected. The requests are wide (−6 to +3 ATM sd), on SPX with SPY bars
for realized vol, and trading days follow NYSE holiday rules.

**Measured, not graded.** A2's crash stress is shown with its dates and
error band, and never becomes a REGIME.md rule. That follows "I am the
trader": models inform, the owner decides.

**Plans reshaped:**
- **TARGET:** T1 stays, as the data batch's Shiller import. T2 and T3 are
  superseded by REGIME_MODEL.
- **BUDGET:** folds into ALLOCATION A3, as one What-if book builder.
- **HORIZON P2:** construction also lives on the What-if page.
- **HORIZON X4** (Heston, FFT pricing) is in, as its own plan after
  VARIANCE.
- **SLEEVE_PLAN** is to be rewritten around the owner's objective
  (orthogonal growth, any instrument, cross-asset trend first) before any
  S milestone is approved.
- **Literature:** Lit-3 to Lit-6 are approved, with LLMs writing proposal
  files only. ML Cookbook is out of scope: it's a separate project, so
  its section was removed from LITERATURE_PLAN.

**Track H, at low scope.** The owner wants the web server started, in a
small way. Approved: H0 and H1 only (health, Black–Scholes, GBM). It's
stdlib, read-only, with no broker and nothing private, bound to localhost
or the LAN, and never public for now. AGENTS.md's ban on Flask/FastAPI,
cloud and Docker stays. H0 writes its own decision entry when it starts.
The server doesn't collect data, because it never connects to IBKR.

**Smaller settlements:**
- look-through N_eff
- What-if books saved as named sets
- max Sharpe shown with its warning
- snapshots deletable after a backup
- HEDGE's remaining defaults (the owner had no preference)
- American SPY pricing, with de-Americanized smiles
- the variance card shows every expiry
- IBKR and Fidelity only
- the design pass waits until after the hedge track
- 40 paper sessions before anything live

**Still open, on purpose:**
- EXECUTION's money limits: the owner's numbers, set at E2 in the
  settings file, never committed.
- REGIME_MODEL's horizons, D and p, f, the inclusion bar, the card's place
  and δ: at Q1's start.
- BUDGET's builder details: at A3.
- SLEEVE's decisions 2–8: after the rewrite.
- Whether M3's time-varying Markov extension is in.
- E1's live check waits on the owner's paper login and ibapi 10.x.

## D60 — The data batch, part 1: terms checked, a series store, Ken French (2026-11-03)

D59 approved the data batch. Its first increment: every source's terms are
checked, one store holds every outside series, and the first provider
arrives.

**The terms checks** (2026-11-03, D34):
- **Ken French's library:** no license is stated, only "Copyright Eugene
  F. Fama and Kenneth R. French". Used locally, never committed, cited
  where shown. Approved.
- **FRED:** the API needs a free key. Its terms ask for the line "This
  product uses the FRED® API but is not endorsed or certified by the
  Federal Reserve Bank of St. Louis." in the app. Third-party series
  (copyright in their notes) are for personal use only. Approved, with
  the key in the settings file like `[sec] identity`, never committed.
- **Cboe directly: refused.** Cboe's terms allow downloading "one copy …
  for your personal non-commercial use", but not to "store … in an
  electronic retrieval system", and the store is one. **VIX comes from
  FRED's `VIXCLS` instead**, under FRED's personal-use terms, or from
  IBKR's index bars under the market-data license the app already works
  under. This replaces D59's "CBOE's VIX history"; the history is the
  same.
- **Shiller:** local only (D59). **SEC N-PORT:** D47's fair-access policy
  already covers it.

**One store for every outside series** (`storage/series_store.py`,
schema v7, after D36's backup):
- **`ext_series`:** per series, its title, unit, frequency, the citation
  its terms ask for, a vintage note ("CRSP 202609"), when it was fetched,
  and the SHA-256 of the file it came from.
- **`ext_values`:** its dated values.

A fetch replaces each series whole, in one transaction. French's files
carry their full history and are revised as CRSP updates, so "as fetched"
means the latest file. A source that serves only a recent window will
merge instead, decided with that source. The record types live in
`data/series.py`: values are fractions for returns (0.01 = 1%), and
missing values are left out, never filled.

**Ken French** (`providers/ken_french.py`, stdlib only):
- the daily FF3 file (from 1926-07-01), the 2×3 FF5 file (from
  1963-07-01), and momentum (from 1926-11-03): eleven series,
  227,103 values
- one request per file, a plain User-Agent, typed errors
- the parser keys on the header row and the 8-digit dates, never on line
  numbers
- −99.99 and −999 count as missing
- the market's total return (Mkt-RF + RF) isn't stored, since it's an
  identity of two stored series

**The service** (`services/external_data.py`):
- It refetches a dataset when its copy is more than a week old: the files
  are rebuilt monthly. `force` refetches regardless.
- A failed dataset keeps its stored copy and says so; the others still
  fetch.
- Reads come from the store, so it works offline.

Ctrl+K's **Fetch Ken French factors** runs it on the worker, and the
outcome goes to the log.

**Checked live** on a copy of the owner's store:
- v6 → v7 migrated, with its backup.
- All three files were fetched, and a second refresh correctly skipped
  them as fresh.
- The data agrees with known history: the market returned 9.8% a year
  from 1926 to 2026, its worst day was 1987-10-19 (−17.4%), 2008
  returned −36.5%, and 2020-02-20 → 03-23 returned −34.2%.
- The store grows by about 10 MB.

**Not yet:**
- FRED (and VIX through it), the Shiller import, N-PORT holdings, the
  daily chain snapshots and the collection on first open: the next
  increments.
- A page listing what's stored.
- No model reads the factors yet. ALLOCATION A2's crash stress and
  LENS W2b's Fama–French regressions are the first readers.

## D61 — The data batch, part 2: Shiller's monthly data, from your CSV (2026-11-03)

TARGET T1, built as D59 decided: a CSV you save from Excel (decision 2a),
used locally only (decision 3). The owner asked for it before FRED, which
waits for a free API key.

**The step:** download `ie_data.xls` from Shiller's site, open it in Excel,
Save As CSV with the Data sheet showing, then Ctrl+K: **Import Shiller
CSV…**. The import runs on the worker and logs what it kept.

**`importers/shiller_csv.py` reads T1's spec:**
- Columns by header label: `P`, `D`, `E`, `CPI` and `Rate GS10` are
  required, each once, and `CAPE` is read when present. A missing or
  repeated label is named.
- Dates: Shiller's `1871.1` is October, and the month is
  round(100·(x − ⌊x⌋)). Months must be consecutive; a gap or a repeat names
  its line. Each month is dated the 1st, FRED's convention, so the two
  will line up.
- Blank and `NA` cells are left out, never filled.
- The last month is provisional and dropped. The footnotes ("Oct price is
  Oct 2nd close", "Sept/Oct CPI estimated") and "earnings through …" go in
  each series' note.
- Quoted thousands separators and a Windows-encoded file are read.

**One change from T1's text:** T1 planned its own tables
(`valuation_months`, `valuation_imports`). D60's series store now holds
every outside series, so Shiller goes there instead:
- six series under `shiller`: `sp.price`, `sp.dividend`, `sp.earnings`,
  `cpi`, `gs10`, `cape`
- monthly; P, D, E and CPI as index levels, GS10 in percent, CAPE as a
  ratio, a unit added to `data/series.py`

An import replaces the series whole, since Shiller revises CPI and recent
earnings, and the file's SHA-256 records which import a number came from.

**Checked** on the owner's local copy from 2026-10-24:
- 1871-01 to 2026-09, with 2026-10 dropped as provisional, and earnings
  through 2026-06.
- CAPE recomputed from the imported P, E and CPI (real price over the
  10-year average of real earnings) agrees with Shiller's own CAPE column
  within 0.2 points at five dates from 1929 to 2025. The gap comes from
  his CPI reference and earnings timing.
- Imported into a store copy beside Ken French's factors: 17 series in
  all.

**Not yet:** REGIME_MODEL is the first reader (valuation from CAPE and the
excess CAPE yield), along with TARGET's superseded T2 rule as one of its
inputs. FRED, N-PORT, the chain snapshots and a page for stored data are
next.

## D62 — The data batch, part 3: funds' holdings from SEC N-PORT (2026-11-03)

D59 chose look-through N_eff for ALLOCATION A1, with fund holdings from
SEC N-PORT filings through the existing SEC provider. Built here: the
holdings arrive and accumulate. A1 reads them later.

**What N-PORT is.** Every US registered fund, unit-trust ETFs like SPY
included, files its full portfolio on Form N-PORT. The public filings
(`NPORT-P`) arrive quarterly, about 60 days after the report date, as XML.
D47's fair-access client covers them: a declared identity and at most ten
requests a second. `SecClient` gains a public `get` for this.

**Finding a fund's latest report** (`providers/sec_nport.py`, stdlib only):
1. **The ticker.** SEC's fund ticker map (`company_tickers_mf.json`) gives
   a series trust's CIK and series id. A unit trust has no series id; its
   CIK comes from the company ticker map instead.
2. **Its filings.** EDGAR's browse feed, as Atom, lists the series' (or
   CIK's) `NPORT-P` filings, amendments included, newest first.
3. **The report.** `primary_doc.xml` in the filing's folder. Each
   `invstOrSec` keeps name, LEI, CUSIP, ISIN, ticker, balance, units, USD
   value, the weight (`pctVal` ÷ 100, a fraction of net assets), the asset
   and issuer categories, country and payoff.
   - Matching is by local element name, so a namespace change doesn't
     break it.
   - `N/A` and blanks stay blank, never guessed.
   - A document for another series is refused.

The layout was read from SPY's real filing of 2026-08-28 (as of
2026-06-30). The tests use fixtures in that shape, with made-up holdings.

**Storage** (`storage/holdings_store.py`, schema v8, after the backup):
- `fund_tickers`: the map, refreshed when a week old.
- `fund_filings`: per fund and report date, with accession, net assets,
  fetch time and the XML's SHA-256.
- `fund_holdings`: the lines, in filing order.

Every report date is kept, so a fund's holdings history accumulates (D59).
A refetch of the same date, an amendment, replaces it.

**The service** (`services/fund_holdings.py`):
- It checks for a newer filing when the stored report was last checked
  more than 30 days ago, or on refresh. That's one feed request, plus the
  ticker map when it's a week old.
- An already-stored accession is only marked as checked, with no document
  fetch.
- A failed check serves the stored report.
- Without `[sec] identity` it names the setting.
- Ctrl+K **Fetch fund holdings…** asks for a ticker and logs the report
  date, the line count and the weight covered.

**The bridge, an open question for A1.** Holdings carry CUSIP and ISIN, but
rarely a ticker. Look-through N_eff only needs the fund's weights, but
merging a fund's line with a stock you also hold directly needs an
ISIN-to-ticker bridge. The candidates are IBKR's ISIN contract lookup
(needs TWS, no new provider), OpenFIGI (a new provider, with a terms
check), or name matching (fragile across share classes). That's A1's to
choose; nothing here guesses.

**Not checked live:** the owner's settings have no `[sec] identity`, and
filling it is the owner's call (D47). Until then the parsers are verified
on fixtures shaped from the real documents. Live checklist: SPY (a unit
trust) and one series-trust ETF, comparing line count and weights with
the issuer's site.

## D63 — The data batch, part 4: daily chain snapshots and the NYSE calendar (2026-11-03)

D59 chose daily chain snapshots, on first open, of a watchlist you set,
with VARIANCE I3's wide request and VARIANCE decision 6's NYSE holiday
rules. Built here.

**The NYSE calendar** (`data/trading_calendar.py`, pure):
- The holidays are computed by rule from 1998: New Year's, MLK, Washington's
  Birthday, Good Friday (the anonymous Gregorian Easter), Memorial Day,
  Juneteenth from 2022, Independence Day, Labor Day, Thanksgiving and
  Christmas.
- **Observed days:** Saturday moves to Friday and Sunday to Monday, but
  a Saturday New Year isn't observed.
- **Unscheduled closures are listed:** 9/11, the days of mourning, and
  Hurricane Sandy.
- **Not modeled:** early closes. A day that closes early still trades.
- **Checked:** the published holiday lists for 2021, 2022, 2024, 2025 and
  2026, and the day counts. Once, against every date in Ken French's daily
  file from 1998 to 2026-09: 7,230 days, an exact match.

**The collector** (`services/chain_collector.py`):
- **When a symbol is due:** the New York date is a trading day, the time
  is at least 09:45 (before the open, option quotes are the previous
  session's), and no snapshot of it from that source is stamped today.
  - Once per symbol per day; a reconnect collects nothing.
  - A day without the app is missing. Nothing is backfilled.
- **The request:** `VARIANCE_REQUEST`, −6 to +3 ATM sd, up to 41 strikes
  per expiry. `ChainService` saves it as it saves every live fetch (D33).
- **Fetch time** is logged per symbol, so the first real runs measure it.
  VARIANCE decision 2 asked for that measurement before choosing the band;
  the band is chosen, and the log now says what it costs.
- A failed symbol is logged, and the rest still run.

**The trigger.** When TWS connects, and the watchlist isn't empty, the
main window queues the due symbols one per worker task. Pages share the
single worker, so a page opened meanwhile waits for one fetch at most,
not the whole list. Ctrl+K **Collect today's chains** runs the same thing
by hand.

**The setting:** `[collect] chains = ["SPX", "SPY"]` in `settings.toml`.
It's empty by default, so nothing is collected until you set it. A bad
value is reported, and the setting stays empty.

**Not checked live:** TWS wasn't connected. The first connection after
09:45 on a trading day, with a watchlist, is the live check: one snapshot
per symbol, the fetch times in the log, and none on a reconnect.

## D64 — A Data page: everything stored, where from, how fresh (2026-11-03)

D59 set the direction to accumulate free data, and D60–D63 built four
ways to fetch it. Until now the only way to see what had arrived was the
log. The Data page (`gui/pages/data.py`, in the sidebar after Markets)
lists what the store holds.

**Cards.** The main column is 0–7 and the Preview panel is 8–11, laid out
8 + 4 as on Review and Valuation (D56).
- **Outside series** (D60, D61): source, series, title, frequency, range,
  value count, and the day it was fetched. **Import Shiller CSV…** and
  **Fetch Ken French** sit on the card.
- **Fund holdings** (D62): each fund's newest N-PORT report, with its line
  count, the sum of its stated weights, how many report dates are stored,
  and when it was last checked. While `[sec] identity` is unset, a note
  says how to set it. **Fetch holdings…** asks for a ticker.
- **Option chains** (D63): per symbol, the days collected, snapshot count,
  the newest one and its quotes. A line names the watchlist, or says how
  to set one. **Collect today's chains** runs it by hand.
- **Price history:** the daily bars per symbol, source and basis, from
  the workstation overview the Dashboard reads.
- **Preview:** select a series to plot it, with its range, fetch time,
  note, and the citation its source's terms ask for (D60).
- A table with no rows shrinks to its header and shows one line on how to
  fill it.

**The preview plots returns as Σ ln(1 + r).** A series whose unit is
`fraction` (French's daily factors) is shown as its cumulative log
return. The daily values themselves are noise at a century's scale, and
the cumulative sum stays readable on a linear axis: a straight line is a
constant growth rate. Every other unit (Shiller's index, dollar and
percent series) is plotted as stored. The panel's title says which.

**The buttons run the Ctrl+K commands.** `Shell` gains `run(name)`. The
main window looks the name up among its published commands and raises
`KeyError` for an unknown one. A button and the search therefore can't
drift apart: each action has one implementation, in `main_window`, which
already owns its dialogs and the worker queueing. After an action, the
page queues a refresh. The worker runs jobs in order, so the refresh runs
after the fetch and shows its result.

**Reads.** One TaskRunner job builds the whole overview. The services
gained the reads they lacked, all offline:
- `FundHoldingsService.catalog()`, from `HoldingsStore.funds()`: one SQL
  query over each fund's newest report.
- `FundHoldingsService.has_identity`.
- `ChainService.snapshots(symbol=None)`, which passes through to the
  chain store.
The page imports no store (test_layering).

**At 1280 px** the Preview panel's minimum (`SIDE_PANEL_WIDTH`) is wider
than four columns, so it drops below the series table, as D56's side
panels do on narrow windows. From about 1440 px it sits beside the table.

**Checked** on a copy of the store holding French's 11 series and
Shiller's 6: screenshots at 1280 and 1440 wide, with Windows fonts.

## D65 — ALLOCATION A1, part 1: a core-aware review (2026-11-04)

ALLOCATION_PLAN's first finding: section 4 graded an index core like a
single stock, so a book built on "a core beta stream" failed its own
checklist. D59 put A1 first. This part makes the review core-aware. The
look-through N_eff (decision 2) is part 2, and needs the ISIN bridge the
owner chose: IBKR's contract lookup, for the stocks held directly.

**The core is shown, not graded, under the single-name rules.** A
holding whose section 3 sleeve is `core` gets a new outcome, `exempt`,
under max holding weight and max risk share. Its value is measured and
shown as before. Every other rule still reads it, and so does every
book-level number: β, volatility, VaR, ES, drawdown, cash. The checklist
shows "exempt: the core".

**Sleeve totals.** Section 3 gains a second table, `| Sleeve | Intended
total |`, told apart from the holdings by its first header.
- A total names a sleeve the holdings table uses (`core`, `hedge`,
  `other` or `stream: <name>`). An unknown one is an error naming its line
  (D49).
- It's a range, and either side may stay blank. A single number is
  refused, because a total exists to be graded. So is `by k SE`, because
  a weight is exact.
- It's graded by section 3's sleeve-grade line, like a holding's range. A
  total without that line is refused. A holding's range without it is
  still silently ungraded, as before; that wasn't changed here.
- The measure is the sum of the sleeve's holdings' |weight|, options by
  delta: the per-holding size rule's own measure, summed.

**The core and the rest** (`models/allocation.py`, math in its
docstring). The book's β is Σ wᵢβᵢ exactly: OLS β is linear in the
dependent series, and cash adds nothing. It splits into the rest's
B_N = Σ_{i∉C} wᵢβᵢ and the core's w_C·β_C. Holding the rest fixed, a
target β* needs a core of w_C* = (β* − B_N)/β_C.
- The Review shows a card with the core, the rest and the book: each
  part's weight, β and the β it supplies.
- **Section 4's Beta band is the target.** No new parameter: the card
  gives the core weights for each side of the band, as computed, and the
  part of that range that needs no leverage (between 0 and 1 − w_N). It
  says when the core's weight today is inside it.
- **Shown, never applied.** No rule grades it. It's how a pick's β is
  "shorted out" by holding less core, in an account that can't short.
- The βs are the risk model's, cov/var over the lookback, so they're the
  ones behind the Beta tile. The journal keeps the split.
- It's Treynor and Black's (1973) market-plus-active split, so the canon
  entry now lives in `models/allocation.py` (test_literature's coverage
  rule).

**Not changed:** the effective-holdings floor still counts the core as one
name. Part 2 replaces that with the look-through count.

**Tests:**
- the β split, exact against a sample regression
- the band's core weights landing the book on each side
- the reachable range in five cases
- what can't be netted
- the parser's totals and each error's line
- the review's exemption, totals and netting
- the Review page's card

**Checked** with a screenshot of the Review page on the tests' made-up
book.

## D66 — ALLOCATION A1, part 2: effective holdings look through the core (2026-11-04)

ALLOCATION decision 2 (D59) chose to look through the core fund rather
than count only the rest. Without it, a core-heavy book scored an
effective-holdings count of 1 to 2.3, though the core held hundreds of
names. On 2026-11-04 the owner chose the bridge between a fund's lines and
the stocks held directly: IBKR's contract lookup.

**The count** (`models/allocation.py`, math in its docstring). It's still
the inverse Herfindahl index, now over exposures to distinct names:
- A core fund held at weight w_F with lines at fractions p_j of its net
  assets contributes w_F·p_j to each line's name.
- A stock held directly and also inside the fund is one name, its two
  exposures added. Owning it twice makes the book more concentrated, not
  more diverse.
- Without funds it equals the risk report's count, so a book without a
  core grades exactly as before.

**What's looked through:** only the core's holdings, each from its newest
N-PORT report (D62). A stream's ETF stays one name, because its lines are
often futures and bills. Lines without a stated weight, and the part of
net assets the lines don't add up to, aren't names. The book's own cash
is left out the same way.

**The bridge: ISINs from IBKR** (`services/security_ids.py`).
- Only the stocks held directly are looked up: a few requests, not one
  per fund line. `IBKRBroker.get_isin` reads `secIdList` from the
  contract details, read-only. A symbol matching contracts with different
  ISINs is refused, never guessed.
- **Stored** in `security_ids` (schema v9, migrated after a backup), and
  served offline. A stored ISIN is asked again only when it's a year old
  and TWS is connected, because a ticker can pass to another company. A
  failed refresh keeps the old one.
- **Checked:** ISO 6166's form and check digit (`data/holdings.valid_isin`),
  before an ISIN is stored or matched.
- **The review never connects on the owner's behalf.** Without TWS, the
  symbols without a stored ISIN stay separate names, and the run notes
  name them: "with TWS connected, the next review looks them up."

**Matching never guesses** (`services/review._line_key`). A line's name
is its ISIN when it has a valid one. Without one, it takes the key of a
stock held directly when its ticker is that stock's symbol. Otherwise
it's its own ticker, CUSIP or name. A stock and a line merge only on the
same ISIN, or on the exact ticker when the line has no ISIN.

**When there's no report:** the effective-holdings rule has no data, and
its reason says why. Usually that's `[sec] identity` unset, or the fund's
holdings not yet fetched. Counting the core as one name would bring back
the false breach this milestone removes.

**The run notes** give:
- the report used: its date, line count and coverage
- the count looked through, beside the count with each fund as one name
- each stock held directly that's also inside the core, with the weight
  it adds through it
- each unmatched symbol and why

**Tests:**
- the check digit on ISO's published examples, and refused forms
- the broker read on a stand-in network: found, none, the same ISIN
  twice, ambiguous, unknown
- the service: stored, offline, IBKR off, invalid, refused, stale and
  refreshed, a failed refresh
- the count: the risk report's without funds, n for n equal lines, scale,
  the merge, two funds overlapping, refusals
- the v8 → v9 migration
- the review end to end: looked through, merged, unmatched; and no
  report

**Checked live:** the v8 → v9 migration on a copy of the owner's store,
with its backup.

**Not checked live:** fetching the core's report needs `[sec] identity`,
and the ISIN lookup needs TWS. Neither was available.

## D67 — ALLOCATION A2: crash stress, measured (2026-11-04)

ALLOCATION_PLAN's third finding was that the drawdown check never sees a
crash. It reads the worst fall of today's mix over the lookback, which can
be two calm years. D59 settled the rest:
- **Data (decision 3):** the longest history, which is Ken French's
  market from 1926.
- **Grading (decision 4):** measured only, shown with its dates and its
  error band, never graded. So there's no section 4 row: the plan's
  proposed "Crash stress" row isn't added.

**The replay** (`models/stress.py`, math in its docstring). A book with
β b to the market holds b in the market and 1 − b in T-bills, borrowing
at the bill rate above 1, and is rebalanced daily:

    Vₜ = Π (1 + r_s + b·x_s)

Here x_s is French's `Mkt-RF` and r_s his `RF`. The measure is V's
largest peak-to-trough fall, with its peak, its trough and the day V
regained the peak. The five worst falls are listed, each a separate spell
under water.
- A day whose factor is zero or less wipes the book out. V stays at zero
  from then. At β 6, that day is 19 October 1987.
- **The fall grows with |β|.** In logs, each spell's change is concave in
  b. The worst fall is set by the minimum over spells, which is concave
  too, zero at b = 0 and never positive (with bills ≥ 0). So it can't
  rise as b moves away from zero. That's why the error band's two ends
  bound the fall.

**The β** is W2's regression of the book on the benchmark, the one the
Beta row grades, with its Newey–West error. The band is β̂ ± 1.96 SE, a
95% band. Without regressions, it's the risk model's β with no band.

**On the Review page**, a "Crash stress" card:
- the worst fall at β̂, its dates, and the band's falls
- the drawdown over the century, with your drawdown tolerance drawn
  across it
- the five worst falls, with when each was regained
- a note: **a floor, not an estimate.** The replay holds β fixed and
  carries no stock-specific risk, and correlations rise in crashes
  (Longin and Solnik, 2001), so a book of stocks loses more.

The journal keeps the β, the worst fall, its dates, the band and the
data's span.

**What it treats as one:** French's market is CRSP's value-weighted
index, and β is measured against the benchmark (SPY by default), which
tracks it closely.

**Not from a paper:** `models/stress.py` is daily accounting, like
`backtest.py`, so test_literature lists it among the exemptions. Roy
(1952) and Longin–Solnik (2001) are cited for context only.

**Without French's series** stored, the card says how to fetch them:
Ctrl+K, **Fetch Ken French factors**. The review reads the stored copy
only and never fetches.

**Checked on the stored French series at β 1:**
- 1929–32: −84.1%, regained in 1945
- 2007–09: −54.6%
- 2000–02: −49.2%
- 1973–74: −48.2%
Those are the market's own total-return falls.

**Tests:**
- β = 1 is the market's own total-return path
- β = 0 never falls
- the fall grows with |β| on both sides
- planted slides found worst first, with exact dates and recoveries
- a spell not yet regained
- the band's ends, a wipe-out, and refusals
- the review: β and SE from the regression, the band, the journal, and
  no French series
- the Review card, with made-up series and without any

**Checked** with a screenshot of the Review page: the tests' made-up book,
with your stored French series.

## D68 — HEDGE U1: the hedge math (2026-11-04)

D59 put HEDGE U1 after ALLOCATION A1–A2. U1 is the hedge math in
`models/hedge.py`: pure and tested, with no page yet (that's U4) and no
VIX history yet (that's U2). It has every candidate HEDGE_PLAN lists and
the yardstick that compares them.

**Rule and linear candidates (§4).** Each gives a hedged daily return
series from the book's:
- **Cash at weight w:** (1 − w)·R_B + w·r.
- **Another stream at weight w:** a Treasury fund, or your trend fund.
- **Scaling the market exposure:** R_H = r + (1 − h(1 − e))·(R_B − r).
  The exposure e ∈ [0, 1] is decided at close t and held to t + 1, and
  the size is h ∈ [0, 1]. h = 0 is the book exactly. Before a rule has
  its history, e = 1, so it doesn't hedge.
  - **Trend on the market:** above its 200-day average, or a positive
    12-month excess return (time-series momentum, Moskowitz–Ooi–
    Pedersen 2012).
  - **Vol targeting:** e = min(1, σ*/σ̂), from 21-day realized vol, or
    GARCH(1,1) (Moreira–Muir 2017). It never levers. The GARCH
    parameters are fitted on the whole series, a stated look-ahead;
    realized vol has none.

**Rolled option programs (§2–3).**
- **The programs:** a put at K = m·S, a put spread (less a put at m₂·S),
  or a zero-cost collar. The collar's call strike solves C(K_c) = P(K)
  by bisection above the forward; the call's price falls in its strike.
- **Rolls:** at expiry, or every n trading days. Each roll buys
  q = h·b·W/S units on the hedged book's value.
- **Self-financing:** premiums come out of the book, and payoffs go back
  in. Each trade pays a half-spread c of each leg's price, and expiry
  settles at intrinsic with no spread.
- **Marks:** Black–Scholes, European, at σ = VIX·ρ·s(k, T), with k the
  log-moneyness to the forward. The smile s is a function U3 will build
  from today's surface; the flat smile runs beside it (decision 5). A
  position keeps the rate and dividend yield of the day it was bought, a
  negligible approximation over 1 to 12 months.
- **Granularity:** whole contracts round n = h·b·V/(100·S). The ratio
  they give is h_eff. On a $50k book half hedged at β 0.9, with the index
  at $500, that's 0.45 contracts, which rounds to none.
- This is Coval–Shumway's (2001) measurement of put returns, on a book.

**The yardstick (§5):**
- **Crash windows by rule:** every spell in which the market's
  total-return level fell at least 15% from a peak. On French's century
  that's 20 windows, from 1929–32 to 2025.
- **Cost:** 252 × the mean of R_B − R_H on the days outside the windows,
  with its Newey–West error.
- **The crash table:** the book's return over each window, unhedged and
  hedged, and what the hedge paid. Its mean is U4's "saved" axis.
- **Δ log growth:** 252 × the mean of the paired daily log difference,
  with its Newey–West error. Pairing cancels the shared market noise.
- **Drawdown:** the deepest fall and the worst 252 days, hedged and not.
- **Convexity:** Henriksson–Merton up and down betas of the hedge's
  monthly return on the market's. Henriksson–Merton (1981) waits for its
  verification in the canon.

**First numbers** on French's 1926–2026 series, for a book at β 0.9.
These are first looks for the lab, not a recommendation: selection
across many candidates flatters the best one.

| Hedge | Δ log growth | Deepest fall | β↑ / β↓ |
|---|---|---|---|
| None | | 80% | |
| Cash 30% | −1.5% ± 0.5% | 67% | −0.28 / −0.26, linear |
| 200-day trend | +1.0% ± 1.1% | 31% | −0.35 / −0.59, convex |
| 10% OTM 3-month put* | +0.1% ± 0.7% | 51% | −0.10 / −0.34, convex |
| 10% OTM 3-month collar* | +0.7% ± 0.8% | 44% | −0.33 / −0.32, linear |

\* With a stand-in for VIX (21-day realized vol × 1.15). It carries
little of the real variance premium, so the puts come out near break-even.
Real VIX history (U2) will price them properly.

**Canon:** Moreira–Muir (2017), Moskowitz–Ooi–Pedersen (2012) and
Coval–Shumway (2001) now live in `models/hedge.py`.

**Tests (33):**
- rule candidates:
  - h = 0 is the book exactly; the timing is t → t + 1
  - the mixes are exact
  - the trend rules go out in planted slides, and wait out their warm-up
  - vol targeting tracks the vol and never levers, for both estimators
- programs:
  - Black–Scholes marks on a single position, to 1e-12
  - settlement at intrinsic in a planted slide, spreads included
  - the plan's worked premium (0.72 per 100 for a 10% OTM 3-month put at
    21%)
  - the collar's strike against parity (at the forward, C = P)
  - crash losses falling as h grows
  - a spread costing and paying less than its put
  - Δg negative for dear puts and positive for cheap ones in a jumpy
    world
  - whole contracts and h_eff
- yardstick:
  - crash windows by rule
  - cost on calm days only
  - Δg against its closed form, and Newey–West against the true error
    for iid and AR(1) differences
  - cash linear, trend convex
  - the worst year
- refusals throughout

## D69 — HEDGE U2: FRED, and the index's history from IBKR (2026-11-04)

U1's option programs mark their puts from VIX history and roll them on
the index. U2 brings both in, along with the bill rates. It also lands
the data batch's last provider, FRED (D59, D60).

**FRED** (`providers/fred.py`, stdlib only).
- **Terms** (D60): a free key, the credit line in the app, and
  third-party series for personal use.
  - The key goes in `settings.toml` as `[fred] key`: 32 lower-case
    letters and digits, checked when the file is read. It's never
    committed, logged, or put in an error message. A refused request
    reports FRED's own reason, never the URL that carries the key.
  - Every FRED series' citation starts with the credit line, so the
    Data page's preview shows it.
  - VIXCLS's adds Cboe's copyright, for personal use.
- **The series:**
  - `VIXCLS`: the VIX at the close, from 1990
  - `DTB3`: the 3-month bill, discount basis, from 1954
  - `DGS3MO`: the 3-month Treasury at constant maturity, from 1981.
    REGIME.md's cash-rate row names it.
- **Stored as published:** percent, in the D60 series store, one SHA-256
  per reply. FRED's "." days are left out, never filled. The models
  convert the units.
- **The refresh:** `ExternalDataService.refresh_fred`. A series is
  fetched when its copy is 20 hours old, or always when forced. Each
  series fails on its own, keeping its stored copy. Without a key, the
  refresh says how to set one.
- **Where it shows:** Ctrl+K **Fetch FRED series**, and a **Fetch FRED**
  button on the Data page. Without a key, that card's note says how to
  get one.

**The index from IBKR.**
- **Index contracts:** the history source now asks for a listed index
  (SPX, VIX) as an index contract, as the chain code already did
  (`option_chain.underlying_spec`, which gained VIX).
- **Bases and volume:** an index's level has no dividends or splits, so
  both price bases read TRADES. It has no volume: IBKR's −1 stays
  negative, and the bar check reads it as missing, as it always has.
- **The depth check:** Ctrl+K **Sync index history from IBKR**, offered
  only with IBKR on. It asks for SPX and VIX daily from 1980, one task
  each, through the cached source, which stores the bars. The log says
  the first day IBKR served, which is HEDGE decision 4's depth.

**Not checked live:** FRED needs your key, and IBKR needs TWS. Until
then, U1's programs can run on French's total-return level with no
dividend, a close stand-in for the price index's options.

**Tests:**
- FRED's JSON shape: "." days left out, the credit line in every
  citation, and each unreadable reply naming its fault
- the key: checked, never in an error, FRED's own reason reported
- the refresh: stale or forced, a failure keeping its copy, no key
- the `[fred]` setting
- SPX and VIX as index contracts with TRADES, and SPY still a stock
- both Ctrl+K commands on the worker
- the sync command absent with IBKR off
- the Data page's FRED note

## D70 — HEDGE U3: the hedge lab's service (2026-11-04)

`services/hedge.py` feeds U1's math with stored data and runs every
candidate on the book. It's offline only: nothing is fetched behind the
owner's back (V0). Whatever is missing is named, with how to get it. U4's
page will call it on the TaskRunner.

**Two books** (HEDGE §1):
- **The β proxy** (`proxy_book`): R_B = r + b·(R_m − r) on French's
  market and bills since 1926. It holds every crash, and it's a floor.
- **Today's mix** (`mix_book`): Portfolio's replay, P&L over today's
  equity with options repriced in full, plus the cash line's interest at
  the cash rate. It's exact, but it covers only the lookback. With h = 0
  a hedge leaves it unchanged, and the tests hold the replay to 1e-15.
- **β** is W2's regression β for the portfolio row, or the risk model's
  without regressions.

**A change to decision 4, approved 2026-11-04.** Decision 4 chose IBKR's
index for the option candidates, because French's series seemed to have
no price levels to strike against. Its total-return level is one.
- A put on it with no dividend has the economics of a put on the price
  index with its dividend, to a fraction of a percent of moneyness a
  quarter.
- It covers the whole VIX window from 1990, at least as deep as IBKR's
  can be.

So the option programs run on it, and IBKR's SPX history (U2) becomes the
cross-check. Today's mix uses its benchmark's own returns the same way.

**Today's shape** (§3): from the newest stored SPX chain, else SPY's,
fitted as the Volatility Lab fits it.
- **σ_ATM,30**, and ρ = σ_ATM,30 / today's VIX (FRED). VIX itself is the
  market's 30-day variance strike, standing in for VARIANCE I1's, which
  isn't built.
- **The smile** s(k, T) = σ(k, T)/σ_ATM,30, on a grid of log-moneyness
  (−0.8 to 0.4) and tenors (7 days to 18 months), interpolated linearly
  and held flat beyond, as the surface holds its vol
  (`models/hedge.grid_smile`).
- **The half-spread** (decision 6): the median (ask − bid)/(ask + bid)
  over the chain's out-of-the-money puts.
- **Without a chain:** flat, ρ = 1, no costs, and it says so. VIX sits
  above the ATM vol, so ρ = 1 overstates every put a little.
- **The flat smile** runs beside it on request (decision 5).

**The candidates** are decision 2's list (`v1_candidates`):
- puts 5, 10 and 20% out of the money, over 1, 3 and 12 months
- put spreads: 95/85 over a month, 90/80 over three
- the zero-cost collar on the 10% put over three months
- the two trend rules
- vol targeting at the book's own long-run vol
- cash
- any stored stream the caller names, read from stored bars (adjusted
  where stored). No holding is named in the code; U4's page asks.

Each runs at h = 25, 50 and 100%, on its own window. Options need VIX, so
they start in 1990, and a stream starts with its own history. Each result
says its window.

**Today's prices** (§6, `today`): a put's or a put spread's cost a year
at today's surface (`models/hedge.roll_cost`), and its percentile among
the same program's VIX-based costs over the window. A collar's net is
zero by construction, so it isn't priced.

**Checked on a copy of your store:** the β proxy at 0.9, the rule
candidates since 1926 (U1's numbers again). The option candidates say
FRED's VIX isn't stored yet, and the shape says there's no stored index
chain.

**Tests:**
- the proxy book and VIX's alignment
- missing data named for French, a stream and the chain
- options on VIX's window, and the rest on the book's
- today's mix equal to Portfolio's replay plus interest, with h = 0
  changing nothing
- the shape against a known SSVI surface: ATM vol, skew, ρ and
  half-spread
- a stream from stored bars, on its own window
- decision 2's list
- today's cost against its formula, and its percentile
- the grid smile and the roll cost against the plan's 2.9% a year

## D71 — HEDGE U4: the Hedge Lab page (2026-11-04)

Decision 9 gave the hedge lab its own dock. It's in the sidebar after
Portfolio (`gui/pages/hedge_lab.py`, the umbrella icon). It doesn't pick a
hedge: every candidate goes on one yardstick, and you choose.

**The side panel:**
- **The book.**
  - The book itself: the β proxy since 1926, or today's mix as the
    review analyses it (`ReviewService.current_view`, which needs
    REGIME.md's history source and cash rate).
  - β defaults to your newest review's, from A2's record
    (`ReviewService.latest_beta`), else 1.
  - The smile: today's surface, or flat beside it.
  - Holdings to try as hedges, read from stored bars.
  - The book's value, optional, for whole contracts.
  - A note says how the options were marked: which surface, ρ and
    half-spread, or flat with ρ = 1 and no costs when no chain is
    stored.
- **The selected hedge:**
  - Δ log growth ± SE, the cost on calm days ± SE, and the average saved
    per crash
  - the deepest fall and the worst 12 months, unhedged → hedged
  - the down and up betas ± SE
  - for an option program: its premiums, payoffs, half-spreads and
    rolls, today's cost a year with its percentile against history, and
    whole contracts on the chain's index, with the h they give

**The main column:**
- **Every hedge:** one row per candidate and size, in decision 2's order.
  Hedges that can't run are listed under the table with why ("Not run:
  … FRED's VIX isn't stored (Ctrl+K: Fetch FRED series)"). Selecting a
  row selects that hedge everywhere.
- **The frontier:** cost a year against the average saved per crash, one
  dot per row. Dominated dots are muted (`models/hedge.dominated`), and
  the selected one is highlighted. Nothing is optimized.
- **Growth and drawdown:** the book unhedged and hedged on the hedge's
  window. Growth is Σ ln(1 + r), so a century stays readable.
- **The crashes:** each window, with the book's return unhedged and
  hedged and what the hedge paid.
  - **Changed from HEDGE_PLAN:** the table is for the selected hedge. One
    column per candidate is 15 columns, which a 1280 px window can't
    show; the frontier and the table above carry the comparison.

**One job:** the whole run is one TaskRunner job. On a copy of your store
with a stand-in VIX, all 48 hedges ran in 10 seconds.

**At 1280 px** the side panel drops below the hedge table, as the GARCH
and Markov labs' inputs do (D56). From about 1440 px it sits beside it.

**Tests:**
- every hedge at three sizes; the selected hedge's card, curves and
  crashes; a rule hedge without program rows
- the option hedges named as missing without VIX
- a bad β naming its field
- the dominance rule
- the review's book and β for the lab
- page geometry at 1280 px

**Checked** with screenshots on a copy of your store, with a stand-in VIX
(21-day realized vol × 1.15) that exists only in the scratch copy.

## D72 — ALLOCATION A3, part 1: what-if vectors, references and the job (2026-11-04)

D59 put A3, the What-if page, after the hedge track. Part 1 is its math
and its job; part 2 is the page.

**A vector** (`models/whatif.py`) is a set of weights, each a share of
equity, with the rest in cash.
- **Typed:** one holding a line ("SPY 60", "QQQ: 12.5%"). A bad line,
  a symbol given twice, or a total past 100% is refused, naming the line.
- **Never a stored snapshot** (finding 5). `to_snapshot` makes a
  transient one, valued on a $100,000 notional, so the vector runs
  through `PortfolioService.analyse`, the analysis Portfolio runs.
- **Long-only, with no leverage.** IRAs can't short or borrow, so a
  vector can't either.

**The builders:**
- today, from the review's book: each holding's market value over equity
- today with the picks scaled to a total: the core unchanged, the picks'
  mix kept
- an index and cash
- today's picks netted into the core at a target β: A1's netting solved
  for the core, w_C = (β* − Σ wᵢβᵢ)/β_C. It's refused when the picks
  alone pass β*, or when it would need leverage.

**The references** keep today's names and invested share, long-only by a
softmax searched with `models/optimize.nelder_mead`:
- **Minimum variance** (Markowitz 1952).
- **Equal risk contribution** (Maillard–Roncalli–Teïletche 2010).
- **Equal weight** (DeMiguel–Garlappi–Uppal 2009). It isn't in the plan.
  It's added because it's the naive mix the optimizers often fail to beat
  out of sample, and it costs nothing.
- **Maximum Sharpe**, labelled "(in-sample)" (decision 6). It's
  Michaud's (1989) "error maximization", and the module docstring says
  why.

They're checked against their two-asset closed forms:
- minimum variance's weight formula
- equal risk as inverse vol, at any correlation
- the tangency portfolio Σ⁻¹μ

**The job** (`services/whatif.py`).
- **The context** is today's book as the review analyses it
  (`ReviewService.current_view`). It fixes the history source, benchmark,
  lookback, cash rate and sleeves, at one as-of time, so every vector
  gets exactly today's window.
- **`compare`** runs each vector through Portfolio's analysis, then
  `ReviewService.grade`, which is new: sections 3 and 4 on any book, as
  measured. It has A1's core exemption, look-through N_eff and netting,
  and A2's crash stress. Sections 5–8 need options, streams, gates and
  valuations, which a vector doesn't have.
- **A vector whose names have no history** says so, and nothing is
  fetched.
- **Named sets** are kept in the view-state store (decision 5).

**The Treynor–Black builder** that BUDGET folded into A3 waits for
BUDGET's decisions 2–7: the budget's β, the fraction f, Σ_ε, a negative
core, grading, and the data's α̂ beside your belief.

**Tests:**
- vectors read and refused by line
- the transient snapshot
- each builder, the netting landing on β* exactly
- the references' closed forms, all long-only on the invested share
- today's vector analysed equal to Portfolio's analysis of the book
- every vector graded as the review grades it (the core exempt)
- the builders and references from the context
- missing history named
- named sets saved and deleted

## D73 — ALLOCATION A3, part 2: the What-if page (2026-11-04)

The page on D72's job (`gui/pages/whatif.py`). It's in the sidebar after
Portfolio. Nothing on it trades, and no vector is ever stored as a
snapshot.

**The side panel builds vectors:**
- **Typed:** a name and one holding a line, added to the comparison. A
  bad line is refused, naming it.
- **Built:**
  - today
  - today's picks to a total (%)
  - an index and cash, on the benchmark (%)
  - today's picks netted into the core at a target β
  - the four references

  Only the chosen builder's parameter field is shown.
- **Saved sets:** save the typed vector by name, or load or delete one
  (decision 5).
- **A note** gives the analysis every vector gets: today's history
  source, benchmark, lookback and cash rate.

**The main column compares them:**
- **The vectors:** each one's holdings and cash. Select one to remove it.
- **The comparison:** the measures down, one column per vector:
  - β ± SE, vol, VaR 95 and ES 99 (a day)
  - the lookback's drawdown, and A2's crash stress with its band
  - effective holdings, looked through the core where it can be
  - the top risk share, growth, and the fails and warns

  With many vectors the table scrolls sideways.
- **Risk shares:** each name's share of each vector's vol.
- **Notes:**
  - max Sharpe's in-sample warning, whenever it's compared (decision 6)
  - the analysis settings
  - each vector that couldn't run, and why
  - why a measure is blank (French's series not fetched, the core's
    N-PORT not stored), said once rather than in every column

**The context** loads when the page opens. Without REGIME.md's section 2
or positions, the page says what's missing, and Compare says to load
today's book first.

**`ReviewService.grade`** now also says why N_eff couldn't look through
the core (`Graded.effective_note`), for those notes.

**Tests:**
- the page on the review tests' made-up book:
  - the context, and today's vector
  - every builder, a typed vector, removal, a refused line
  - the four references
  - the comparison's shape, the max Sharpe warning, and the blank-measure
    note
  - risk shares
  - a set saved, loaded and deleted
- the page without a regime, saying what it needs
- page geometry at 1280 px

**Checked** with screenshots on that made-up book: seven vectors (today,
an index mix, a netted mix, and the four references) compared.

## D74 — ALLOCATION A4: snapshot hygiene (2026-11-04)

ALLOCATION finding 5: one export imported twice under two layouts made the
review draft a trade that never happened, and a snapshot couldn't be
deleted. Decision 7 settled it: deleting is allowed, after a backup, and
only by your action.

**Each import keeps its file's hash** (schema v10, migrated after a
backup).
- `position_snapshots.sha256` is the file's SHA-256, or for several files
  of one day, the hash of their sorted hashes, so their order doesn't
  matter.
- Snapshots from before v10, and ones kept from the broker, have an empty
  hash. A re-import of one of those older files can't be caught, but
  every import from now on can.

**A file imported before is flagged before it's stored**
(`DuplicateImportError`, nothing saved). The message names the earlier
import, and says what a second copy would do:
- **Under another layout:** two snapshots of one day under two layouts
  make the review draft trades that never happened.
- **The same layout, with newer imports since:** it becomes the newest
  snapshot, so the review would read those positions as today's. That's
  the same phantom, and the tests reproduce it.
- **The same layout, still the newest:** a second copy only lengthens the
  history.

The Portfolio page asks first: no leaves it unimported, and yes imports it
anyway.

**Deleting a snapshot.** The Portfolio page's new **Imported snapshots**
card lists every stored import: its number, layout, as-of date and
import time.
- **Delete…** asks first, then backs the whole store up and deletes the
  snapshot and its lines.
- **The backup** goes to `backups/`, named `…before-deleting-snapshot-N…`,
  through `storage/schema.backup_file`. That's D36's backup made general;
  the migrations use it too.
- **The card keeps the backup's path** in its subtitle, even if the
  re-analysis afterwards fails.
- **Only ever on your click.** Nothing deletes automatically.

**Tests:**
- the same file flagged before it's stored, then stored when allowed
- the other-layout message
- several files hashing the same in any order
- a delete: the backup holds the deleted snapshot, the store doesn't, and
  an unknown number is refused
- the review's drafts going phantom with an older file re-imported, and
  back to the real ones once it's deleted
- the v9 → v10 migration shaped like a new file, old imports kept with
  empty hashes
- the Portfolio page: asking before a re-import (no, then yes), and
  deleting after a backup

**Checked live:** the v9 → v10 migration on a copy of your store, with its
backup. Your five stored snapshots are kept, with empty hashes.

## D75 — VARIANCE I1: model-free implied variance (2026-11-05)

VARIANCE_PLAN's first milestone: the price of variance read off a chain,
offline. `models/implied_variance.py`, with the derivation in its
docstring, as the plan set it out. Nothing reads it yet: I2 adds the
realized side and the premium, I4 the card.

**Two estimators, each checking the other:**
- **From the fitted smile:** each slice's K_var·T as 2∫e⁻ᵏ b(k, w(k)) dk,
  split into the quoted range and SVI's extrapolated wings, with the
  extrapolated share. Simpson on 4,000 intervals per piece, broken at
  k = 0 (where the out-of-the-money price has a kink) and at the quoted
  range's ends. It runs out to where z = −d₂ reaches ±8.
- **From the quotes (the VIX way):** CBOE's sum over out-of-the-money
  quotes, with the K₀ term, at mid, bid and ask. A strike with no bid is
  skipped, and two in a row end that side, as CBOE does. The bid and ask
  sums bound the quoted strikes' contribution with no model, since every
  weight is positive.
- **The z-form** (the smile's total variance averaged under a standard
  normal in z) is computed too, as the test of the first. They agree to
  about 10⁻¹¹.

**Three choices the plan left open:**
- **The exact K₀ term,** 2[F/K₀ − 1 − ln(F/K₀)]. CBOE uses its
  second-order form, (F/K₀ − 1)²; the exact one costs nothing.
- **The band check is like-for-like.** The plan compared the fit's
  integral over the quoted range with the quotes' band. But the sum and
  the integral differ by discretization and by the half-strips past the
  end strikes (Jiang & Tian 2005). So the check sums the *fitted* prices
  on the same strikes with the same weights, and flags a fit outside the
  band.
- **K₀ is the highest strike at or below F with a two-sided put.** If its
  call isn't quoted, parity gives it, from the chain's carry.

**Across expiries:**
- constant-maturity tenors, linear in total variance between the
  bracketing expiries, never extrapolated (the headline is 30 days)
- the quote sums and their band, interpolated alike
- forward variances, with the calendar check

**A correction to the plan.** Its table gave 16.5% for the VIX sum on
5-point strikes over −6 to +3 sd. The code gives 16.4%. That's the band's
own integral, which strikes that dense should match, and the right value.
Every other figure in the table reproduces: 16.6%, the band shares (86%,
95%, 98.5%), and the steep skew's 19.3% and 73%. So do the Merton
example's 14.88% against 15.03%.

**Also:**
- **`SyntheticChainSpec` gains `k_range_sd`.** The default is today's −2.5
  to +1.5, so every existing draw is unchanged. The tests use it for
  decision 2's −6 to +3.
- **The canon (derivatives strand) gains five papers:**
  - Carr–Madan 1998, recorded as unchecked: Crossref has only its 2001
    reprint, with no year
  - Demeterfi et al. 1999
  - CBOE 2003, the VIX white paper, recorded as unchecked: it has no
    identifier
  - Jiang–Tian 2005
  - Fukasawa 2012

  Britten-Jones–Neuberger 2000 and Lee 2004 now also list the new module.

**Tests (25):**
- a flat smile giving σ² exactly
- the k-form equal to the z-form on fitted SVI, joint SSVI and the true
  surface
- the plan's worked example
- on the synthetic world (four seeds, both bands):
  - the fit's sum inside the band at every expiry
  - the whole-smile estimate within half the band of the truth
  - the wide band cutting the extrapolated share from about 18% to
    under 5%
- a lifted fit flagged
- on a Merton chain:
  - the spanning integral matching −2E[ln S_T/F] to 10⁻⁶
  - sitting below E[QV] by the jump term
  - the quote sum on a dense chain getting there too
- the sum converging at h² as strikes densify, and truncation only
  lowering it
- K₀ and parity; skipped and cut-off strikes; chains that can't make a
  sum
- interpolation exact on additive variance, never extrapolated
- forward variances and the calendar flag
- inputs validated

**Not checked live:** the SPX/SPY check with TWS running (the plan's last
I1 item) needs TWS. It records the extrapolated share at today's band and
at a wide one. Decision 2 already settled on the wide band, so the check
measures it rather than deciding it.

## D76 — VARIANCE I2: the realized side and the variance premium (2026-11-05)

VARIANCE_PLAN's second milestone. The math is in
`models/variance_premium.py`, with the derivation in its docstring, and the
service is `load_variance_premium` in `services/vol_context.py`. Nothing
shows it yet: the card is I4.

**Windows count trading days (decision 6).**
- **The ex-post window:** a snapshot on day d₀ prices the NYSE trading days
  after d₀, through d₀ + 30 calendar days. Those returns run from d₀'s
  close.
- **The trailing window:** the 30 calendar days ending at the last close
  before d₀, so nothing in it looks ahead.
- **The count is exact,** not 30 × 252/365, which can be a day off: about
  5% of a month's variance.

**Realized variance** is the sum of squared close-to-close log returns
over the window: adjusted closes (D38), zero mean, as variance swaps
settle. Every trading day in the window, and the one before it, needs a
bar, or the window is left out and named. Bars on non-trading days are
ignored.

**GARCH's expectation** over a window that starts s trading days after
the last bar is E[RV] = n(μ² + σ̄²) + (h − σ̄²)·pˢ(1 − pⁿ)/(1 − p). At
s = 0 and μ = 0 it is n times `average_variance_forecast(n)`. The μ²
term counts because RV is zero-mean; it's tiny, but exact.

**The three premiums:** against GARCH, against trailing realized (the
Bollerslev–Tauchen–Zhou measure), and ex post.
- **Units:** each compares totals over one window, then annualizes both by
  the same 1/T (never 252 against 365). It's shown in vol points for
  reading, and averaged in variance.
- **Pending windows:** an ex-post value is pending until its window's
  last bar exists, and never enters an average until then.
- **Split-sized gaps:** a window holding one (D57), or missing a bar, is
  named and left out.

**The mean's error, and a limit on it.**
- **The method:** Newey–West on a constant (`models/regression.ols`),
  with the lag at twice the windows' overlap. Daily 30-day windows each
  share returns with about 20 later ones.
- **Measured coverage:** on Gaussian returns the ±1.96 SE interval
  covered 85% at one year of history, 89% at two and 93% at five. It is
  too narrow on a short history.
- **What the result says alongside:** how many non-overlapping windows the
  history holds, about a dozen a year.
- **The alternative left out:** Kiefer–Vogelsang fixed-b critical values
  raised the one-year coverage to about 92%. They would add a paper and
  coefficients I couldn't verify here.

**The history is the quote sum, not a fit.**
- **Each stored day** (its first snapshot, the collector's) is valued with
  `quote_tenor`: the quote sums alone at 30 days, the VIX's own number.
- **Why:** a surface fit takes about 0.27 s a snapshot, so a year of
  stored days would take over a minute. The sum takes 0.3 ms and needs no
  model.
- **What it misses:** with the wide variance request (decision 2) it holds
  about 98% of the variance.
- **Today's fitted K_var,** wings included, is shown beside it. The
  trailing premium and its percentile use the quote sum, so today is
  ranked like for like.

**Today's percentile:** today's trailing premium among those on every
earlier snapshot, ties counted half, once there are 20.

**Only completed sessions count.** A bar dated today is dropped, since the
session may still be open. GARCH sees bars only up to the newest
snapshot's day.

**Also:**
- `implied_variance.quote_tenor`, with a shared bracketing helper for
  `constant_maturity`
- `ChainService.load(id)`
- `vol_context` gets a `_garch` helper, shared with `load_vol_context`.
  The Volatility Lab's `garch_vol` curve is unchanged; I4 sets the
  premium beside it.

**Canon:**
- **New:** Bollerslev–Tauchen–Zhou 2009 and Driessen–Maenhout–Vilkov
  2009, both checked on Crossref.
- **Now also listing the new module:** Carr–Wu 2009, Newey–West 1987 and
  Bollerslev 1986.

**Tests:**
- **The model (17):**
  - the GBM world priced at its own variance: mean zero within three
    errors
  - a planted 1.15σ premium recovered
  - coverage across 120 seeds near the table
  - no look-ahead: values appear only once their window closes, and
    appending bars changes none
  - trailing values known at the snapshot
  - Thanksgiving, weekend and trailing windows counted right
  - realized variance ignoring a stray holiday bar
  - the GARCH sum against its closed form, with skipped days and a mean
  - D57 and missing-bar exclusions
  - overlap, independent windows, the percentile, validation
- **The service (3):** today's premiums and the history from stored
  chains, with:
  - another symbol's bars
  - the day's first snapshot
  - a session still open
  - a split-sized gap named and its windows left out
  - no stored chains
- **The I1 module (1 more):** `quote_tenor`.

**Not checked live:** SPX's chains against SPY's bars need stored SPX
snapshots and TWS.

## D77 — VARIANCE I4: the variance premium on the Volatility Lab (2026-11-05)

VARIANCE_PLAN's last milestone, the card. I3's variance chain request is
still to do. The page is `gui/pages/volatility_lab.py`; the service is
`load_variance_premium` in `services/vol_context.py`.

**The Term structure view gains √K_var per expiry.**
- **Where it comes from:** the fit on the page. The fit job now also
  computes `implied_variance(fit)`, which takes 26 ms on the default chain.
- **How it's drawn:** beside ATM IV. A point is hollow when over half of
  its K_var comes from the extrapolated wings.
- **The band:** the quote sum's bid–ask band, from the quotes alone, as
  the VIX sums them. The gap between the line and the band is what the
  wings add.

**Two new cards, below the views.**
- **Variance premium**, for the fit on the page:
  - **Tiles at the 30-day tenor:**
    - √K_var, with ATM and the wings' share
    - the quote sum, with its bid–ask
    - the premium against GARCH
    - the quote sum's premium against trailing realized
  - **A row per expiry:**
    - the sessions in its window
    - √K_var, ATM IV and the wings' share
    - GARCH, and the premium against it
    - realized over as many calendar days before, and the premium
      against that
  - **The service's notes:** offline bars, missing windows, split gaps.
- **Premium history:**
  - **The chart:** each stored day's 30-day quote sum against the
    realized vol after it and before it. A second panel shows the premium
    in vol points, with the mean drawn as a reference line.
  - **The tiles:**
    - the mean ex-post premium, with its z and window counts
    - today's percentile
    - the windows complete, pending and left out
  - **A synthetic chain** has no history, and the card says so.

**One GARCH fit, as of the chain.**
- **What the screenshots showed:** on a stored SPX chain from
  2025-10-31, the overlay's 30-day GARCH read 12.9% and the card's 15.5%.
  The overlay was fitted on bars to today, the card on bars to the chain's
  day.
- **The fix:** the premium job returns its GARCH fit and trailing vols as
  the overlay's context. The chart and the cards now draw the same fit, as
  of the chain.
  - Compare with history, loaded before any fit, is still as of now.
  - The lookback is counted back from the chain.
  - Bars load back as far as the longest expiry's trailing window.
- **What still differs, on purpose:** the chart's GARCH curve assumes 252
  sessions a year, while the card counts each window's own (decision 6).
  - **An example:** the 30 days after Oct 31 hold 19 sessions (weekends
    and Thanksgiving). That window's GARCH reads 15.9%, against the
    curve's 16.5%.
  - **Where the page says so:** the table's Sessions column, and the
    GARCH tooltips.

**Inputs live in one place.** The premium uses Compare with history's
source, symbol and lookback (SPY for SPX, decision 3). The card's Load
history button runs the same load, so there are no new fields.

**Threading, and D15.**
- **When it prices:** one history load prices the fit on the page. After
  that, every new fit (a fetch, a refit, SVI or SSVI) is repriced in a
  TaskRunner job of its own.
- **While it prices:** the cards and the overlay are blank until the job
  lands. Nothing on the page describes an earlier fit or another day's
  GARCH.
- **Late results:** the worker is FIFO (D9), but a fit can still land
  between a pricing's submit and its result.
  - Each result carries the fit it priced, and one for a replaced fit is
    dropped.
  - A fit that lands during the first load is priced when the load
    returns.

**The service changed shape.** I2's version is unmerged, so nothing else
called it.
- **It takes a fit, not a symbol:**
  `load_variance_premium(market, chains, fit, source, realized_symbol, ...)`.
  - The page's fit is today's.
  - The chain's symbol names the stored history. `history=False` skips
    it, for a synthetic chain.
- **A chain with no stored snapshots** is now priced, with a note. Before,
  it was refused.
- **New in the result:**
  - each expiry's trailing premium (the same length, before the chain)
  - `tenor_trading_days`
  - the `VolContext`

**Also:**
- `QuoteTenor.vol_band`
- `CurveChart`:
  - per-point hollow markers (`Curve.hollow`)
  - a band's readout format and color
- The history chart reads out one line per panel.

**Tests:**
- **The service (6):** I2's three, reworked, and three new:
  - **Today's premiums and the history:**
    - each expiry's trailing premium against its window's realized sum
    - the shared GARCH: its returns, its end and the first expiry's
      expectation
    - today's quote sum from the fitted chain, not the day's first
      snapshot
  - **A split-sized gap** named, and its windows left out
  - **A chain with no stored snapshots,** priced with a note
  - **`history=False`:** no lookup, and no note
  - **Bars that end before the windows:** GARCH and the trailing windows
    say why
  - **A blank realized symbol** is refused
- **The GUI (3):**
  - **A synthetic fit:**
    - √K_var and its band on the term chart, and in the hover readout
    - the cards empty until loaded, then filled
    - the overlay's GARCH is the card's
    - a stale result dropped
    - a refit repriced
  - **Stored SPX chains against SPY's stored bars, offline:**
    - the newest snapshot on the page
    - the overlay as of its day
    - every window complete, with the mean, the percentile and the chart
  - **Hollow markers** drawn as outlines
- **The I1 module:** `vol_band` (one more assertion).

**Checked by eye:** offscreen at 1280 px, on 170 synthetic SPX days: the
term chart and both cards.

**Not checked live:** that needs stored SPX chains and TWS. I3's variance
chain request (a wider band and more strikes, with its fetch time
measured) is still to do.

## D78 — REGIME_MODEL Q1: the inputs, each as it was known at the time (2026-11-05)

The owner approved Q1 on 2026-11-05. REGIME_MODEL_PLAN's other
milestones, and its open decisions, still wait.

The math is in `models/regime_inputs.py`, with the derivation in its
docstring. The service is `services/regime_inputs.py`. Nothing shows the
inputs yet: Q2 builds on them and Q5 is the card.

**One rule: a value on day t uses only what was published by t's close**
(Goyal & Welch 2008's discipline).
- **Daily inputs** use returns through t.
- **A monthly value** for month m, with a publication lag of ℓ months, is
  used from the 1st of month m + ℓ + 1, and is indexed by that date.
- **The lags:**
  - P and GS10: 0, since monthly averages are known when the month ends
  - CPI: 1
  - earnings and dividends: 4. Your Oct 24 file held earnings through
    June. The earlier scratch valuation run used 3, and the lag is a
    parameter.

**The inputs:**
- **Daily, on French's market** (Mkt-RF + RF):
  - realized volatility over 21 days (zero-mean, as D76's is)
  - the 12-month log excess return
  - the total-return index against its 200-day average
  - GARCH's expected volatility over the next 21 days
  - the HMM's calm-regime probability, with K = 2
- **Monthly, on Shiller's data:**
  - CAPE in real time: the latest price over the latest CPI known, and 120
    months of real earnings ending at the latest earnings known
    (Campbell & Shiller 1988)
  - the excess CAPE yield: 1/CAPE less (GS10 − the trailing 10-year
    inflation)
  - the stock–bond correlation over 36 months:
    - the stock's total return, with the dividend lagged like earnings
    - a 10-year par bond repriced each month at the next GS10
- **Each input** is kept raw, as a real-time percentile, and as a
  real-time z. Both scales use only earlier values. They wait for 120
  months or 1,260 days of them, and ties count half.

**GARCH and the HMM are refitted walk-forward.**
- **When and on what:** once a year, on the first trading day of January,
  on the 10 years before it. Nothing on or after the refit day is used.
- **Between refits,** the parameters run their causal recursions forward
  from the window's start.
- **Nothing later moves an earlier value.** Refits are anchored to
  calendar years, so appending data moves none. A window counts as full
  when the data start within its first week.
- **Measured on French's market, 1926–2026:** 90 refits from 1937, none
  failed. They took 218 s, about 2.4 s a year, more than the 0.3 ms a day
  I'd measured on simulated data. It's a worker-thread job, or Q3's.
- **Why, and what it found about W7:** one fit on the whole sample would
  give each past day parameters from later data. W7's calm-gate history
  does exactly that (D49). Today's gate value is fine, since the sample
  ends there. Its chart and persistence read a history with that
  look-ahead. That's left as it is, and noted.

**Left out, for decisions of yours:**
- **The credit spread.** FRED serves Moody's monthly Baa yield from 1919,
  but Moody's notice on it says the data may not be "stored for
  subsequent use" for redistribution or resale without consent. Its
  graph says the data are copyrighted. D60 refused storing Cboe's data on
  a similar clause. So it waits for your terms call (D34).
- **A daily stock–bond correlation:** it needs FRED's DGS10.
- **The horizons** (decision 3): 21 days for GARCH is a parameter until
  Q2.

**Tests (14):**
- **The scales:**
  - the percentile against a direct count, with ties and missing values
  - z against the earlier mean and sd
  - both missing until their minimum
- **The lags:** changing one month of P, CPI or E first moves CAPE
  exactly ℓ + 1 months later.
- **Closed forms:**
  - CAPE with no lags is Shiller's formula
  - the ECY is its definition, with the real lags
  - a par bond's month: an unmoved yield returns c/12, and a small move
    matches the duration approximation
- **The correlation:**
  - it equals Pearson's on each window
  - it recovers a planted ρ = 0.5 within its Fisher interval. The yields
    are backed out from planted bond returns.
- **The daily inputs** against their definitions.
- **The walk-forward:**
  - refits each January
  - nothing before the first
  - a refit's GARCH volatility and calm probability match a fit by hand
    on its window
- **No look-ahead:** truncating every series mid-way changes no earlier
  value, on any input or scale, the walk-forward included.
- **The service:**
  - every input from stored series, with their provenance
  - without Shiller, or without French, the inputs it can build, and
    how to fetch the rest

**Canon:** Goyal–Welch 2008, Campbell–Shiller 1988, Hamilton 1989 and
Bollerslev 1986 now list the module.

**Checked live:** French's factors were fetched into a scratch store and
the century built.
- **On 2026-09-30, French's last day:**
  - realized volatility 10.7%
  - GARCH 13.5% over the next 21 days
  - calm probability 98.6%
  - the 12-month log excess return +10.3%
  - the index 5.8% above its 200-day average
- **Against the plan's worked example** (SPY, Oct 28): 9.6%, 13.8%, 99%,
  and 6.5% above.
- **Known episodes:** the calm probability was about 0 at the ends of
  October 1987, October 2008 and March 2020, with GARCH volatility at
  61%, 65% and 55%. In mid-2017 it was 0.99, at 13%.

**Not checked live:** the Shiller inputs. Your Shiller CSV isn't on disk,
and your store has no outside series until PRs #22 on are merged.

## D79 — REGIME_MODEL Q2: σ̂, μ̂ and the tail (2026-11-05)

**Decision 3, the horizons,** was settled on 2026-11-05: 21 trading days
for σ̂, and 12 months for μ̂ and the tail. The derivation was written into
`models/regime_model.py`'s docstring and approved before any code, as the
plan asks. Each point the review raised was kept as proposed:
- the inputs enter raw
- the expected-sign table
- the floor is in log terms
- equal weights for μ̂, and a linear pool for the tail
- month-end origins

**Origins.**
- **When:** the last trading day of each month, from July 1926.
- **The targets** over the next 12 months, from French's market:
  - the log excess return
  - the deepest fall, including one from the origin's own level
- **What's known when:** a target is known only 12 months later. At each
  origin, every model is fitted on the origins whose targets were known by
  then, and on nothing else.
- **Overlap:** the targets overlap by 11 months. Errors use Newey–West
  with 22 lags, twice the overlap.

**σ̂.** Q1's walk-forward now keeps each day's next variance and each
year's GARCH parameters. `garch_vol_ahead(walk, n)` reads any horizon,
and its 21-day value is exactly Q1's input.

**μ̂: a combination forecast** (Rapach, Strauss & Zhou 2010).
- **The forecast:** the historical mean plus the average of each input's
  OLS tilt.
- **Campbell & Thompson's restrictions** (2008, new in the canon, checked
  on Crossref):
  - a slope with the wrong sign falls back to the mean
  - the combination is floored at 0
- **Its error,** by the delta method on influence functions, with
  `ols`'s Newey–West kernel and small-sample factor:
  - with one input it equals `ols`'s c′Vc exactly
  - with none, the mean's error
- **The floor:** the error is of the unfloored forecast, and the card will
  say when the floor binds.

**The tail: P(fall ≥ d within 12 months)** for d = 10, 20, 30 and 40%.
- **The models:** one logistic regression per input, fitted by IRLS in
  numpy.
- **Falling back to the base rate:** an input does when
  - its slope has the wrong sign
  - its events are perfectly separated
  - its sample has fewer than 3 episodes. An episode is a run of event
    origins, so one crash counts once, not twelve times.
- **The pool:** the inputs' probabilities are averaged, then made
  non-increasing in d by pool-adjacent-violators.
- **The interval:** the delta method on the logistic's influence, formed
  on the logit scale so it stays in (0, 1).

**Measured coverage of the ±1.96 SE intervals** (300 seeds). On
overlapping targets they run a little short, as D76's did:
- **μ̂'s mean:** 89% with 29 years of targets, 91% with 50, 92% with 90.
- **The tail, overlapping 12-month events:** 91% with 50 years, 92% with
  90.
- **The tail, one-month events with no overlap:** 94%.

**On French's real century, with the five daily inputs** (Shiller isn't
in the scratch store), over 1,203 origins: the inputs took 214 s, μ̂
0.3 s and the tail 3.8 s.
- **μ̂ doesn't beat the historical mean.**
  - The combination's R²_OS is −0.9%.
  - Each input's is between −0.6% and −1.5%.
  - That's Goyal & Welch's finding again. The valuation inputs, where the
    earlier scratch evidence was, need Shiller, and Q3 decides what
    stays.
- **The tail beats the base rate** at every threshold. Its Brier skill is
  +7.9% at 10%, +7.1% at 20%, +2.3% at 30% and +4.0% at 40%. Volatility
  clusters, and falls come with it.
- **On 2026-09-30:**
  - σ̂ 13.5% over 21 days, and 18.6% over 252
  - μ̂ +6.7% ± 1.7%, against a historical mean of +6.6%
  - P(fall ≥ 20% within 12 months) 21% [15%, 29%], against a base rate
    of 25.5%
  - P(≥ 40%) 2.9% [1.1%, 7.1%]
- **Episodes:**
  - **2008-09-30:** P(≥ 20%) was 61%. The fall that followed was 41%.
  - **2007-09-28:** 23%, before a 27% fall.
  - **1999-12-31:** 19%, before 22%.
  - **2017-12-29:** 17%, before 20%.

These are research readings, not targets: Q4 makes the rules, and nothing
reads them yet.

**Tests (15):**
- **The origins:**
  - month-ends
  - the targets against their sums
  - the deepest fall against brute force
  - the known dates
- **σ̂:** at 21 days it equals Q1's, and at 252 the closed form.
- **Planted truths:** a planted slope is recovered with R²_OS > 0.1, and
  a useless input averages R²_OS < 0 over 40 seeds.
- **No look-ahead:** changing a target that becomes known later moves no
  earlier forecast, and truncation moves none.
- **The restrictions:**
  - a wrong sign gives the mean
  - an input with no expected sign is free
  - a negative premium is floored
- **The errors:** equal to `ols`'s, and both coverages measured.
- **IRLS:** the closed form with one binary regressor, and separation
  detected.
- **Episodes and pool-adjacent-violators.**
- **The tail:**
  - calibrated within binomial error in each decile, with a better Brier
    score than the base rate
  - monotone in d, and untouched where already monotone
  - a wrong sign or too few episodes gives the base rate
- **Together:** the estimates run on Q1's inputs.

**Canon:** Campbell–Thompson 2008 is added (Crossref: print 2008, online
2007). Rapach–Strauss–Zhou 2010 and Goyal–Welch 2008 now list the
module.

**Next:** Q3, the evidence run, needs decision 6 (the inclusion bar).

## D80 — REGIME_MODEL Q3a: Research gets a moving cash rate and recorded runs (2026-11-05)

The owner started Q3 on 2026-11-05 and set the inclusion bar (decision
6): a family-wise p of 0.05, from the circular-shift test.

**Q3 comes in three parts:**
- **Q3a, here:** the two things TARGET T4 said Research lacks. It involves
  no decisions.
- **Q3b:** the evidence harness, derived first for review. It has to score
  variants with a provisional β rule, before Q4 builds the real one.
- **Q3c:** the family-wise shift test and the saved run.

**The 2026-10-25 test, to reproduce.** The scratch run
(`spikes/target/combo_timing.py`, local) defines it:
- **The statistic:** the classical t of the β-timed excess return's α on
  the market.
- **The null:** every rule's β path is shifted by the same k days, at
  least five years from either end, over 2,000 draws.
- **The family p:** the share of shifts whose best t, across all rules,
  reaches the observed best.

**A cash series in the engine** (`models/backtest.py`).
- **The problem:** over a century the bill rate runs from 0 to 15%, and
  the engine earned a constant rate.
- **The change:** `backtest` takes each session's cash return, dated by
  the session it's earned in, as French's RF is published. R1's
  derivation holds with f replaced by f_(t+1).
- **One source of cash:** a series and a nonzero `cash_rate` together are
  an error.
- **The result records the series it used,** a constant one from
  `cash_rate`, so the statistics measure excess over the same cash.

**Statistics over it** (`models/backtest_stats.py`). Sharpe, its error,
Sortino, the rolling Sharpe and the deflated Sharpe each take the series,
and subtract it period by period. With a constant series they equal the
annual-rate results.

**Recorded runs** (`ResearchService.record`). These are decisions made
outside Research's five strategies, such as the regime model's β path.
- **What they keep:** the prices, the decisions, the cash series, and the
  input series behind the decisions, with their provenance. A free-form
  `details` holds the model's specification.
- **The same evidence as a strategy run:**
  - the SHA-256, which covers the cash and input series too
  - replay without fetching, checked against the saved equity
  - the same buy-and-hold benchmark
- **The kind:** a recorded run's payload says `"kind": "recorded"`. Older
  runs have none, and still reopen as strategy runs.
- **On the Research page,** the log opens a recorded run without touching
  the experiment form. The page names its model, its inputs with their
  dates, and its cash. The exposure axis widens past 100% when a run
  holds more.

**Tests (8):**
- **The engine:**
  - a constant series equals the annual rate exactly
  - idle cash compounds a moving rate
  - half-invested earns half of it
  - bad series are refused
- **The statistics:** a constant series matches the rate, and a moving
  one is subtracted period by period.
- **The service:**
  - a recorded run replays with its cash
  - changing the stored cash breaks the fingerprint
  - its inputs are checked
- **The page:** a recorded run opens from the log. The existing Research
  tests all still pass.

**Next: Q3b's derivation, for review.** The harness has to set the β rule
it scores variants with, the growth measure and the ledger. The real
rule needs decisions 4 (D and p) and 5 (f), and Q4 builds it.

## D81 — REGIME_MODEL Q3b–Q3c: the evidence run, and what it keeps (2026-11-05)

Q3b's derivation was written into `models/regime_evidence.py`'s docstring
and approved before any code. Each reviewed point was kept as proposed:
- timing α as the gate
- the whole-window bar, plus a positive α after publication
- October's 31 rules ported and counted
- the one-input-alone variants included
- the publication years
- the constant-β level read at run time

Q3c built it:
- `models/timing_rules.py`: October's 31 rules
- `models/regime_evidence.py`: timing α, both shift tests and the
  reports
- `services/regime_evidence.py`: the run
- Ctrl+K: "Run the regime evidence"

**The port is exact.** On `spikes/target`'s own French and Shiller files,
all 31 β paths match the scripts bit for bit. All 31 classical t's match
to two decimals, from ECY × vol's 4.85 down to the dip rule's −4.63.

**What the run does** (about 4½ minutes: the inputs take 219 s, the rest
46):
1. **Q1's inputs:** French's century and Shiller's data.
2. **57 daily β paths:**
   - October's 31
   - vol targeting (μ̂ is the historical mean)
   - the full model, the model without each input, and each input alone
   - each input's difference path, full minus without
3. **The shift test:** all 57 shifted together, 2,000 times, on their
   common days, 1937-07-01 to 2026-09-30.
4. **The tail:** each input's forecast and the pool, at four thresholds:
   36 forecasts, each shifted against its events.
5. **What's kept:**
   - a μ̂ input whose difference path has adjusted p ≤ 0.05 and a positive
     α after its paper's year
   - a tail input whose forecast passes the same way
6. **The save:** one Research recorded run. Its details hold the ledger:
   every path's id, its SHA-256 and its statistics.

**The result on French and your Shiller CSV: no input is kept.**
- **The family has timing:** p_FW is 0.002. The best path is ECY alone, at
  t 4.64.
- **ECY passes the whole-window bar but fails after publication.** Its
  difference path has t 4.19, α +5.3% ± 1.6% and adjusted p 0.009. Since
  2021, after its 2020 paper, its α is −0.10% ± 0.77%. That's
  indistinguishable from zero, but not positive, so the protocol drops
  it.
- **The other difference paths:**
  - CAPE is positive but short of the bar (t 1.31, adjusted p 0.97)
  - the two vol measures, both trends, the calm probability and the
    stock–bond correlation all *lower* timing α when added (t −1.3 to
    −3.2)
- **The tail:** realized vol, GARCH, the calm probability and the pool
  each beat the base rate at 10–20%, by 6–10% in Brier score. Their timing
  is real on its own: p < 0.001 against randomly timed deviations. Across
  the 36-forecast family, the best adjusted p was 0.095 (the pool at 20%),
  so none clears 0.05. Vol's skill since 2017 is negative.
- **The kept model is vol targeting:** μ̂ is the historical mean, with
  GARCH's σ̂. At f = ½ with no leverage, its growth over constant exposure
  is −0.02% ± 0.52% a year, with a worst drawdown of −51%.
- **For reference, the full model** had t 4.01 (adjusted p 0.022), μ̂
  R²_OS +7.1% against the historical mean, and growth +0.15% ± 0.56% at
  f = ½.
- **October's ECY rules** still clear the family in this window:
  - ECY cheap 10%/40%: adjusted p 0.018
  - ECY cheap: 0.022
  - ECY × vol: 0.025

**What the result can't settle:**
- **The post-publication window is short.** Six years can't tell a
  surviving half-effect from none. Loosening the rule now would be a
  choice made after seeing the result. If you make it, the ledger records
  it as a new variant, and the family counts it.
- **Literature selection** is the risk McLean & Pontiff measure. This is
  what it looks like.

**Found while building, in the tail's test:**
- **The null shifts each forecast's deviation from the base rate,** not
  the whole forecast.
  - **Why:** the base rate drifts over a century. Shifting a whole
    forecast put that drift out of line, so a worthless forecast looked
    skilled.
  - **The test:** a forecast of the base rate plus noise read p = 0.0
    before, and reads p > 0.2 now.
- **Keeping a tail input also takes positive skill.** Under this null, a
  forecast worse than the base rate can still beat randomly timed
  deviations: ECY at 30% had skill −0.007 and p 0.009.
- **A forecast with nothing to test is left out with p = 1:** an exact
  base rate (a NaN skill had read as p = 0), or a forecast that never
  leaves the base rate.

**Tests (12):**
- **Timing:**
  - scale-free, with constant β at zero
  - α equal to `ols` with Newey–West
- **The shift tests:**
  - planted timing passes the family test, and adjusting never lowers a p
  - a noise family passes at about its level over 100 seeds
  - the tail's shift finds a planted skill and not noise
  - it leaves out forecasts with nothing to test
  - it doesn't find skill in a worthless forecast on a drifting base rate
- **The reports:**
  - the growth rule and its report
  - crisis drawdowns from the market's own peak and trough
- **October's rules** keep their shapes.
- **The run end to end** on synthetic French and Shiller data, saved and
  replayed.
- **Ctrl+K** runs on the worker with the app's services.

**Canon,** each checked on Crossref:
- **New:** Brock, Lakonishok & LeBaron 1992, Campbell & Shiller 1998,
  Shiller, Black & Jivraj 2020 (an SSRN preprint) and Romano & Wolf 2005.
- **Now also listing the modules:** Moreira & Muir 2017, Moskowitz, Ooi &
  Pedersen 2012, Ang & Bekaert 2002 and McLean & Pontiff 2016.

**Not run on your store:** it gets the outside-series tables when PRs #22
on merge. Then fetch French, import Shiller, and run Ctrl+K "Run the
regime evidence".
