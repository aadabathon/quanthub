# CURRENT_STATE.md

**Update this file whenever meaningful work lands.** Last updated:
**2026-08-13** (v0.1 complete).

## Status

v0.1 foundation is implemented, tested, and verified running (GUI launched
and visually checked). `pip install -e ".[dev,ibkr]"` done against system
Python 3.12.6; `python -m pytest -q` → **68 passed**; `python -m quant_hub`
opens the workstation.

Working end-to-end today:

- Dashboard: symbol/range/source → chart + return stats on synthetic data.
- Markets: watchlist quotes (add/remove/refresh) on synthetic data.
- IBKR: connect/disconnect UI + broker plumbing for daily history and
  snapshot quotes (see "Untested" below).
- Log panel streaming all `logging` output; connection status indicator.
- 7 placeholder views (Options Lab, Volatility, Kalman, GARCH, Markov,
  Portfolio, Research).

## Untested / known limits

- **IBKR has never been tested against a live TWS/Gateway** — no TWS was
  available during development. Code is defensive (timeouts, graceful ERROR
  state, app fully functional without it), but expect a shakedown session:
  first `Connect` click with paper TWS on 127.0.0.1:7497 may surface
  version quirks in the installed `ibapi` 9.81 (PyPI). Official 10.x from
  interactivebrokers.github.io is the upgrade path; both signatures are
  tolerated.
- IBKR history requests always end "now" (D11); durations round up and the
  adapter slices.
- `get_last_price` relies on delayed data (`reqMarketDataType(3)`) when no
  market-data subscriptions exist.
- Stats panel needs ≥ 2 prices; vol/Sharpe are NaN-guarded ("n/a").
- No persistence of any kind (watchlist edits, window size, symbols are all
  session-only). Deliberate for v0.1.

## Version control

**Nothing is committed yet.** The git repo root is the parent `Fin-SW/`
directory; the owner is redoing their GitHub setup and handles commits.
A root `.gitignore` exists. `git status` shows `tradinghub/` untracked.

## Environment

- Windows 11, system Python 3.12.6 (no venv), numpy 2.0.2 / pandas 2.2.3 /
  matplotlib 3.10.0 / pytest 9.0.3 / ibapi 9.81.1.post1.
- Package installed editable as `quant-hub`; console script `quant-hub` also
  works.

## Next-step candidates (owner picks; see ROADMAP.md)

1. Live IBKR shakedown against paper TWS.
2. First model module (GARCH(1,1) or Kalman) — creates `models/`, follows the
   GBM exemplar, math derivation first.
3. Synthetic-data expansion (OU / correlated / regime-switching) to feed
   model validation.
