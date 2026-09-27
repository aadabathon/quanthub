# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-09-27** (housekeeping before the Options/Volatility lab milestones:
pure `models/black_scholes.py` with an IV solver, shared
`models/optimize.py` + GARCH optimizer bug fix, snake_case renames,
bytecode untracked, doc paths fixed).

## Status

v0.4 remains live-verified against TWS 7496. `py -3.12 -m pytest -q` →
**410 passed** (no network needed).

Black-Scholes (2026-09-27, D26): `models/black_scholes.py` is the pure
home for European pricing, greeks (D25 trader scaling: `vega`/`rho` per
1-point move, `theta` per calendar day), `no_arbitrage_bounds`, and
`implied_volatility` (bracketed Newton; raises for prices outside the
bounds). The old `services/Options/` package is deleted.
`gui/views/options_lab.py` is still the manual-entry calculator (price +
greeks; no IV input and no options-data source yet).

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

1. **Milestone A — Options Lab** on synthetic inputs: vectorized pricing,
   dividend yield, multi-leg payoff/P&L, greek profiles, Monte Carlo vs
   closed-form validation.
2. **Milestone B — Volatility Lab**: synthetic SVI surface → noisy chain →
   IV inversion → SVI refit (true vs fitted), realized-vol panel.
3. Then the data side: options chains and real portfolio data via IBKR.
