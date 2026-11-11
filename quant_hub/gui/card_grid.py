"""The card grid (D56): every card on a page can be dragged, resized and saved.

A page lays its cards on a grid ``GRID_COLUMNS`` columns wide, in rows
``GRID_ROW`` px tall, with ``SPACE_L`` between cards. A card's place is
(x, y, w, h) in grid units. Drag a card by any part of it that isn't a
control (its title, its padding, a tile's face) and it follows the mouse:
a dashed outline shows where it will land, and the other cards make room
as that changes. Drag the grip in its bottom-right corner to resize it.
Both snap to the grid, and nothing animates, so a card lands at once.

**Never smaller than its content.** A card is at least its minimum size,
and at least its height-for-width when it holds wrapped text. At any width
the grid works out the *shown* layout: each card where it was put, widened
and heightened to its minimum, moved left to fit, and then every card falls
as far up as it can without overlapping one placed before it (in order of
row, then column; the card being dragged goes first). The places the user
chose are kept, so a card widened by a narrow window shrinks back when the
window grows.

**Hidden cards take no room**, so a note that appears pushes the cards
below it down, and one that goes away lets them back up.

**Saving is the page's** (``pages.base.saved_grid``): ``state``/``restore``
carry the layout, and ``changed`` fires after every drop and resize.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QWidget

from quant_hub.gui.theme import GRID_COLUMNS, GRID_ROW, PALETTE, SPACE_L

GRID_VERSION = 1
_GAP = SPACE_L
_GRIP = 14                # px: the resize grip's square
_GRIP_INSET = 3           # px in from the card's corner


@dataclass(frozen=True)
class Place:
    """A card's place in grid units: column, row, width, height."""

    x: int
    y: int
    w: int
    h: int

    def overlaps(self, other: Place) -> bool:
        return (self.x < other.x + other.w and other.x < self.x + self.w
                and self.y < other.y + other.h and other.y < self.y + self.h)


@dataclass
class _Drag:
    name: str
    resizing: bool
    press: QPoint                 # global position of the press
    start: QRect                  # the card's rect at the press
    target: Place | None = None   # where it would land now
    active: bool = False          # past the start distance


