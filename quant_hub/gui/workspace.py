"""The page workspace (G1, D55): every page is one dock.

Pages can sit side by side, tab together or float onto another monitor,
and the arrangement is saved. Cards stay inside their page: a page reads
top to bottom as one argument, and one job fills it (D55).

The workspace is a ``QMainWindow`` nested in the shell, used only for its
dock areas. Its central widget stays hidden, so the docks fill it. Drops
aren't animated, so a page lands where it's dropped at once.

**Shown.** A page is shown when it can be seen: its dock is open, and it
floats or is the front tab of its group. Qt doesn't hide a group's back
tabs, it parks them outside the workspace, and its ``visibilityChanged``
misfires around floating. So the workspace works visibility out itself:
any show, hide, move or resize of a dock schedules a *settle* one
event-loop turn later, which calls ``on_show``/``on_hide`` for the pages
whose state changed. A drag hides and shows a dock more than once, and by
the settle it is back where it was, so a re-dock doesn't refresh a page
twice. While a mouse button is down (a drag, a splitter, a tab being
pressed) the settle waits for its release, so a drag doesn't refresh the
pages it passes over. ``open_page`` settles at once, so a page's
``on_show`` has run when it returns (once the window is shown; before that
nothing is visible).

**Floating.** The float button and a double-click on the title bar float
a page down and right of where it was, a little smaller, so it reads as a
panel of its own (dragging it out places it under the mouse instead). The
dock is hidden while Qt rebuilds it as a window and shown once placed;
shown at once, the new window paints a frame of nothing first. Toggles
closer together than ``_TOGGLE_GAP_S`` are ignored.

**Lazy.** A dock is made empty, and its page is built the first time the
dock is shown (or when ``prepare`` asks), so a restored layout doesn't
build every page at launch.

**Fit.** Qt grows the window when a split needs more width than the window
has, even past the screen. When pages side by side need more than the
screen's width, the page dropped last joins the active page's tabs. If no
page was dropped (a layout restored onto a smaller screen), every docked
page joins one group.

**Saved layout.** ``layout_state``/``restore_layout`` carry the pages that
have docks, Qt's ``saveState`` (base64), the active page and a version. A
page that no longer exists is dropped. A state Qt rejects, another
version, or a layout with no open page falls back to the default: the
first page alone, each page opened later tabbed beside it. A floating page
restored off every screen docks again.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QByteArray, QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget

from quant_hub.gui.components import DockTitleBar, set_state
from quant_hub.gui.pages.base import Page

_logger = logging.getLogger(__name__)

LAYOUT_VERSION = 1
# Docks start in one area. Nesting lets a page split any other, so the area
# only orders them; the hidden central widget leaves the docks all the room.
_AREA = Qt.LeftDockWidgetArea
_SETTLE_EVENTS = (QEvent.Show, QEvent.Hide, QEvent.Move, QEvent.Resize)
_HELD_RETRY_MS = 50       # how often a settle checks whether the mouse is released
_FLOAT_OFFSET = 48        # px down and right of the docked place: a floated page lands here
_FLOAT_SCALE = 0.8        # of the docked size, so the app shows around it
_TOGGLE_GAP_S = 0.35      # float toggles closer together than this are ignored


class Workspace(QMainWindow):
    """The docks. ``active_changed`` names the page that was last brought
    forward, focused, or picked from a tab strip."""

    active_changed = Signal(str)

    def __init__(self, icons: dict[str, str], build: Callable[[str], Page],
                 fixed: frozenset[str] = frozenset()) -> None:
        """``icons`` names every page, in sidebar order, with its icon.
        ``build(name)`` makes a page. Pages in ``fixed`` can't float."""
        super().__init__()
        self.setWindowFlags(Qt.Widget)          # a child of the shell, not a window
        self.setObjectName("workspace")
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks)
        # Its menu of every dock would show pages outside open_page's rules.
        self.setContextMenuPolicy(Qt.PreventContextMenu)
        central = QWidget()
        self.setCentralWidget(central)
        central.hide()
        self._icons = icons
        self._build = build
        self._fixed = fixed
        self._docks: dict[str, QDockWidget] = {}
        self._shown: set[str] = set()
        self._active: str | None = None
        self._recent: list[str] = []         # pages by when they were last active, newest first
        self._dropped: str | None = None     # the page a drag docked last, for the fit check
        self._settling = False
        self._closed = False
        self._last_toggle = 0.0
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.setInterval(0)
        self._settle_timer.timeout.connect(self._settle)
        self.tabifiedDockWidgetActivated.connect(lambda dock: self._set_active(dock.objectName()))
        QApplication.instance().focusChanged.connect(self._on_focus)

    # -- pages ------------------------------------------------------------
    @property
    def active(self) -> str | None:
        return self._active

    def shown(self) -> list[str]:
        """The pages that can be seen now, in sidebar order."""
        return [name for name in self._icons if name in self._shown]

    def prepare(self, name: str) -> None:
        """Make a page's dock and build the page without showing it. The
        Volatility Lab's browser must join the window before its first show
        (D40). A new dock stays closed."""
        dock = self._dock(name)
        if self.dockWidgetArea(dock) == Qt.NoDockWidgetArea and not dock.isFloating():
            self.addDockWidget(_AREA, dock)      # a child of the window; still closed
        self._ensure_page(name)

    def open_page(self, name: str) -> None:
        """Bring a page to the front. A closed page opens tabbed with the
        active page. Settles, so the page's on_show has run."""
        dock = self._dock(name)
        self._ensure_page(name)
        if dock.isHidden():
            self._place(dock)
            dock.show()
        dock.raise_()
        self._set_active(name)
        self._settle(force=True)

    def toggle_floating(self, name: str) -> None:
        """Float a page down and right of where it was, or dock it back."""
        now = time.monotonic()
        if now - self._last_toggle < _TOGGLE_GAP_S:
            return
        self._last_toggle = now
        dock = self._docks[name]
        if not dock.features() & QDockWidget.DockWidgetFloatable:
            return
        if dock.isFloating():
            dock.hide()
            dock.setFloating(False)              # back to where it was docked
            dock.show()
            dock.raise_()                        # in front of its tab group (hidden, it couldn't be)
            self._set_active(name)
            return
        target = QRect(dock.mapToGlobal(QPoint(_FLOAT_OFFSET, _FLOAT_OFFSET)),
                       QSize(int(dock.width() * _FLOAT_SCALE), int(dock.height() * _FLOAT_SCALE)))
        dock.hide()
        dock.setFloating(True)
        dock.setGeometry(_onto_screen(target, dock.screen().availableGeometry()))
        dock.show()
        self._set_active(name)

    def reset(self, first: str) -> None:
        """The default layout: ``first`` alone. Pages opened later join it
        as tabs."""
        for dock in self._docks.values():
            if dock.isFloating():
                dock.setFloating(False)
            self.removeDockWidget(dock)          # also hides it
        self._dropped = None
        self.open_page(first)

    def close_pages(self) -> None:
        """At shutdown: every shown page gets on_hide, so each saves its
        state, and nothing settles after."""
        self._closed = True
        self._settle_timer.stop()
        for name in self.shown():
            self._shown.discard(name)
            try:
                self._docks[name].widget().on_hide()
            except Exception:
                _logger.exception("error saving %s's state at shutdown", name)

    # -- the saved layout ---------------------------------------------------
    def layout_state(self) -> dict:
        return {"version": LAYOUT_VERSION, "pages": list(self._docks), "active": self._active,
                "state": self.saveState(LAYOUT_VERSION).toBase64().data().decode("ascii")}

    def restore_layout(self, saved: dict) -> bool:
        """Rebuild a saved layout's docks, empty (each page builds when it's
        first shown), and restore Qt's state. False means use the default."""
        pages, state = saved.get("pages"), saved.get("state")
        if (saved.get("version") != LAYOUT_VERSION or not isinstance(pages, list)
                or not isinstance(state, str)):
            return False
        names = [name for name in pages if name in self._icons]    # gone pages drop out
        for name in names:
            self.addDockWidget(_AREA, self._dock(name))
        restored = self.restoreState(QByteArray.fromBase64(state.encode("ascii")),
                                     LAYOUT_VERSION)
        open_names = [name for name in names if not self._docks[name].isHidden()]
        if not restored or not open_names:
            for name in names:
                dock = self._docks.pop(name)
                self.removeDockWidget(dock)
                dock.deleteLater()
            return False
        for name in open_names:
            dock = self._docks[name]
            if dock.isFloating() and (name in self._fixed or QGuiApplication.screenAt(
                    dock.geometry().center()) is None):
                dock.setFloating(False)          # a missing monitor, or no longer floatable
        active = saved.get("active")
        self._set_active(active if active in open_names else open_names[0])
        self._dropped = None
        return True

    # -- docks --------------------------------------------------------------
    def _dock(self, name: str) -> QDockWidget:
        dock = self._docks.get(name)
        if dock is None:
            dock = QDockWidget(name, self)
            dock.setObjectName(name)             # saveState knows docks by objectName
            features = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable
            if name not in self._fixed:
                features |= QDockWidget.DockWidgetFloatable
            dock.setFeatures(features)
            title = DockTitleBar(dock, self._icons[name])
            title.float_toggled.connect(lambda n=name: self.toggle_floating(n))
            dock.setTitleBarWidget(title)
            dock.topLevelChanged.connect(lambda floating, n=name: self._on_top_level(n, floating))
            dock.topLevelChanged.connect(lambda floating, d=dock: set_state(d, "floating", floating))
            # Hidden until placed. A new child isn't hidden yet, just not
            # shown, so "isHidden" would mistake it for an open dock.
            dock.hide()
            dock.installEventFilter(self)
            self._docks[name] = dock
        return dock

    def _ensure_page(self, name: str) -> None:
        dock = self._docks[name]
        if dock.widget() is None:
            dock.setWidget(self._build(name))

    def _place(self, dock: QDockWidget) -> None:
        """A closed dock opens as a tab of the active page, or alone when no
        page is docked."""
        anchor = self._anchor(exclude=dock)
        if anchor is None:
            self.addDockWidget(_AREA, dock)
        else:
            self.tabifyDockWidget(anchor, dock)

    def _anchor(self, exclude: QDockWidget | None = None) -> QDockWidget | None:
        """The docked, open page most recently active."""
        for name in self._recent + [n for n in self._docks if n not in self._recent]:
            dock = self._docks.get(name)
            if (dock is not None and dock is not exclude and not dock.isHidden()
                    and not dock.isFloating()
                    and self.dockWidgetArea(dock) != Qt.NoDockWidgetArea):
                return dock
        return None

    # -- visibility ---------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        if (not self._closed and event.type() in _SETTLE_EVENTS
                and isinstance(watched, QDockWidget)):
            self._settle_timer.start(0)
        return super().eventFilter(watched, event)

    def _on_top_level(self, name: str, floating: bool) -> None:
        if not floating:
            # Docked again: Qt files it back into its old tab group, behind
            # whichever page is in front. The page just placed comes first.
            self._dropped = name
            self._docks[name].raise_()
        if not self._closed:
            self._settle_timer.start(0)

    @staticmethod
    def _can_be_seen(dock: QDockWidget) -> bool:
        if not dock.isVisible():                 # closed, or the window isn't shown
            return False
        # A back tab is parked outside the workspace, so nothing of it shows.
        return dock.isFloating() or not dock.visibleRegion().isEmpty()

    def _settle(self, force: bool = False) -> None:
        if self._closed:
            return
        if self._settling:                       # a page's on_show navigated
            self._settle_timer.start(0)
            return
        if not force and QGuiApplication.mouseButtons() != Qt.NoButton:
            self._settle_timer.start(_HELD_RETRY_MS)    # mid-drag: wait for the release
            return
        self._settle_timer.stop()
        self._settling = True
        try:
            self.layout().activate()             # place the docks (and park back tabs) first
            for name, dock in list(self._docks.items()):
                shown = self._can_be_seen(dock)
                if shown and dock.widget() is None:
                    dock.setWidget(self._build(name))
                if shown == (name in self._shown):
                    continue
                if shown:
                    self._shown.add(name)
                    dock.widget().on_show()
                else:
                    self._shown.discard(name)
                    dock.widget().on_hide()
            self._fit_screen()
            if self._active not in self._shown:
                fallback = next((n for n in self._recent + self.shown() if n in self._shown), None)
                if fallback is not None:
                    self._set_active(fallback)
            only_one = sum(not dock.isHidden() for dock in self._docks.values()) <= 1
            for dock in self._docks.values():
                dock.titleBarWidget().close_button.setVisible(not only_one)
        finally:
            self._settling = False

    def _set_active(self, name: str) -> None:
        if name in self._recent:
            self._recent.remove(name)
        self._recent.insert(0, name)
        if name != self._active:
            self._active = name
            self.active_changed.emit(name)

    def _on_focus(self, _old: QWidget | None, now: QWidget | None) -> None:
        widget = now
        while widget is not None:
            if isinstance(widget, QDockWidget) and self._docks.get(widget.objectName()) is widget:
                self._set_active(widget.objectName())
                return
            widget = widget.parentWidget()

    # -- fit ------------------------------------------------------------------
    def _room(self) -> int:
        """The width the window may take: its screen's."""
        screen = self.window().screen()
        return screen.availableGeometry().width() if screen is not None else 1 << 30

    def _fit_screen(self) -> None:
        window = self.window()
        docked = [dock for dock in self._docks.values()
                  if not dock.isHidden() and not dock.isFloating()]
        if not window.isVisible() or len(docked) < 2:
            return
        group = {docked[0], *self.tabifiedDockWidgets(docked[0])}
        if all(dock in group for dock in docked):            # one tab group: nothing side by side
            return
        room = self._room()
        if window.minimumSizeHint().width() <= room:
            return
        dropped = self._docks.get(self._dropped) if self._dropped else None
        anchor = self._anchor(exclude=dropped)
        if dropped is not None and dropped in docked and anchor is not None:
            self.tabifyDockWidget(anchor, dropped)
            dropped.raise_()
        else:
            anchor = anchor or docked[0]
            for dock in docked:
                if dock is not anchor:
                    self.tabifyDockWidget(anchor, dock)
        self._dropped = None
        if window.width() > room:
            window.resize(room, window.height())


def _onto_screen(rect: QRect, screen: QRect) -> QRect:
    """``rect`` moved (not resized) so it lies on ``screen`` where it can."""
    moved = QRect(rect)
    if moved.right() > screen.right():
        moved.moveRight(screen.right())
    if moved.bottom() > screen.bottom():
        moved.moveBottom(screen.bottom())
    if moved.left() < screen.left():
        moved.moveLeft(screen.left())
    if moved.top() < screen.top():
        moved.moveTop(screen.top())
    return moved
