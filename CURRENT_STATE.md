# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-10-10** (Qt migration: Q1–Q5 done. Every working page now runs in
the Qt UI. Q6, retiring the Tk code, is next.)

## Status

v0.4 remains live-verified against TWS 7496. `py -3.12 -m pytest -q` →
**573 passed** (no network or display needed; the Qt smoke tests run
offscreen; the suite now takes about 100 s, most of it Qt smoke tests).

Qt UI, milestone Q5 (2026-10-10, D31) — Volatility Lab (saved state shared
with the Tk page):
- **Experiment card:** chain row (spot, rate, div, expiries, strikes,
  spread, noise, seed) and true-SSVI row (ATM vol, long-run, κ, ρ, η), plus
  Generate & fit. A malformed expiry list is flagged on the field.
- **Surface fit card:** a Smiles / Term structure / Surface switch.
  - Smiles: 2×3 small multiples (bid–ask bars, mids, true dashed, SVI fit),
    each panel on its own scale; hover reads fit / true / nearest quote.
  - Term structure: ATM IV (SVI) with markers vs the true curve; "Compare
    with history" adds the GARCH expected-vol curve and realized-vol
    diamonds from any source.
  - Surface: heatmap in k/√θ on the validated sequential ramp, with a
    percent colour bar and cell hover.
- **Right panel:** fit-by-expiry table (SVI vs true ATM, χ²/pt) and checks
  (butterfly/calendar with good/critical tone, quote exclusions).
- **Tests:** smoke tests cover the fit, all views, heatmap hover, bad
  expiries, the history overlay, and a heavy-noise (40% spread, 2× noise)
  run.

Qt UI, milestone Q4 (2026-10-10, D31) — Options Lab
(`gui_qt/pages/options_lab/`, saved state shared with the Tk page):
- **Underlying card:**
  - source / symbol / lookback / Load, then spot, vol, rate, div yield
  - the provenance line as its subtitle, plus a MANUAL / LOADED / EDITED
    badge
  - loading a new symbol re-centres strikes
- **Pricer:**
  - Call/Put, strike, days, and "Price from σ" vs "σ from price" (IV
    inversion)
  - recalculates as you type (150 ms debounce)
  - profile chart: price or one greek vs spot at four expiries (ordinal
    ramp), hover reads all four
  - greek tiles underneath
  - impossible quotes show an explanation card instead of empty axes
- **Strategy:**
  - P&L at expiry with green/red profit/loss shading and breakeven dots
  - a days-forward slider redraws the T+h curve live
  - tiles: net entry / max profit / max loss / breakevens
  - legs table with an add-leg editor (Delete removes a leg)
  - template panel and position greeks
- **Monte Carlo:**
  - 10K/100K/1M paths, seed, antithetic
  - log-x convergence chart with the 95% band funnelling onto the closed
    form
  - CONSISTENT / OUTSIDE 95% / INVESTIGATE / STALE verdict badge
  - terminal-price histogram with a strike marker
- **Shell-wide fixes:**
  - `ScrollPage` now fills the viewport and scrolls only below the content
    minimum (word-wrapped labels had made pages size to their preferred
    height)
  - `PageStack` sizes tabs to the visible page
  - the default window opens at 88% of the screen, capped at 1920×1240
    (1920×1224 here)
- **Checked against the closed forms:** an ATM call, then inverting its own
  price recovers σ = 20.00% exactly (smoke test).

Qt UI, milestone Q3 (2026-10-10, D31) — Simulator:
- **Path card:** a "Sample path | Monte Carlo" toggle.
  - Sample path: close, drawdown (shaded) and rolling vol stacked under
    one crosshair, with a true-σ reference line. The vol window is an
    inline field in the card header; changing it re-analyses the same draw.
  - Monte Carlo: up to 160 faint paths plus 50% and 90% bands from the new
    `data.simulation.path_percentiles` (tested against the lognormal
    quantile closed form), the median, the E[Sₜ] = S₀·exp(μt) curve, and
    path 1 (the sample path) in orange.
