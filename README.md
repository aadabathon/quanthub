# Quant Hub

A modular quantitative finance research and trading workstation. v0.1 is the
foundation: application shell, synthetic market data, a working dashboard, and
read-only IBKR connectivity.

## Running

```bash
pip install -e ".[dev,ibkr]"   # from this directory; drop [ibkr] to skip TWS support
python -m quant_hub            # or: quant-hub
pytest                         # 68 tests, no GUI or network required
```

## Architecture

Imports point strictly downward; `tests/test_layering.py` enforces this.

```
gui/            Tkinter/ttk presentation. Renders state, forwards intent.
services/       Application facade (MarketService). No tkinter, no ibapi.
data/           Pure domain: PriceHistory/Quote types, GBM generator, stats.
                Usable from notebooks/scripts with zero GUI machinery.
broker/         Infrastructure. The ONLY package that imports ibapi.
                Read-only: no order code exists anywhere.
visualization/  Matplotlib figure construction (no tkinter; GUI embeds them).
events.py       Thread-safe bus: background threads publish, GUI drains.
app.py          Composition root: the one place everything is wired.
```

### Concurrency model

Two rules produce the whole design:

1. **Only the main thread touches Tkinter.** Background work re-enters the GUI
   through one `root.after` pump (`MainWindow._pump`) that drains the event
   bus and the task-result queue.
2. **ibapi's callback/threading model never leaves `broker/ibkr.py`.** The
   network thread's callbacks fill per-request buffers and set
   `threading.Event`s; callers see plain blocking methods with timeouts that
   raise `BrokerError`. The GUI invokes those via `gui/task_runner.py`
   (`submit(fn, on_done, on_error)` — callbacks run on the GUI thread).

### Data flow

```
Dashboard "Load" ── TaskRunner (worker thread)
    └─ MarketService ── active DataSource
         ├─ SyntheticSource ── GBM generator ──► PriceHistory
         └─ IBKRSource ── IBKRBroker ── TWS ───► PriceHistory
    └─ result queued ── after-pump ── charts + stats (GUI thread)
```

Both sources return the same validated `PriceHistory`, so every consumer is
source-agnostic — the property future backtesting/model layers rely on.

## IBKR setup

Run TWS or IB Gateway with API access enabled (Configure → API → Settings →
"Enable ActiveX and Socket Clients"). Defaults in `quant_hub/config.py` target
paper TWS on `127.0.0.1:7497`. The app is fully functional on synthetic data
without TWS, and never places orders. PyPI's `ibapi` is the old 9.81 build;
the current official API from <https://interactivebrokers.github.io> also
works — the broker code tolerates both callback signatures.

## Quantitative code conventions

`data/synthetic.py` (GBM) is the exemplar every future model should follow:
the mathematical description and its discretization documented in the module
docstring, assumptions and units stated, validated inputs, and tests that
include statistical checks against closed-form moments. Models never import
GUI or broker code.

## Roadmap

Planned modules (nav placeholders exist): options-chain analysis and pricing,
IV surfaces, Kalman/state-space models, GARCH, Markov regime models, portfolio
analytics and Kelly sizing, strategy research and backtesting, paper trading.
