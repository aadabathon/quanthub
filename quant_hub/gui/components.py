"""The component layer: every page is assembled from these, never from raw
styled widgets. Presentation only — no data access, no math.

Styling is by ``role``/``variant`` dynamic properties resolved in
``theme.stylesheet``; a component sets the property and the stylesheet does
the rest, so a visual change is one edit in ``theme.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QStackedWidget,
    QAbstractItemView,
    QButtonGroup,
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from quant_hub.gui.theme import (
    CONTROL_HEIGHT,
    DOCK_BUTTON,
    DOCK_TITLE_HEIGHT,
    FIELD_M,
    PALETTE,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
    icon,
)


def set_role(widget: QWidget, role: str) -> QWidget:
    widget.setProperty("role", role)
    return widget


def set_state(widget: QWidget, name: str, value) -> None:
    """Change a styling property *after* construction. Qt resolves stylesheet
    selectors at polish time, so the widget must be re-polished to pick up
    e.g. ``[invalid="true"]`` or a new badge ``tone``."""
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        set_role(widget, role)
    widget.setWordWrap(wrap)
    return widget


class ElidedLabel(QLabel):
    """One-line label that elides with "…" instead of demanding its full text
    width — long subtitles (paths, fit summaries) must never widen a card.
    The full text is available as a tooltip when it doesn't fit."""

    def __init__(self, text: str = "", role: str | None = None) -> None:
        super().__init__()
        if role:
            set_role(self, role)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self._full = ""
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def full_text(self) -> str:
        return self._full

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.ElideRight, max(self.width(), 40))
        self.setText(shown)
        self.setToolTip(self._full if shown != self._full else "")


def button(text: str, variant: str | None = None, icon_name: str | None = None,
           on_click: Callable[[], None] | None = None) -> QPushButton:
    # "&" marks a mnemonic in Qt button text; double it so it shows literally.
    widget = QPushButton(text.replace("&", "&&"))
    if variant:
        widget.setProperty("variant", variant)
    if icon_name:
        color = "#ffffff" if variant == "primary" else PALETTE.text_secondary
        widget.setIcon(icon(icon_name, color))
    widget.setCursor(Qt.PointingHandCursor)
    widget.setFixedHeight(CONTROL_HEIGHT)
    if on_click is not None:
        widget.clicked.connect(lambda _checked=False: on_click())
    return widget


def divider(vertical: bool = False) -> QFrame:
    line = QFrame()
    set_role(line, "vdivider" if vertical else "divider")
    return line


class IconTile(QFrame):
    """A tinted rounded square holding one icon (KPI tiles, tool rows)."""

    def __init__(self, icon_name: str, size: int = 44, glyph: int = 22) -> None:
        super().__init__()
        set_role(self, "iconTile")
        self.setFixedSize(size, size)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        glyph_label = QLabel()
        glyph_label.setPixmap(icon(icon_name, PALETTE.series).pixmap(QSize(glyph, glyph)))
        glyph_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(glyph_label)


