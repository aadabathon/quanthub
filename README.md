# Quant Hub 1.0

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
  - Markov Lab: Gaussian regimes, filtered versus hindsight states and forecasts
- **Research:** six daily strategies with rolling training/refits, explicit
  costs and risk sizing, matched benchmarks, sweeps with IID deflated Sharpe,
  and exact saved inputs/decisions. A local paper library keeps PDFs, notes
  and links to experiments.
- **Portfolio:** imported positions, option-aware risk, historical/Gaussian
  VaR and expected shortfall, risk contributions and Kelly uncertainty.
- **Dashboard:** what the local store holds, a market explorer, and a model
  sandbox.

Synthetic labs know the true process, so they show TRUE vs ESTIMATE
side by side. Brokerage access is read-only by design: there is no order
code anywhere.

## Getting started without a broker

On Windows, install Python 3.12, open a terminal in this directory, and run:

```bash
py -3.12 -m pip install -e ".[dev]"
py -3.12 -m quant_hub          # or the quant-hub launcher (windowed)
py -3.12 -m pytest -q          # no display or network required
```

The synthetic labs and Research controls work immediately. In Research,
press **Run** for a random-walk control; choose **Planted trend** and a short
momentum lookback to test whether a rule can find a known mechanism. These
are simulated worlds, not forecasts. Use Dashboard → Import CSV… for your
own daily histories, then select `file` and its price basis in Research.

The first launch creates `~/.quant_hub/settings.toml`. Set `enabled = false`
under `[ibkr]` to hide broker controls. Brokerage support is optional:
install it with `py -3.12 -m pip install -e ".[dev,ibkr]"` and follow IBKR
setup below. There is no automatic connection or reconnection.

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
whether and how to reach Interactive Brokers, your SEC identity
(Valuation), and where the Review page finds your `REGIME.md` and your
exports. Don't use IBKR? Set
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

## Portfolio

The Portfolio page imports a broker's positions CSV (Import positions…)
and shows its risk.

- **Headline:** volatility, one-day VaR and expected shortfall (historical
  and Gaussian), beta to a benchmark, and the worst drawdown today's mix
  would have had over the lookback.
- **Holdings table:** each holding's share of the value beside its share of
  the risk, sorted by risk.
- **Sizing:** Kelly leverage, shown with its standard error.
- **Growth:** today's mix's growth rate, CAGR and vol drag, from the
  replay with options repriced in full.
  - Each sleeve's contribution and verdict: adds, oversized, undersized
    or costs.
  - Its minimum-variance size (the hedge ratio, well estimated) and its
    growth-optimal size (shown with its much larger error).
  - A read-only what-if, and the options book's greeks and annual carry.
- **Regressions:** each holding's and the portfolio's α and β on the
  benchmark, and on up to three extra factor ETFs, with Newey–West errors
  (optionally the Dimson sum β for stale prices); a rolling β for any row;
  and the residual links between
  holdings, strongest first, answering whether the streams are
  orthogonal.

Fidelity's downloads are read as they are:
- the Portfolio Positions download, one account per row
- the Positions view's download, which has no account column, so all its
  rows count as the view's account (e.g. "All Accounts")

To keep accounts apart, export each account from the Positions view and
select all of the day's files in one import: they become one snapshot,
with the accounts in the account selector.

Any other broker works through the generic layout:

```text
symbol,quantity,price,cost_basis,account
AAPL,50,227.52,7500,Brokerage
-AAPL260116C250,-1,4.15,,Brokerage
CASH,1250,,,Brokerage
```

- Options are `ROOT YYMMDD C|P STRIKE`, contracts negative when short.
- Imports are kept as dated snapshots.
- Before any import, the page shows a $100,000 example portfolio.

## Valuation

A discounted cash flow from a company's SEC filings: the owner's DCF
modeler, inside the app.

- **Company:** type a ticker or CIK. Its 10-K facts come from the SEC's free
  companyfacts API and are kept in the local store; Refresh refetches.
  Every gap in the filings is named with its reason, never filled in.
- **Assumptions:** seeded from the company's own trailing averages, and
  editable as one number for every year, or one per year
  (`12, 10, 8, 6, 5`).
  - The conventions are choices: the terminal cash flow (the original's
    last-year, or steady-state), value as of the year end or today, and
    netting marketable securities.
- **Result:** value per share, the EV bridge with the terminal value's share
  of it, the projection, a WACC × terminal-growth grid, and what the price
  implies: every growth rate and WACC that gives it, and whether there can
  be only one.
- **Price:** from a source (its quote, else its last close) or typed, for the
  margin of safety.
- **Screen:** your holdings or a list of tickers, each seeded from its own
  filings, ranked cheapest first, exported as CSV.
- **Saved valuations:** immutable records. Reopening one says whether the
  filings have changed since.

The SEC asks every program that downloads its filings to say who is asking,
so add your name and email to `settings.toml`:

```toml
[sec]
identity = "Your Name you@example.com"
```

Filings already in the store work without it. An unlevered DCF is the
wrong model for banks, insurers and REITs. IFRS filers aren't mapped yet.

## Review

