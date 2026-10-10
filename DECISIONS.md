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
