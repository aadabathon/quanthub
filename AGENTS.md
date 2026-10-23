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
  numpy, pandas, matplotlib, PySide6, pyqtgraph, qtawesome (the Qt GUI,
  D31 — the owner accepts a heavy desktop app), ibapi (optional), pytest.
  Local SQLite persistence via the standard-library `sqlite3` is allowed
  and is the preferred persistence mechanism for historical market data
  (D17). Explicitly banned for now: React/Electron, Flask/FastAPI, external
  database infrastructure (PostgreSQL/MySQL/Redis/Dockerized/cloud DBs),
  Docker, cloud infra, ML frameworks, DI frameworks.

## Hard rules

1. **Read-only with respect to brokerage activity.** No order-placement code
   anywhere, under any flag. This is a deliberate safety posture, not an
   unfinished feature. Do not add reconnect-automatically behavior either.
2. **Layering is enforced by `tests/test_layering.py`** — Qt libraries
   appear only in `gui/` and `app.py` (and tkinter nowhere); `data/`
   imports nothing from other layers and never ibapi; only `broker/` may
   import ibapi; `models/` knows only `data/`; `storage/` knows only `data/`
   and sqlite3; `importers/` and `providers/` (online data, the SEC) know
   only `data/` and the stdlib (pandas too, for importers);
   `visualization/` and `services/` never import the GUI. GUI
   pages may call pure `models/` functions directly (no service wrapper
   needed, D26). If your change fights the layering
   test, your design is wrong, not the test.
3. **Only the GUI thread touches widgets.** Background → GUI crossings go
   through `events.py` (EventBus) or `tasks.py` (TaskRunner). Never call a
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
# from quanthub/  (Windows; the project runs on Python 3.12)
py -3.12 -m pip install -e ".[dev,ibkr]"   # once; [ibkr] optional
py -3.12 -m pytest -q                      # full suite, no GUI/network needed
py -3.12 -m quant_hub                      # launch the app
```

Use the `py -3.12` launcher: bare `python` on this machine resolves to a
3.11 install that has neither pytest nor the project's dependencies.

**GUI work (Qt, D31).** Build pages from `gui/components.py`, style
through `role`/`variant` properties resolved in `gui/theme.py` (no colors
or font sizes in pages), and render charts with the pyqtgraph widgets in
`gui/charts.py`; see ARCHITECTURE.md's GUI conventions. Smoke tests run
headless (`QT_QPA_PLATFORM=offscreen`, see `tests/test_gui_smoke.py`). For
screenshots, build with `quant_hub.app.create_app()`, `show()`, pump
`app.processEvents()`, then `window.grab().save(path)`. That captures the
window itself, whatever is in front of it. pyqtgraph hover only fires with
`mouseRateLimit=0` (set in the theme) and on real mouse-move events sent to
the canvas viewport (`QTest.mouseMove` doesn't reach an inactive window).
In pyqtgraph 0.14, enable `setClipToView`/`setDownsampling` on a curve only
*after* adding it to a plot: an item created with `clipToView=True` crashes
during `addItem` (the scene sees it before its ViewBox does). Screenshot and
driver scripts should point `AppConfig(db_path=...)` at a copy of the store:
pages save UI state on hide. More pyqtgraph traps: `PlotCurveItem` ignores a
plot's log mode (transform x yourself; `PlotDataItem` handles it), and an
`AxisItem.tickStrings` override must route `self.logMode` to
`logTickStrings` itself. Outside a running event loop (tests, scripts),
`deleteLater()` never fires: flush it with
`QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)` or windows
pile up. Scripts outside the project dir need `PYTHONPATH=...\quanthub`
(the editable install is not on every interpreter path).

Wrapped text in a `ScrollPage`'s main column squeezes the cards below it:
the body opts out of height-for-width, so a wrapped label's real height
isn't counted in the page minimum, and the layout takes the difference
from a neighbour (D48). Put long wrapped text in the fixed-width side
panel, or use a table or an elided subtitle. Even in the side panel, a
wrapped `Note` gets the height of a guessed, wider line and can lose its
last line: give it the width it will have (`Note(..., width=...)`) and it
sets its own minimum height (D49). Check a new page's geometry (each
card's height against its `minimumSizeHint`, each table's columns against
its viewport) as well as its pixels. `DataTable.balance_columns` never
shrinks a column below its natural width; pin long text columns with
`fix_column` so they elide.

Qt WebEngine (the 3D surface, D32):
- **Load order.** Import `QtWebEngineWidgets` before the `QApplication`
  exists (`app.py` does, through the page imports).
- **No draws while hidden.** Chromium stalls drawing in a hidden view, so
  `Surface3D` only sends a figure while shown, and its loading cover sits
  over the view instead of hiding it.
- **Offscreen tests.** Offscreen WebGL is reliable only for a process's
  first web view, and even then shader compiles sometimes fail. That's
  why the real-draw test runs in a subprocess and skips on WebGL errors.
- **Screenshots.** `window.grab()` does capture the web view.

## Repo layout note

The project lives at `C:\Users\adams\Projects\quanthub`, which is its own
git repository (the old `tradinghub/` directory name and the parent
`Fin-SW/` arrangement are history). Generated artifacts (`__pycache__/`,
`*.pyc`, local DBs) are `.gitignore`d, untracked, and never project state. Commit only when the owner asks; they manage GitHub
setup.
