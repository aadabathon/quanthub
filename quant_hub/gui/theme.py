"""Centralized visual styling: every color, font, and ttk style in one file.

The app commits to a single dark "terminal" look. Colors come from a
validated dark-surface data-viz palette (adjacent-series CVD deltas and 3:1
contrast on the dark surface verified), so GUI chrome and charts read as one
system. Status colors are reserved for state (connection health, pos/neg
values) and are never reused as chart series colors.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    # Surfaces
    page: str = "#0d0d0d"          # window background plane
    surface: str = "#1a1a19"       # panels, chart surface
    surface_alt: str = "#232322"   # nav rail, inputs, hover
    border: str = "#33332f"        # hairline separators
    grid: str = "#2c2c2a"          # chart gridlines
    # Ink
    text: str = "#ffffff"
    text_secondary: str = "#c3c2b7"
    muted: str = "#898781"
    # Accents (series-1 blue of the dark palette)
    accent: str = "#3987e5"
    accent_active: str = "#5598e7"
    # Status (reserved: state only, never series)
    good: str = "#0ca30c"
    warning: str = "#fab219"
    serious: str = "#ec835a"
    critical: str = "#d03b3b"
    # Selection
    select_bg: str = "#1c5cab"


PALETTE = Palette()

FONT_UI = ("Segoe UI", 10)
FONT_UI_BOLD = ("Segoe UI", 10, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_TITLE = ("Segoe UI Semibold", 13)
FONT_HEADING = ("Segoe UI Semibold", 11)
FONT_MONO = ("Consolas", 10)
FONT_MONO_LG = ("Consolas", 20, "bold")
FONT_MONO_SM = ("Consolas", 9)

# Spacing scale: use these instead of ad-hoc pixel values in views.
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24


def apply_theme(root: tk.Tk) -> None:
    """Configure ttk styles for the whole application. Call once at startup."""
    p = PALETTE
    style = ttk.Style(root)
    style.theme_use("clam")  # the only built-in theme that honors color options

    root.configure(background=p.page)

    # Base defaults every ttk widget inherits.
    style.configure(
        ".",
        background=p.surface,
        foreground=p.text,
        bordercolor=p.border,
        darkcolor=p.surface,
        lightcolor=p.surface,
        troughcolor=p.surface_alt,
        focuscolor=p.accent,
        font=FONT_UI,
    )

    # Frames / labels ------------------------------------------------------
    style.configure("Page.TFrame", background=p.page)
    style.configure("Surface.TFrame", background=p.surface)
    style.configure("Nav.TFrame", background=p.surface_alt)
    # A bordered panel: groups of controls / stats sit in these.
    style.configure(
        "Card.TFrame",
        background=p.surface,
        bordercolor=p.border,
        borderwidth=1,
        relief="solid",
    )
    style.configure("TLabel", background=p.surface, foreground=p.text)
    style.configure("Title.TLabel", font=FONT_TITLE, background=p.page, foreground=p.text)
    style.configure("ViewTitle.TLabel", font=FONT_TITLE, foreground=p.text)
    style.configure("Heading.TLabel", font=FONT_HEADING, foreground=p.text_secondary)
    style.configure("Muted.TLabel", foreground=p.muted)
    style.configure("Secondary.TLabel", foreground=p.text_secondary)
    style.configure("Mono.TLabel", font=FONT_MONO)
    style.configure("Hero.TLabel", font=FONT_MONO_LG)
    style.configure("Header.TLabel", background=p.page, foreground=p.text_secondary)
    # Uppercase card captions ("SIMULATION", "SAMPLE ESTIMATES", ...).
    style.configure("CardHeading.TLabel", font=FONT_SMALL, foreground=p.muted)
    # Small captions next to input fields (μ, σ, days, ...).
    style.configure("Field.TLabel", font=FONT_SMALL, foreground=p.text_secondary)
    # Section captions in the navigation rail ("MODELS").
    style.configure(
        "NavSection.TLabel",
        font=FONT_SMALL,
        foreground=p.muted,
        background=p.surface_alt,
    )

    # Navigation buttons ---------------------------------------------------
    style.configure(
        "Nav.TButton",
        background=p.surface_alt,
        foreground=p.text_secondary,
        anchor="w",
        borderwidth=0,
        padding=(16, 8),
        font=FONT_UI,
    )
    style.map(
        "Nav.TButton",
        background=[("active", p.surface)],
        foreground=[("active", p.text)],
    )
    style.configure(
        "NavActive.TButton",
        background=p.surface,
        foreground=p.text,
        anchor="w",
        borderwidth=0,
        padding=(16, 8),
        font=FONT_UI_BOLD,
    )
    style.map("NavActive.TButton", background=[("active", p.surface)])

    # Action buttons -------------------------------------------------------
    style.configure(
        "Accent.TButton",
        background=p.accent,
        foreground=p.text,
        borderwidth=0,
        padding=(12, 5),
        font=FONT_UI_BOLD,
    )
    style.map(
        "Accent.TButton",
        background=[("disabled", p.surface_alt), ("active", p.accent_active)],
        foreground=[("disabled", p.muted)],
    )
    style.configure(
        "TButton", background=p.surface_alt, borderwidth=0, padding=(12, 5)
    )
    style.map("TButton", background=[("active", p.border)])

    # Inputs ---------------------------------------------------------------
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(
            widget,
            fieldbackground=p.surface_alt,
            foreground=p.text,
            insertcolor=p.text,
            bordercolor=p.border,
            arrowcolor=p.text_secondary,
            padding=4,
        )
        style.map(
            widget,
            fieldbackground=[("readonly", p.surface_alt)],
            foreground=[("disabled", p.muted)],
        )
    # Combobox dropdown list (plain tk, styled via the option database).
    root.option_add("*TCombobox*Listbox.background", p.surface_alt)
    root.option_add("*TCombobox*Listbox.foreground", p.text)
    root.option_add("*TCombobox*Listbox.selectBackground", p.select_bg)
    root.option_add("*TCombobox*Listbox.selectForeground", p.text)
    root.option_add("*TCombobox*Listbox.font", FONT_UI)

    # Tables ---------------------------------------------------------------
    style.configure(
        "Treeview",
        background=p.surface,
        fieldbackground=p.surface,
        foreground=p.text,
        rowheight=26,
        borderwidth=0,
        font=FONT_MONO,
    )
    style.configure(
        "Treeview.Heading",
        background=p.surface_alt,
        foreground=p.text_secondary,
        borderwidth=0,
        font=FONT_UI_BOLD,
        padding=(8, 6),
    )
    style.map(
        "Treeview",
        background=[("selected", p.select_bg)],
        foreground=[("selected", p.text)],
    )
    style.map("Treeview.Heading", background=[("active", p.surface_alt)])

    # Notebook (dashboard tabs) -------------------------------------------
    style.configure("TNotebook", background=p.surface, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure(
        "TNotebook.Tab",
        background=p.surface_alt,
        foreground=p.text_secondary,
        padding=(14, 6),
        borderwidth=0,
        font=FONT_UI,
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", p.surface)],
        foreground=[("selected", p.text)],
    )

    # Misc -----------------------------------------------------------------
    style.configure("TSeparator", background=p.border)
    style.configure(
        "Vertical.TScrollbar",
        background=p.surface_alt,
        troughcolor=p.surface,
        arrowcolor=p.muted,
        borderwidth=0,
    )
