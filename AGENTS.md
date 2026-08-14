# AGENTS.md — working in this repository

Read this first. Companion docs: [ARCHITECTURE.md](ARCHITECTURE.md) (how the
system works), [DECISIONS.md](DECISIONS.md) (why it is this way — do not
relitigate silently), [ROADMAP.md](ROADMAP.md) (what comes next),
[CURRENT_STATE.md](CURRENT_STATE.md) (what is done and what is untested).

## The owner and how to work with them

The owner is an experienced trader with near-graduate-level math and solid
Python. They intend to understand, maintain, and extend **every line** of this
codebase themselves — the standing constraint is:

- This is a long-term project, not a code-generation target. Optimize for
  clean architecture, mathematical correctness, readability, testability, and
  extensibility — never feature count.
- Explain significant design decisions, threading choices, and mathematics
  when introducing them. Derive nontrivial math before implementing it.
- Work in small increments: a few files at a time, milestone-by-milestone,
  with owner approval between milestones. No large refactors without an
  explained justification first.
- No new dependencies without an explanation of why. Currently allowed:
  numpy, pandas, matplotlib, ibapi (optional), pytest. Local SQLite
  persistence via the standard-library `sqlite3` is allowed and is the
  preferred persistence mechanism for historical market data (D17).
  Explicitly banned for now: React/Electron/Qt, Flask/FastAPI, external
  database infrastructure (PostgreSQL/MySQL/Redis/Dockerized/cloud DBs),
  Docker, cloud infra, ML frameworks, DI frameworks.

## Hard rules

1. **Read-only with respect to brokerage activity.** No order-placement code
   anywhere, under any flag. This is a deliberate safety posture, not an
   unfinished feature. Do not add reconnect-automatically behavior either.
2. **Layering is enforced by `tests/test_layering.py`** — `data/` imports
   nothing from other layers and never tkinter/ibapi; only `broker/` may
   import ibapi; `storage/` knows only `data/` and sqlite3; `visualization/`
   and `services/` never import tkinter. If your change fights the layering
   test, your design is wrong, not the test.
3. **Only the GUI thread touches Tkinter.** Background → GUI crossings go
   through `events.py` (EventBus) or `gui/task_runner.py`. Never call a
   blocking service/broker method directly from a widget callback — submit it
   to the TaskRunner.
4. **Quant modules follow the GBM exemplar** (`quant_hub/data/synthetic.py`):
   math documented in the module docstring (model, discretization,
   assumptions, units), validated inputs, no unexplained constants, tests
   including statistical validation against closed forms where possible.
5. **Do not implement roadmap items ahead of schedule.** Placeholders are
   intentional. Build what the current milestone asks and stop.

## Commands

```bash
# from tradinghub/  (Windows, system Python 3.12)
pip install -e ".[dev,ibkr]"    # once; [ibkr] optional
python -m pytest -q             # full suite, no GUI/network needed
python -m quant_hub             # launch the app
```

Smoke-testing GUI changes: build via `quant_hub.app.create_app()` (returns
`(root, main_window)` without entering the mainloop), drive
`main_window.show_view(name)` with `root.after`, then `root.destroy()`.
Two gotchas: scripts outside the project dir need
`PYTHONPATH=...\tradinghub` (the editable install is not on every
interpreter path), and screenshots via `PIL.ImageGrab` capture whatever
window is frontmost — call `root.attributes("-topmost", True)` and
`root.update()` first or you photograph your own terminal.

## Repo layout note

The project lives at `C:\Users\adams\Projects\tradinghub`, which is its own
git repository (the old parent `Fin-SW/` arrangement is history). Generated
artifacts (`__pycache__/`, `*.pyc`, local DBs) are `.gitignore`d and are
never project state. Commit only when the owner asks; they manage GitHub
setup.
