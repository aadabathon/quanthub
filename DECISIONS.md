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
