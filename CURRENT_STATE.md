# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-10-10**: the Qt migration (D31) is complete and the Tk front end is
retired. Every page runs in the PySide6 + pyqtgraph GUI.

## Status

v0.5 (the Qt UI). Brokerage connectivity is live-verified against TWS 7496 (2026-08-14;
see below). `py -3.12 -m pytest -q` → **574 passed** in about 35 s, with no
network or display needed (the GUI smoke tests run offscreen).

### The app

Launch with `py -3.12 -m quant_hub` or the `quant-hub` launcher, which is
what the desktop shortcut targets. The GUI is `quant_hub/gui/` (Qt, D31) on
`core.py`'s wiring.

- **Shell:**
  - icon sidebar (core pages, then RESEARCH)
  - breadcrumb and Ctrl+K search over pages and stored symbols
  - IBKR connect/disconnect
  - status bar with a log drawer that starts closed
  - opens at 88% of the screen, capped at 1920×1240
- **Design system:** navy tokens and a generated stylesheet in `theme.py`,
  with the palette re-validated for CVD and contrast. Shared components
  share one control height, three field widths and a 340 px inspector
  column. All charts are pyqtgraph with a snapped crosshair readout.
- **Dashboard:** read from the local store only
  (`services/workstation.py`):
  - KPI tiles
  - market explorer: price + volume, 1M–ALL windows anchored at the last
    stored bar
  - sparkline watchlist
  - illustrative GBM forecast fan
  - store table and research shortcuts
- **Markets:**
  - page-owned source
  - watchlist with mode badges and stored-1Y sparklines
  - inspector: TWS reason when unavailable, stored history, Sync 1Y, open
    in the explorer
- **Simulator:**
  - sample path (close, drawdown, rolling vol vs true σ)
  - Monte Carlo fan with 50/90% bands (`path_percentiles`), the E[Sₜ]
    curve and the sample path
  - return and terminal histograms
  - TRUE | ESTIMATE table
  - one job → one state (D15)
- **Options Lab:**
  - underlying card (manual/synthetic/ibkr, provenance, MANUAL / LOADED /
    EDITED)
  - Pricer recalculates as you type (price ↔ IV, profiles across expiries,
    greek tiles)
  - Strategy: signed P&L shading, breakevens, days-forward slider, legs
    editor, templates
  - Monte Carlo: log-x convergence funnel, verdict badge
- **GARCH Lab:**
  - synthetic or market-history fits
  - stacked returns + conditional vol (true σₜ underneath in synthetic
    mode)
  - residual histogram vs N(0, 1)
  - TRUE | FIT table
- **Volatility Lab:**
  - experiment card (chain + true SSVI)
  - smile small multiples, term structure with the GARCH/realized overlay,
    surface heatmap in k/√θ
  - fit-by-expiry table and arbitrage checks
- **Planned modules** (Kalman, Markov, Portfolio, Research) show
  placeholders.
- **State:** every page's inputs persist per view through `ViewStateStore`
  (the shapes predate the Qt UI, so old inputs carried over).
- **Measured speeds:**
  - Dashboard symbol switch: 34–74 ms to painted
  - Simulator: 500 paths in ~75 ms, 5,000 in ~110 ms
  - chart hover: ~1 ms per move
  - for comparison, Tk took ~120 ms per chart redraw and ~450 ms per
    resize step
- **Verification:** screenshot-driven at 1280×800, 1720×1040 and
  1920×1224 on a copy of the store, plus synthetic hover events. The
  desktop shortcut was launched and confirmed to load Qt, not Tk.

### Models and data

- **Volatility (Milestone B, D30):**
  - `data/options.py`: the `OptionChain` contract
  - `models/svi.py`: raw SVI (derivatives, density/`g(k)`, Lee bound), the
    SSVI surface, and a spread-weighted fit
  - `models/vol_surface.py`: synthetic OTM chains → IV inversion →
    per-expiry fits → butterfly/calendar checks
  - `services/vol_context.py`: realized vols plus the GARCH forecast
