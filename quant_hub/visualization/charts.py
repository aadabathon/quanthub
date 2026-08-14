"""Reusable matplotlib rendering for the analytics pipeline.

Design rules applied throughout (from the data-viz method the theme palette
comes from): thin lines in the single series color; recessive hairline grids
and muted axis ink; text always in text colors, never the series color;
selective direct labels instead of numbers on every point; status colors are
*never* used for chart series.

Layering: this module renders — it computes nothing. Every quantity drawn
here (returns, rolling vol, drawdowns, distribution moments, Monte Carlo
percentiles) arrives precomputed inside ``SeriesAnalysis`` /
``GBMSimulationResult`` / ``MonteCarloSummary`` from the data layer. The only
arithmetic permitted is presentation math (the normal-pdf curve drawn from
supplied moments, axis limits, subsampling paths for legibility).

All public functions take a ``Figure``, clear it, and redraw in place, so the
GUI can own one long-lived embedded figure per canvas and never leak
figures/canvases across refreshes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from matplotlib.axes import Axes
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

from quant_hub.data.simulation import GBMSimulationResult, MonteCarloSummary
from quant_hub.data.stats import SeriesAnalysis
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
    mono_family: str = "Consolas"


def create_figure(style: ChartStyle | None = None) -> Figure:
    """A themed, empty figure ready for any ``plot_*`` function below."""
    style = style or ChartStyle()
    fig = Figure(figsize=(8, 4.2), dpi=100, layout="constrained")
    fig.patch.set_facecolor(style.surface)
    return fig


# --------------------------------------------------------------------------
# Shared axis styling
# --------------------------------------------------------------------------

def _style_axes(ax: Axes, style: ChartStyle, right_ticks: bool = False) -> None:
    ax.set_facecolor(style.surface)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(style.baseline)
    ax.grid(True, axis="y", color=style.grid, linewidth=0.8)
    ax.set_axisbelow(True)
    if right_ticks:
        ax.yaxis.tick_right()
    ax.tick_params(colors=style.muted, labelsize=8, length=0)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontfamily(style.font_family)


def _date_axis(ax: Axes, style: ChartStyle) -> None:
    locator = AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(ConciseDateFormatter(locator))
    for label in ax.get_xticklabels():
        label.set_fontfamily(style.font_family)


def _title(ax: Axes, text: str, style: ChartStyle) -> None:
    ax.set_title(
        text,
        loc="left",
        color=style.text_secondary,
        fontsize=9,
        fontfamily=style.font_family,
    )


# --------------------------------------------------------------------------
# Axes-level renderers (composed by the figure-level functions)
# --------------------------------------------------------------------------

def _draw_price(ax: Axes, history: PriceHistory, style: ChartStyle) -> None:
    _style_axes(ax, style, right_ticks=True)
    ax.plot(history.frame.index, history.closes, color=style.line, linewidth=1.6)
    _date_axis(ax, style)
    _title(ax, f"{history.symbol} · close · {history.source}", style)

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
        fontfamily=style.mono_family,
        va="center",
    )
    ax.margins(x=0.01)


def _draw_drawdown(ax: Axes, analysis: SeriesAnalysis, style: ChartStyle) -> None:
    _style_axes(ax, style, right_ticks=True)
    dd = analysis.drawdowns
    ax.fill_between(dd.index, dd, 0.0, color=style.line, alpha=0.25, linewidth=0)
    ax.plot(dd.index, dd, color=style.line, linewidth=1.0)
    ax.axhline(0.0, color=style.baseline, linewidth=0.8)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    _date_axis(ax, style)
    _title(ax, f"drawdown · max {analysis.stats.max_drawdown:.1%}", style)
    ax.margins(x=0.01)


def _draw_distribution(
    ax: Axes, analysis: SeriesAnalysis, style: ChartStyle, return_kind: str
) -> None:
    """Histogram of periodic returns with an optional N(mean, std) overlay.

    The overlay uses the *sample* moments computed in the data layer; drawing
    density = counts normalization is handled by ``density=True`` so the pdf
    curve and the bars share units.
    """
    if return_kind == "log":
        returns, mean, std = (
            analysis.log_returns,
            analysis.stats.mean_log_return,
            analysis.stats.std_log_return,
        )
    elif return_kind == "simple":
        returns, mean, std = (
            analysis.simple_returns,
            analysis.stats.mean_simple_return,
            analysis.stats.std_simple_return,
        )
    else:
        raise ValueError(f"return_kind must be 'log' or 'simple', got {return_kind!r}")

    _style_axes(ax, style)
    ax.hist(
        returns,
        bins=min(60, max(10, len(returns) // 8)),
        density=True,
        color=style.line,
        alpha=0.75,
        edgecolor=style.surface,
        linewidth=0.4,
    )
    if np.isfinite(std) and std > 0:
        x = np.linspace(float(returns.min()), float(returns.max()), 200)
        pdf = np.exp(-0.5 * ((x - mean) / std) ** 2) / (std * math.sqrt(2 * math.pi))
        ax.plot(x, pdf, color=style.text_secondary, linewidth=1.2, linestyle="--")
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=1))
    ax.set_yticks([])
    ax.grid(False)
    _title(ax, f"{return_kind} return distribution · normal overlay", style)


def _draw_rolling_vol(
    ax: Axes, analysis: SeriesAnalysis, style: ChartStyle, true_sigma: float | None
) -> None:
    _style_axes(ax, style, right_ticks=True)
    vol = analysis.rolling_vol
    if len(vol) > 0:
        ax.plot(vol.index, vol, color=style.line, linewidth=1.4)
        _date_axis(ax, style)
    else:
        ax.text(
            0.5, 0.5, "window exceeds sample",
            transform=ax.transAxes, ha="center",
            color=style.muted, fontsize=9, fontfamily=style.font_family,
        )
    if true_sigma is not None:
        ax.axhline(true_sigma, color=style.text_secondary, linewidth=1.0, linestyle="--")
        ax.annotate(
            f" σ true {true_sigma:.0%}",
            xy=(0.0, true_sigma),
            xycoords=("axes fraction", "data"),
            xytext=(2, 3),
            textcoords="offset points",
            color=style.text_secondary,
            fontsize=8,
            fontfamily=style.font_family,
        )
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    _title(
        ax,
        f"rolling volatility · {analysis.vol_window}d window · annualized ×√{analysis.periods_per_year}",
        style,
    )
    ax.margins(x=0.01)


# --------------------------------------------------------------------------
# Figure-level compositions (what the GUI calls)
# --------------------------------------------------------------------------

def plot_price_history(
    fig: Figure, history: PriceHistory, style: ChartStyle | None = None
) -> None:
    """Redraw ``fig`` with the close-price line for ``history``."""
    style = style or ChartStyle()
    fig.clear()
    _draw_price(fig.add_subplot(111), history, style)


def plot_lab_overview(
    fig: Figure,
    analysis: SeriesAnalysis,
    style: ChartStyle | None = None,
    return_kind: str = "log",
    true_sigma: float | None = None,
) -> None:
    """The dashboard's main composition: price, drawdown, distribution,
    rolling volatility — all rendered from one ``SeriesAnalysis``.

    ``true_sigma`` (the simulation's σ, when known) draws a reference line on
    the rolling-volatility panel so sampling noise around the truth is
    visible; pass None for observed (non-simulated) data.
    """
    style = style or ChartStyle()
    fig.clear()
    grid = fig.add_gridspec(3, 2, height_ratios=[5, 2, 4])
    ax_price = fig.add_subplot(grid[0, :])
    ax_dd = fig.add_subplot(grid[1, :], sharex=ax_price)
    ax_dist = fig.add_subplot(grid[2, 0])
    ax_vol = fig.add_subplot(grid[2, 1])

    _draw_price(ax_price, analysis.history, style)
    ax_price.tick_params(labelbottom=False)
    _draw_drawdown(ax_dd, analysis, style)
    _draw_distribution(ax_dist, analysis, style, return_kind)
    _draw_rolling_vol(ax_vol, analysis, style, true_sigma)


def plot_monte_carlo(
    fig: Figure,
    result: GBMSimulationResult,
    summary: MonteCarloSummary,
    style: ChartStyle | None = None,
    max_paths_drawn: int = 60,
) -> None:
    """Monte Carlo composition: a fan of sample paths (subsampled for
    legibility) beside the terminal-price distribution with its closed-form
    references.

    Left: the first ``max_paths_drawn`` paths plus the closed-form expectation
    curve :math:`S_0 e^{\\mu t}`. Right: histogram of :math:`S_T` over *all*
    paths with the sample mean and the 5th/95th percentiles marked, plus the
    theoretical mean — mean sits above median because :math:`S_T` is
    lognormal.
    """
    style = style or ChartStyle()
    fig.clear()
    grid = fig.add_gridspec(1, 2, width_ratios=[3, 2])
    ax_fan = fig.add_subplot(grid[0, 0])
    ax_term = fig.add_subplot(grid[0, 1])

    # -- fan of paths ------------------------------------------------------
    _style_axes(ax_fan, style, right_ticks=True)
    shown = min(result.n_paths, max_paths_drawn)
    for path in result.paths[:shown]:
        ax_fan.plot(result.dates, path, color=style.line, linewidth=0.6, alpha=0.28)
    ax_fan.plot(
        result.dates,
        result.expected_price_curve(),
        color=style.text_secondary,
        linewidth=1.3,
        linestyle="--",
    )
    _date_axis(ax_fan, style)
    p = result.params
    _title(
        ax_fan,
        f"{shown}/{result.n_paths} paths · μ={p.mu:.0%} σ={p.sigma:.0%} "
        f"T={result.horizon_years:.2g}y · dashed: $E[S_t]=S_0e^{{\\mu t}}$",
        style,
    )
    ax_fan.margins(x=0.01)

    # -- terminal distribution --------------------------------------------
    _style_axes(ax_term, style)
    ax_term.hist(
        result.terminal_prices,
        bins=min(60, max(10, result.n_paths // 12)),
        density=True,
        color=style.line,
        alpha=0.75,
        edgecolor=style.surface,
        linewidth=0.4,
    )
    ax_term.axvline(summary.expected, color=style.text_secondary, linewidth=1.2, linestyle="--")
    ax_term.axvline(summary.mean, color=style.text, linewidth=1.0)
    for level in (5, 95):
        if level in summary.percentiles:
            ax_term.axvline(
                summary.percentiles[level],
                color=style.muted,
                linewidth=0.9,
                linestyle=":",
            )
    ax_term.set_yticks([])
    ax_term.grid(False)
    _title(
        ax_term,
        "terminal price S(T) · solid: sample mean · dashed: E[S(T)] · dotted: p5/p95",
        style,
    )


def plot_message(fig: Figure, message: str, style: ChartStyle | None = None) -> None:
    """Clear ``fig`` and display a muted centered message (empty states)."""
    style = style or ChartStyle()
    fig.clear()
    fig.text(
        0.5, 0.5, message,
        ha="center", va="center",
        color=style.muted, fontsize=10, fontfamily=style.font_family,
    )
