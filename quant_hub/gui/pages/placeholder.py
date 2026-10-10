"""One page class for every planned roadmap module (D7's pattern)."""

from __future__ import annotations

from PySide6.QtCore import Qt

from quant_hub.gui.components import Card, EmptyState, PageHeader
from quant_hub.gui.pages.base import ScrollPage


class PlaceholderPage(ScrollPage):
    def __init__(self, title: str, icon_name: str, description: str) -> None:
        super().__init__()
        self.body.addWidget(PageHeader(title, description))
        card = Card()
        card.body.addWidget(EmptyState(icon_name, "Planned module",
                                       "On the roadmap — not built yet. See ROADMAP.md."),
                            1, Qt.AlignCenter)
        card.setMinimumHeight(360)
        self.body.addWidget(card)
        self.body.addStretch(1)