- **Distributions:**
  - daily returns (log/simple toggle, normal overlay from the sample
    moments)
  - terminal S(T), with median / mean / E[S(T)] / p5 / p95 markers
    labelled in the readout legend
- **Estimates:** TRUE | ESTIMATE for μ, σ, μ − σ²/2, then sample-path
  statistics with signed values colored green/red.
- **D15 holds:** one TaskRunner job produces (sim, analysis, MC summary,
  bands); a smoke test asserts the analysis is of the simulated path.
- **Speed:** 500 paths × 252 days render in ~75 ms end to end, 5,000 paths
  in ~110 ms.
- **Layout:** fits 1720×1040 without scrolling; below 1300 px the two
  distribution cards stack.

Qt UI, milestone Q2 (2026-10-10, D31):
- **Markets:**
  - watchlist table: Symbol / Last / Mode badge / As of / Source / stored
    1Y sparkline, with balanced columns
  - page-owned source; add symbol (fetches just that quote); Delete or the
    inspector button removes a row
  - right-hand inspector: price and badge, the TWS reason in a red note when
    unavailable, stored-history sparkline and coverage, "Sync 1Y history",
    "Open in market explorer"
  - watchlist and source share the Tk page's saved state
- **GARCH Lab:**
  - inputs panel switches between synthetic and market history, showing
    only the active group
  - charts: stacked returns + annualized conditional vol with one crosshair
    (true σₜ under the fitted one in synthetic mode); residual histogram
    with N(0, 1) and hoverable bins
  - stats table gains a TRUE column for synthetic fits
  - invalid inputs are flagged red with the message in the status line
- **Shared components:** size tokens (control height, field widths,
  inspector width) now used on every Qt page, the Dashboard included.
- **Verified:** screenshots at 1280×800 and 1720×1040 (on a copy of the
  store) and 6 new smoke tests covering fits, error paths, add/remove,
  unavailable reasons and chart hover.
- **Not yet done:** Markets/GARCH against live TWS. With IBKR disconnected,
  GARCH market mode on `ibkr` fails for any window reaching today
  (CachedSource must fetch the uncached tail); the page shows the error and
  keeps the previous chart labeled.

Qt UI, milestone Q1 (2026-10-10, D31). It is the default: `py -3.12 -m quant_hub` and the `quant-hub` launcher
(the desktop shortcut) open it; `--classic` / `quant-hub-classic` open Tk.
- `core.py` now holds the non-GUI wiring for both front ends; the classic
  Tk UI is unchanged (`py -3.12 -m quant_hub`).
- `gui_qt/`:
  - navy design system: `theme.py` tokens + generated stylesheet, palette
    re-validated for CVD and contrast
  - components layer
  - pyqtgraph charts with a snapped crosshair readout
  - shell: icon sidebar, breadcrumb, Ctrl+K search over pages and stored
    symbols, IBKR connect, status bar with a log drawer that starts closed
- The new Dashboard (`services/workstation.py`), reading only the local
  store:
  - KPI tiles
  - market explorer: price + volume, 1M–ALL windows anchored at the last
    stored bar
  - sparkline watchlist with 1-day change
  - store table
  - GBM forecast fan fitted to the explorer's symbol, labeled illustrative
  - research shortcuts
- Every other nav entry shows a placeholder: "moving to the new interface,
  use the classic UI", or "planned".
- Measured on this machine: symbol switch 34–74 ms to fully painted
  (SQLite reads, 1,000-path simulation, both charts); window change ~30 ms;
  hover ~1 ms per mouse move including repaint. Tk took ~120 ms per chart
  redraw and ~450 ms per resize step.
- Verified by screenshots at 1280×800, 1480×920 and 1720×1040, plus
  synthetic hover events.
- Not yet done by hand: clicking through the live app, Connect against
  live TWS from the Qt top bar, and the Ctrl+K popup styling (covered only
  by tests).

Volatility Lab (Milestone B, 2026-09-27, D30):
- `data/options.py` defines the `OptionChain` contract.
- `models/svi.py` holds raw SVI (derivatives, density/`g(k)`, Lee bound),
  the SSVI surface, and a spread-weighted fit on the shared Nelder–Mead.
