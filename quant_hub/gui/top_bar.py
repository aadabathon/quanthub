"""Top bar: breadcrumb, Ctrl+K command search, IBKR connection control.

Connection state arrives as ``ConnectionEvent``s, dispatched on the GUI
thread by the main window's pump; connect/disconnect are blocking broker
calls, so they go through the TaskRunner (never called from the click
handler directly). State is always dot + text, never color alone.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QSize, QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QFrame, QHBoxLayout, QLabel, QLineEdit

from quant_hub.broker.base import Broker, ConnectionEvent, ConnectionState
from quant_hub.events import EventBus
from quant_hub.tasks import TaskRunner
from quant_hub.gui.components import StatusDot, button, label
from quant_hub.gui.theme import PALETTE, SPACE_L, SPACE_M, SPACE_S, SPACE_XL, icon

_logger = logging.getLogger(__name__)

STATE_INK = {
    ConnectionState.DISCONNECTED: PALETTE.muted,
    ConnectionState.CONNECTING: PALETTE.warning,
    ConnectionState.CONNECTED: PALETTE.good,
    ConnectionState.ERROR: PALETTE.critical,
}

TOP_BAR_HEIGHT = 60


class CommandSearch(QLineEdit):
    """Type-to-filter over named commands (pages, stored symbols); Enter or a
    click runs the chosen one."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("search")
        self.setPlaceholderText("Search pages and stored symbols…")
        self.addAction(icon("ph.magnifying-glass", PALETTE.muted), QLineEdit.LeadingPosition)
        self.setFixedWidth(360)
        self._commands: dict[str, Callable[[], None]] = {}
        self._model = QStringListModel()
        completer = QCompleter(self._model, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.popup().setObjectName("completerPopup")
        completer.activated.connect(self._run)
        self.setCompleter(completer)
        self.returnPressed.connect(self._on_return)

        # Keyboard hint inside the field's right edge.
        hint_row = QHBoxLayout(self)
        hint_row.setContentsMargins(0, 0, SPACE_S, 0)
        hint_row.addStretch(1)
        hint_row.addWidget(label("Ctrl K", "kbd"))
        self.setTextMargins(0, 0, 52, 0)

    def set_commands(self, commands: dict[str, Callable[[], None]]) -> None:
        self._commands = commands
        self._model.setStringList(list(commands))

    def _on_return(self) -> None:
        text = self.text().strip()
        if text in self._commands:
            self._run(text)
            return
        matches = [name for name in self._commands if text.lower() in name.lower()]
        if text and matches:
            self._run(matches[0])

    def _run(self, name: str) -> None:
        action = self._commands.get(name)
        # QCompleter writes its choice into the field *after* emitting
        # activated, so clear on the next event-loop turn, not now.
        QTimer.singleShot(0, self.clear)
        self.clearFocus()
        if action is not None:
            action()


class TopBar(QFrame):
    def __init__(self, broker: Broker, tasks: TaskRunner, bus: EventBus,
                 ibkr_enabled: bool = True) -> None:
        super().__init__()
        self.setObjectName("topBar")
        self.setFixedHeight(TOP_BAR_HEIGHT)
        self._broker = broker
        self._tasks = tasks

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_XL, 0, SPACE_XL, 0)
        layout.setSpacing(SPACE_M)

        crumb_icon = QLabel()
        crumb_icon.setPixmap(icon("ph.squares-four", PALETTE.text_secondary).pixmap(QSize(18, 18)))
        layout.addWidget(crumb_icon)
        layout.addWidget(label("Workstation", "secondary"))
        layout.addWidget(label("/", "muted"))
        self._page = label("", "rowTitle")
        layout.addWidget(self._page)
        layout.addStretch(1)

        self.search = CommandSearch()
        layout.addWidget(self.search)
        layout.addSpacing(SPACE_L)

        self._status = StatusDot()
        layout.addWidget(self._status)
        self._button = button("Connect IBKR", "primary", on_click=self._on_button)
        layout.addWidget(self._button)
        # IBKR turned off in settings (D34): no connection controls at all.
        for widget in (self._status, self._button):
            widget.setVisible(ibkr_enabled)
        self._render(broker.state)
        bus.subscribe(ConnectionEvent, lambda event: self._render(event.state))

    def set_page(self, name: str) -> None:
        self._page.setText(name)

    def _render(self, state: ConnectionState) -> None:
        self._status.set_state(state.value.capitalize(), STATE_INK[state])
        connected = state is ConnectionState.CONNECTED
        self._button.setText("Disconnect" if connected else "Connect IBKR")
        self._button.setEnabled(state is not ConnectionState.CONNECTING)

    def _on_button(self) -> None:
        if self._broker.state is ConnectionState.CONNECTED:
            self._tasks.submit(self._broker.disconnect, on_error=self._log_error)
        else:
            self._button.setEnabled(False)
            self._tasks.submit(self._broker.connect, on_error=self._log_error)

    def _log_error(self, exc: Exception) -> None:
        _logger.error("IBKR: %s", exc)
        self._render(self._broker.state)
