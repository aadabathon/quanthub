"""The application shell: sidebar, top bar, page stack, log drawer,
status bar — and THE PUMP.

Threading (D1/D9): one task-worker thread, one event bus, and a single
GUI-thread ``QTimer`` that drains both every 16 ms (one 60 Hz frame), so
background results land within a frame. Draining empty queues costs
microseconds, so the short interval is free.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.core import Core
from quant_hub.gui.pages.base import Commands, Page, Shell
from quant_hub.gui.sidebar import Sidebar
from quant_hub.gui.status_bar import LogDrawer, StatusBar
from quant_hub.gui.top_bar import TopBar

PUMP_INTERVAL_MS = 16

PageFactory = Callable[[Shell], Page]
PageSections = list[tuple[str | None, list[tuple[str, str, PageFactory]]]]
"""[(section title or None, [(page name, icon name, factory), ...]), ...]"""


class MainWindow(QMainWindow):
    def __init__(self, core: Core, sections: PageSections) -> None:
        super().__init__()
        self._core = core
        self.setWindowTitle("Quant Hub")
        self._factories = {name: factory for _, items in sections for name, _, factory in items}
        self._pages: dict[str, Page] = {}
        self._active: str | None = None
        self._commands: dict[str, Commands] = {}
        self._shell = Shell(navigate=self.show_page, publish_commands=self._publish_commands)

        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        main = QHBoxLayout()
        main.setSpacing(0)
        outer.addLayout(main, 1)

        self._sidebar = Sidebar([(title, [(n, i) for n, i, _ in items]) for title, items in sections])
        self._sidebar.navigate.connect(self.show_page)
        main.addWidget(self._sidebar)

        column = QVBoxLayout()
        column.setSpacing(0)
        main.addLayout(column, 1)
        self._top = TopBar(core.broker, core.tasks, core.bus)
        column.addWidget(self._top)
        self._stack = QStackedWidget()
        column.addWidget(self._stack, 1)
        self._drawer = LogDrawer(core.bus)
        self._drawer.setVisible(False)
        column.addWidget(self._drawer)

        self.status = StatusBar(core.bus, core.broker.state)
        self.status.toggle_logs.connect(self._toggle_logs)
        outer.addWidget(self.status)

        shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        shortcut.activated.connect(self._focus_search)
        self._publish_commands("pages", {
            f"Go to {name}": (lambda n=name: self.show_page(n)) for name in self._factories
        })

        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump)
        self._pump_timer.start(PUMP_INTERVAL_MS)
        self.show_page(next(iter(self._factories)))

    # -- navigation -----------------------------------------------------
    def show_page(self, name: str) -> Page:
        """Raise (lazily creating) the named page; returns it."""
        if name not in self._pages:
            page = self._factories[name](self._shell)
            self._stack.addWidget(page)
            self._pages[name] = page
        page = self._pages[name]
        if name != self._active:
            if self._active is not None:
                self._pages[self._active].on_hide()
            self._active = name
            self._stack.setCurrentWidget(page)
            self._sidebar.set_active(name)
            self._top.set_page(name)
            page.on_show()
        return page

    def shutdown(self) -> None:
        """Give the active page its on_hide so it can persist state."""
        self._pump_timer.stop()
        if self._active is not None:
            self._pages[self._active].on_hide()

    # -- shell services -------------------------------------------------
    def _publish_commands(self, owner: str, commands: Commands) -> None:
        self._commands[owner] = commands
        merged: Commands = {}
        for group in self._commands.values():
            merged |= group
        self._top.search.set_commands(merged)

    def _focus_search(self) -> None:
        self._top.search.setFocus()
        self._top.search.selectAll()

    def _toggle_logs(self) -> None:
        is_open = not self._drawer.isVisible()
        self._drawer.setVisible(is_open)
        self.status.set_logs_open(is_open)

    def _pump(self) -> None:
        self._core.bus.dispatch_pending()
        self._core.tasks.process_results()