- `models/vol_surface.py` quotes synthetic OTM chains (spread, mid noise,
  no-bid wings), inverts IVs, fits per expiry, and runs the butterfly and
  calendar checks.
- `models/garch.py` gains `next_variance` and `average_variance_forecast`.
- `services/vol_context.py` provides realized vols plus the GARCH forecast
  from any source.

GUI (`gui/views/volatility_lab.py`, replacing the placeholder): a
SYNTHETIC CHAIN card, then tabs for Smiles (small multiples: bid–ask IV,
mids, true, fit), Term structure (ATM IV fit vs true, with "compare with
history" GARCH/realized overlay), and Surface (heatmap in k/√θ). A
diagnostics panel shows per-expiry ATM fit vs true with χ²/pt, the
arbitrage checks, and quote exclusions. The whole pipeline runs ~0.25 s on
the TaskRunner. It was driven in the real app with screenshots, including
a heavy-noise stress case.

Options Lab (Milestone A, 2026-09-27, D26/D28/D29):
- `models/black_scholes.py`: Black-Scholes-Merton with dividend yield,
  vectorized `price_array`/`greeks_array`, a typed `Greeks` (D25 trader
  scaling), `MarketInputs`, no-arbitrage bounds, and the bracketed-Newton IV
  solver (~11 price evaluations, ~0.2 ms per solve).
- `models/strategy.py`: multi-leg positions (signed contracts plus stock,
  recorded or fair entry premiums), P&L/greeks at any horizon, exact
  piecewise-linear expiry profile for single-expiry positions (grid
  fallback, marked "≈", for calendars), 11 templates.
- `models/option_monte_carlo.py`: risk-neutral MC with antithetic variates,
  running estimate ± SE, and a z-score vs the closed form.
- `services/underlying.py`: any source → spot plus realized vol, with
  provenance.

GUI (`gui/views/options_lab/`): an UNDERLYING strip (manual / synthetic /
ibkr, Load on the TaskRunner, provenance line, EDITED flag) over three tabs:
- **Pricer**: price ↔ IV, greeks, and a greek-vs-spot profile across four
  times to expiry.
- **Strategy**: templates or hand-added legs, P&L at expiry and T+h,
  breakevens, and position greeks.
- **Monte Carlo**: convergence chart plus a terminal distribution.

Loading a new symbol re-centres strikes. The old calculator's saved inputs
migrate. Verified by driving the real app on the synthetic source with
screenshots at 1280×800.

GARCH (2026-09-20, optimizer fixed 2026-09-27, D27): `models/garch.py`
provides stationary GARCH(1,1) simulation, conditional-variance evaluation,
and a Gaussian-QMLE fit via the shared `models/optimize.py` Nelder–Mead.
The pre-fix optimizer searched only a line (degenerate simplex), so any
GARCH fits made before 2026-09-27 should be rerun. GARCH Lab exposes
synthetic simulation and explicit market-history fitting through the
SQLite-first `ibkr` cache.


Page architecture (D24): **Dashboard** = workstation overview (IBKR
connection card, local-store summary card, page guide); **Markets** = real
observations with a page-owned source selector, columns
Symbol/Last/Mode/As-of/Source, per-row UNAVAILABLE states with the TWS
reason shown in the detail line, and a persist-only "Sync 1Y history"
button; **Simulator** = the GBM/Monte Carlo laboratory, synthetic-only,
math/analytics/tests untouched; **GARCH Lab** is the first real MODELS page,
with five remaining placeholders. It fits synthetic GARCH paths or explicit
market-history log returns through a selected source (the `ibkr` source reads
SQLite coverage first). Log panel is collapsible (header strip shows the
latest message).

Quote provenance (D22/D23): `Quote.mode: MarketDataMode`; IBKR quotes
stream briefly (self-cancelled), normalize live+delayed ticks and the
`marketDataType` callback, treat entitlement errors 10167/10089 as
advisories, and time out into `NoMarketDataError` carrying the advisory
text. `MarketService.active_source` is **deleted** — every call names its
source; no page can change another page's data meaning.

**Live verification (2026-08-14):** SPY/QQQ/MSFT/TLT/GLD → DELAYED quotes
with real per-symbol last-trade timestamps; AAPL → UNAVAILABLE ("requires
additional subscription for API") while the connection stayed CONNECTED;
synthetic refresh labels rows SYNTHETIC; Simulator runs; Dashboard store
card lists the persisted series.

## Untested / known limits

- Volatility Lab chains are synthetic only (no IBKR chain adapter yet).
  Per-expiry SVI fits are independent, so they overfit very noisy chains:
  at 40% spreads and 2× noise, ATM errors reach several vol points. A
  joint SSVI fit is the planned remedy. "Compare with history" with
  `ibkr` is untested against live TWS.

- Options Lab `ibkr` load has not been exercised against live TWS. It uses
  the same `CachedSource`/quote paths the Markets page verified, but the
  quote → last-close fallback has only been tested with a fake source.
- Options are European with a continuous dividend yield: no American early
  exercise, discrete dividends, chains, skew, or live positions. Strategy
  scenarios hold σ/r/q fixed over the horizon (no vol dynamics).
- Rate and dividend yield are manual inputs (no source yet).

- Quote refresh is serial (one TaskRunner worker, D9): each entitled symbol
  ~1s; an unavailable symbol costs the full `quote_timeout_s` (5s).
- FROZEN/DELAYED_FROZEN modes are normalized but not yet observed live
  (market was open during verification).
- 10089's meaning is account-dependent; for this account it means no API
  delayed data for that instrument (verified for AAPL).
- Watchlist symbols, Markets source, dashboard layout, and window geometry
  are **not persisted** (deferred with the SettingsStore, per the reduced-M6
  scope). Historical charting has no GUI surface yet — "Sync 1Y history"
  persists only; richer historical research is a later milestone.
- Prior M4 limits unchanged (end-"now" fetches, daily bars only, STK/USD
  only, no retry/backoff).
- GARCH uses a Gaussian QMLE and deterministic NumPy-only multi-start
  Nelder–Mead search. It enforces stationary GARCH(1,1), requires at least
  20 finite returns, and has no robust/heavy-tailed innovation option,
  confidence intervals, rolling refit, or out-of-sample forecast evaluation.
- GARCH Lab's annualized-volatility display assumes 252 daily periods/year.
  Synthetic and GUI construction paths are tested; a real cached/IBKR history
  fit has not yet been manually live-verified.

## Version control / environment

Repo at `C:\Users\adams\Projects\quanthub` (formerly `tradinghub`); owner
commits. Committed through the Black-Scholes calculator (3ef7cdb) and the
agent docs (996f6fc); GARCH and the 2026-09-27 housekeeping are staged or
uncommitted. Bytecode is untracked as of 2026-09-27 (`.gitignore` already
covered it). Run everything with `py -3.12`: bare `python` is a 3.11
install without pytest. Windows 11, Python 3.12.6, ibapi
9.81.1.post1; TWS live on 127.0.0.1:7496, Read-Only API enabled. Store:
`~/.quant_hub/market_data.db`.

## Next steps (updated 2026-10-10; see ROADMAP.md)

0. Qt migration (D31), one page per milestone with owner review:
   - ~~Q2: Markets + GARCH Lab~~ (done 2026-10-10)
   - ~~Q3: Simulator~~ (done 2026-10-10)
   - ~~Q4: Options Lab~~ (done 2026-10-10)
   - ~~Q5: Volatility Lab~~ (done 2026-10-10)
   - Q6: delete `gui/` and `app.py`, make Qt the default


1. ~~Milestone A — Options Lab~~ (done 2026-09-27).
2. ~~Milestone B — Volatility Lab~~ (done 2026-09-27).
3. Next phase: the data side. Live-verify both labs' `ibkr` loads, then an
   IBKR option-chain adapter emitting `OptionChain`, real positions as
   `OptionLeg`s, and the trading dashboard.