class _Grip(QWidget):
    """A card's resize grip: three short diagonal strokes."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFixedSize(_GRIP, _GRIP)
        self.setCursor(Qt.SizeFDiagCursor)
        self.setToolTip("Drag to resize")

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(PALETTE.muted), 1.2))
        for reach in (5, 9, 13):
            painter.drawLine(_GRIP - reach, _GRIP - 1, _GRIP - 1, _GRIP - reach)


class CardGrid(QWidget):
    """Cards on a snapping grid. ``add`` them in reading order with their
    default places; the user moves and resizes them from there."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._order: list[str] = []
        self._cards: dict[str, QWidget] = {}
        self._names: dict[QWidget, str] = {}
        self._grips: dict[QWidget, str] = {}
        self._defaults: dict[str, Place] = {}
        self._places: dict[str, Place] = {}      # where the user put each card
        self._shown: dict[str, Place] = {}       # the shown layout at the current width
        self._height = 0
        self._drag: _Drag | None = None
        self._ghost = QFrame(self)
        self._ghost.setProperty("role", "gridGhost")
        self._ghost.hide()
        self._pending = QTimer(self)
        self._pending.setSingleShot(True)
        self._pending.setInterval(0)
        self._pending.timeout.connect(self._arrange)

    # -- building -----------------------------------------------------------
    def add(self, name: str, widget: QWidget, x: int, y: int, w: int,
            h: int | None = None) -> QWidget:
        """Put ``widget`` at column ``x``, row ``y``, ``w`` columns wide and
        ``h`` rows tall (None: tall enough for its size hint). Returns it."""
        if name in self._cards:
            raise ValueError(f"two cards named {name!r}")
        if not (0 <= x < GRID_COLUMNS and 1 <= w <= GRID_COLUMNS - x and y >= 0):
            raise ValueError(f"{name!r}: ({x}, {y}, {w}) doesn't fit {GRID_COLUMNS} columns")
        hidden = widget.testAttribute(Qt.WA_WState_ExplicitShowHide) and widget.isHidden()
        widget.setParent(self)
        widget.setVisible(not hidden)
        widget.installEventFilter(self)
        if h is None:
            h = _rows(max(widget.sizeHint().height(), widget.minimumSizeHint().height()))
        place = Place(x, y, w, max(1, h))
        self._order.append(name)
        self._cards[name] = widget
        self._names[widget] = name
        self._defaults[name] = place
        self._places[name] = place
        grip = _Grip(self)
        grip.installEventFilter(self)
        self._grips[grip] = name
        self._pending.start()
        return widget

    def add_column(self, cards: list[tuple[str, QWidget, int | None]], x: int, w: int,
                   y: int = 0, min_width: int = 0) -> None:
        """Stack ``(name, widget, rows)`` cards down column ``x`` from row
        ``y``. ``min_width`` keeps each card at least that many px wide (a
        side panel's cards, whose wrapped notes are sized for its width)."""
        for name, widget, h in cards:
            if min_width:
                widget.setMinimumWidth(min_width)
            self.add(name, widget, x, y, w, h)
            y += self._defaults[name].h

    # -- the saved layout ---------------------------------------------------
    def state(self) -> dict:
        return {"version": GRID_VERSION,
                "cards": {name: [p.x, p.y, p.w, p.h] for name, p in self._places.items()}}

    def restore(self, saved: dict) -> None:
        """Take a saved layout's places for the cards this grid has. Cards
        it doesn't know are dropped; cards it doesn't name keep their
        defaults and find room."""
        cards = saved.get("cards") if saved.get("version") == GRID_VERSION else None
        if not isinstance(cards, dict):
            return
        for name, values in cards.items():
            if name in self._places and _valid(values):
                self._places[name] = Place(*values)
        self._pending.start()

    def reset(self) -> None:
        """Back to the page's own layout."""
        self._places = dict(self._defaults)
        self._arrange()
        self.changed.emit()

    def shown_place(self, name: str) -> Place | None:
        """Where a card is shown now (None while it's hidden)."""
        return self._shown.get(name)

    def place(self, name: str) -> Place:
        """Where the user put a card."""
        return self._places[name]

    # -- moving and resizing (the mouse handlers use these too) -------------
    def move_card(self, name: str, x: int, y: int) -> None:
        current = self._shown.get(name, self._places[name])
        self._commit(name, replace(current, x=max(0, min(x, GRID_COLUMNS - current.w)),
                                   y=max(0, y)))

    def resize_card(self, name: str, w: int, h: int) -> None:
        current = self._shown.get(name, self._places[name])
        self._commit(name, replace(current, w=max(1, min(w, GRID_COLUMNS - current.x)),
                                   h=max(1, h)))

    # -- Qt -----------------------------------------------------------------
    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        widest = max((_min_width(card) for card in self._cards.values() if not card.isHidden()),
                     default=0)
        return QSize(widest, self._height)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return self.minimumSizeHint()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._arrange()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        kind = event.type()
        name = self._grips.get(watched) if isinstance(watched, _Grip) else self._names.get(watched)
        if name is None:
            return super().eventFilter(watched, event)
        if kind in (QEvent.Show, QEvent.Hide, QEvent.LayoutRequest) and not isinstance(watched, _Grip):
            self._pending.start()              # shown, hidden, or its minimum may have moved
        elif kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            card = self._cards[name]
            self._drag = _Drag(name, isinstance(watched, _Grip),
                               event.globalPosition().toPoint(), card.geometry())
            return True
        elif kind == QEvent.MouseMove and self._drag is not None:
            self._drag_to(event.globalPosition().toPoint())
            return True
        elif kind == QEvent.MouseButtonRelease and self._drag is not None:
            self._drop()
            return True
        return super().eventFilter(watched, event)

    # -- the drag -------------------------------------------------------------
    def _drag_to(self, point: QPoint) -> None:
        drag = self._drag
        delta = point - drag.press
        card = self._cards[drag.name]
        if not drag.active:
            if delta.manhattanLength() < QApplication.startDragDistance():
                return
            drag.active = True
            QApplication.setOverrideCursor(Qt.SizeFDiagCursor if drag.resizing else Qt.ClosedHandCursor)
            if not drag.resizing:
                self._ghost.show()
            card.raise_()
        cell = self._cell()
        current = self._shown.get(drag.name, self._places[drag.name])
        if drag.resizing:
            w = round((drag.start.width() + delta.x() + _GAP) / (cell + _GAP))
            h = round((drag.start.height() + delta.y() + _GAP) / (GRID_ROW + _GAP))
            target = replace(current, w=max(1, min(w, GRID_COLUMNS - current.x)), h=max(1, h))
        else:
            moved = drag.start.translated(delta)
            left = max(0, min(moved.x(), self.width() - moved.width()))
            card.move(left, max(0, moved.y()))         # follows the mouse, unsnapped
            x = round(left / (cell + _GAP))
            y = round(max(0, moved.y()) / (GRID_ROW + _GAP))
            target = replace(current, x=max(0, min(x, GRID_COLUMNS - current.w)), y=max(0, y))
        if target != drag.target:                       # only re-lay out when the snap changes
            drag.target = target
            self._arrange()

    def _drop(self) -> None:
        drag, self._drag = self._drag, None
        if drag.active:
            QApplication.restoreOverrideCursor()
            self._ghost.hide()
        if drag.active and drag.target is not None:
            self._commit(drag.name, drag.target)
        else:
            self._arrange()

    def _commit(self, name: str, target: Place) -> None:
        """Land a card at ``target``. The cards it pushed keep the rows they
        were pushed to, so the result is the layout the drag showed."""
        shown = self._layout(self.width(), name, target)
        for other, place in shown.items():
            if other == name:
                self._places[other] = place
            else:
                self._places[other] = replace(self._places[other], x=place.x, y=place.y)
        self._arrange()
        self.changed.emit()

    # -- layout -------------------------------------------------------------
    def _cell(self, width: int | None = None) -> float:
        width = self.width() if width is None else width
        return max(1.0, (width - (GRID_COLUMNS - 1) * _GAP) / GRID_COLUMNS)

    def _layout(self, width: int, first: str | None = None,
                first_place: Place | None = None) -> dict[str, Place]:
        """The shown layout at ``width``: minimums met, then each card as
        high as it fits. ``first`` is placed first, at ``first_place``."""
        cell = self._cell(width)
        wanted: list[tuple[str, Place]] = []
        for name in self._order:
            card = self._cards[name]
            if card.isHidden():
                continue
            place = first_place if name == first else self._places[name]
            w = min(GRID_COLUMNS, max(place.w, _cols(_min_width(card), cell)))
            h = max(place.h, _rows(_min_height(card, _span(w, cell))))
            wanted.append((name, Place(min(place.x, GRID_COLUMNS - w), place.y, w, h)))
        wanted.sort(key=lambda item: (item[0] != first, item[1].y, item[1].x))
        placed: dict[str, Place] = {}
        for name, place in wanted:
            if name != first:
                place = replace(place, y=_lowest_free(place, placed.values()))
            placed[name] = place
        return placed

    def _rect(self, place: Place, cell: float) -> QRect:
        return QRect(round(place.x * (cell + _GAP)), place.y * (GRID_ROW + _GAP),
                     round(_span(place.w, cell)), place.h * GRID_ROW + (place.h - 1) * _GAP)

    def _arrange(self) -> None:
        self._pending.stop()
        if self.width() <= 0:
            return
        drag = self._drag
        pinned = (drag.name, drag.target) if drag is not None and drag.target is not None else (None, None)
        self._shown = self._layout(self.width(), *pinned)
        cell = self._cell()
        for grip, name in self._grips.items():
            card = self._cards[name]
            place = self._shown.get(name)
            if place is None:
                grip.hide()
                continue
            rect = self._rect(place, cell)
            following = drag is not None and drag.active and not drag.resizing and drag.name == name
            if following:
                self._ghost.setGeometry(rect)
            else:
                card.setGeometry(rect)
            corner = card.geometry().bottomRight()
            grip.move(corner.x() - _GRIP - _GRIP_INSET + 1, corner.y() - _GRIP - _GRIP_INSET + 1)
            grip.show()
            grip.raise_()
        if drag is not None and drag.active:
            self._ghost.lower()
            self._cards[drag.name].raise_()
        height = max((self._rect(p, cell).bottom() + 1 for p in self._shown.values()), default=0)
        if height != self._height:
            self._height = height
            self.updateGeometry()


