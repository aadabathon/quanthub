"""SQLite-backed store for per-view UI state (watchlists, form inputs, ...).

Schema (one table, created idempotently on open)
--------------------------------------------------
``view_state``
    One row per view, keyed by an app-chosen name (``"markets"``,
    ``"simulator"``, ...). ``state_json`` is an opaque JSON object — this
    store never looks inside it. That's deliberate: as new tools land, each
    one owns its own state shape without this schema, or any other view's
    row, ever needing to change.

Threading contract
-------------------
Same as ``BarStore``: opened on the main thread, connection created with
``check_same_thread=False``, every public method holds an internal lock.
State is small and read/written from the GUI thread on view show/hide, not
from the task-worker thread, but the lock costs nothing and keeps the
contract identical to the rest of ``storage/``.

Inputs must be JSON-serializable (``str``/``int``/``float``/``bool``/``None``/
``list``/``dict``). The caller owns backward-compatibility of its own shape
across app versions — this store applies no schema to the blob, so a view
reading a state dict should use ``.get()`` with defaults, not direct
indexing.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS view_state (
    view       TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class StateStoreError(Exception):
    """A database-level failure (wraps ``sqlite3.Error``)."""


class ViewStateStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise StateStoreError(f"could not open state store at {db_path}: {exc}") from exc
        _logger.info("view-state store ready: %s", db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def load(self, view: str) -> dict[str, Any]:
        """Saved state for ``view``, or ``{}`` if nothing has been saved yet."""
        with self._lock:
            try:
                row = self._conn.execute(
                    "SELECT state_json FROM view_state WHERE view=?", (view,)
                ).fetchone()
            except sqlite3.Error as exc:
                raise StateStoreError(f"load failed for {view!r}: {exc}") from exc
        if row is None:
            return {}
        try:
            return json.loads(row[0])
        except json.JSONDecodeError as exc:
            _logger.warning("corrupt state for %r, ignoring: %s", view, exc)
            return {}

    def save(self, view: str, state: dict[str, Any]) -> None:
        """Persist ``state`` for ``view``, replacing whatever was there."""
        payload = json.dumps(state)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO view_state (view, state_json, updated_at)"
                        " VALUES (?, ?, ?)"
                        " ON CONFLICT (view) DO UPDATE SET"
                        " state_json=excluded.state_json, updated_at=excluded.updated_at",
                        (view, payload, now),
                    )
            except sqlite3.Error as exc:
                raise StateStoreError(f"save failed for {view!r}: {exc}") from exc
