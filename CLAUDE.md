# CLAUDE.md — Quant Hub

**Read [AGENTS.md](AGENTS.md) before making changes.** How it works →
[ARCHITECTURE.md](ARCHITECTURE.md).

Non-negotiables, in brief:

- **No orders.** Brokerage access is read-only, and `tests/test_layering.py`
  fails on any order call. Never auto-reconnect.
- **Layering is enforced** by `tests/test_layering.py` — if a change fights
  it, the change is wrong. Only `broker/` imports ibapi; `data/` imports no
  other layer.
- **Only the GUI thread touches widgets**; blocking calls go through
  `tasks.py` (TaskRunner), pushes through `events.py`.
- **The GUI is Qt (D31)**: pages are built from `gui/components.py`, styled
  only via `gui/theme.py`, and chart with `gui/charts.py` (pyqtgraph).
- New quant modules follow the `data/synthetic.py` exemplar: math in the
  docstring, validated inputs, statistical tests. Derive math before coding it.
- Small increments, milestone approval between phases, no new dependencies or
  building ahead without discussion. Explain design/threading/math decisions
  to the maintainer — they maintain every line.

Commands (from the repository root, via the 3.12 launcher): `py -3.12 -m
pytest -q` · `py -3.12 -m quant_hub` (the app; `quant-hub` is the windowed
launcher) · install `py -3.12 -m pip install -e ".[dev,ibkr]"`. Commit
only when asked. Local SQLite (stdlib `sqlite3`, D17) is the approved store
for acquired market data — `~/.quant_hub/market_data.db`; no external DBs.
