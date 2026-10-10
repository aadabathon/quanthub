"""Bottom status bar (store + IBKR state, latest log line, Logs toggle) and
the log drawer it opens.

Both subscribe to bus events; the main window's pump dispatches them on the
GUI thread, so handlers may touch widgets directly. The drawer starts closed:
the latest message is always visible in the bar, the history on demand.
"""

from __future__ import annotations

import html

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout

from quant_hub.broker.base import ConnectionEvent, ConnectionState
from quant_hub.events import EventBus, LogEvent
from quant_hub.gui.components import StatusDot, divider, label
from quant_hub.gui.theme import PALETTE, SPACE_L, SPACE_M, SPACE_S, icon, mono_font
from quant_hub.gui.top_bar import STATE_INK

_LEVEL_INK = {
    "DEBUG": PALETTE.muted,
    "INFO": PALETTE.text_secondary,
    "WARNING": PALETTE.warning,
    "ERROR": PALETTE.critical,
    "CRITICAL": PALETTE.critical,
}
_MAX_LOG_LINES = 500   # bounded so a long session cannot grow memory
DRAWER_HEIGHT = 200


class StatusBar(QFrame):
    toggle_logs = Signal()

    def __init__(self, bus: EventBus, initial: ConnectionState) -> None:
        super().__init__()
        self.setObjectName("statusBar")
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_L, 0, SPACE_M, 0)
        layout.setSpacing(SPACE_M)

        self._store = StatusDot("Local store", PALETTE.muted)
        layout.addWidget(self._store)
        layout.addWidget(divider(vertical=True))
        self._ibkr = StatusDot()
        layout.addWidget(self._ibkr)
        layout.addSpacing(SPACE_L)
        self._latest = label("", "muted")
        self._latest.setTextFormat(Qt.PlainText)
        layout.addWidget(self._latest, 1)

        self._logs = QPushButton(" Logs")
        self._logs.setProperty("variant", "ghost")
        self._logs.setCursor(Qt.PointingHandCursor)
        self._logs.setLayoutDirection(Qt.RightToLeft)   # icon after text
        self._logs.clicked.connect(lambda _c=False: self.toggle_logs.emit())
        layout.addWidget(self._logs)
        self.set_logs_open(False)

        self._render_ibkr(initial)
        bus.subscribe(ConnectionEvent, lambda event: self._render_ibkr(event.state))
        bus.subscribe(LogEvent, self._on_log)

    def set_store_state(self, text: str, ok: bool) -> None:
        self._store.set_state(text, PALETTE.good if ok else PALETTE.critical)

    def set_logs_open(self, is_open: bool) -> None:
        name = "ph.caret-down" if is_open else "ph.caret-up"
        self._logs.setIcon(icon(name, PALETTE.text_secondary))
        self._logs.setIconSize(QSize(14, 14))

    def _render_ibkr(self, state: ConnectionState) -> None:
        self._ibkr.set_state(f"IBKR {state.value}", STATE_INK[state])

    def _on_log(self, event: LogEvent) -> None:
        self._latest.setText(f"{event.timestamp:%H:%M:%S}  {event.message[:160]}")


class LogDrawer(QFrame):
    def __init__(self, bus: EventBus) -> None:
        super().__init__()
        self.setObjectName("logDrawer")
        self.setFixedHeight(DRAWER_HEIGHT)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_S, SPACE_S, SPACE_S)
        self._text = QPlainTextEdit()
        self._text.setObjectName("log")
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(_MAX_LOG_LINES)
        self._text.setFont(mono_font(12))
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        layout.addWidget(self._text)
        bus.subscribe(LogEvent, self._on_log)

    def _on_log(self, event: LogEvent) -> None:
        ink = _LEVEL_INK.get(event.level, PALETTE.text_secondary)
        self._text.appendHtml(
            f"<span style='color:{PALETTE.muted}'>{event.timestamp:%H:%M:%S}</span>&nbsp;&nbsp;"
            f"<span style='color:{ink}'>{event.level:<8}&nbsp;{html.escape(event.message)}</span>"
        )
