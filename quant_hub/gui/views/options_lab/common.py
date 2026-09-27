"""Small widget helpers shared by the Options Lab tabs (presentation only)."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from quant_hub.gui.theme import FONT_MONO, FONT_MONO_SM, PALETTE, SPACE_S, SPACE_XS
from quant_hub.visualization.charts import ChartStyle, create_figure

CHART_STYLE = ChartStyle(
    surface=PALETTE.surface, text=PALETTE.text, text_secondary=PALETTE.text_secondary,
    muted=PALETTE.muted, grid=PALETTE.grid, baseline=PALETTE.border, line=PALETTE.accent,
)

Reporter = Callable[[str, bool], None]
"""``report(message, is_error)``: the lab's single status line."""


def entry(
    parent: tk.Misc, caption: str, var: tk.StringVar, row: int, col: int, width: int = 8,
    on_commit: Callable[[], None] | None = None,
) -> ttk.Entry:
    """Caption + entry on a grid row; Return commits (recalculates)."""
    ttk.Label(parent, text=caption, style="Field.TLabel").grid(
        row=row, column=col, sticky="w", padx=(SPACE_S, 2), pady=2
    )
    widget = ttk.Entry(parent, textvariable=var, width=width, font=FONT_MONO)
    widget.grid(row=row, column=col + 1, sticky="w", padx=(0, SPACE_XS), pady=2)
    if on_commit is not None:
        widget.bind("<Return>", lambda _e: on_commit())
    return widget


def combo(
    parent: tk.Misc, caption: str, var: tk.StringVar, values: list[str], row: int, col: int,
    width: int = 8, on_commit: Callable[[], None] | None = None,
) -> ttk.Combobox:
    ttk.Label(parent, text=caption, style="Field.TLabel").grid(
        row=row, column=col, sticky="w", padx=(SPACE_S, 2), pady=2
    )
    widget = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
    widget.grid(row=row, column=col + 1, sticky="w", padx=(0, SPACE_XS), pady=2)
    if on_commit is not None:
        widget.bind("<<ComboboxSelected>>", lambda _e: on_commit())
    return widget


def stat_rows(parent: tk.Misc, rows: list[tuple[str, str]]) -> dict[str, tk.Label]:
    """Caption-left / mono-value-right rows; returns value labels by key."""
    labels: dict[str, tk.Label] = {}
    for key, caption in rows:
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=caption, style="Secondary.TLabel").pack(side="left")
        value = tk.Label(row, text="—", font=FONT_MONO_SM, background=PALETTE.surface,
                         foreground=PALETTE.text)
        value.pack(side="right")
        labels[key] = value
    return labels


def heading(parent: tk.Misc, text: str, **pack) -> None:
    ttk.Label(parent, text=text, style="CardHeading.TLabel").pack(anchor="w", **pack)


def embed_figure(parent: tk.Misc) -> tuple[Figure, FigureCanvasTkAgg]:
    """One long-lived figure + canvas for a tab (never recreated per refresh)."""
    figure = create_figure(CHART_STYLE)
    canvas = FigureCanvasTkAgg(figure, master=parent)
    widget = canvas.get_tk_widget()
    # Request a token size: the grid decides the height and the canvas's
    # <Configure> handler resizes the figure to fit, instead of the figure's
    # nominal 420px forcing neighbours (legs table, side panel) off-screen.
    widget.configure(background=PALETTE.surface, highlightthickness=0, width=200, height=120)
    widget.pack(fill="both", expand=True)
    return figure, canvas


def parse_float(var: tk.StringVar, name: str, scale: float = 1.0) -> float:
    """Parse a numeric field; ``scale`` converts display units (e.g. % -> 0.01)."""
    text = var.get().strip()
    try:
        return float(text) * scale
    except ValueError:
        raise ValueError(f"{name} must be a number (got {text!r})") from None


def days_label(days: float) -> str:
    return f"{days:g}d"