class Card(QFrame):
    """Rounded panel: optional title/subtitle header with an actions slot on
    the right, then ``body`` (a QVBoxLayout) for content."""

    def __init__(self, title: str | None = None, subtitle: str | None = None,
                 padding: int = SPACE_L) -> None:
        super().__init__()
        set_role(self, "card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(padding, padding - 2, padding, padding)
        outer.setSpacing(SPACE_M)

        self._actions = QHBoxLayout()
        self._actions.setSpacing(SPACE_S)
        if title is not None:
            header = QHBoxLayout()
            header.setSpacing(SPACE_M)
            titles = QVBoxLayout()
            titles.setSpacing(2)
            titles.addWidget(label(title, "cardTitle"))
            self.subtitle = ElidedLabel(subtitle or "", "cardSubtitle")
            titles.addWidget(self.subtitle)
            header.addLayout(titles, 1)
            header.addLayout(self._actions)
            outer.addLayout(header)
            # Attach the layout first: showing a parentless label creates
            # a temporary desktop window (and a flashing taskbar entry).
            self.subtitle.setVisible(bool(subtitle))

        self.body = QVBoxLayout()
        self.body.setSpacing(SPACE_S)
        outer.addLayout(self.body, 1)

    def add_action(self, widget: QWidget) -> None:
        self._actions.addWidget(widget, 0, Qt.AlignTop)

    def set_subtitle(self, text: str) -> None:
        self.subtitle.set_full_text(text)
        self.subtitle.setVisible(bool(text))


class MetricTile(QFrame):
    """A compact headline number: caption, value (optionally toned for a
    signed quantity), and a one-line hint. Rows of these sit under charts."""

    def __init__(self, caption: str, hint: str = "") -> None:
        super().__init__()
        set_role(self, "card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_M + 2, SPACE_M, SPACE_M + 2, SPACE_M)
        layout.setSpacing(2)
        layout.addWidget(label(caption, "metricCaption"))
        self._value = ElidedLabel("—", "metricValue")
        layout.addWidget(self._value)
        self._hint = ElidedLabel(hint, "metricHint")
        layout.addWidget(self._hint)

    def set_value(self, value: str, tone: str = "", hint: str | None = None) -> None:
        self._value.set_full_text(value)
        if (self._value.property("tone") or "") != tone:
            set_state(self._value, "tone", tone)
        if hint is not None:
            self._hint.set_full_text(hint)

    def value_text(self) -> str:
        return self._value.full_text()


def metric_row(tiles: Sequence[QWidget]) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(SPACE_M)
    for tile in tiles:
        row.addWidget(tile, 1)
    return row


class KpiTile(QFrame):
    def __init__(self, icon_name: str, caption: str) -> None:
        super().__init__()
        set_role(self, "card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_L, SPACE_L, SPACE_L, SPACE_L)
        layout.setSpacing(SPACE_L)
        layout.addWidget(IconTile(icon_name, size=56, glyph=28), 0, Qt.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(caption, "kpiLabel"))
        self._value = label("—", "kpiValue")
        text.addWidget(self._value)
        layout.addLayout(text, 1)

    def set_value(self, value: str, tooltip: str = "") -> None:
        self._value.setText(value)
        self.setToolTip(tooltip)


class PageHeader(QWidget):
    """Page title + subtitle on the left; the page's one status line on the
    right (progress notes, load provenance, errors)."""

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_L)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label(title, "pageTitle"))
        if subtitle:
            titles.addWidget(label(subtitle, "pageSubtitle"))
        row.addLayout(titles)
        row.addStretch(1)
        self._status = ElidedLabel("", "statusInfo")
        self._status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._status.setMinimumWidth(240)
        row.addWidget(self._status, 2, Qt.AlignBottom)

    def set_status(self, text: str, error: bool = False) -> None:
        set_state(self._status, "role", "statusError" if error else "statusInfo")
        self._status.set_full_text(text)


class SegmentedControl(QWidget):
    """A row of mutually exclusive toggle buttons; emits the chosen option."""

    changed = Signal(str)

    def __init__(self, options: Sequence[str], current: str, fill: bool = False) -> None:
        """``fill``: segments share the full width equally (inspector panels)
        instead of sizing to their text (toolbars)."""
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS + 2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for option in options:
            btn = button(option, "segment")
            btn.setCheckable(True)
            btn.setFocusPolicy(Qt.NoFocus)
            if fill:
                btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setChecked(option == current)
            btn.clicked.connect(lambda _c=False, o=option: self.changed.emit(o))
            self._group.addButton(btn)
            self._buttons[option] = btn
            layout.addWidget(btn)

    def set_current(self, option: str) -> None:
        if option in self._buttons:
            self._buttons[option].setChecked(True)


class Select(QPushButton):
    """A dropdown picker drawn as a button + caret + menu (a QComboBox can only
    draw its arrow from an image file under a stylesheet)."""

    current_changed = Signal(str)

    def __init__(self, min_width: int = 140) -> None:
        super().__init__()
        self.setProperty("variant", "select")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(min_width)
        self.setFixedHeight(CONTROL_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(SPACE_M, 0, SPACE_M, 0)
        self._label = label("", "rowTitle")
        caret = QLabel()
        caret.setPixmap(icon("ph.caret-down", PALETTE.text_secondary).pixmap(QSize(14, 14)))
        for child in (self._label, caret):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self._label)
        row.addStretch(1)
        row.addWidget(caret)
        self._menu = QMenu(self)
        self.setMenu(self._menu)
        self._current = ""

    def set_items(self, items: Sequence[str], current: str | None = None) -> None:
        """Replace the options without emitting ``current_changed``."""
        self._menu.clear()
        for item in items:
            self._menu.addAction(item, lambda i=item: self._choose(i))
        self._current = current if current in items else (items[0] if items else "")
        self._label.setText(self._current)

    def current(self) -> str:
        return self._current

    def set_current(self, item: str) -> None:
        if item != self._current:
            self._choose(item)

    def _choose(self, item: str) -> None:
        self._current = item
        self._label.setText(item)
        self.current_changed.emit(item)


class MenuButton(QPushButton):
    """The "⋯" overflow button on a card header."""

    def __init__(self, items: Sequence[tuple[str, Callable[[], None]]]) -> None:
        super().__init__()
        self.setProperty("variant", "ghost")
        self.setIcon(icon("ph.dots-three", PALETTE.text_secondary))
        self.setIconSize(QSize(20, 20))
        self.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self)
        for text, callback in items:
            menu.addAction(text, callback)
        self.setMenu(menu)


