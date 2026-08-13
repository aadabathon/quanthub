"""Price-history chart rendering.

Design rules applied (from the data-viz method the theme palette comes from):
single series so no legend (the title names it); a thin 1.8px line in the
series color; recessive hairline grid and muted axis ink; text always in text
colors, never the series color; one selective direct label (the last price)
instead of numbers on every point.

Functions take a ``Figure`` and draw into it so the GUI can own one embedded
figure and redraw it in place, avoiding per-refresh figure/canvas leaks.
"""

from __future__ import annotations

from dataclasses import dataclass

from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure

from quant_hub.data.types import PriceHistory


@dataclass(frozen=True)
class ChartStyle:
    """Colors/fonts for chart rendering, supplied by the caller (the GUI maps
    its theme palette into this; notebooks can use the defaults)."""

    surface: str = "#1a1a19"
    text: str = "#ffffff"
    text_secondary: str = "#c3c2b7"
    muted: str = "#898781"
    grid: str = "#2c2c2a"
    baseline: str = "#383835"
    line: str = "#3987e5"
    font_family: str = "Segoe UI"


def create_figure(style: ChartStyle | None = None) -> Figure:
    """A themed, empty figure ready for ``plot_price_history``."""
    style = style or ChartStyle()
    fig = Figure(figsize=(8, 4.2), dpi=100, layout="constrained")
    fig.patch.set_facecolor(style.surface)
    return fig


def plot_price_history(
    fig: Figure, history: PriceHistory, style: ChartStyle | None = None
) -> None:
    """Redraw ``fig`` with the close-price line for ``history``."""
    style = style or ChartStyle()
    fig.clear()
    ax = fig.add_subplot(111)

    ax.set_facecolor(style.surface)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(style.baseline)

    ax.grid(True, axis="y", color=style.grid, linewidth=0.8)
    ax.set_axisbelow(True)

    ax.plot(history.frame.index, history.closes, color=style.line, linewidth=1.8)

    # Price axis on the right, terminal-style.
    ax.yaxis.tick_right()
    ax.tick_params(colors=style.muted, labelsize=9, length=0)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontfamily(style.font_family)

    locator = AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))

    ax.set_title(
        f"{history.symbol} · close · {history.source}",
        loc="left",
        color=style.text_secondary,
        fontsize=10,
        fontfamily=style.font_family,
    )

    # Selective direct label: the latest close, at the end of the line.
    last_ts = history.frame.index[-1]
    last_price = float(history.closes.iloc[-1])
    ax.annotate(
        f" {last_price:,.2f}",
        xy=(last_ts, last_price),
        xytext=(2, 0),
        textcoords="offset points",
        color=style.text,
        fontsize=9,
        fontfamily="Consolas",
        va="center",
    )
    ax.margins(x=0.01)
