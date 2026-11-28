"""Navigation rail: brand mark, sectioned page buttons with icons (scrolling
when the window is shorter than the list), version."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from quant_hub import __version__
from quant_hub.gui.components import label
from quant_hub.gui.theme import PALETTE, SPACE_L, SPACE_M, SPACE_S, SPACE_XS, icon

NavSections = list[tuple[str | None, list[tuple[str, str]]]]
"""[(section title or None, [(page name, icon name), ...]), ...]"""

SIDEBAR_WIDTH = 232


class LogoMark(QWidget):
    """The "Q" mark: a gradient ring with a tail, painted (no image asset)."""

    def __init__(self, size: int = 34) -> None:
        super().__init__()
        self.setFixedSize(size, size)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = self.width()
        stroke = side * 0.17
        gradient = QLinearGradient(QPointF(0, 0), QPointF(side, side))
        gradient.setColorAt(0.0, PALETTE.accent_hover)
        gradient.setColorAt(1.0, PALETTE.accent)
        pen = QPen(gradient, stroke)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        inset = stroke / 2 + 1
        ring = QRectF(inset, inset, side - 2 * inset, side - 2 * inset)
        # Qt angles are 1/16 degree, counter-clockwise from 3 o'clock; leave a
        # gap at the lower right where the tail exits.
        painter.drawArc(ring, -20 * 16, -300 * 16)
        painter.drawLine(QPointF(side * 0.58, side * 0.58), QPointF(side - inset, side - inset))


class Sidebar(QFrame):
    navigate = Signal(str)

    def __init__(self, sections: NavSections) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(SIDEBAR_WIDTH)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_M, SPACE_L + 2, SPACE_M, SPACE_M)
        layout.setSpacing(SPACE_XS)

        brand = QHBoxLayout()
        brand.setContentsMargins(SPACE_S, 0, 0, 0)
        brand.setSpacing(SPACE_M)
        brand.addWidget(LogoMark())
        brand.addWidget(label("QuantHub", "brand"))
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(SPACE_L + 6)

        # The page buttons scroll between the brand and the version line.
        # The window may be shorter than the list (its minimum height is
        # fixed, not the sum of the sidebar's), and a plain layout would
        # then squeeze every button until its label is cut in half.
        self._scroll = QScrollArea()
        self._scroll.setObjectName("navScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav = QWidget()
        nav.setObjectName("navList")
        entries = QVBoxLayout(nav)
        entries.setContentsMargins(0, 0, 0, 0)
        entries.setSpacing(SPACE_XS)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for index, (title, items) in enumerate(sections):
            if index > 0:
                entries.addSpacing(SPACE_L)
            if title:
                caption = label(title.upper(), "section")
                caption.setContentsMargins(SPACE_M, 0, 0, SPACE_XS)
                entries.addWidget(caption)
            for name, icon_name in items:
                btn = QPushButton(f"  {name}")
                btn.setProperty("nav", True)
                btn.setCheckable(True)
                btn.setCursor(Qt.PointingHandCursor)
                btn.setIcon(icon(icon_name, PALETTE.text_secondary, on=PALETTE.series))
                btn.setIconSize(QSize(20, 20))
                btn.clicked.connect(lambda _c=False, n=name: self.navigate.emit(n))
                self._group.addButton(btn)
                self._buttons[name] = btn
                entries.addWidget(btn)
        entries.addStretch(1)
        self._scroll.setWidget(nav)
        layout.addWidget(self._scroll, 1)

        version = label(f"Quant Hub v{__version__} · read-only", "muted")
        version.setContentsMargins(SPACE_M, 0, 0, 0)
        layout.addWidget(version)

    def set_active(self, name: str) -> None:
        button = self._buttons[name]
        button.setChecked(True)
        # Ctrl+K can open a page whose button is scrolled out of sight.
        self._scroll.ensureWidgetVisible(button, 0, SPACE_S)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        # The page restored at startup was set active before there was any
        # geometry to scroll by.
        checked = self._group.checkedButton()
        if checked is not None:
            self._scroll.ensureWidgetVisible(checked, 0, SPACE_S)
