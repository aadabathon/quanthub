"""Design tokens and the generated stylesheet: every color, font and size.

One dark navy look (D31). Widgets never carry hex constants or font sizes;
they set a ``role`` dynamic property (``card``, ``title``, ``muted`` …) and
the stylesheet below styles each role once. Charts read the same ``PALETTE``.

Color provenance:
* Series colors keep D13's validated dark categorical steps (blue, orange,
  aqua), re-checked against the navy card surface ``#0f1522`` with the
  data-viz validator: all-pairs CVD ΔE ≥ 9.4, normal-vision ΔE ≥ 20.9,
  contrast ≥ 3:1.
* Status and text inks were chosen by WCAG contrast on that surface: text
  15.5:1, secondary 8.2:1, muted 5.0:1, good 7.7:1, warning 8.5:1, critical
  5.7:1 (all ≥ 4.5:1 for small text). Status colors mean *state* only and
  always ship with a sign, label or icon, never color alone.
* White on the accent button is 4.6:1.
"""

from __future__ import annotations

from dataclasses import dataclass

import pyqtgraph as pg
import qtawesome as qta
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Palette:
    # Surfaces, darkest to lightest
    page: str = "#0a0e17"           # window background
    sidebar: str = "#0b101b"
    surface: str = "#0f1522"        # cards, chart backgrounds
    raised: str = "#141c2b"         # inputs, table headers, segmented track
    hover: str = "#18223a"
    border: str = "#1c2538"         # card hairlines
    border_strong: str = "#283350"  # input outlines, focus-adjacent
    grid: str = "#172033"           # chart gridlines
    # Ink
    text: str = "#e8edf5"
    text_secondary: str = "#a3afc2"
    muted: str = "#7a879c"
    # Accent (interactive) — distinct from the series blue below
    accent: str = "#1f6feb"
    accent_hover: str = "#388bfd"
    accent_soft: str = "#12213b"    # active nav item, icon tiles
    accent_soft_border: str = "#1d3a6b"
    # Series (validated categorical slots 1-3, dark steps)
    series: str = "#3987e5"
    series_light: str = "#6da7ec"   # ordinal step 300: medians, info badges (6.5:1)
    # Ordinal ramp of the series hue (D13's validated --ordinal steps): ordered
    # curves such as time to expiry, step 0 first (darkest = longest).
    ramp: tuple[str, ...] = ("#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb")
    # Sequential ramp for magnitude (heatmaps), dark -> light so low values
    # recede into the dark surface; one hue, validated (D13, --ordinal).
    sequential: tuple[str, ...] = ("#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4",
                                   "#cde2fb")
    series_2: str = "#d95926"
    series_3: str = "#199e70"
    # Status (state only)
    good: str = "#2fbf71"
    warning: str = "#e5a50a"
    critical: str = "#f0605a"
    # Tinted fills behind status text (badges, error notes)
    good_soft: str = "#10301f"
    warning_soft: str = "#33270a"
    critical_soft: str = "#2a1517"
    critical_border: str = "#4a2326"


PALETTE = Palette()

FONT_UI = "Segoe UI Variable Text"
FONT_DISPLAY = "Segoe UI Variable Display"
FONT_MONO = "Cascadia Mono"
_FALLBACKS = {FONT_UI: "Segoe UI", FONT_DISPLAY: "Segoe UI", FONT_MONO: "Consolas"}

# Spacing scale (px). Layout margins/spacings use these, never ad-hoc numbers.
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24
RADIUS = 10          # cards
RADIUS_S = 7         # buttons, inputs, nav items

# Control sizing: every input, select, segment and button is one height, and
# fields come in three widths, so rows of controls line up across pages.
CONTROL_HEIGHT = 34
FIELD_S = 88         # short numbers (alpha, seed, days)
FIELD_M = 128        # symbols, sources, most numbers
FIELD_L = 176        # free text
SIDE_PANEL_WIDTH = 340   # right-hand inspector column on every page


