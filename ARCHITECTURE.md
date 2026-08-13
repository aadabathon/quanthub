# ARCHITECTURE.md

How Quant Hub is put together. For *why*, see [DECISIONS.md](DECISIONS.md).

## Layers

Imports point strictly downward. `tests/test_layering.py` scans source files
and fails the build on violations.

```
gui/            Tkinter/ttk presentation. Renders state, forwards user intent.
                No math, no data munging, no broker calls beyond interfaces.
services/       Application facade. May import data/ and broker interfaces
                (broker/base.py, broker/types.py). Never tkinter, never ibapi.
data/           Pure domain: types, generators, statistics. NumPy/pandas only.
                Imports nothing from any other quant_hub package.
broker/         Infrastructure. The ONLY package allowed to import ibapi.
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
│   ├── main_window.py         shell: header/nav/container/log + THE PUMP
│   ├── theme.py               all colors, fonts, ttk styles (dark terminal)
│   ├── task_runner.py         worker thread + result queue (tkinter-free)
│   ├── views/base.py          View base class (on_show/on_hide lifecycle)
│   ├── views/dashboard.py     symbol/range/source → chart + stats
│   ├── views/markets.py       quote watchlist
│   ├── views/placeholder.py   ONE class for all 7 future modules
│   └── widgets/               log_panel.py, status_bar.py (ConnectionBar)
├── broker/
│   ├── base.py                Broker protocol, ConnectionState, ConnectionEvent
│   ├── types.py               ContractSpec, HistoricalBar, BrokerError tree
│   ├── ibkr.py                IBKRBroker: callbacks → blocking calls
│   └── market_source.py       IBKRSource: Broker → DataSource adapter
├── data/
│   ├── types.py               PriceHistory (validated), Quote
│   ├── sources.py             DataSource protocol, SyntheticSource
│   ├── synthetic.py           GBM (the quant-module exemplar)
│   └── stats.py               ReturnStats, summary_stats, max_drawdown
├── services/market_service.py source registry + get_history/get_quote facade
└── visualization/charts.py    ChartStyle, create_figure, plot_price_history
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

## Data flow

```
Dashboard "Load" (GUI thread)
  └► TaskRunner.submit ──────────────── (task-worker thread)
       └► MarketService.get_history ── active DataSource
            ├─ SyntheticSource ─ GBM path, sliced ────► PriceHistory
            └─ IBKRSource ─ IBKRBroker.get_history ──► PriceHistory
  ◄─ result queue ◄──────────────────────
after-pump → on_done(history) (GUI thread)
  ├► visualization.charts.plot_price_history → FigureCanvasTkAgg
  └► data.stats.summary_stats → stats labels
```

`PriceHistory` (sorted unique DatetimeIndex; `close` required, finite, > 0;
OHLCV passed through when present) is the contract that makes every consumer
source-agnostic. Validation lives there once — future models must not
re-validate defensively.

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
- Long-lived matplotlib: the dashboard owns ONE Figure; redraws go through
  `plot_price_history(fig, ...)` which clears and redraws — never create a
  new Figure/canvas per refresh.

## Quant-module conventions (follow the GBM exemplar)

Every future quantitative module (`data/synthetic.py` is the template):
mathematical description + discretization in the module docstring;
assumptions and units stated; inputs validated in `__post_init__`/entry;
no unexplained numerical constants; diagnostics where meaningful; tests
covering determinism (seeded), shape, domain invariants, invalid-input
rejection, and statistical validation against closed forms when they exist.
Models never import GUI, broker, or service code.
