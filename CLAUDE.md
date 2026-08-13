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
- **Only the GUI thread touches Tkinter**; blocking calls go through
  `gui/task_runner.py`, pushes through `events.py`.
- New quant modules follow the `data/synthetic.py` exemplar: math in the
  docstring, validated inputs, statistical tests. Derive math before coding it.
- Small increments, milestone approval between phases, no new dependencies or
  roadmap-jumping without discussion. Explain design/threading/math decisions
  to the owner — they maintain every line.

Commands (from `tradinghub/`): `python -m pytest -q` · `python -m quant_hub` ·
install `pip install -e ".[dev,ibkr]"`. Git root is the parent `Fin-SW/`;
commit only when asked.