- **Options (Milestone A, D26/D28/D29):**
  - `models/black_scholes.py`: BSM with dividend yield, vectorized, typed
    `Greeks` (D25), bracketed-Newton IV
  - `models/strategy.py`: multi-leg P&L/greeks, exact expiry profile, 11
    templates
  - `models/option_monte_carlo.py`: antithetic MC vs the closed form
  - `services/underlying.py`
- **GARCH (D27):** stationary GARCH(1,1) simulation and Gaussian QMLE on the
  shared Nelder–Mead. Fits made before the 2026-09-27 optimizer fix should
  be rerun.
- **Simulation:** `data/simulation.py` adds `path_percentiles`, tested
  against the lognormal quantile closed form.
- **Quote provenance (D22/D23):** `Quote.mode: MarketDataMode`. IBKR quotes
  stream briefly and self-cancel, and treat entitlement codes 10167/10089
  as advisories. A missing price is a typed `NoMarketDataError` carrying the
  reason. There is no global active source.
- **Live verification (2026-08-14), Tk era, same broker and services code:**
  - SPY/QQQ/MSFT/TLT/GLD → DELAYED quotes with real last-trade timestamps
  - AAPL → UNAVAILABLE ("requires additional subscription for API") while
    the connection stayed CONNECTED

## Untested / known limits

- **Not yet verified against live TWS:**
  - the Qt UI's IBKR paths: Connect from the top bar, Markets quotes, Sync,
    and the Options/GARCH/Vol Lab `ibkr` loads
  - the Options Lab quote → last-close fallback, which has only been tested
    with a fake source
- **IBKR disconnected:** market-history loads on `ibkr` fail for any window
  reaching today, because `CachedSource` must fetch the uncached tail. The
  pages show the error and keep the previous chart labelled.
- **GUI details covered only by tests:** the Ctrl+K popup styling.
- **Volatility Lab:** chains are synthetic only (no IBKR chain adapter yet).
  Independent per-expiry SVI fits overfit very noisy chains (several vol
  points ATM at 40% spreads, 2× noise); a joint SSVI fit is the planned
  remedy.
- **Options:** European exercise with a continuous dividend yield only; no
  American exercise, discrete dividends or live positions. Strategy
  scenarios hold σ/r/q fixed. Rate and dividend yield are manual inputs.
- **Quotes:**
  - refresh is serial (one TaskRunner worker, D9): about 1 s per entitled
    symbol, and the full `quote_timeout_s` (5 s) per unavailable one
  - FROZEN modes have not been observed live yet
  - 10089 is account-dependent (here it means no API delayed data)
- **Not persisted:** window geometry and the log drawer.
- **Build artifacts:** `quant_hub.egg-info/` was untracked on 2026-10-10;
  it's pip-generated and covered by `.gitignore`.
- **Market data (prior M4 limits):** fetches end "now", daily bars only,
  STK/USD only, no retry/backoff.
- **GARCH:** Gaussian QMLE only, with no heavy tails, confidence intervals,
  rolling refit or out-of-sample evaluation. Annualization assumes 252
  periods/year.
- **Deferred by choice:** a GPU 3-D vol surface.

## Version control / environment

Repo at `C:\Users\adams\Projects\quanthub`; the owner commits. The last
commit is `f7e4d33 ui stuff`, which holds Milestone B and the Qt migration
through Q5 with Tk still present; the Tk retirement (Q6) is uncommitted.
Run everything with `py -3.12`: bare `python` is a 3.11 install without
pytest. Environment:
- Windows 11, Python 3.12.6
- PySide6 6.11, pyqtgraph 0.14, qtawesome 1.4
- ibapi 9.81.1.post1
- TWS live on 127.0.0.1:7496 with Read-Only API enabled
- store at `~/.quant_hub/market_data.db`

## Next steps (see ROADMAP.md)

1. ~~Milestones A/B (Options Lab, Volatility Lab)~~ (2026-09-27).
2. ~~Qt migration Q1–Q6~~ (2026-10-10).
3. The data side:
   - live-verify the Qt UI and the labs' `ibkr` loads against TWS
   - then an IBKR option-chain adapter emitting `OptionChain`
   - real positions as `OptionLeg`s
   - the trading dashboard
