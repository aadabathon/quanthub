# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-10-15**: the option-chain adapter (C1–C4) is complete. Real chains
reach the Volatility Lab and are saved; the joint SSVI fit is an option
(D35); the Options Lab can price from the market surface. Before that
(2026-10-14): the Kalman module and the 3D vol surface (D32), on the Qt UI
(D31). Open items for the owner are listed under Open decisions.

## Status

v0.5 (the Qt UI). Brokerage connectivity is live-verified against TWS 7496 (2026-08-14;
see below). `py -3.12 -m pytest -q` → **737 passed** in about 85 s, with no
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
  - Flat σ / Surface toggle (C4): Surface prices every contract from the
    newest saved chain's fitted surface; the legs table has a σ column
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
   - ~~C2: real chains in the Volatility Lab~~ (2026-10-15).
     - A Synthetic / Market chain mode on the Chain card. Market mode
       takes a source (`ibkr`, or `stored` for the latest saved snapshot,
       which needs no broker), a symbol, target expiries, strikes per
       expiry, and a fallback rate.
     - The job fetches, saves the snapshot, implies carry, fits and meshes.
       The Carry card shows each expiry's forward ±SE and its branch.
     - The fit table's TRUE column becomes the provider's model IV: the
       median of the four quotes nearest the forward.
     - Checks count excluded in-the-money quotes.
     - `storage/chain_store.py` keeps snapshots in the same database
       (D33). `services/option_chains.py` is the chain service.
     - Fits use out-of-the-money quotes only (`otm_only`). Live, this
       didn't change SPY's ATM IVs (0.01 vol pt), so early exercise isn't
       its misfit. It's kept because a parity strike's two sides give the
       same IV twice, which would double-weight the money.
     - The Experiment card's quote-noise inputs got their own row, so the
       page fits at 1280×800. That supersedes the separate clipping-fix
       session's uncommitted change.
     - Live in the app: SPY and SPX in about 42 s each, all expiries
       fitted, every arbitrage check ok. SPX's model IVs agree within
       0.1–0.3 vol pt.
     - Caveat: IBKR's delayed model IVs for SPY are sparse and sometimes
       stale (92d: 20.7% against a 13.8% smile). Shown as received.
     - Tests: `tests/test_chain_store.py` (8), and the offline market-mode
       smoke tests.
   - ~~C3: joint SSVI fit~~ (2026-10-15), `models/ssvi_fit.py`.
     - Arbitrage-free by construction: γ ∈ (0, ½], η(1+|ρ|) ≤ 2, θ
       nondecreasing.
     - The search: θ profiled by golden section, pooling adjacent
       violators for calendar order, under a 3-D Nelder–Mead.
     - `fit_surface(method="svi" | "ssvi")`. The Volatility Lab has an
       SVI / SSVI toggle on the Fit card that refits the loaded chain, and
       the subtitle shows ρ, η, γ.
     - Synthetic, 8 seeds of heavy noise: worst ATM error 4.6 → 1.6 vol
       points, arbitrage-free 8/8 against 3/8. χ² calibrated: 0.96 of
       expected.
     - Live SPY/SPX: it fits far worse than SVI (χ²/pt 1,015 vs 7, and 28
       vs 0.6), even fitted one expiry at a time. Real smiles lie outside
       the SSVI slice family.
     - So independent SVI stays the default (D35).
     - Tests: `tests/test_ssvi_fit.py` (15), including "never worse than
       the truth", plus a GUI toggle test. A fit takes 1.3–2.5 s.
   - ~~C4: market vol into the Options Lab~~ (2026-10-15),
     `models/market_surface.py`.
     - `MarketSurface` turns a fit into (σ, r, q) at any strike and expiry.
       - Carry: ln(F/S)/T and −ln D/T per fitted expiry, linear in T
         between expiries and flat outside them.
       - Vol: total variance linear in T at fixed log-moneyness. Implied
         vol is held flat before the first and after the last fitted
         expiry.
       - Every point says whether it's extrapolated: outside the fitted
         expiries, or outside a used slice's quoted strikes.
     - `OptionLeg` gained optional `vol`, `rate` and `dividend_yield`.
       `apply_surface` fills them (sticky strike). Legs without them use
       the card's flat inputs, as before.
     - The underlying card has a Flat σ / Surface toggle.
       - Surface fits the newest *saved* chain for the symbol
         (`services/option_chains.load_market_surface`), so it needs no
         broker. With no saved chain, the error says to fetch one in the
         Volatility Lab.
       - Spot becomes the chain's, the one its forwards were measured
         against. σ, rate and div grey out.
       - A SURFACE badge, and a subtitle naming the chain's source, as-of
         time and snapshot. The mode is saved with the page.
     - Pricer: σ, r and q from the surface at the contract's strike and
       expiry. The vol tile reads "16.76% surface", plus "· extrapolated"
       where it applies. Implying σ from a price uses the surface carry.
     - Strategy: a σ column in the legs table. Extrapolated vols show in
       amber with a " *" and a tooltip.
     - Re-centring now keys on the spot as well as the symbol. Strikes
       rebuild when the symbol changes, or when spot is more than 20% from
       where they were built (`RECENTRE_MOVE`).
       - Found live: the saved state said "centred on SPY", but that was
         synthetic SPY at $385. The real chain's SPY is at $771, so the
         legs sat in the extrapolated wing at 78–87% vol.
     - Template strikes are now the rounded centre plus whole listed
       steps. Rounding each strike on its own had made a 5% butterfly at
       $771.51 into 735/770/810, its tails $500 apart and max loss ≠ debit.
       Now 730/770/810.
     - Live, on the saved IBKR SPY chain:
       - the SPY 740 put at 92 days is 16.76% surface vol, $10.70
       - a 30-day 730/770/810 call fly gets 18.6 / 13.3 / 10.5% (the
         skew), with a $1,964 debit
       - surface prices land inside the bid–ask for SPX 129 of 149
         quotes, and SPY 46 of 149 (penny-wide spreads, and SVI's SPY
         misfit)
     - Tests: `tests/test_market_surface.py` (14), template symmetry (5),
       and 3 Options Lab smoke tests (surface pricing, re-centring at
       another price, the no-saved-chain hint).
   - The option-chain adapter (C) is complete. Next: M, Markov regimes,
     after owner approval.
6. After the roadmap: the DCF port (SEC EDGAR + SQLite), planned in its own
   session.
