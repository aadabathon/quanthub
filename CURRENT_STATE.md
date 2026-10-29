# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-10-29**: G1 landed (D55): pages are docks.
- Each page sits in one Qt dock (`gui/workspace.py`). Drag a page's title
  bar to put it beside another, onto its tabs, or out on its own (the
  second monitor). Double-click the bar, or use its button, to float or
  dock it. Cards stay inside their page.
- The layout and the window's size and place are saved at shutdown and
  restored at launch. Ctrl+K's **Reset layout** goes back to the first
  page alone.
- `on_show`/`on_hide` now follow whether a page can be seen, so several
  pages can be shown at once. Navigation opens a closed page as a tab of
  the active one, which looks like the old app plus a tab strip.
- A drop that would make the window wider than its screen becomes a tab.
  A page docked back comes to the front.
- The Volatility Lab floats: checked on the owner's display, where it
  redraws floating and docked again without recreating the main window.
- No new dependency.
- Tested: 12 new smoke tests. Not tested: dragging with a real mouse. The
  tests drive the same moves through Qt's API, and the drag itself is Qt's
  own. Offscreen, the float test skips its draw step for lack of WebGL
  (like the 3D test), after checking the main window and the tabs.

Earlier the same day: no page clips sideways any more.
- At the window's old fixed 1100 px minimum, seven pages lost up to 171 px
  of their right edge, the Dashboard the most.
- Each page now asks for its content's width (`pages/base.py`), so the
  window's minimum width follows the pages. With Windows fonts that's
  about 1,220 px at launch and 1,256 px once every page is built.
- The Dashboard explorer's meta line (bar size, source, bar count) elides
  instead of widening the page. Every page now fits a 1280 px window
  (half of a 2560 px monitor). At that width the line loses its last
  word, which its tooltip shows.
- Two smoke tests check this: no page clips at the window's minimum, and
  the minimum fits 1280 px with Windows fonts.

Before that, **2026-10-25**: one rule for "one day decides the numbers" (D53). The
Portfolio and Review pages now read the same flags. A day is flagged when
it outweighs the rest of the mix's series or of a holding's own:
z² > n − 1. V0's |z| > 10 and W7's t(3) null are gone.