Cell = str | tuple[str, str] | QWidget
"""A table cell: plain text, (text, ink color), or a widget (sparklines)."""


class DataTable(QTableWidget):
    """Read-only, row-selecting table with header-aligned columns."""

    row_activated = Signal(int)

    selection_changed = Signal(int)   # selected row, or -1

    def __init__(self, columns: Sequence[tuple[str, str]], stretch: int = -1,
                 row_height: int = 38, focusable: bool = False) -> None:
        super().__init__(0, len(columns))
        self._set_headers(columns)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        # Focusable tables take keyboard input (Markets: Delete removes a row).
        self.setFocusPolicy(Qt.StrongFocus if focusable else Qt.NoFocus)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setTextElideMode(Qt.ElideRight)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        if stretch >= 0:
            header.setSectionResizeMode(stretch, QHeaderView.Stretch)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._balanced = False
        self._fixed: dict[int, int] = {}
        self.cellClicked.connect(lambda row, _col: self.row_activated.emit(row))
        self.itemSelectionChanged.connect(
            lambda: self.selection_changed.emit(self.selected_row()))

    def selected_row(self) -> int:
        rows = self.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def set_columns(self, columns: Sequence[tuple[str, str]]) -> None:
        """Replace the columns (their rows go too), for tables whose columns
        follow the data, such as one per regression factor."""
        self.set_rows([])
        self.setColumnCount(len(columns))
        self._set_headers(columns)
        if self._balanced:
            self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._balance()

    def _set_headers(self, columns: Sequence[tuple[str, str]]) -> None:
        self._aligns = [
            (Qt.AlignRight if align == "right" else Qt.AlignLeft) | Qt.AlignVCenter
            for _, align in columns
        ]
        for col, (title, _) in enumerate(columns):
            item = QTableWidgetItem(title)
            item.setTextAlignment(self._aligns[col])
            self.setHorizontalHeaderItem(col, item)

    def balance_columns(self) -> None:
        """Content-aware spacing: every column gets its natural width (header,
        cells, or its ``fix_column`` width) and any spare width is shared
        equally among the non-fixed columns. Recomputed on resize and on new
        rows, so wide windows don't open one big gap and narrow ones don't
        truncate. Overrides any ``stretch`` column."""
        self._balanced = True
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self._balance()

    def fix_column(self, col: int, width: int) -> None:
        """Pin a column's width (cell widgets such as badges don't size it)."""
        self._fixed[col] = width
        self.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
        self.setColumnWidth(col, width)
        self._balance()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._balance()

    def _balance(self) -> None:
        if not self._balanced:
            return
        header = self.horizontalHeader()
        cols = range(self.columnCount())
        natural = [self._fixed.get(c) or max(header.sectionSizeHint(c), self.sizeHintForColumn(c))
                   for c in cols]
        flexible = [c for c in cols if c not in self._fixed]
        spare = max(self.viewport().width() - sum(natural), 0)
        share = spare // len(flexible) if flexible else 0
        for c in cols:
            header.resizeSection(c, natural[c] + (share if c in flexible else 0))

    def set_rows(self, rows: Sequence[Sequence[Cell]]) -> None:
        # Retire old cell widgets *now*: Qt only deletes a replaced widget on
        # the next event-loop pass, and until then it would paint over (or
        # beside) its replacement.
        for r in range(self.rowCount()):
            for c in range(self.columnCount()):
                old = self.cellWidget(r, c)
                if old is not None:
                    old.hide()
                    self.removeCellWidget(r, c)
        self.clearContents()
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                if isinstance(cell, QWidget):
                    self.setCellWidget(r, c, cell)
                    continue
                text, color = cell if isinstance(cell, tuple) else (cell, None)
                item = QTableWidgetItem(text)
                item.setTextAlignment(self._aligns[c])
                if color:
                    item.setForeground(QBrush(QColor(color)))
                self.setItem(r, c, item)
        self._balance()


