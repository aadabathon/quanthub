# CLAUDE.md — Quant Hub

**Read [AGENTS.md](AGENTS.md) before making changes.** Docs map: how it works →
[ARCHITECTURE.md](ARCHITECTURE.md) · why → [DECISIONS.md](DECISIONS.md) ·
what's next → [ROADMAP.md](ROADMAP.md) · what's done/untested →
[CURRENT_STATE.md](CURRENT_STATE.md) (keep it updated as work lands).

Non-negotiables, in brief:

- **Read-only brokerage.** No order-placement code, ever, under any flag.
- **Layering is enforced** by `tests/test_layering.py` — if a change fights
  it, the change is wrong. Only `broker/` imports ibapi; `data/` imports no
  other layer.
- **Only the GUI thread touches widgets**; blocking calls go through
  `gui/task_runner.py`, pushes through `events.py`.
- **The GUI is moving to Qt (D31)**: new UI work goes in `gui_qt/`, built
  from `gui_qt/components.py` and styled only via `gui_qt/theme.py`.
- New quant modules follow the `data/synthetic.py` exemplar: math in the
  docstring, validated inputs, statistical tests. Derive math before coding it.
- Small increments, milestone approval between phases, no new dependencies or
  roadmap-jumping without discussion. Explain design/threading/math decisions
  to the owner — they maintain every line.

Commands (from `quanthub/`, via the 3.12 launcher — bare `python` is a 3.11
without pytest): `py -3.12 -m pytest -q` · `py -3.12 -m quant_hub` (Qt UI; `--classic`
for the old Tk UI) · install
`py -3.12 -m pip install -e ".[dev,ibkr]"`. The project directory
(`C:\Users\adams\Projects\quanthub`) is its own git repository; commit
only when asked. Local SQLite (stdlib `sqlite3`, D17) is the approved store
for acquired market data — `~/.quant_hub/market_data.db`; no external DBs.
