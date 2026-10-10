"""One page class for everything not built in the Qt UI yet (D7's pattern).

Two honest states: a working Tk page that hasn't been ported (point at the
classic UI, which still has it), or a planned roadmap module.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from quant_hub.gui_qt.components import Card, EmptyState, PageHeader
from quant_hub.gui_qt.pages.base import ScrollPage


class PlaceholderPage(ScrollPage):
    def __init__(self, title: str, icon_name: str, description: str, ported_later: bool) -> None:
        super().__init__()
        self.body.addWidget(PageHeader(title, description))
        card = Card()
        if ported_later:
            empty = EmptyState(
                icon_name, f"{title} is moving to the new interface",
                "It still works in the classic UI while it's ported: "
                "run  py -3.12 -m quant_hub --classic.",
            )
        else:
            empty = EmptyState(icon_name, "Planned module",
                               "On the roadmap — not built yet. See ROADMAP.md.")
        card.body.addWidget(empty, 1, Qt.AlignCenter)
        card.setMinimumHeight(360)
        self.body.addWidget(card)
        self.body.addStretch(1)
