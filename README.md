# Quant Hub

A personal quantitative research and trading workstation: a desktop app with
a fast, modern Qt interface over a layered Python core.

- **Market data:** IBKR quotes with provenance; daily history validated and
  persisted to local SQLite through a coverage-aware incremental cache.
- **Research labs:**
  - Simulator: GBM and Monte Carlo against the closed forms
  - GARCH Lab: conditional volatility, synthetic or real history
  - Options Lab: Black–Scholes–Merton, implied vol, multi-leg strategies,
    a Monte Carlo check
  - Volatility Lab: SVI/SSVI implied-vol surfaces with arbitrage checks, as
    a heatmap or a rotatable 3D surface
  - Kalman Lab: a hidden price level (and trend) filtered from noisy closes,
    with the smoother and maximum-likelihood fits
- **Dashboard:** what the local store holds, a market explorer, and a model
  sandbox.

Synthetic labs know the true process, so they show TRUE vs ESTIMATE
side by side. Brokerage access is read-only by design: there is no order
code anywhere.

## Running

```bash
pip install -e ".[dev,ibkr]"   # from this directory; drop [ibkr] to skip TWS support
python -m quant_hub            # or the quant-hub launcher (windowed)
pytest -q                      # no display or network required
```

## Architecture

Imports point strictly downward; `tests/test_layering.py` enforces this.

```
gui/            PySide6 (Qt) presentation with pyqtgraph charts: renders
                state, forwards intent. No math, no broker calls.
services/       Application facade (MarketService), cache/sync layer,
                page-facing data assembly. Never the GUI toolkit or ibapi.
models/         Pure quantitative models (Black-Scholes, strategies, GARCH,
                SVI/vol surfaces, Monte Carlo, shared optimizer).
data/           Pure domain: PriceHistory/Quote/OptionChain types, GBM
                generator, simulation, return/risk analytics, validation.
                Usable from notebooks/scripts with zero GUI machinery.
broker/         Infrastructure. The ONLY package that imports ibapi.
                Read-only: no order code exists anywhere.
storage/        SQLite persistence (stdlib sqlite3) for acquired bars and
                per-page UI state; DB at ~/.quant_hub/market_data.db.
visualization/  Matplotlib figures for scripts and notebooks.
events.py       Thread-safe bus: background threads publish, GUI drains.
tasks.py        TaskRunner: one worker thread; results land on the GUI thread.
core.py, app.py Composition root: the one place everything is wired.
```

### Concurrency model

Two rules produce the whole design:

1. **Only the GUI thread touches widgets.** Background work re-enters the
   GUI through one 16 ms `QTimer` pump (`MainWindow._pump`) that drains the
   event bus and the task-result queue.
2. **ibapi's callback/threading model never leaves `broker/ibkr.py`.** The
   network thread's callbacks fill per-request buffers and set
   `threading.Event`s; callers see plain blocking methods with timeouts that
   raise `BrokerError`. The GUI invokes those via `tasks.py`
   (`submit(fn, on_done, on_error)` — callbacks run on the GUI thread).

### Data flow

```
page action ── TaskRunner (worker thread)
    ├─ synthetic experiment (GBM, GARCH, SSVI chain) ── true process known
    └─ MarketService ── named source (no global "active" source)
         ├─ SyntheticSource ── GBM generator ──► PriceHistory
         └─ CachedSource ── SQLite hit? ──────► PriceHistory
              └─ miss/partial: IBKRSource ── IBKRBroker ── TWS
                 └─ validate ── persist ── serve from store
    then pure models/analytics ──► one immutable result
    └─ queued ── pump ── every chart and stat on the page renders from it
```

Every consumer works from the same validated `PriceHistory`, so code is
source-agnostic, which is the property future backtesting/model layers rely
on.

See ARCHITECTURE.md (how), DECISIONS.md (why), ROADMAP.md (next) and
CURRENT_STATE.md (done / untested).

## Settings

The first run writes `~/.quant_hub/settings.toml`, a commented file of the
defaults. Edit it and restart. It sets where the market-data store lives,
and whether and how to reach Interactive Brokers. Don't use IBKR? Set
`enabled = false` under `[ibkr]`, and its controls and sources disappear.
The app is complete on synthetic data without it. A mistake in the file
never stops the app: it's reported in the log, and that setting keeps its
default. `QUANT_HUB_SETTINGS=path/to/other.toml` uses another file.

The store is versioned. Before an update changes its schema, the app copies
the whole database into `~/.quant_hub/backups/`, and never deletes those
copies itself.

## Importing data

The Dashboard's Market data card imports CSV files of daily bars (its menu:
Import CSV…). Imported bars are validated like any other provider's and
stored under the source `file`, which every page can then use. The generic
layout:

```text
date,open,high,low,close,volume,adj_close
2024-01-02,472.16,473.67,470.49,472.65,123623700,466.71
```

- Any case and column order; other columns are ignored.
- `close` is the price as traded; `adj_close`, adjusted for dividends too.
  A file needs at least one, and stores the bases it has. `open`, `high`,
  `low` and `volume` are optional.
- Dates are YYYY-MM-DD.
- A `symbol` column lets one file hold several symbols. Without one, the
  file name is the symbol (`SPY.csv`), or the app asks.
- Rows that fail validation are dropped and reported.

Stooq downloads use this layout as they are. Nasdaq.com and Yahoo Finance
downloads are recognized by their headers.

## Price bases

Every series is either **traded** (as it traded, split-adjusted) or
**adjusted** (for dividends too, so returns are total returns). The GARCH,
Kalman and Markov Labs have a Prices choice, Adjusted by default. Price
displays use traded prices. From IBKR, adjusted prices are refetched whole
once a day, since every dividend rescales the past. A split after the cache
stored a symbol is detected and repaired.

## IBKR setup

Run TWS or IB Gateway with API access enabled (Configure → API → Settings →
"Enable ActiveX and Socket Clients"). The default settings target live TWS
on `127.0.0.1:7496` with TWS's **Read-Only API** enabled — the app contains
no order code, so a read-only socket loses nothing. Paper TWS is 7497, IB
Gateway 4001 (live) or 4002 (paper): set `port` in settings.toml. The app is
fully functional on synthetic data without TWS, and never places orders. PyPI's
`ibapi` is the old 9.81 build; the current official API from
<https://interactivebrokers.github.io> also works — the broker code tolerates
both callback signatures.

## Quantitative code conventions

`data/synthetic.py` (GBM) is the exemplar every future model should follow:
the mathematical description and its discretization documented in the module
docstring, assumptions and units stated, validated inputs, and tests that
include statistical checks against closed-form moments. Models never import
GUI or broker code.

## Roadmap

Next: the data side (live IBKR verification, an option-chain adapter feeding
the Volatility Lab, real positions, a trading dashboard). Planned modules
with nav placeholders: Markov regime models,
portfolio analytics and Kelly sizing, strategy research and backtesting.
See ROADMAP.md.