class NavRow(QFrame):
    """A clickable row: icon tile, title + subtitle, trailing chevron."""

    clicked = Signal()

    def __init__(self, icon_name: str, title: str, subtitle: str) -> None:
        super().__init__()
        set_role(self, "navRow")
        self.setAttribute(Qt.WA_Hover)
        self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_S, SPACE_S, SPACE_S, SPACE_S)
        layout.setSpacing(SPACE_M)
        layout.addWidget(IconTile(icon_name, size=40, glyph=20))
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(ElidedLabel(title, "rowTitle"))
        text.addWidget(ElidedLabel(subtitle, "muted"))
        layout.addLayout(text, 1)
        chevron = QLabel()
        chevron.setPixmap(icon("ph.caret-right", PALETTE.text_secondary).pixmap(QSize(16, 16)))
        layout.addWidget(chevron)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (Qt override)
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StatusDot(QWidget):
    """State as dot + text (never color alone)."""

    def __init__(self, text: str = "", color: str = PALETTE.muted, role: str = "secondary") -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        self._text = label(text, role)
        layout.addWidget(self._dot, 0, Qt.AlignVCenter)
        layout.addWidget(self._text)
        self.set_state(text, color)

    def set_state(self, text: str, color: str) -> None:
        self._dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self._text.setText(text)


class PageStack(QStackedWidget):
    """A stack whose size follows the *visible* page. QStackedWidget sizes to
    its largest page, so a tall tab would force scrolling on a short one;
    hidden pages here get an Ignored size policy, which layouts skip."""

    def __init__(self) -> None:
        super().__init__()
        self.currentChanged.connect(self._fit)

    def addWidget(self, widget: QWidget) -> int:  # noqa: N802 (Qt override)
        index = super().addWidget(widget)
        self._fit()
        return index

    def _fit(self, _index: int = -1) -> None:
        for i in range(self.count()):
            page = self.widget(i)
            policy = (QSizePolicy.Preferred if page is self.currentWidget()
                      else QSizePolicy.Ignored)
            page.setSizePolicy(policy, policy)
        self.updateGeometry()


class EmptyState(QWidget):
    """Centered icon + title + explanation, for 'nothing to show yet'."""

    def __init__(self, icon_name: str, title: str, body: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(SPACE_S)
        glyph = QLabel()
        glyph.setPixmap(icon(icon_name, PALETTE.muted).pixmap(QSize(32, 32)))
        glyph.setAlignment(Qt.AlignCenter)
        layout.addWidget(glyph)
        self._title = label(title, "emptyTitle")
        self._title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title)
        self._body = label(body, "muted", wrap=True)
        self._body.setAlignment(Qt.AlignCenter)
        self._body.setMaximumWidth(420)
        layout.addWidget(self._body, 0, Qt.AlignHCenter)

    def set_text(self, title: str, body: str = "") -> None:
        self._title.setText(title)
        self._body.setText(body)


# --------------------------------------------------------------------- forms

class TextArea(QPlainTextEdit):
    """Plain-text notes editor; presentation stays in the shared theme."""

    def __init__(self, text: str = "", placeholder: str = "") -> None:
        super().__init__()
        set_role(self, "editor")
        self.setPlainText(text)
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(96)


