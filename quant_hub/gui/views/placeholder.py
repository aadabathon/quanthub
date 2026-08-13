"""One placeholder class for every not-yet-built module.

Deliberate: the navigation architecture exists now; pretending the models do
would be worse than saying "planned".
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from quant_hub.gui.views.base import View


class PlaceholderView(View):
    def __init__(self, parent: tk.Misc, title: str, description: str) -> None:
        super().__init__(parent)
        center = ttk.Frame(self, style="Surface.TFrame")
        center.place(relx=0.5, rely=0.42, anchor="center")

        ttk.Label(center, text=title, style="ViewTitle.TLabel").pack()
        ttk.Label(
            center,
            text=description,
            style="Secondary.TLabel",
            wraplength=420,
            justify="center",
        ).pack(pady=(10, 6))
        ttk.Label(center, text="— planned module —", style="Muted.TLabel").pack()
