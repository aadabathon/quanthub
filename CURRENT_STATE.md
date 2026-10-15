# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-10-14**: the Kalman module (K1 model, K2 Kalman Lab page) and the 3D
vol surface (S3D, Plotly, D32) landed, on top of the completed Qt
migration (D31). Open items for the owner are listed under Open decisions.

## Status

v0.5 (the Qt UI). Brokerage connectivity is live-verified against TWS 7496 (2026-08-14;
see below). `py -3.12 -m pytest -q` → **644 passed** in about 60–75 s, with no
network or display needed (the GUI smoke tests run offscreen). The real 3D
draw runs in a fresh subprocess and skips, with the reason, when offscreen
Chromium can't compile WebGL shaders; that happens on some runs.

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
    and the surface as a heatmap in k/√θ or a rotatable 3D view (D32)
  - the 3D view (Plotly in Qt WebEngine):
    - the fitted surface, with the fitted expiries as white lines
    - the true SSVI as a faint wireframe
    - quotes with bid–ask whiskers
    - hover readouts; the camera is kept across refits
  - 3D timings, measured on an RTX 4060 Ti:
    - built when the page is first shown (~0.7 s of GUI time, overlapping
      the fit)
    - first draw ~0.4 s after choosing 3D, redraws ~0.1 s
  - when WebGL is missing, the 3D view says so instead of loading forever
  - fit-by-expiry table and arbitrage checks, now also between expiries
    (the interpolated mesh)
- **Kalman Lab (K2):**
  - synthetic (TRUE vs FIT) or source history
  - local level or local linear trend
  - filtered level with its 95% band over the smoothed level (labelled
    hindsight) and, in synthetic mode, the true level
  - for the trend model, a drift panel whose axis includes the band, so
    the drift's real uncertainty shows
  - 3M / 1Y / ALL chart windows (the fit always uses every close)
  - standardized-innovation histogram
  - the local level's EWMA readout: q, K̄ and half-life
  - an info note when a variance fits to zero
  - in synthetic mode, the likelihood at the truth beside the fitted one
- **Planned modules** (Markov, Portfolio, Research) show placeholders.
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

- **Kalman K1 (2026-10-14), `models/kalman.py`** (page: Kalman Lab):
  - generic linear Gaussian state space with scalar observations:
    Joseph-form filter, NaN = missing, prediction-error likelihood over an
    approximately diffuse prior with the first m observed terms excluded,
    RTS smoother, simulation
  - local level (closed-form steady-state gain = optimal EWMA weight) and
    local linear trend
  - MLE over log(σ²/Var(Δy)) on the shared Nelder–Mead, three starts.
    Variances driven below e⁻²⁰·Var(Δy) are reported in `zero_variances`.
    On daily log prices, zero noise is common: the model can't produce
    positively autocorrelated changes.
  - The fit uses a scalar-arithmetic likelihood for m ≤ 2 (about 20× faster
    than numpy per step); tests hold it to the reference filter.
  - Fits on ten years of daily data: local level ~0.2 s, local linear trend
    ~1.3 s.
  - `tests/test_kalman.py` (52 tests) follows the docstring's validation
    plan: brute-force Gaussian conditioning, the EWMA identity, band
    coverage and white innovations, a likelihood-ratio recovery check,
    missing data, bad inputs.
- **Volatility (Milestone B, D30):**
  - `data/options.py`: the `OptionChain` contract
  - `models/svi.py`: raw SVI (derivatives, density/`g(k)`, Lee bound), the
    SSVI surface, and a spread-weighted fit
  - `models/vol_surface.py`: synthetic OTM chains → IV inversion →
    per-expiry fits → butterfly/calendar checks
  - `surface_mesh` (S3D): total variance interpolated linearly in T at
    fixed k
    - it adds no calendar arbitrage and holds ATM forward variance constant
      between expiries
    - g is checked on every interpolated row
    - it marks k-extrapolated wing points
    - on the default chain, nodes match the truth to 2e-8, with up to ~1.1
      vol points of interpolation error between them
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
- **Live verification (2026-10-15), Qt UI, TWS 7496 read-only, 9/9 pass:**
  - The check drove the app's own handlers against a copy of the store,
    with its own client id.
  - Connected from the top bar.
  - Markets quotes: all seven watchlist symbols came back DELAYED,
    including AAPL, which was UNAVAILABLE in August.
  - Sync 1Y for SPY filled 2026-08-17 → 2026-10-15 in 10 s.
  - The GARCH, Kalman and Volatility Lab `ibkr` loads each took 1–3 s.
  - The Options Lab loaded spot from the delayed quote and realized vol.
  - Disconnect ended DISCONNECTED, not ERROR.
  - It found the D11 duration bug: leading gaps were fetched short and
    marked covered anyway. Fixed, with a regression test, and re-verified
    live: a 5Y request after a 1Y sync now returns all 1,253 bars.
- **Live verification (2026-08-14), Tk era, same broker and services code:**
  - SPY/QQQ/MSFT/TLT/GLD → DELAYED quotes with real last-trade timestamps
  - AAPL → UNAVAILABLE ("requires additional subscription for API") while
    the connection stayed CONNECTED

## Untested / known limits

- **Not yet verified against live TWS:** the Options Lab quote →
  last-close fallback. Every watchlist symbol now has a delayed quote, so
  live data didn't exercise it; it's tested only with a fake source.