class Field(QWidget):
    """Caption above an input. Numbers parse through ``number()``, which marks
    the field invalid (red outline) and raises ``ValueError`` with a message
    naming the field; pages show that message in their status line."""

    committed = Signal()   # Return pressed

    def __init__(self, caption: str, value: str = "", width: int = FIELD_M,
                 placeholder: str = "", uppercase: bool = False,
                 inline: bool = False) -> None:
        """``inline`` puts the caption left of the input on one control-height
        row (for card headers and toolbars) instead of above it."""
        super().__init__()
        self._caption = caption
        layout = QHBoxLayout(self) if inline else QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_S if inline else SPACE_XS)
        layout.addWidget(label(caption, "fieldCaption"))
        self.edit = QLineEdit(value)
        self.edit.setFixedSize(width, CONTROL_HEIGHT)
        self.edit.setPlaceholderText(placeholder)
        self.edit.returnPressed.connect(self.committed.emit)
        self.edit.textEdited.connect(lambda _t: self._mark(False))
        if uppercase:
            self.edit.textEdited.connect(self._force_upper)
        layout.addWidget(self.edit)
        if not inline:
            self.setFixedWidth(width)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        self.edit.setText(value)
        self._mark(False)

    def number(self, scale: float = 1.0, integer: bool = False,
               minimum: float | None = None, allow_blank: bool = False) -> float | int | None:
        """Parse the field; ``scale`` converts display units (% -> 0.01)."""
        text = self.text()
        if allow_blank and not text:
            self._mark(False)
            return None
        try:
            value = int(text) if integer else float(text)
        except ValueError:
            self._mark(True)
            kind = "a whole number" if integer else "a number"
            raise ValueError(f"{self._caption} must be {kind} (got {text!r})") from None
        if minimum is not None and value < minimum:
            self._mark(True)
            raise ValueError(f"{self._caption} must be at least {minimum:g}")
        self._mark(False)
        return value if integer else value * scale

    def fail(self, message: str) -> ValueError:
        """Flag the field and return the error for the caller to raise (for
        validation ``number()`` can't express, e.g. a list of expiries)."""
        self._mark(True)
        return ValueError(message)

    def required_text(self) -> str:
        text = self.text()
        self._mark(not text)
        if not text:
            raise ValueError(f"{self._caption} is required")
        return text

    def _mark(self, invalid: bool) -> None:
        if bool(self.edit.property("invalid")) != invalid:
            set_state(self.edit, "invalid", invalid)

    def _force_upper(self, text: str) -> None:
        if text != text.upper():
            position = self.edit.cursorPosition()
            self.edit.setText(text.upper())
            self.edit.setCursorPosition(position)


class SelectField(QWidget):
    """Caption above a ``Select``."""

    def __init__(self, caption: str, items: Sequence[str], current: str | None = None,
                 width: int = FIELD_M) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        layout.addWidget(label(caption, "fieldCaption"))
        self.select = Select(min_width=width)
        self.select.setFixedWidth(width)
        self.select.set_items(list(items), current)
        layout.addWidget(self.select)
        self.setFixedWidth(width)

    def current(self) -> str:
        return self.select.current()


def form_grid(fields: Sequence[QWidget], columns: int) -> QGridLayout:
    """Fields flowed into a fixed-column grid with uniform gaps."""
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(SPACE_M)
    grid.setVerticalSpacing(SPACE_M)
    for i, field in enumerate(fields):
        grid.addWidget(field, i // columns, i % columns, Qt.AlignLeft)
    grid.setColumnStretch(columns, 1)
    return grid


# --------------------------------------------------------------------- stats

class StatTable(QWidget):
    """Caption | value rows for model output, optionally with a TRUE column
    (synthetic experiments know the answer). Rows are grouped; groups are
    separated by a hairline."""

    def __init__(self, groups: Sequence[tuple[str | None, Sequence[tuple[str, str]]]],
                 value_header: str = "FIT", truth_header: str = "TRUE") -> None:
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_L)
        grid.setVerticalSpacing(SPACE_S)
        grid.setColumnStretch(0, 1)
        self._values: dict[str, QLabel] = {}
        self._truths: dict[str, QLabel] = {}
        self._truth_header = label(truth_header, "statHeader")
        value_head = label(value_header, "statHeader")
        row = 0
        for g, (title, rows) in enumerate(groups):
            if g > 0:
                grid.addWidget(divider(), row, 0, 1, 3)
                row += 1
            grid.addWidget(label((title or "").upper(), "statHeader"), row, 0)
            if g == 0:
                for col, head in ((1, self._truth_header), (2, value_head)):
                    head.setAlignment(Qt.AlignRight)
                    grid.addWidget(head, row, col)
            row += 1
            for key, caption in rows:
                grid.addWidget(label(caption, "statCaption"), row, 0)
                truth = label("", "statTruth")
                value = label("—", "statValue")
                for col, cell in ((1, truth), (2, value)):
                    cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    grid.addWidget(cell, row, col)
                self._truths[key], self._values[key] = truth, value
                row += 1
        self.show_truth(False)

    def show_truth(self, visible: bool) -> None:
        self._truth_header.setVisible(visible)
        for cell in self._truths.values():
            cell.setVisible(visible)

    def set_value(self, key: str, value: str, truth: str = "", tone: str = "") -> None:
        """``tone`` ("good"/"critical") colors a signed value; the text keeps
        its sign, so color is never the only signal."""
        cell = self._values[key]
        cell.setText(value)
        if (cell.property("tone") or "") != tone:
            set_state(cell, "tone", tone)
        self._truths[key].setText(truth)

    def value_text(self, key: str) -> str:
        return self._values[key].text()

    def clear(self) -> None:
        for key in self._values:
            self.set_value(key, "—")


