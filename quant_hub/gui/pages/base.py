"""Page base classes and the narrow interface pages get to the shell.

Pages are created lazily on first navigation and stay alive, so their state
survives switching (as in the Tk ``View``). ``on_show``/``on_hide`` bracket
visibility: refresh on show, persist on hide.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from quant_hub.gui.theme import SPACE_L, SPACE_XL

Commands = dict[str, Callable[[], None]]

# Live recalculation waits this long after the last keystroke: long enough
# that typing "105" doesn't recompute at "1" and "10", short enough to feel
# immediate. Closed-form work itself is sub-millisecond.
LIVE_DELAY_MS = 150


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
        """Called when another page replaces this one, and at shutdown."""


class _BodyLayout(QVBoxLayout):
    """Layout of a ``ScrollPage``'s content. Word-wrapped labels give a
    layout height-for-width, and QScrollArea then sizes the content to its
    *preferred* height (scrolling a page that would fit). Opting out keeps
    the rule simple: fill the viewport, scroll only below the minimum."""

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt override)
        return False


class ScrollPage(Page):
    """A page whose content scrolls vertically instead of clipping when the
    window is short. Build content into ``self.body``."""

    def __init__(self) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
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
