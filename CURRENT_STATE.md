# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-09-20** (GARCH domain foundation + interactive GARCH Lab complete;
not yet live-verified against TWS).

## Status

v0.4 remains live-verified against TWS 7496. `py -3.12 -m pytest -q` →
**265 passed** (no network needed).

Options Lab (uncommitted, 2026-08-19): `services/Options/options_service.py`
prices European options under Black-Scholes and reports greeks —
`delta`/`gamma` as raw derivatives, `vega`/`rho` per 1-percentage-point
move, `theta` per calendar day (D25; convention documented in the module
docstring). `gui/views/Options_lab.py` is a manual-entry calculator (no
options-data source wired in) under the MODELS nav section.
GARCH model foundation (uncommitted, 2026-09-20): `models/garch.py` provides
stationary GARCH(1,1) simulation, conditional-variance evaluation, and a
NumPy-only Gaussian-QMLE fit with constrained parameters. It is deliberately
pure and source-agnostic. GARCH Lab now exposes synthetic simulation and
explicit market-history fitting through the existing SQLite-first `ibkr` cache.


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

Repo at `C:\Users\adams\Projects\tradinghub` (one `init` commit; M3+M4+M6
uncommitted; owner commits; `__pycache__` still tracked by the old commit —
untracking is the owner's call). Windows 11, Python 3.12.6, ibapi
9.81.1.post1; TWS live on 127.0.0.1:7496, Read-Only API enabled. Store:
`~/.quant_hub/market_data.db`.

## Next-step candidates (owner picks; see ROADMAP.md)

1. Kalman state-space model/lab — the second concrete model can now inform
   any shared model conventions.
2. Historical-research surface (chart persisted series; the deferred
   Markets detail pane) + SettingsStore for UI preferences.
3. Synthetic-data expansion (OU / correlated / regime-switching).
4. Pinned historical end-times (lifts D11).