def font_family(preferred: str) -> str:
    """The preferred family if installed (Windows 11 ships all three),
    else its classic Windows fallback."""
    return preferred if preferred in QFontDatabase.families() else _FALLBACKS[preferred]


def mono_font(px: int = 12) -> QFont:
    font = QFont(font_family(FONT_MONO))
    font.setPixelSize(px)
    return font


def icon(name: str, color: str | None = None, on: str | None = None) -> QIcon:
    """A Phosphor/MDI icon tinted with theme ink. ``on`` tints the checked
    state (nav items), so one QIcon serves both."""
    options = {"color": color or PALETTE.text_secondary}
    if on is not None:
        options |= {"color_on": on, "color_on_active": on}
    return qta.icon(name, **options)


def stylesheet(p: Palette = PALETTE) -> str:
    ui, display = font_family(FONT_UI), font_family(FONT_DISPLAY)
    return f"""
* {{ font-family: "{ui}"; font-size: 13px; color: {p.text}; outline: none; }}
QMainWindow, QWidget#appRoot {{ background: {p.page}; }}
QWidget#page, QScrollArea#pageScroll, QWidget#pageBody {{ background: {p.page}; border: none; }}
QToolTip {{ background: {p.raised}; color: {p.text}; border: 1px solid {p.border_strong};
            padding: 4px 8px; border-radius: 4px; }}

/* ---- text roles -------------------------------------------------------- */
QLabel {{ background: transparent; }}
QLabel[role="pageTitle"] {{ font-family: "{display}"; font-size: 28px; font-weight: 600; }}
QLabel[role="pageSubtitle"] {{ font-size: 15px; color: {p.text_secondary}; }}
QLabel[role="cardTitle"] {{ font-family: "{display}"; font-size: 17px; font-weight: 600; }}
QLabel[role="cardSubtitle"] {{ font-size: 12px; color: {p.text_secondary}; }}
QLabel[role="kpiLabel"] {{ font-size: 12px; color: {p.text_secondary}; }}
QLabel[role="kpiValue"] {{ font-family: "{display}"; font-size: 24px; font-weight: 600; }}
QLabel[role="secondary"] {{ color: {p.text_secondary}; }}
QLabel[role="muted"] {{ color: {p.muted}; font-size: 12px; }}
QLabel[role="section"] {{ color: {p.muted}; font-size: 11px; font-weight: 600;
                          letter-spacing: 1px; }}
QLabel[role="rowTitle"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="emptyTitle"] {{ font-family: "{display}"; font-size: 16px; font-weight: 600; }}
QLabel[role="brand"] {{ font-family: "{display}"; font-size: 20px; font-weight: 600; }}
QLabel[role="kbd"] {{ color: {p.muted}; font-size: 11px; border: 1px solid {p.border_strong};
                      border-radius: 4px; padding: 0px 5px; }}
QLabel[role="readout"] {{ color: {p.text_secondary}; font-size: 12px; }}
QLabel[role="fieldCaption"] {{ color: {p.text_secondary}; font-size: 12px; }}
QLabel[role="groupTitle"] {{ color: {p.muted}; font-size: 11px; font-weight: 600;
                             letter-spacing: 1px; }}
QLabel[role="statCaption"] {{ color: {p.text_secondary}; }}
QLabel[role="statValue"] {{ color: {p.text}; font-weight: 600; }}
QLabel[role="statValue"][tone="good"] {{ color: {p.good}; }}
QLabel[role="statValue"][tone="critical"] {{ color: {p.critical}; }}
QLabel[role="statTruth"] {{ color: {p.text_secondary}; }}
QLabel[role="statHeader"] {{ color: {p.muted}; font-size: 11px; font-weight: 600;
                             letter-spacing: 1px; }}
QLabel[role="statusInfo"] {{ color: {p.muted}; font-size: 12px; }}
QLabel[role="statusError"] {{ color: {p.critical}; font-size: 12px; }}
QLabel[role="heroSymbol"] {{ font-family: "{display}"; font-size: 22px; font-weight: 600; }}
QLabel[role="heroValue"] {{ font-family: "{display}"; font-size: 30px; font-weight: 600; }}
QLabel[role="badge"] {{ border-radius: 5px; padding: 2px 8px; font-size: 11px;
                        font-weight: 600; letter-spacing: 0.5px; }}
QLabel[role="badge"][tone="good"] {{ color: {p.good}; background: {p.good_soft}; }}
QLabel[role="badge"][tone="warning"] {{ color: {p.warning}; background: {p.warning_soft}; }}
QLabel[role="badge"][tone="critical"] {{ color: {p.critical}; background: {p.critical_soft}; }}
QLabel[role="badge"][tone="info"] {{ color: {p.series_light}; background: {p.accent_soft}; }}
QLabel[role="badge"][tone="neutral"] {{ color: {p.text_secondary}; background: {p.raised}; }}
QFrame[role="note"] {{ background: {p.critical_soft}; border: 1px solid {p.critical_border};
                      border-radius: 8px; }}
QFrame[role="note"] QLabel {{ color: {p.text}; }}

/* ---- cards & tiles ----------------------------------------------------- */
QFrame[role="card"] {{ background: {p.surface}; border: 1px solid {p.border};
                       border-radius: {RADIUS}px; }}
QFrame[role="iconTile"] {{ background: {p.accent_soft}; border: 1px solid {p.accent_soft_border};
                           border-radius: 9px; }}
QFrame[role="navRow"] {{ background: transparent; border: 1px solid {p.border};
                         border-radius: 9px; }}
QFrame[role="navRow"]:hover {{ background: {p.hover}; border-color: {p.border_strong}; }}
QFrame[role="divider"] {{ background: {p.border}; border: none; max-height: 1px; }}
QFrame[role="vdivider"] {{ background: {p.border_strong}; border: none; max-width: 1px; }}

/* ---- shell ------------------------------------------------------------- */
QFrame#sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
QFrame#topBar {{ background: {p.page}; border-bottom: 1px solid {p.border}; }}
QFrame#statusBar {{ background: {p.sidebar}; border-top: 1px solid {p.border}; }}
QFrame#logDrawer {{ background: {p.sidebar}; border-top: 1px solid {p.border}; }}

QPushButton[nav="true"] {{ background: transparent; border: 1px solid transparent;
    border-radius: {RADIUS_S}px; padding: 9px 12px; text-align: left; font-size: 14px;
    color: {p.text_secondary}; }}
QPushButton[nav="true"]:hover {{ background: {p.hover}; color: {p.text}; }}
QPushButton[nav="true"]:checked {{ background: {p.accent_soft};
    border: 1px solid {p.accent_soft_border}; color: {p.text}; font-weight: 600; }}

/* ---- buttons ----------------------------------------------------------- */
QPushButton {{ background: {p.raised}; border: 1px solid {p.border_strong};
    border-radius: {RADIUS_S}px; padding: 6px 14px; color: {p.text}; }}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.muted}; }}
QPushButton[variant="primary"] {{ background: {p.accent}; border: 1px solid {p.accent};
    color: #ffffff; font-weight: 600; padding: 7px 18px; }}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.raised}; border-color: {p.border_strong};
    color: {p.muted}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: none; padding: 4px 6px;
    color: {p.text_secondary}; }}
QPushButton[variant="ghost"]:hover {{ background: {p.hover}; color: {p.text}; }}
QPushButton[variant="segment"] {{ background: {p.raised}; border: 1px solid {p.border};
    border-radius: 6px; padding: 0px 12px; min-width: 22px; color: {p.text_secondary};
    font-size: 12px; }}
QPushButton[variant="segment"]:hover {{ background: {p.hover}; color: {p.text}; }}
QPushButton[variant="segment"]:checked {{ background: {p.accent}; border-color: {p.accent};
    color: #ffffff; }}
QPushButton[variant="select"] {{ background: {p.surface}; border: 1px solid {p.border_strong};
    min-height: 32px; padding: 0px; }}
QPushButton[variant="select"]:hover {{ border-color: {p.muted}; }}
QPushButton::menu-indicator {{ width: 0px; image: none; }}

/* ---- inputs ------------------------------------------------------------ */
QLineEdit, QComboBox {{ background: {p.surface}; border: 1px solid {p.border_strong};
    border-radius: {RADIUS_S}px; padding: 6px 10px; selection-background-color: {p.accent}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {p.accent}; }}
QLineEdit#search {{ background: {p.surface}; padding-left: 6px; }}
QLineEdit:disabled {{ color: {p.muted}; background: {p.page}; border-color: {p.border}; }}
QLineEdit[invalid="true"] {{ border-color: {p.critical}; }}
QLabel:disabled {{ color: {p.muted}; }}
QPushButton[variant="select"]:disabled {{ background: {p.page}; border-color: {p.border}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView, QListView#completerPopup {{ background: {p.raised};
    border: 1px solid {p.border_strong}; selection-background-color: {p.accent_soft};
    selection-color: {p.text}; padding: 4px; outline: none; }}

/* ---- tables ------------------------------------------------------------ */
QTableWidget {{ background: transparent; border: none; gridline-color: transparent;
    selection-background-color: {p.accent_soft}; selection-color: {p.text}; }}
QTableWidget::item {{ border-bottom: 1px solid {p.border}; padding: 0px 8px; }}
QTableWidget::item:hover {{ background: {p.hover}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {p.raised}; color: {p.text_secondary}; border: none;
    border-bottom: 1px solid {p.border}; padding: 7px 8px; font-size: 12px; }}

/* ---- menus ------------------------------------------------------------- */
QMenu {{ background: {p.raised}; border: 1px solid {p.border_strong}; border-radius: 8px;
    padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}

/* ---- scrolling --------------------------------------------------------- */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QCheckBox {{ color: {p.text_secondary}; spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {p.border_strong}; background: {p.surface}; }}
QCheckBox::indicator:hover {{ border-color: {p.muted}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent};
    image: none; }}

QSlider::groove:horizontal {{ height: 4px; background: {p.raised}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: {p.text}; border: 2px solid {p.accent}; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}

QLabel[role="metricCaption"] {{ color: {p.text_secondary}; font-size: 12px; }}
QLabel[role="metricValue"] {{ font-family: "{display}"; font-size: 20px; font-weight: 600; }}
QLabel[role="metricValue"][tone="good"] {{ color: {p.good}; }}
QLabel[role="metricValue"][tone="critical"] {{ color: {p.critical}; }}
QLabel[role="metricHint"] {{ color: {p.muted}; font-size: 11px; }}

QPlainTextEdit#log {{ background: transparent; border: none; color: {p.text_secondary};
    selection-background-color: {p.accent}; }}
"""


def apply_theme(app: QApplication) -> None:
    """Install the style, palette, stylesheet and pyqtgraph defaults. Call once."""
    p = PALETTE
    # Fusion is the one built-in style that fully honors stylesheets and a
    # custom QPalette identically on every platform.
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.Window, p.page), (QPalette.Base, p.surface),
        (QPalette.AlternateBase, p.raised), (QPalette.Text, p.text),
        (QPalette.WindowText, p.text), (QPalette.Button, p.raised),
        (QPalette.ButtonText, p.text), (QPalette.Highlight, p.accent),
        (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, p.raised),
        (QPalette.ToolTipText, p.text), (QPalette.PlaceholderText, p.muted),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet(p))
    # mouseRateLimit=0: pyqtgraph otherwise *drops* (not defers) mouse moves
    # closer than 10 ms apart, so the crosshair can stop one step behind the
    # cursor. Our hover handlers are a binary search + setPos, and Qt already
    # coalesces queued mouse moves, so no limit is needed.
    pg.setConfigOptions(antialias=True, background=p.surface, foreground=p.muted,
                        mouseRateLimit=0)