- **Store repaired (2026-10-15).** The D11 bug had left one hole: SPY's
  coverage claimed 2021-08-14 onward, but its bars started 2022-08-16.
  - Backup first: `~/.quant_hub/market_data.db.bak-2026-10-15`.
  - The coverage row was narrowed to the bars, then a live load through
    `CachedSource` fetched the missing year: SPY now holds 1,297 bars from
    2021-08-16.
  - A re-audit found every series' coverage matching its bars.
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
- **3D surface:** synthetic chains only, like the rest of the Volatility
  Lab. The 3D draw is verified by screenshots on the owner's GPU and by the
  subprocess test, which may skip offscreen.

## Open decisions (waiting on the owner)

- **Kalman K2 review:** the Kalman Lab page landed; the owner reviews it.
  - Live `ibkr` history loads are untested: the screenshots fed stored SPY
    and NVDA bars to the page directly.
  - Possible follow-ups, not started:
    - the trend model's Holt equivalence: its steady-state filter is
      Holt's linear smoothing with α = K̄₁, β = K̄₂/K̄₁
    - an exact diffuse initialization
- **S3D review:** the 3D surface landed (D32); the owner reviews it.
  - Volatility Lab clipping at 1280 px wide is pre-existing: the
    Experiment card's first row needs 1160 px. It's flagged as a separate
    task.
- **DCF modeler port (owner request 2026-10-14):** bring in the owner's
  existing DCF tool, which pulls SEC EDGAR data and stores it in SQLite.
  Deferred by the owner until after the current roadmap. Plan it in its own
  session then, starting from the existing source folder.

## Version control / environment

Repo at `C:\Users\adams\Projects\quanthub`; the owner commits and pushes.
Recent commits:
- `e750ab9` v0.5 (Tk retired)
- `9173172` the Kalman derivation

Run everything with `py -3.12`: bare `python` is a 3.11 install without
pytest. Environment:
- Windows 11, Python 3.12.6
- PySide6 6.11 (Qt WebEngine included), pyqtgraph 0.14, qtawesome 1.4,
  plotly 6.5 (declared, D32; only its bundled plotly.min.js is used)
- ibapi 9.81.1.post1
- TWS live on 127.0.0.1:7496 with Read-Only API enabled
- store at `~/.quant_hub/market_data.db`

## Next steps (see ROADMAP.md)

1. ~~Milestones A/B (Options Lab, Volatility Lab)~~ (2026-09-27).
2. ~~Qt migration Q1–Q6~~ (2026-10-10).
3. ~~Owner-requested, ahead of the data phase: Kalman K1 and K2, and the
   3D surface (S3D)~~ (2026-10-14).
4. ~~Live-verify the Qt UI against TWS~~ (2026-10-15, 9/9; the D11 duration
   bug was found and fixed).
5. **The road to v1.0** (ROADMAP.md, planned 2026-10-15), in order C → M → F
   → P → R:
   - C: option chains from IBKR
   - M: Markov regimes
   - F: data foundations (store safety, adjusted prices)
   - P: Portfolio, with the read-only account
   - R: Research and backtesting

   - Decisions approved 2026-10-15: D33, D34 (provider-agnostic).
   - ~~C0 live spike~~ (2026-10-15): delayed option quotes work for free.
     SPY: 40 quotes in 2.5 s. SPX: 20 in 0.7 s. IBKR model IV/greeks come
     too (D33).
   - C1 acquisition landed (2026-10-15).
     - `OptionChain` gains per-expiry carry and `reference_iv`.
     - `ChainRequest` and the selection rules live in `data/options.py`,
       with the `OptionChainSource` port.
     - `IBKRBroker` gains option parameters, per-expiry strike lists and
       batched option quotes (no regulatory snapshots); 10091 is now an
       advisory.
     - `broker/option_chain.py` is the two-pass adapter, caching strike
       lists for the day.
     - `tests/test_option_chains.py` has 30 tests.
     - Live: SPY and SPX chains, about 180 quotes each, 6 expiries, all
       fitted and arbitrage-free. Our IVs are within 0.1 vol point of
       IBKR's model IVs (median), even on a flat placeholder carry.
     - Speed: about 40 s for the day's first chain (strike lists, ~4 s
       per expiry), then about 15 s.
   - ~~C1b parity-implied carry~~ (2026-10-15), `models/carry.py`.
     - Weighted regression of C − P on K per expiry. It falls back to a
       fixed rate when the rate is implausible or its SE exceeds 1%, and
       records Student-t dof.
     - Synthetic chains gained an optional `parity_pairs`.
     - `tests/test_carry.py` has 15 tests:
       - noise-free recovery to 1e-9
       - interval coverage of 94–95% (±1.96 would give 88% at n = 5)
       - a wrong flat dividend yield: median IV error from 1.08 to 0.04
         vol points
     - Live, χ²/pt: SPY from 8–122 (flat) to 0.3–12; SPX from 3.6–43
       to 0.0–3.8.
     - SPX's longer expiries take the regression branch (r 4.9–6.2%);
       SPY's 1-point strikes stay on the fixed rate.
     - Caveat for the page: q_T is measured against the stale delayed
       spot, so short-dated yields are noise (SPY 7d: −1.3%). Show
       forwards, not short-dated q.
   - Next: C2, real chains in the Volatility Lab (source selector, chain
     quality, implied-carry table, snapshot persistence).
6. After the roadmap: the DCF port (SEC EDGAR + SQLite), planned in its own
   session.