def _span(columns: int, cell: float) -> float:
    return columns * cell + (columns - 1) * _GAP


def _cols(px: int, cell: float) -> int:
    return max(1, min(GRID_COLUMNS, math.ceil((px + _GAP) / (cell + _GAP))))


def _rows(px: int) -> int:
    return max(1, math.ceil((px + _GAP) / (GRID_ROW + _GAP)))


def _min_width(card: QWidget) -> int:
    return max(card.minimumSizeHint().width(), card.minimumWidth())


def _min_height(card: QWidget, width: float) -> int:
    height = max(card.minimumSizeHint().height(), card.minimumHeight())
    if card.hasHeightForWidth():
        height = max(height, card.heightForWidth(int(width)))
    return height


def _lowest_free(place: Place, placed) -> int:
    """The highest row (smallest y) at which ``place`` overlaps nothing placed."""
    y = 0
    while True:
        candidate = replace(place, y=y)
        blockers = [other for other in placed if candidate.overlaps(other)]
        if not blockers:
            return y
        y = max(other.y + other.h for other in blockers)


def _valid(values) -> bool:
    if not (isinstance(values, list) and len(values) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in values)):
        return False
    x, y, w, h = values
    return 0 <= x < GRID_COLUMNS and 1 <= w <= GRID_COLUMNS - x and y >= 0 and h >= 1
