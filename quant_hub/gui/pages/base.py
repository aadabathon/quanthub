"""Page base classes and the narrow interface pages get to the shell.

Pages are normally created on first navigation and stay alive, so their
state survives switching (as in the Tk ``View``). The Volatility Lab is
prepared before the main window is shown for WebEngine's graphics setup
(D40). ``on_show``/``on_hide`` bracket the time a page can be seen: refresh
on show, persist on hide. Pages live in docks (D55), so several can be shown
at once.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from quant_hub.data.types import PriceBasis, PriceHistory
from quant_hub.gui.components import CardGrid, SelectField
from quant_hub.gui.theme import SPACE_L, SPACE_XL

Commands = dict[str, Callable[[], None]]

# Live recalculation waits this long after the last keystroke: long enough
# that typing "105" doesn't recompute at "1" and "10", short enough to feel
# immediate. Closed-form work itself is sub-millisecond.
LIVE_DELAY_MS = 150


PRICE_BASES = {"Adjusted": PriceBasis.ADJUSTED, "Traded": PriceBasis.TRADED}


def offline_note(history: PriceHistory) -> str:
    """" · offline, stored through <day>" for a history the source served
    from its store (V0), else "": appended wherever a page names its data."""
    if history.offline_reason is None:
        return ""
    return f" · offline, stored bars through {history.end:%b %d, %Y}"


def basis_field(saved: dict, width: int) -> SelectField:
    """The Prices choice on pages that do return math (D38): adjusted unless
    the saved state says otherwise."""
    current = saved.get("basis")
    field = SelectField("Prices", list(PRICE_BASES),
                        current if current in PRICE_BASES else "Adjusted", width)
    field.setToolTip("Adjusted: for splits and dividends, so returns are total returns, "
                     "right for return math. Traded: the prices as they traded.")
    return field


def saved_grid(grid: CardGrid, store, key: str) -> CardGrid:
    """A page's card layout (D56), restored from the view-state store under
    ``key`` and saved there after every drop and resize. Call it once every
    card is added."""
    grid.restore(store.load(key))
    grid.changed.connect(lambda: store.save(key, grid.state()))
    return grid


def debounce(owner: QObject, fn: Callable[[], None], ms: int = LIVE_DELAY_MS) -> Callable[[], None]:
    """A trigger that runs ``fn`` once, ``ms`` after its last call."""
    timer = QTimer(owner)
    timer.setSingleShot(True)
    timer.setInterval(ms)
    timer.timeout.connect(fn)
    return timer.start


@dataclass(frozen=True)
class Shell:
    """What a page may ask of the main window — nothing more.

    ``navigate(name)`` raises a page and returns it (so a caller can act on
    it, e.g. select a symbol). ``publish_commands(owner, commands)`` replaces
    ``owner``'s entries in the Ctrl+K search.
    """

    navigate: Callable[[str], "Page"]
    publish_commands: Callable[[str, Commands], None]


class Page(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("page")
        self.setAttribute(Qt.WA_StyledBackground)

    def on_show(self) -> None:
        """Called each time the page becomes visible."""

    def on_hide(self) -> None:
        """Called when the page stops being visible (closed, or another tab
        in front of it), and at shutdown."""


class _BodyLayout(QVBoxLayout):
    """Layout of a ``ScrollPage``'s content. Word-wrapped labels give a
    layout height-for-width, and QScrollArea then sizes the content to its
    *preferred* height (scrolling a page that would fit). Opting out keeps
    the rule simple: fill the viewport, scroll only below the minimum."""

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return False


class _PageScroll(QScrollArea):
    """A ``ScrollPage``'s scroll area. It scrolls only vertically, so content
    wider than the page would lose its right edge. Instead it asks for the
    content's minimum width (plus the vertical scroll bar's), and the window
    can't shrink below it. A plain QScrollArea's minimum ignores its content.

    The content re-lays out on every label update. Its minimum is passed up
    to the window only when it changes."""

    def __init__(self) -> None:
        super().__init__()
        self._content_width = 0

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        hint = super().minimumSizeHint()
        return QSize(max(hint.width(), self._needed_width()), hint.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt override)
        # setWidget installed this filter on the content. A LayoutRequest
        # there means its minimum may have changed.
        if watched is self.widget() and event.type() == QEvent.LayoutRequest:
            width = self._needed_width()
            if width != self._content_width:
                self._content_width = width
                self.updateGeometry()
        return super().eventFilter(watched, event)

    def _needed_width(self) -> int:
        content = self.widget()
        if content is None:
            return 0
        return (content.minimumSizeHint().width()
                + self.verticalScrollBar().sizeHint().width() + 2 * self.frameWidth())


class ScrollPage(Page):
    """A page whose content scrolls vertically instead of clipping when the
    window is short. The window can't be narrower than the content, so
    nothing clips sideways either. Build content into ``self.body``."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = _PageScroll()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("pageBody")
        self.body = _BodyLayout(content)
        self.body.setContentsMargins(SPACE_XL, SPACE_XL - 4, SPACE_XL, SPACE_XL)
        self.body.setSpacing(SPACE_L)
        scroll.setWidget(content)
        outer.addWidget(scroll)
