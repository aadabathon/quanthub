"""Background execution for the GUI, with results delivered on the GUI thread.

The GUI must never block (services can wait tens of seconds on IBKR) and
worker threads must never touch widgets. ``TaskRunner`` is the single bridge:

    task_runner.submit(
        lambda: market_service.get_history("SPY", start, end, source="ibkr",
                                           basis=PriceBasis.TRADED),
        on_done=self._show_history,    # called on the GUI thread
        on_error=self._show_error,     # called on the GUI thread
    )

``submit()`` may be called from the GUI thread at any time; the callable runs
on one background worker thread; the callback is queued and invoked by
``process_results()``, which the main window's pump (a GUI-thread timer)
calls. One worker (not a pool) is deliberate (D9): requests are
user-initiated and serial, and a single worker gives FIFO ordering for free.

Deliberately toolkit-free -- plain threads and queues, fully unit-testable,
and outside the ``gui`` package so the composition root and tests can use it
without importing Qt.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Job:
    fn: Callable[[], Any]
    on_done: Callable[[Any], None] | None
    on_error: Callable[[Exception], None] | None


_SHUTDOWN = object()


class TaskRunner:
    def __init__(self) -> None:
        self._jobs: queue.SimpleQueue = queue.SimpleQueue()
        self._results: queue.SimpleQueue = queue.SimpleQueue()
        self._worker = threading.Thread(
            target=self._work_loop, name="task-worker", daemon=True
        )
        self._worker.start()

    def submit(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Run ``fn`` on the worker; deliver its result/exception to the GUI."""
        self._jobs.put(_Job(fn=fn, on_done=on_done, on_error=on_error))

    def process_results(self, max_items: int = 20) -> None:
        """Invoke pending callbacks. Must be called from the GUI thread."""
        for _ in range(max_items):
            try:
                callback, payload = self._results.get_nowait()
            except queue.Empty:
                return
            if callback is None:
                if isinstance(payload, Exception):
                    _logger.error("Unhandled task error", exc_info=payload)
                continue
            try:
                callback(payload)
            except Exception:
                _logger.exception("Task callback failed")

    def shutdown(self, timeout: float = 2.0) -> None:
        """Stop the worker. Queued-but-unstarted jobs are dropped on the floor
        by process exit; the worker is a daemon thread precisely so a hung
        broker call cannot prevent application shutdown."""
        self._jobs.put(_SHUTDOWN)
        self._worker.join(timeout=timeout)

    # ------------------------------------------------------- worker thread
    def _work_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is _SHUTDOWN:
                return
            try:
                result = job.fn()
            except Exception as exc:
                self._results.put((job.on_error, exc))
            else:
                self._results.put((job.on_done, result))
