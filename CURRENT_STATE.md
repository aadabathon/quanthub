# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-09-27** (Milestone A — Options Lab — complete on synthetic and
source-backed inputs; IBKR load path not yet live-verified).

## Status

v0.4 remains live-verified against TWS 7496. `py -3.12 -m pytest -q` →
**480 passed** (no network needed).

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

## Next steps (agreed 2026-09-27; see ROADMAP.md)

1. ~~Milestone A — Options Lab~~ (done 2026-09-27).
2. **Milestone B — Volatility Lab**: synthetic SVI surface → noisy chain →
   IV inversion → SVI refit (true vs fitted), realized-vol panel.
3. Then the data side: options chains and real portfolio data via IBKR.
