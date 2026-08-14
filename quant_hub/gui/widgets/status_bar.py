"""Header connection cluster: IBKR state indicator + connect/disconnect button.

State changes arrive as ``ConnectionEvent``s on the bus (published by the
broker from its network thread, dispatched here on the GUI thread). The state
is always shown as *color + text* -- never color alone.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

from quant_hub.broker.base import Broker, ConnectionEvent, ConnectionState
from quant_hub.events import EventBus
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import FONT_UI_BOLD, PALETTE

_logger = logging.getLogger(__name__)

STATE_COLORS = {
    ConnectionState.DISCONNECTED: PALETTE.muted,
    ConnectionState.CONNECTING: PALETTE.warning,
    ConnectionState.CONNECTED: PALETTE.good,
    ConnectionState.ERROR: PALETTE.critical,
}


class ConnectionBar(ttk.Frame):
    def __init__(
        self, parent: tk.Misc, broker: Broker, tasks: TaskRunner, bus: EventBus
    ) -> None:
        super().__init__(parent, style="Page.TFrame")
        self._broker = broker
        self._tasks = tasks

        self._indicator = tk.Label(
            self,
            text="●",  # ●
            background=PALETTE.page,
            foreground=STATE_COLORS[ConnectionState.DISCONNECTED],
            font=FONT_UI_BOLD,
        )
        self._label = ttk.Label(self, text="IBKR: DISCONNECTED", style="Header.TLabel")
        self._button = ttk.Button(
            self, text="Connect", style="Accent.TButton", command=self._on_button
        )

        self._indicator.pack(side="left")
        self._label.pack(side="left", padx=(4, 12))
        self._button.pack(side="left")

        bus.subscribe(ConnectionEvent, self._on_connection_event)

    def _on_button(self) -> None:
        state = self._broker.state
        if state is ConnectionState.CONNECTED:
            self._tasks.submit(self._broker.disconnect, on_error=self._log_error)
        else:
            self._button.configure(state="disabled")
            self._tasks.submit(self._broker.connect, on_error=self._log_error)

    @staticmethod
    def _log_error(exc: Exception) -> None:
        _logger.error("IBKR: %s", exc)

    def _on_connection_event(self, event: ConnectionEvent) -> None:
        self._indicator.configure(foreground=STATE_COLORS[event.state])
        self._label.configure(text=f"IBKR: {event.state.value.upper()}")
        connected = event.state is ConnectionState.CONNECTED
        connecting = event.state is ConnectionState.CONNECTING
        self._button.configure(
            text="Disconnect" if connected else "Connect",
            state="disabled" if connecting else "normal",
        )
