"""Typed publish/subscribe event bus bridging background threads to the GUI.

Widgets may only be touched from the GUI (main) thread. Background threads
(the IBKR network thread, the task worker) therefore never call into the GUI
directly; they ``publish()`` immutable event objects onto this bus, and the
GUI drains the bus from a timer pump running on the main thread. This makes
every thread crossing in the application go through exactly one, visible,
mechanism.

Thread contract:
    * ``publish()``      -- safe from any thread.
    * ``subscribe()``    -- GUI thread only, during startup wiring.
    * ``dispatch_pending()`` -- GUI thread only (the pump).

Events are frozen dataclasses keyed by their exact type. ``LogEvent`` is
defined here; broker events live in ``quant_hub.broker.base`` next to the
state enum they carry.
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LogEvent:
    """A log record destined for the GUI log panel."""

    level: str
    message: str
    timestamp: datetime


class EventBus:
    """Minimal thread-safe event bus (queue in, typed dispatch out)."""

    def __init__(self) -> None:
        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        self._subscribers: dict[type, list[Callable[[object], None]]] = {}

    def publish(self, event: object) -> None:
        """Enqueue an event. Safe from any thread; never blocks."""
        self._queue.put(event)

    def subscribe(self, event_type: type, handler: Callable) -> None:
        """Register ``handler`` for events of exactly ``event_type``."""
        self._subscribers.setdefault(event_type, []).append(handler)

    def dispatch_pending(self, max_events: int = 200) -> None:
        """Dispatch queued events to subscribers. GUI thread only.

        ``max_events`` bounds work per pump tick so a burst of events cannot
        starve the GUI event loop.
        """
        for _ in range(max_events):
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                return
            for handler in self._subscribers.get(type(event), []):
                try:
                    handler(event)
                except Exception:
                    _logger.exception(
                        "Event handler failed for %s", type(event).__name__
                    )


class EventBusLogHandler(logging.Handler):
    """Forwards standard ``logging`` records onto the bus as ``LogEvent``s.

    Installing this on the root logger means every module's ordinary
    ``logging`` calls appear in the GUI log panel with no GUI awareness.
    """

    def __init__(self, bus: EventBus, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._bus = bus

    def emit(self, record: logging.LogRecord) -> None:
        self._bus.publish(
            LogEvent(
                level=record.levelname,
                message=self.format(record),
                timestamp=datetime.fromtimestamp(record.created),
            )
        )