Also **2026-10-25**: E1's code landed (D54). Portfolio's **From IBKR** reads
the login's accounts, positions and balances, read-only, into a kept
snapshot. The broker callbacks read both `ibapi` 9.81 and 10.x (10.33 moved
`error()`'s code). Still the owner's: the 10.x install and the live check.
Earlier the same day, the execution track was approved and E0 landed (D52,
[EXECUTION_PLAN.md](EXECUTION_PLAN.md)).
- D12 and hard rule 1 are amended: orders may exist only on one guarded
  path. `tests/test_layering.py` now enforces that statically.
- The settings and the order journal's schema are designed in the plan.
- No order code exists yet; E1 (accounts, read-only) is next.
- Still the owner's to settle: the money limits, the paper length, the
  ordering against LENS_PLAN, and a paper login.

Before that, **2026-10-24**: V0 landed (D50): data you can trust.
- History loads serve the store's bars, labelled, when IBKR is offline.
- Portfolio flags the days that decide its numbers, and shows the numbers
  without them beside the headline.
- The P&L histogram clips to the unflagged days.
- V0 is the first milestone of a proposed reorder,
  [LENS_PLAN.md](LENS_PLAN.md); the rest waits on the owner. Its visual
  direction is a spike, `spikes/portfolio_v2.py` (gitignored).

Before that, **2026-10-20**: the personal workstation phase was approved (D43,
[WORKSTATION_PLAN.md](WORKSTATION_PLAN.md)). W0 has started: the
`REGIME.md` template, and a dry run for the real Fidelity export. No app
code has changed. Before that,
**2026-10-19**: v1.0.0 Research foundation implemented (D42): six causal
strategies, seeded controls, risk sizing, the Research page, immutable saved
evidence and a local PDF/reference library. All completed experiments and
sweep trials are saved; reopening replays captured decisions and data.
The owner's “ship v1” authorized completing the remaining milestones.
R1's exact self-financing engine and IID-labelled statistics remain the
accounting foundation (D41). Also: first-visit window flashes fixed (D40).
Card subtitles are
parented before being shown; the Volatility Lab and its browser are prepared
before the main window appears, with fitting still deferred to the visit.
Before that: Portfolio shipped lean (D39), data foundations are complete
(D36–D38), and the option-chain and Markov modules are complete.
Remaining live acceptance limits are listed below; they are not silently
treated as verified.

## Status

v1.0.0. Brokerage connectivity was live-verified against TWS 7496 (2026-08-14;
see below). `py -3.12 -m pytest -q` → **993 passed, 1 skipped** in about
136 s (2026-10-19), with no
network or display needed (the GUI smoke tests run offscreen). The real 3D
draw runs in a fresh subprocess and skips, with the reason, when offscreen
Chromium can't compile WebGL shaders; that happens on some runs.

Final follow-up after extracting rolling Sharpe into the pure statistics
module: **86 passed, 1 skipped** (statistics, all GUI smoke tests, layering;
65.5 s). The v1.0.0 wheel built and its Research modules imported from an
extracted wheel independently of the checkout. Normal startup against the
owner's configured store succeeded; brokerage remained disconnected.
Final Research/Dashboard checks after broadening the implementation
fingerprint and removing the last planned-feature label: **15 passed**.

R1 checkpoint (2026-10-18): **68 new tests** cover accounting/causality and statistical
calibration; those plus the four layering checks passed independently.
The full suite, including the 3D draw on that run, passed. One existing
pandas date-inference warning comes from the malformed-date validation test.

Navigation fix verification (2026-10-18, D40): GUI smoke tests plus layering
checks **52 passed, 1 skipped** (offscreen WebGL). On Windows with a copied
store, startup, first visits to Volatility Lab and Options Lab, and the
actual 3D surface draw kept one native window, with no temporary subtitle
windows. The new regression observes transient Show events during startup
and every page's first visit, and checks that preparation starts no fit.

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
  - app icon (2026-10-24): a blue α in a ring on a near-black tile, the
    ML Cookbook β icon's layout (`gui/app_icon.py`, regenerate with
    `py -3.12 -m quant_hub.gui.app_icon`). The window, taskbar and the
    desktop shortcut use `assets/quanthub.ico`. `main()` sets an explicit
    AppUserModelID so the taskbar shows it instead of Python's icon. The
    desktop shortcut got the same ID from a one-off pywin32 script (the
    Properties dialog can't set it). A recreated shortcut would lack it:
    it still works, but a pin and the running window become two taskbar
    buttons. Not yet checked on the real taskbar.
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
    - built before the main window is shown (D40); its ~0.7 s of GUI
      construction time is paid at launch, and fitting starts on the visit
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
- **Markov Lab (M2):**
  - synthetic worlds with known regimes (two, three, or none), or source
    history
  - 2 regimes, 3, or Auto (the BIC sweep over 1–3)
  - the price tinted by regime, more strongly the more turbulent, over
    stacked regime probabilities
    - a Filtered / Smoothed / Viterbi toggle, with the hindsight views
      labelled
    - in synthetic mode, a strip of the true regimes
  - per-regime vol, drift, expected stay, long-run share and share of the
    sample's days, with TRUE beside FIT when the counts match
  - log L, BIC and the BIC of every count tried (a fit that ran out of EM
    iterations is marked)
  - the transition matrix, and the regime odds today, in 1, 5 and 20 days,
    and in the long run
  - notes for non-convergence, a regime on the variance floor, and BIC
    choosing a different count than the synthetic truth
- **Research v1:** Experiments, Saved runs and Papers replace the final
  placeholder. Six long/cash rules use rolling training windows and forward
  filters; only test returns are reported. Source, basis, seed, exact prices,
  targets, fits, costs, statistics and implementation fingerprint are saved.
  Notes remain editable separately. Parameter grids retain every trial and
  a selection manifest; IID DSR requires an explicit independent-trial count.
  The local paper catalog supports PDF copies, source references, search,
  reading status, notes and links to experiments. No PDFs are bundled.
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
- **IBKR disconnected (changed 2026-10-24, D50):** `ibkr` history loads
  serve the stored, covered bars, labelled offline. They still fail when
  nothing is stored for the window, or when the stored bars have a
  weekday gap inside it.
  - Exchange holidays count as missed sessions in the staleness note.
  - Not yet checked against a live TWS session that drops mid-request.
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
- **GARCH Lab:** Gaussian QMLE only, with no heavy tails or confidence
  intervals. Research provides rolling refits and out-of-sample evaluation
  separately. Annualization assumes 252 periods/year.
- **3D surface:** synthetic and stored/live market chain fits use the same
  mesh. The draw is verified by screenshots on the owner's GPU and by the
  subprocess test, which may skip on offscreen WebGL failures.

## Open decisions (waiting on the owner)

- **Research after v1:** [RESEARCH_DESIGN.md](RESEARCH_DESIGN.md) retains the
  broader direction: personalized portfolio decisions, comparison across
  matching runs, richer literature search and annotations. The foundation
  is implemented; those extensions remain later work.

- **Kalman K2 review:** the Kalman Lab page landed; the owner reviews it.
  - Live `ibkr` history loads are untested: the screenshots fed stored SPY
    and NVDA bars to the page directly.
  - Possible follow-ups, not started:
    - the trend model's Holt equivalence: its steady-state filter is
      Holt's linear smoothing with α = K̄₁, β = K̄₂/K̄₁
    - an exact diffuse initialization
- **S3D review:** the 3D surface landed (D32); the owner reviews it.
  - The earlier 1280-pixel input clipping was fixed when market chains
    shipped (C2, entry below); it is no longer an open task.
- **DCF modeler port (owner request 2026-10-14):** planned 2026-10-20 as
  W4–W6 of the personal workstation phase (D43).
  - The tool is `C:\Users\adams\Projects\Fin-SW\DCF_proj` (`dcfmodeler`
    0.2.0; 28 offline tests pass).
  - Correction: it doesn't store anything. It fetches live through
    edgartools, and SQLite was only the plan.
  - W4 (the engine) landed 2026-10-21 (D46). Waiting on the owner: which
    of the three conventions (terminal flow, valuation date, securities
    netting) become the page's defaults.

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
   - The option-chain adapter (C) is complete.
   - ~~M1: Gaussian hidden Markov model~~ (2026-10-15), `models/markov.py`.
     The derivation was reviewed before the code.
     - The filter uses log-shifted emissions, with a log-space redo for a
       day whose best regime is impossible. The backward smoother is
       Kim's, and Viterbi runs in logs.
     - EM has closed-form M-steps and is monotone by Gibbs' inequality.
       - π is fixed at uniform.
       - A variance floor (1% of the sample variance) keeps the
         likelihood bounded. Without it, a regime collapses onto stale
         zero returns (σ ≈ 1e-150).
       - Starts: volatility quantiles plus 4 seeded random starts, short
         runs of 25 iterations, at most 500 iterations.
     - States are sorted by σ. BIC picks K from 1–3. Also: the stationary
       distribution, expected durations, and regime forecasts with
       predictive moments.
     - A fit that runs out of iterations usually means K is too big. On
       i.i.d. returns, 3 regimes were still crawling after 2,000
       iterations.
     - Synthetic, against the truth:
       - every fit beats the true parameters' log L (by 1–10 nats)
       - σ within 2.2 SE, p_ii within 3.6 SE, μ within 4.1 SE (14
         seeded fits)
       - BIC picks the true K, including 1 on i.i.d. returns
       - calibration error under 0.03 (a transposed P scores 0.27)
     - Real SPY (1,296 days, 2021–2026; sweep 1.5 s): BIC prefers 3
       regimes, narrowly over 2. Vols are 9 / 17 / 33% annualized, on
       45 / 44 / 10% of days. The April 2025 selloff is in the 33%
       regime.
     - Tests: `tests/test_markov.py` (40, about 20 s). The filter,
       smoother and Viterbi are checked against enumeration of all Kⁿ
       paths. Plus the closed forms (occupancy, stay lengths, λʰ
       clustering) and forecasts.
   - ~~M2: the Markov Lab page~~ (2026-10-15), `gui/pages/markov_lab.py`.
     - Above, under The app. Regimes are sorted by vol, so the calmest is
       always green and the most turbulent orange, in every panel and
       table.
     - The chart layer gained `Shade`, a per-day background tint (one
       image, a pixel per calendar day, so there are no seams and no
       weekend shift), and stacked panels, whose readout shows each
       series' own value.
     - The default synthetic world has three regimes. Every candidate
       count is identified there, so the BIC sweep converges in about
       4 s. On the two-regime world, the 3-regime fit runs out its
       iterations (about 9 s).
     - Synthetic default (2,520 days, seed 42): BIC picks 3 and Viterbi
       labels 89% of days correctly. That sample spent 28 / 55 / 17% of
       its days in the three regimes, against a long run of 46 / 42 /
       12%, so the page compares the fit with the sample's realized share
       too.
     - Live against TWS on SPY through the ibkr path:
       - 5 years (1,252 returns, 1.9 s): 3 regimes at 9 / 17 / 34% vol
       - 10 years (2,510 returns, 6.7 s with the fetch): 8 / 19 / 60% vol.
         The 60% regime is the COVID crash (3.6% of days, 9-day stays).
       - Today: 86–89% calm.
     - Tests: 3 smoke tests (truth and views, Auto on market history,
       notes).
   - Markov regimes (M) are complete.
   - ~~F1: settings and store safety~~ (2026-10-15, D36).
     - `~/.quant_hub/settings.toml` (or `QUANT_HUB_SETTINGS`), read with
       `tomllib` over the defaults.
       - It covers the store path and IBKR's enabled, host, port and client
         id. The first run writes a commented template.
       - Every problem is logged and keeps that setting's default; none is
         fatal.
       - IBKR turned off registers no IBKR sources and hides the top bar's
         Connect control and the status bar's IBKR state. Pages fall back
         to synthetic.
       - The default port stays 7496 (TWS live). Your setup needs no file;
         the template your first start writes holds exactly today's values.
     - `storage/schema.prepare_store` runs before the stores open the file.
       - One schema version, recorded in `schema_migrations`.
       - Before any migration, the whole file is copied into `backups/`
         (SQLite online backup). Steps are transactional, and a file from a
         newer app is refused.
       - v0 → v1 only adopts existing files. Your next launch will leave
         `~/.quant_hub/backups/market_data.v0.<time>.db`.
       - Checked on a copy of your store: backed up, adopted, all 5,313
         bars intact; a second start made no new backup.
     - `CachedSource` rejects non-daily bar sizes
       (DATA_SYSTEM_IMPROVEMENTS #7).
     - Tests: `tests/test_config.py` (15), `tests/test_schema.py` (6), the
       cache guard (3), and 2 smoke tests (startup backup, IBKR off).
   - ~~F2: file import, the universal provider~~ (2026-10-15, D37).
     - `importers/bar_csv.py` reads the generic layout
       (`date,open,high,low,close,volume`, optional `symbol`; Stooq's own)
       and presets detected by header: Nasdaq.com (`$`, MM/DD/YYYY, newest
       first) and Yahoo Finance (takes `Close`; `Adj Close` waits for F3).
       - An unreadable date stops the import, naming the row.
       - `$`, thousands separators and `null` are tolerated.
     - `services/file_import.py` validates each symbol like IBKR's bars and
       stores them under source `file`.
       - All or nothing per file. A re-import replaces the same dates.
       - `FileSource` serves imports to every page, keeping no coverage.
       - It has no live quote, so the Options Lab uses the last close.
     - The Dashboard's Market data menu has Import CSV….
       - The symbol comes from the file name (`SPY.csv`, `^GSPC.csv`) or
         is asked for.
       - The result reads "Imported SPY: 1,296 bars, … (Yahoo Finance
         layout; 1 row dropped)", and the explorer charts the import.
       - A symbol stored from two sources shows as "SPY · file", so
         neither hides the other.
     - Round trip on your real SPY (1,297 bars):
       - written as Yahoo and Nasdaq files and imported into a copy, with
         closes identical to IBKR's
       - Yahoo's `null` row dropped and reported
       - a Markov fit on the `file` source finds the same 3 regimes
     - Tests: `tests/test_bar_csv.py` (13), `tests/test_file_import.py`
       (15), and a Dashboard smoke test. Layering now covers
       `importers/`.
   - ~~F3: adjusted prices~~ (2026-10-16, D38).
     - Spike against TWS (`spikes/spike_adjusted.py`):
       - TRADES is already split-adjusted.
       - ADJUSTED_LAST is TRADES times a factor that steps at ex-dates,
         f = 1 − D/C_prev. The implied SPY dividend is $1.6959 against
         $1.6956.
       - The latest prices are equal in both.
     - `PriceBasis` (traded / adjusted).
       - `MarketService.get_history` requires it, and every
         `PriceHistory` carries it.
       - `get_return_history` gives adjusted, or traded where an import has
         no adjusted prices, for pages with no Prices choice.
     - Schema v2: the bars key includes the basis; a `refreshes` table.
       Your store copy migrated v1 → v2 with a backup, all 5,313 bars
       traded.
     - `CachedSource`:
       - Adjusted bars are refetched whole once a day; later syncs that day
         add only what they fetch.
       - The split guard repairs traded history straddling a split.
     - Files declare their basis by column: `close` traded, `adj_close`
       adjusted (Yahoo's both).
     - GARCH, Kalman and Markov have a Prices choice (Adjusted). Realized
       vol and the vol overlay use adjusted; Markets and the Dashboard show
       traded.
     - Tests: in `tests/test_cached_source.py`, an IBKR-like fake checks:
       - total return over a dividend
       - the daily refetch with no seam
       - a split after caching, repaired
       - same-day alignment
       - the guard's cases

       Also the migration (a migrated file equals a new one), file bases,
       the fallback, the IBKR series choice, and the Prices smoke test.
     - **Not yet live through the cache:** TWS was closed at the end
       (2026-10-16, 00:43). The spike ran live the day before.
   - Data foundations (F) are complete.
   - ~~P: Portfolio, shipped lean~~ (2026-10-16, D39), risk first, for a
     stock and ETF portfolio that uses options sparingly.
     - `data/positions.py`: `Position`, `OptionContract`,
       `PositionSnapshot`.
     - `importers/positions_csv.py`: the generic layout and the Fidelity
       preset.
       - It handles `$` values, `SPAXX**` as cash, the Pending Activity
         row, options like `-AAPL260116C250`, and the disclaimer footer.
       - The "Date downloaded" line dates the snapshot.
     - `storage/position_store.py` keeps every import as a dated snapshot.
     - `models/portfolio.py`, derivation in its docstring:
       - vol and Euler risk contributions, beta;
       - historical VaR/ES (options repriced in full) and Gaussian VaR/ES;
       - the drawdown of today's mix replayed over the lookback;
       - N_eff;
       - Kelly f* with its error 1/(σ√T).
     - `services/portfolio.py`:
       - Weights come from the export's own values.
       - Options are priced at the file's underlying price and the
         underlying's realized vol.
       - Holdings without history, and expired options, are named, with the
         share of equity left unmodelled.
       - A $100,000 example portfolio covers the time before any import.
     - The Portfolio page:
       - six risk tiles;
       - holdings sorted by risk share against weight (amber where risk
         outruns value);
       - today's mix replayed against the benchmark, with its drawdown;
       - the one-day P&L histogram with VaR marks and the Gaussian curve;
       - sizing (mean, vol drag, Kelly ± error, N_eff);
       - Import positions…, and an account selector.
     - Tests:
       - `tests/test_portfolio.py` (19): the identities, the closed forms,
         VaR exceeded on 5% of 40,000 fresh days, covariance recovery, and
         the put repriced on a crash
       - `tests/test_positions_csv.py` (13)
       - `tests/test_portfolio_service.py` (8)
       - 2 smoke tests
     - **Not yet checked against a real Fidelity export.** The preset
       follows Fidelity's known format; one file of yours will confirm it.
   - Deferred to v1.1: the optimizers and frontier, Ledoit–Wolf, IBKR
     positions, the rebalance ticket.
   - ~~R1: daily backtest engine and statistics~~ (2026-10-18, D41).
     - `models/backtest.py`: vectorized self-financing costs, drift-aware
       rebalancing, decision-at-t → return-at-t+1, cash and explicit limits.
     - `models/backtest_stats.py`: CAGR, simple-return vol, Sharpe with its
       IID SE, Sortino, drawdown, turnover, PSR and DSR.
     - Walk-forward callbacks receive only training/current history.
     - Tests check a separate share/cash ledger, hand cost identities,
       future perturbation, Gaussian SE/coverage and many-trial nulls.
     - Sharpe inference assumes independent returns; there is no serial
       dependence correction. DSR needs the real trial history and an
       explicit independent-trial count.
   - ~~R2/R3 and lean paper library~~ (2026-10-19, D42).
     - `models/strategies.py`: momentum, mean reversion, Kalman trend/level,
       GARCH volatility and Markov calm filtering. Fits exclude their test
       block; all position decisions use known observations only.
     - `portfolio.scale_to_risk`: homogeneous volatility sizing with a
       gross exposure cap. GARCH's Gaussian log-return variance is converted
       to simple-return variance before sizing.
     - `models/research_worlds.py`: null martingale prices, AR(1) log-return
       trend and mean-reverting log levels. Explicit scenario parameters.
     - `services/research.py` and `storage/research_store.py`: exact data,
       targets, fits and results; fingerprints and replay; SQLite v3 backed
       up before migration. All complete sweep trials plus manifest kept.
     - `gui/pages/research/`: experiments, results, trades, fit windows,
       sweep heatmap/DSR, saved-run notes, PDF/reference catalog and links.
     - Native screenshots checked at 1280×800 and 1720×1040 on an isolated
       store. GUI smoke checks cover runs, replay, notes, grids, PDF imports
       and paper/experiment links. All six rules pass future-perturbation
       invariance; model parameter JSON round trips and GARCH timing checked.
     - **External acceptance remains:** a real Fidelity positions export;
       fresh adjusted-history/cache verification with TWS open. No TWS
       window was available in this release session. No new live pass is
       claimed. Research is daily, long/cash, idealized close execution;
       costs omit impact/taxes/borrow and Sharpe inference assumes IID.
     - Back up both the SQLite file and sibling `market_data.papers/`.
       Automatic schema backups copy the database, not the PDF folder.
6. **The personal workstation phase** (approved 2026-10-20, D43), W0 → W7:
   see ROADMAP.md.
   - **Reorder proposed (2026-10-24, [LENS_PLAN.md](LENS_PLAN.md)):**
     V0 → V1 → W2 → L → W2b → O. Only V0 is approved. W2–W7 have since
     landed through PR #2. What's left of the plan:
     - V1
     - the CAPM extras (security market line, GRS, Kalman β)
     - L, W2b and O
   - **~~V0~~ landed (2026-10-24, D50):**
     - `SourceOfflineError` on the port; `CachedSource` serves one stored,
       covered stretch when offline
     - `PriceHistory.offline_reason`, shown by Portfolio, the labs,
       Markets' sync and research provenance
     - `weekdays_missed`
     - influence diagnostics in `models/portfolio.py`: shares, robust z,
       flags at |z| > 10, drivers, the "without" report
     - `Histogram` clip
     - Portfolio's "Days that decide the numbers" card
     - Full suite after merging main (PRs #2 and #3): 1,252 passed,
       1 skipped (172 s).
     - Tests: 10 cache, 4 ranges, 7 influence, 1 service, and one GUI test
       of the whole offline path.
     - Checked on a copy of the owner's store with TWS closed and the real
       clock. The page ran on stored bars through the last session and
       flagged one day.
   - **One rule for both pages (2026-10-25, D53; amends D49 and D50):**
     - The comparison and the derivation came first. The owner approved
       the robust share ŝ = z² / (z² + n − 1) > ½, on the mix and on each
       holding.
     - `models/portfolio.py` gained:
       - `INFLUENCE_SHARE`, replacing `INFLUENCE_Z`
       - `robust_share`, `influential_days` and `variance_shares`
       - `HoldingDay`, and on `InfluentialDay` the fields `holdings` and
         `robust_share`
     - `models/regime.py` lost `Dominance` and its t(3) calibration.
     - `services/review.py` reads `view.report.influence` and removes
       those days. The Review card shows one row per series a day decides.
     - Tests:
       - `TestInfluence` covers the holding-only flag, masking at both
         levels, the deletion identity, the closed form, z > √(n − 1),
         t(3)'s rate in a binomial band, and rejections.
       - The review test checks that the graded numbers equal Portfolio's
         WITHOUT.
       - The W7 dominance tests are gone.
     - Measured on a copy of the owner's store: the one day V0 found is
       still the only flag, across the mix and every holding. SPY's
       Apr 9, 2025 is no longer flagged by itself.
     - Not yet seen on screen with the owner's real book. The GUI smoke
       tests cover both cards.
   - **Open (LENS_PLAN decisions):**
     - the visual direction (the spike)
     - the order after V0
     - the CAPM workshop's extras on top of the landed W2
     - Ken French's library and FRED as providers
   - W0 in progress:
     - The `REGIME.md` template is drafted; the owner fills in the values.
     - A dry run for positions files (`spikes/fidelity_check.py`, local and
       untracked) reads the export with the app's importer, stores nothing,
       and reconciles it.
     - **Real exports checked (2026-10-21).** Four owner exports (Jun 1,
       Jun 29, Jul 27, Oct 21) turned out to be Fidelity's Positions-view
       layout (D43). The D39 preset read them as generic: cash came in as
       stocks, and the date was the import time.
     - A second preset now reads them, with 6 new tests on a made-up file
       of the same shape. Full suite: 1,002 passed (126 s), with the
       known pandas date-inference warning; 1,010 after the per-account
       import below.
     - All four now parse with every row accounted for.
       - Jun 29's value matches Fidelity's Totals row to the cent.
       - Jun 1's cost basis matches to the cent, but its value is a few
         hundred dollars above Fidelity's Totals: unexplained, and it
         isn't a missing row.
     - **Checked against Fidelity's own screens (2026-10-21).** The
       owner's screenshots of the all-accounts view and each account match
       the Oct 21 export exactly in quantities, cash lines and cost basis.
       Values differ only by price moves after the export, and each
       screen's rows sum to its own total.
     - **One export per account.** The Positions-view download has no
       account column, so separate accounts need one export each.
       - Portfolio's *Import positions…* now takes several files from the
         same day as one snapshot (`combine_snapshots`,
         `read_positions_files`).
       - It refuses different days, an account in two files, rows without
         an account, and an All Accounts export beside per-account ones.
       - The dry run takes several files too.
       - Not yet seen: a real per-account export, whose preamble should
         name the account.
     - **The analysis with TWS open (2026-10-22).**
       - The owner's in-app imports had failed. Their checkout predated
         this preset, so the Oct 21 export was read as generic and its cash
         lines as stocks.
       - The analysis then said "0 common days of returns; the model needs
         60". The app wasn't connected to IBKR, so every history failed.
       - Now, on a copy of the store with IBKR connected: every holding
         loads (adjusted, 500 common days, ~5 s).
       - Two fixes came out of it:
         - **Clear load errors.** When no holding loads, the error says so
           and gives the first reason; for "not connected" it points at
           Connect IBKR (TWS being open isn't enough). A holding with fewer
           than 60 days in the lookback is named and left out, instead of
           shrinking every holding's window to its own. An all-cash
           account says it has no market risk.
         - **The split guard allows a cent's rounding.** The first live
           adjusted refresh flagged a holding as a corporate action. Its
           pre-split history, split-adjusted to low prices, had come back
           rounded differently two months apart, by up to 0.04%, against a
           fixed 0.01% tolerance. The slack is now that plus a cent at each
           day's price; a split still fails by orders of magnitude.
     - **Not yet done:**
       - checking the import's total against Fidelity's site
     - Note: the page analyses the most recently *imported* snapshot, so
       import older exports first.
     - **W2 derivation written for review** (`models/regression.py`,
       docstring only, as K1 was). Its coverage claims were checked by
       simulation first: in two variance regimes, classical intervals
       cover 78.9% against the 78.4% closed form; under AR(1) residuals,
       White covers 84.9% against 85.0%.
   - **W2 landed (2026-10-21, D44)** after the owner's "continue on".
     - `models/regression.py`: OLS with classical, White and Newey–West
       covariances, the Dimson sum, causal rolling fits, and Fisher-z
       residual correlations.
       - `tests/test_regression.py` (18) follows the docstring's plan.
       - Coverage against the closed forms at 3,000 runs each: two
         variance regimes, and AR(1) residuals.
       - Also: the Bartlett block-sum identity and PSD, invariance under
         factor recombination, causality of the rolling fit, Fisher
         coverage, and the portfolio identity.
     - `services/portfolio.py` adds a `Regressions` bundle to each
       analysis. Its portfolio β equals the Beta tile's (tested).
     - The Portfolio page gains three cards below the existing ones:
       - Regressions on the benchmark: α a year ± SE, grey when within its
         error of zero; β ± SE; lag β; R²; residual vol; days
       - Rolling β for the clicked row, with a ±1.96 band
       - Residual links, strongest first, amber when not orthogonal
       - A Lagged β choice (Off / 1 day) sits in the inputs.
     - Screenshots at 1280×800 and 1720×1040 on a copy of the store, with
       real fonts: no horizontal overflow (content 823 px in a 1,038 px
       viewport at 1280).
     - **Extra factors** (D44, amended): up to three ETFs as partial
       betas.
       - Factor returns are kept apart from the risk model's, which is
         tested unchanged. A factor without history is named.
       - The regressions card now has two tables: α, β, R² and residual
         vol; then the factors' and lag's betas when there are any. At
         1280 px the nine-column version overflowed.
     - Not yet: a run on the owner's real holdings, which needs the
       import with TWS open.
   - **W3 derivation written for review** (2026-10-21,
     `models/growth.py`, docstring only).
     - Growth from the replay's per-position excess series.
     - An exact attribution, since per-position covariances with the
       portfolio sum to its variance.
     - The hurdle at the margin, with *oversized* when a position adds
       growth overall but loses it at the margin.
     - Two sleeve sizes: c_mv, which minimizes variance, and c* = c_mv +
       the sleeve's own Kelly fraction, which maximizes growth.
     - Book greeks and annual carry.
     - Simulations changed two parts of the plan:
       - the quadratic matches realized log growth to about 0.003% even
         with puts and crashes; it's delta-counting that misses
         convexity
       - over two years, ĉ* scattered 141 times more widely than ĉ_mv
         (2.52 ± 0.20): its error was 28 times today's size, though its
         intervals still covered 94.9%
     - Code waits for the owner's review.
   - **W3 landed (2026-10-21, D45)** after the owner's approval.
     - `models/growth.py`: growth, attribution, the four verdicts, both
       sizes with errors, what-if, book greeks.
       - `risk_report` now keeps each position's replay series, value and
         underlying.
       - An option's value is the export's own premium when known.
     - `tests/test_growth.py` (20) follows the docstring's plan:
       - the attribution is exact with a put in the book
       - the margin matches finite differences for a stock and the put
       - the closed form holds at sample moments
       - c* agrees with a grid search, equals c_mv plus the sleeve's own
         Kelly fraction, and keeps f(2 − f) under fractional sizing
       - coverage of both sizes at 95% over 1,500 two-year runs, with
         c*'s error more than ten times c_mv's
       - orthogonal streams add ½ SR² each, exactly
       - quadratic and realized log growth agree within 0.01%
       - book greeks match finite-difference repricing
     - The service groups sleeves by underlying. For stock-only books its
       growth equals the Sizing card's (tested); the Sizing card now
       shows W3's growth.
     - The Portfolio page gains two cards after One-day P&L:
       - Growth: four tiles, then the sleeves table (weight, contribution
         in bp, verdict, min-variance size, growth-optimal size) and a
         what-if
       - Options book: dollar delta, 1% gamma P&L, vega, theta, annual
         carry; shown only with options
     - Screenshots at 1280 with real fonts, for the example and a book
       with a protective put.
       - At first, five tiles in one row truncated the delta and pushed
         the page to a 1,028 px minimum. In two rows it's 865 px in a
         1,038 px viewport.
       - The first screenshot also showed a sleeve weight using the
         model's put premium, not the file's. Fixed.
   - **W4 landed (2026-10-21, D46)**: the owner's DCF engine, ported.
     - `data/fundamentals.py` holds the contract (loud failure, a
       `FundamentalsSource` port, marketable securities).
     - `models/dcf.py` holds the engine, sensitivity, multiples, market
       comparison, reverse DCF with every root, calibration and seeding.
       Its derivation is in the docstring.
     - The open conventions are options defaulting to the original:
       - the terminal flow: last-year or steady-state
       - the valuation date s, with EV × (1+W)^s
       - netting marketable securities
     - **A bug in the original, fixed:** `latest()` picked the oldest year
       in its adapter's newest-first order. A 22% understatement in the
       worked example.
     - `tests/test_dcf.py` (36):
       - the owner's engine, analysis and seeding tests (16), golden 18.784
         intact
       - new: the Gordon identity under both conventions at N = 1, 3, 5, 10;
         the steady-state flow's closed form and the 7.9% / 78% figures;
         the roll-forward; the two-root reverse DCF and the monotone case;
         unique implied WACC; securities netting; the latest-year
         regression test; rejections
     - No page yet: that's W6. The SEC adapter is W5.
   - **W5 landed (2026-10-21, D47)**: SEC fundamentals through the stdlib.
     - `providers/sec_edgar.py` is a new layer (data/ + stdlib only,
       layering-tested). It has the client (identity header, gzip, 10 a
       second, explained errors), facts as filed, and the pure mapping to
       `FinancialHistory`.
     - The mapping rules: annual forms and spans; latest filing wins
       (restatements); tag priority year by year; non-overlapping debt
       parts; weighted diluted shares, else the cover page, labelled.
       Every gap is a reason.
     - `storage/fundamentals_store.py` keeps facts, filers and the ticker
       map: schema v4, migrated after the backup. `services/fundamentals.py`
       serves the store for a day, refetches on Refresh, and serves the
       stored copy when a refetch fails.
     - `[sec] identity` is in the settings (the template documents it).
       Fetching needs it; stored facts don't.
     - Tests:
       - `tests/test_sec_edgar.py` (31): every mapping rule on a made-up
         filer in the API's JSON shape (a restatement, a tag switch,
         quarterly noise, both debt layouts, the share fallback, a debt-
         free filer the DCF refuses loudly, EUR, 52/53-week labels), plus
         identifiers and the client against a fake opener
       - `tests/test_fundamentals_service.py` (10): store round trips, the
         v3 → v4 migration with its backup, caching and refresh, the
         identity, and failed fetches
       - 1 config test for `[sec] identity`
       - Full suite: 1,137 passed, 1 skipped (the offscreen 3D draw)
     - **Not yet live:** no request has reached the SEC from here. The
       check against real 10-Ks (AAPL and two others) needs the owner's
       identity in `settings.toml`.
   - **W6 landed (2026-10-21, D48)**: the Valuation page (core section,
     after Portfolio).
     - Load a ticker or CIK, and the assumptions seed from trailing
       averages. Edit them as one number or one per year; the DCF
       recalculates as you type.
     - Conventions: terminal flow, value as of, securities netting.
     - It shows the tiles (value, price, margin of safety, terminal share),
       filings with a Trailing row, the EV bridge and projection, the
       WACC × g grid, and the reverse DCF as every root plus uniqueness.
     - Price from a source (quote, else last close) or typed.
     - Screen: holdings or tickers, ranked cheapest first, failures as
       rows, CSV.
     - Saved valuations: immutable, schema v5. Reopening checks a
       fingerprint of the filing data.
     - `services/valuation.py` and `storage/valuation_store.py`.
       `FinancialHistory.fingerprint()`.
     - Tests:
       - `tests/test_valuation_service.py` (13): prices and their
         fallback; the screen's ranking and failure rows; the ported
         `rank_screen` test; CSV; save and reopen across a restatement;
         the valuation date; refusals; the fingerprint; the v4 → v5
         migration
       - 3 page smoke tests: load, value, price, save, reopen; per-year
         paths, overrides and bad input; the screen and export
       - Full suite: 1,154 passed
     - Screenshots at 1280 with real fonts found and fixed two layout
       bugs:
       - the Screen card's one-row controls set a 1,178 px minimum (now
         871 px in 1,038 px)
       - a wrapped note squeezed the Valuation card and hid the terminal
         row (D48; the trap is now in AGENTS.md)
     - **Not live yet:** real companies need the owner's `[sec]
       identity`. Also open: which conventions become the page's
       defaults.
   - **W7 started (2026-10-22): REGIME.md and the checks' derivation.**
     - The owner is filling in REGIME.md, section by section. Sections 1
       and 2 are done; sections 3–10 are open. The filled-in copy lives at
       `~/.quant_hub/REGIME.md`, outside the public repository. The
       template's stale lines were corrected (where the copy lives, the
       regression window, the cash rate, valuation refresh).
     - W2 ran on the owner's real holdings for the first time, on a copy
       of the store with history fetched through TWS. One holding's
       variance came almost entirely from a single news day. That led to
       the one-day-dominance flag in the derivation.
     - **W7 derivation written for review** (`models/regime.py`, docstring
       only). It covers:
       - breaches at an evidence level k, with the noise rate 1 − Φ(k) at
         the boundary
       - persistence as a delay, not a filter
       - the one-day-dominance flag, calibrated on a t(3) null
       - the filtered HMM and GARCH gates
       - the screen checks
     - WORKSTATION_PLAN's W7 records what was settled along the way: the
       rules' source and parser, the positions folder, journal trade
       drafts, and the cash rate.
   - **W7 landed (2026-10-23, D49)** after the owner approved the
     derivation: the Review page, the workflow's front door.
     - `models/regime.py`: the checks, with `data/regime.py` holding the
       records (Rule, Limit, Measure, RuleSet). The dominance calibration
       was a 100,000-run table that `calibrate_dominance()` reproduced;
       D53 replaced it.
     - `importers/regime_md.py` reads REGIME.md. The template gained what
       parsing needed: section 7's units, `by k SE`, the sleeve-size grade,
       the dominant-day choice, and section 1's N. The owner's private copy
       got the same changes, with their values kept.
     - `storage/regime_store.py`, schema v6: rule versions by text hash,
       immutable reviews with notes, hand-placed trades.
     - `services/review.py` runs the chain: rules, the newest export,
       history, risk, regressions and growth by the owner's sleeves,
       one-day dominance, the gates, saved valuations re-run, the
       checklist, section 1's persistence, and the journal.
       `PortfolioService.analyse` gained `sleeves` and `exclude`.
     - Settings: `[regime] path` (default `~/.quant_hub/REGIME.md`) and
       `[positions] folder`.
     - The Review page sits after Dashboard. `Note` gained a width so
       wrapped side-panel notes keep their last line.
     - Screenshots at 1280×800 and 1720×1040 with real fonts, on made-up
       data: every table fits, the content's minimum is 775 px in a
       1,038 px viewport, and no card is below its minimum.
     - Tests, 71 new:
       - `tests/test_regime.py` (26): the boundary rate 1 − Φ(k) and power
         by simulation; Fisher agreement with W2; run lengths; one-day
         dominance (closed form, null exceedance, the calibration row);
         filtered gates; GARCH against Monte Carlo; implied growth;
         rejections
       - `tests/test_regime_md.py` (31): the template reads all-off; every
         kind of row; every unreadable value names its line
       - `tests/test_regime_store.py` (4), including the v5 → v6 migration
       - `tests/test_review_service.py` (7), end to end on made-up tickers
         with a planted jump
       - 2 page smoke tests and a config test
       - Full suite: 1,225 passed
     - **Not yet:**
       - The owner's values for sections 3–10. The review runs with
         whatever is filled in.
       - A live review of the real book (TWS open, `[positions] folder`
         set).
       - Fetching the cash rate from FRED, a new provider, for discussion.
7. **The execution track, paper first** (approved 2026-10-25, D52), E0 → E6.
   See [EXECUTION_PLAN.md](EXECUTION_PLAN.md) and ROADMAP.md.
   - **~~E0~~ landed (2026-10-25):**
     - D12 and hard rule 1 amended (AGENTS.md, CLAUDE.md, README).
     - `tests/test_layering.py` gained two static checks:
       - `placeOrder`, `cancelOrder` and `reqGlobalCancel` appear only in
         `broker/orders.py`
       - only `services/execution.py` and `core.py` import it
       Neither module exists yet, so today no order call exists anywhere.
       Planted calls and imports were checked to fail, and look-alike
       names to pass.
     - The settings (`[execution]`, off by default, money limits fail
       closed) and the journal's SQLite schema are designed for E2.
   - **E1 code landed (2026-10-25, D54):**
     - `IBKRBroker.get_managed_accounts`, `get_positions` and
       `get_account_summary`, read-only, with subscriptions always
       cancelled
     - `error()` decoding chosen by the installed `ibapi`'s signature
     - `services/accounts.py` maps positions and balances to a snapshot,
       naming what it leaves out
     - `PortfolioService.keep`
     - Portfolio's **From IBKR** button (only with IBKR on)
     - Tests, 34 new:
       - `tests/test_ibkr_accounts.py` (25): both error forms, Decimal
         quantities, option rights and expiries, and the plumbing against
         a fake network (timeouts, a mid-read disconnect, late updates,
         cancels)
       - `tests/test_accounts_service.py` (7): the mapping, what's left
         out, and a kept read analysed like an import
       - 2 GUI tests, plus the IBKR-off test extended
     - **Not yet verified live.** TWS wasn't running, and 10.x isn't
       installed (it needs IBKR's license accepted).
       `spikes/e1_check.py` (local) does the read-only check: by default
       it masks account ids and hides amounts.
