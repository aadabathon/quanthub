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
