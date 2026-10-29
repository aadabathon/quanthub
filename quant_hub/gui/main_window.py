"""The application shell: sidebar, top bar, the page workspace, log drawer,
status bar — and THE PUMP.

Threading (D1/D9): one task-worker thread, one event bus, and a single
GUI-thread ``QTimer`` that drains both every 16 ms (one 60 Hz frame), so
background results land within a frame. Draining empty queues costs
microseconds, so the short interval is free.

Pages live in docks (``workspace.py``, D55). The layout and the window's
size and place are saved in the view-state store at shutdown and restored
at launch; Ctrl+K's "Reset layout" goes back to the default.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QKeySequence, QScreen, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMainWindow, QVBoxLayout, QWidget

from quant_hub.core import Core
from quant_hub.gui.pages.base import Commands, Page, Shell
from quant_hub.gui.sidebar import Sidebar
from quant_hub.gui.status_bar import LogDrawer, StatusBar
from quant_hub.gui.top_bar import TopBar
from quant_hub.gui.workspace import Workspace

PUMP_INTERVAL_MS = 16
LAYOUT_KEY = "workspace"
# Pages that can't float (D55): none. Floating the Volatility Lab and docking
# it back keeps its 3D surface drawing and doesn't recreate the main window
# (tests/test_gui_smoke.py checks it in a fresh process).
FIXED_PAGES: frozenset[str] = frozenset()

PageFactory = Callable[[Shell], Page]
PageSections = list[tuple[str | None, list[tuple[str, str, PageFactory]]]]
"""[(section title or None, [(page name, icon name, factory), ...]), ...]"""


class MainWindow(QMainWindow):
    def __init__(self, core: Core, sections: PageSections) -> None:
        super().__init__()
        self._core = core
        self.setWindowTitle("Quant Hub")
        self._factories = {name: factory for _, items in sections for name, _, factory in items}
        self._first = next(iter(self._factories))
        self._pages: dict[str, Page] = {}
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
        self._top = TopBar(core.broker, core.tasks, core.bus, core.config.ibkr_enabled)
        column.addWidget(self._top)
        icons = {name: icon_name for _, items in sections for name, icon_name, _ in items}
        self._workspace = Workspace(icons, self._build_page, FIXED_PAGES)
        self._workspace.active_changed.connect(self._on_active)
        column.addWidget(self._workspace, 1)
        self._drawer = LogDrawer(core.bus)
        self._drawer.setVisible(False)
        column.addWidget(self._drawer)

        self.status = StatusBar(core.bus, core.broker.state, core.config.ibkr_enabled)
        self.status.toggle_logs.connect(self._toggle_logs)
        outer.addWidget(self.status)

        shortcut = QShortcut(QKeySequence("Ctrl+K"), self)
        shortcut.activated.connect(self._focus_search)
        self._publish_commands("pages", {
            **{f"Go to {name}": (lambda n=name: self.show_page(n)) for name in self._factories},
            "Reset layout": self.reset_layout,
        })

        self._pump_timer = QTimer(self)
        self._pump_timer.timeout.connect(self._pump)
        self._pump_timer.start(PUMP_INTERVAL_MS)
        self._saved_layout = core.state_store.load(LAYOUT_KEY)
        if not self._workspace.restore_layout(self._saved_layout):
            self.show_page(self._first)

    # -- navigation -----------------------------------------------------
    @property
    def _active(self) -> str | None:
        """The page last brought forward, focused, or picked from a tab."""
        return self._workspace.active

    def prepare_page(self, name: str) -> Page:
        """Build and attach a page without showing it or running on_show.

        The composition root prepares the WebEngine page before the native
        window is first shown (D40).
        """
        self._workspace.prepare(name)
        return self._pages[name]

    def show_page(self, name: str) -> Page:
        """Bring the named page forward (building it on first use; a closed
        page opens as a tab of the active one); returns it."""
        self._workspace.open_page(name)
        return self._pages[name]

    def reset_layout(self) -> None:
        """The first page alone, and the window at its default size."""
        self._workspace.reset(self._first)
        self.apply_default_size(self.screen())

    def _build_page(self, name: str) -> Page:
        page = self._factories[name](self._shell)
        self._pages[name] = page
        return page

    def _on_active(self, name: str) -> None:
        self._sidebar.set_active(name)
        self._top.set_page(name)

    # -- the window -----------------------------------------------------
    def restore_geometry(self) -> bool:
        """The saved size and place, if any. Qt moves a window saved on a
        screen that's gone back onto one that's there."""
        text = self._saved_layout.get("geometry")
        return isinstance(text, str) and self.restoreGeometry(
            QByteArray.fromBase64(text.encode("ascii")))

    def apply_default_size(self, screen: QScreen) -> None:
        """Most of the screen (a workstation), capped so very large monitors
        still get a window rather than a wall, and centered."""
        available = screen.availableGeometry()
        self.resize(min(1920, int(available.width() * 0.88)),
                    min(1240, int(available.height() * 0.88)))
        self.move(available.center() - self.rect().center())

    def shutdown(self) -> None:
        """Every visible page gets on_hide so it can persist state; then the
        layout and the window's geometry are saved."""
        self._pump_timer.stop()
        self._workspace.close_pages()
        layout = self._workspace.layout_state()
        layout["geometry"] = self.saveGeometry().toBase64().data().decode("ascii")
        self._core.state_store.save(LAYOUT_KEY, layout)

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