Your trading regime, written down in `REGIME.md` and checked against your
book every time you press **Run review**. It's decision support only: it
records trades you place by hand and never places one.

1. Copy the repository's `REGIME.md` to `~/.quant_hub/REGIME.md` and fill in
   the `___` values. Keep the filled-in copy out of the repository. A blank
   leaves its rule off, so start with a few rules.
2. Optionally, point the review at the folder you save broker exports to.
   It reads the newest one by the date printed inside it:

   ```toml
   [positions]
   folder = "~/Documents/exports"
   ```

   Without a folder, it uses the newest snapshot imported on the Portfolio
   page.
3. Press **Run review**. It does the following:
   - reads your rules, storing each new version of the file
   - loads the positions and their history
   - runs the risk, regressions and growth by your sleeves
   - fits the market-state gates on the benchmark
   - re-runs your saved valuations on today's filings and price
   - grades every rule

What the page shows:
- **Checklist:** every rule over what it governs, breaches first. A rule
  that reads an estimate shows how many standard errors past its limit it
  is. With `by k SE` in its value, it breaches only beyond k.
- **Thesis by stream:** section 1's tests (growth hurdle, orthogonality,
  beta creep), and how many reviews in a row each stream has failed.
- **Market state:** the calm regime's filtered probability, its odds ahead,
  and the volatility forecast.
- **One day dominating:** a holding whose variance is mostly one day's,
  such as a takeover or approval jump. Section 4 says whether the checks
  read the numbers with that day or without it.
- **Journal:** trades drafted from the change since the export before,
  notes, and trades you record by hand. Every review is kept whole, with
  the rules it ran under.

## Research workspace

**Experiments** contains Data, Rule and Costs forms. Training prices precede
each test block; reported returns exclude warm-up. Momentum, mean reversion,
Kalman trend/level, GARCH volatility targeting and Markov calm-regime rules
use equal asset budgets with a volatility target and a long/cash exposure
cap. Model rules refit on rolling training windows and use forward filters.
The benchmark enters an equal-weight buy-and-hold basket on the same dates,
with the same costs; its exposure is 100%, independent of the risk target.

Equity, drawdown, rolling Sharpe, exposure, trade notionals, fees and fit
windows are inspectable. The threshold's meaning is explained for each
rule. Sweeps vary the signal/risk window and threshold (GARCH uses individual
experiments). All trials and the sweep manifest are saved. DSR assumes IID
returns and an explicitly chosen effective independent-trial count. A
selected test window is **not an untouched holdout**, and earlier searches
outside that grid still count when assessing evidence.

**Saved runs** replays the exact captured prices and target weights, without
fetching newer bars or refitting. Each run retains its configuration,
provenance, fitted parameters, software source fingerprint, statistics and
editable conclusion. Reopen warns when the implementation has changed.
The close-t execution convention is idealized; impact, taxes, borrow costs
and serial-dependence corrections are outside v1.

**Papers** supports PDF import, source-only references, metadata, topic,
reading status, searchable notes and links to experiments. Five foundational
references seed the catalog; their PDFs are not bundled. Import your own
files or attach a PDF to an existing reference. Imported copies live in
`market_data.papers/` beside the SQLite store, named by content hash. Back up
both the database and that folder; schema backups copy the database only.
Attach PDF… can repair a missing file without replacing its notes.

The pure engine is also available from Python:

Target weights use decision dates and earn the following session's
return. Costs include entry and drift-driven rebalancing, paid from equity.

```python
import pandas as pd
from quant_hub.models.backtest import BacktestConfig, backtest
from quant_hub.models.backtest_stats import performance_summary

dates = pd.bdate_range("2025-01-02", periods=4)
prices = pd.DataFrame({"ASSET": [100, 110, 99, 105]}, index=dates)
targets = pd.DataFrame({"ASSET": [0.0, 1.0, 1.0]}, index=dates[:-1])
run = backtest(prices, targets, BacktestConfig(half_spread_bps=2, commission_bps=1))
report = performance_summary(run.net_returns, turnover=run.turnover)
# The first 10% jump was observed before entering: the strategy does not earn it.
print(run.equity)
```

`walk_forward_targets` passes only training history to fitting and history
through the decision date to prediction. Sharpe inference explicitly assumes
independent returns; deflated Sharpe also requires the recorded trial
Sharpes and an explicit independent-trial count. The accounting and
statistical derivations are in the two modules' docstrings (D41).

## Price bases

Every series is either **traded** (as it traded, split-adjusted) or
**adjusted** (for dividends too, so returns are total returns). The GARCH,
Kalman and Markov Labs have a Prices choice, Adjusted by default. Price
displays use traded prices. From IBKR, adjusted prices are refetched whole
once a day, since every dividend rescales the past. A split after the cache
stored a symbol is detected and repaired.

## Optional IBKR setup

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

The v1 research foundation is implemented. Remaining external acceptance
checks are a real Fidelity positions export and a fresh end-to-end adjusted
history/cache run against TWS. Portfolio optimizers, streaming, option
strategy backtests and richer personalization remain later work. See
[ROADMAP.md](ROADMAP.md) and [CURRENT_STATE.md](CURRENT_STATE.md).