class Badge(QLabel):
    """A small status pill: text + tone (good/warning/critical/info/neutral).
    Tone is a state color, so the text always names the state too."""

    def __init__(self, text: str = "", tone: str = "neutral") -> None:
        super().__init__(text)
        set_role(self, "badge")
        self.setProperty("tone", tone)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set_badge(self, text: str, tone: str) -> None:
        self.setText(text)
        if self.property("tone") != tone:
            set_state(self, "tone", tone)


def centered(widget: QWidget, align=Qt.AlignLeft) -> QWidget:
    """Wrap a small widget (a badge) so a table cell centers it vertically."""
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(SPACE_S, 0, SPACE_S, 0)
    layout.addWidget(widget, 0, align | Qt.AlignVCenter)
    return holder


class Note(QFrame):
    """An inline explanation box: ``critical`` for a problem (why a quote is
    unavailable), ``info`` for a finding the reader should know about."""

    def __init__(self, icon_name: str = "ph.warning", tone: str = "critical",
                 width: int | None = None) -> None:
        super().__init__()
        set_role(self, "note")
        self.setProperty("tone", tone)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M, SPACE_S, SPACE_M, SPACE_S)
        layout.setSpacing(SPACE_S)
        glyph = QLabel()
        ink = PALETTE.series_light if tone == "info" else PALETTE.critical
        glyph.setPixmap(icon(icon_name, ink).pixmap(QSize(16, 16)))
        layout.addWidget(glyph, 0, Qt.AlignTop)
        self._text = label("", wrap=True)
        layout.addWidget(self._text, 1)
        self._width = width

    def set_text(self, text: str) -> None:
        self._text.setText(text)
        if self._width is not None:
            # A page body ignores height-for-width (pages/base.py), so the
            # wrapped text would get the height of a guessed, wider line. At
            # a known width (a side panel), the note sets its own minimum.
            self._text.ensurePolished()
            room = self._width - 2 * SPACE_M - 16 - SPACE_S
            self._text.setMinimumHeight(self._text.heightForWidth(room))


class DockTitleBar(QFrame):
    """A docked page's title bar (D55): the page's icon and name, then float
    and close buttons. Qt's own dock handling does the rest: dragging the
    bar moves the page, and double-clicking it floats or docks the page.
    Mouse events the labels ignore reach the dock, which is what makes
    that work."""

    def __init__(self, dock: QDockWidget, icon_name: str) -> None:
        super().__init__()
        set_role(self, "dockTitle")
        self.setFixedHeight(DOCK_TITLE_HEIGHT)
        self._dock = dock
        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACE_M, 0, SPACE_XS, 0)
        layout.setSpacing(SPACE_S)
        glyph = QLabel()
        glyph.setPixmap(icon(icon_name, PALETTE.text_secondary).pixmap(QSize(16, 16)))
        layout.addWidget(glyph)
        layout.addWidget(label(dock.windowTitle(), "dockTitleText"))
        layout.addStretch(1)
        self.float_button = self._tool_button(self._toggle_floating)
        self.close_button = self._tool_button(dock.close)
        self.close_button.setIcon(icon("ph.x", PALETTE.text_secondary))
        self.close_button.setToolTip("Close this page")
        layout.addWidget(self.float_button)
        layout.addWidget(self.close_button)
        dock.topLevelChanged.connect(self._show_floating)
        dock.featuresChanged.connect(self._show_features)
        self._show_floating(dock.isFloating())
        self._show_features(dock.features())

    @staticmethod
    def _tool_button(on_click: Callable[[], None]) -> QPushButton:
        widget = QPushButton()
        widget.setProperty("variant", "ghost")
        widget.setIconSize(QSize(14, 14))
        widget.setFixedSize(DOCK_BUTTON, DOCK_BUTTON)
        widget.setCursor(Qt.PointingHandCursor)
        widget.clicked.connect(lambda _checked=False: on_click())
        return widget

    def _toggle_floating(self) -> None:
        self._dock.setFloating(not self._dock.isFloating())

    def _show_floating(self, floating: bool) -> None:
        self.float_button.setIcon(icon("ph.arrow-square-in" if floating else "ph.arrow-square-out",
                                       PALETTE.text_secondary))
        self.float_button.setToolTip("Dock this page" if floating else "Float this page")

    def _show_features(self, features) -> None:
        self.float_button.setVisible(bool(features & QDockWidget.DockWidgetFloatable))
