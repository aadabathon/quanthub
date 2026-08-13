"""Base class for navigable views.

A view is just a themed ``ttk.Frame`` with lifecycle hooks. Views are created
lazily on first navigation (cheap startup) and then kept alive and raised, so
their widget state survives switching away and back.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class View(ttk.Frame):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent, style="Surface.TFrame")

    def on_show(self) -> None:
        """Called every time the view becomes the active view."""

    def on_hide(self) -> None:
        """Called when the user navigates away from this view."""
