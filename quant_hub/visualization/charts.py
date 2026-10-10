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
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.dates import AutoDateLocator, ConciseDateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

from quant_hub.data.simulation import GBMSimulationResult, MonteCarloSummary
from quant_hub.data.stats import SeriesAnalysis
from quant_hub.data.types import PriceHistory
from quant_hub.models.option_monte_carlo import MonteCarloPricing
from quant_hub.models.vol_surface import SmileCurves


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
    # Ordinal ramp of the series hue for ordered curves (e.g. time to expiry):
    # slot 0 is the primary series, later slots step lighter. One hue with
    # monotone lightness, validated on the dark surface (--ordinal).
    ramp: tuple[str, ...] = ("#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb")
    # Sequential ramp for magnitude (heatmaps), dark→light on the dark
    # surface so low values recede; one hue, validated (--ordinal).
    sequential: tuple[str, ...] = ("#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb")
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

def plot_garch_overview(
    fig: Figure,
    returns,
    fit,
    style: ChartStyle | None = None,
    periods_per_year: int = 252,
) -> None:
    """Draw observed returns, fitted annualized volatility, and residuals.

    ``returns`` and ``fit`` are computed by the model layer before rendering.
    The only arithmetic here is volatility's presentation conversion from
    per-period variance to annualized standard deviation.
    """
    style = style or ChartStyle()
    fig.clear()
    grid = fig.add_gridspec(3, 1, height_ratios=[2, 3, 2])
    ax_returns = fig.add_subplot(grid[0])
    ax_vol = fig.add_subplot(grid[1], sharex=ax_returns)
    ax_residuals = fig.add_subplot(grid[2])

    _style_axes(ax_returns, style, right_ticks=True)
    ax_returns.plot(returns.index, returns.to_numpy(), color=style.line, linewidth=0.7)
    ax_returns.axhline(0.0, color=style.baseline, linewidth=0.8)
    ax_returns.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=1))
    _title(ax_returns, "log returns", style)
    ax_returns.tick_params(labelbottom=False)

    _style_axes(ax_vol, style, right_ticks=True)
    annualized = np.sqrt(fit.conditional_variances * periods_per_year)
    ax_vol.plot(returns.index, annualized, color=style.line, linewidth=1.3)
    long_run = math.sqrt(fit.parameters.unconditional_variance * periods_per_year)
    ax_vol.axhline(long_run, color=style.text_secondary, linewidth=1.0, linestyle="--")
    ax_vol.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    _date_axis(ax_vol, style)
    _title(ax_vol, "conditional volatility · solid: fitted · dashed: long-run", style)
    ax_vol.margins(x=0.01)

    _style_axes(ax_residuals, style)
    ax_residuals.hist(
        fit.standardized_residuals,
        bins=min(60, max(10, len(fit.standardized_residuals) // 8)),
        density=True,
        color=style.line,
        alpha=0.75,
        edgecolor=style.surface,
        linewidth=0.4,
    )
    x = np.linspace(-4.0, 4.0, 200)
    ax_residuals.plot(x, np.exp(-0.5 * x**2) / math.sqrt(2.0 * math.pi), color=style.text_secondary, linewidth=1.1, linestyle="--")
    ax_residuals.set_yticks([])
    ax_residuals.grid(False)
    _title(ax_residuals, "standardized residuals · dashed: N(0, 1)", style)


# --------------------------------------------------------------------------
# Options Lab
# --------------------------------------------------------------------------

def _legend(ax: Axes, style: ChartStyle, loc: str = "upper left") -> None:
    legend = ax.legend(loc=loc, frameon=False, fontsize=8)
    for text in legend.get_texts():
        text.set_color(style.text_secondary)
        text.set_fontfamily(style.font_family)


def _reference_line(ax: Axes, x: float, label: str, style: ChartStyle, tier: int = 0) -> None:
    """Dotted vertical marker labelled at the top; ``tier`` stacks labels of
    nearby markers (spot and strike are often a few pixels apart)."""
    ax.axvline(x, color=style.muted, linewidth=0.8, linestyle=":")
    ax.annotate(
        label, xy=(x, 1.0), xycoords=("data", "axes fraction"), xytext=(3, -10 - 11 * tier),
        textcoords="offset points", color=style.muted, fontsize=8,
        fontfamily=style.font_family,
    )


def plot_option_profile(
    fig: Figure,
    spots: np.ndarray,
    curves: list[tuple[str, np.ndarray]],
    metric: str,
    spot: float,
    strike: float,
    style: ChartStyle | None = None,
) -> None:
    """One option metric vs spot, one curve per time-to-expiry.

    ``curves`` is ordered from the contract's own expiry toward expiry day;
    colors step along the ordinal ramp in that order (time is ordered, so
    one hue light→dark, not categorical hues).
    """
    style = style or ChartStyle()
    fig.clear()
    ax = fig.add_subplot()
    _style_axes(ax, style, right_ticks=True)
    for i, (label, values) in enumerate(curves):
        color = style.ramp[min(i, len(style.ramp) - 1)]
        ax.plot(spots, values, color=color, linewidth=2.0 if i == 0 else 1.4, label=label)
    ax.axhline(0.0, color=style.baseline, linewidth=0.8)
    _reference_line(ax, spot, "spot", style)
    _reference_line(ax, strike, "strike", style, tier=1)
    ax.margins(x=0.02)
    _title(ax, f"{metric} vs spot · by days to expiry", style)
    _legend(ax, style)


def plot_strategy_pnl(
    fig: Figure,
    spots: np.ndarray,
    expiry_pnl: np.ndarray,
    expiry_label: str,
    spot: float,
    breakevens: tuple[float, ...],
    horizon_pnl: np.ndarray | None = None,
    horizon_label: str = "",
    style: ChartStyle | None = None,
) -> None:
    """Strategy P&L vs scenario spot: at expiry, and optionally at a horizon."""
    style = style or ChartStyle()
    fig.clear()
    ax = fig.add_subplot()
    _style_axes(ax, style, right_ticks=True)
    ax.axhline(0.0, color=style.baseline, linewidth=1.0)
    ax.fill_between(spots, expiry_pnl, 0.0, color=style.ramp[0], alpha=0.12, linewidth=0)
    ax.plot(spots, expiry_pnl, color=style.ramp[0], linewidth=2.0, label=expiry_label)
    if horizon_pnl is not None:
        ax.plot(spots, horizon_pnl, color=style.ramp[2], linewidth=1.4, label=horizon_label)
    for level in breakevens:
        if spots[0] <= level <= spots[-1]:
            ax.plot([level], [0.0], marker="o", markersize=5, color=style.text,
                    markeredgecolor=style.surface, markeredgewidth=2)
            ax.annotate(
                f"{level:,.2f}", xy=(level, 0.0), xytext=(0, -14), textcoords="offset points",
                ha="center", color=style.text_secondary, fontsize=8,
                fontfamily=style.mono_family,
            )
    _reference_line(ax, spot, "spot", style)
    ax.yaxis.set_major_formatter(lambda value, _pos: f"${value:,.0f}")
    ax.margins(x=0.02)
    _title(ax, "P&L vs underlying price · dots: breakevens", style)
    _legend(ax, style)


def plot_monte_carlo_pricing(
    fig: Figure, result: MonteCarloPricing, style: ChartStyle | None = None
) -> None:
    """Convergence of the MC estimate to the closed form, plus the terminal law."""
    style = style or ChartStyle()
    fig.clear()
    grid = fig.add_gridspec(1, 2, width_ratios=[3, 2])
    ax_conv = fig.add_subplot(grid[0])
    ax_dist = fig.add_subplot(grid[1])

    _style_axes(ax_conv, style, right_ticks=True)
    n = result.checkpoints
    band = 1.96 * result.running_standard_error
    ax_conv.fill_between(
        n, result.running_estimate - band, result.running_estimate + band,
        color=style.line, alpha=0.18, linewidth=0, label="95% interval",
    )
    ax_conv.plot(n, result.running_estimate, color=style.line, linewidth=1.6, label="MC estimate")
    ax_conv.axhline(result.closed_form, color=style.text_secondary, linewidth=1.0,
                    linestyle="--", label="closed form")
    ax_conv.set_xscale("log")
    ax_conv.xaxis.set_major_formatter(lambda value, _pos: f"{value:,.0f}")
    ax_conv.set_xlabel("paths", color=style.muted, fontsize=8)
    _title(ax_conv, "estimate vs paths · band narrows like 1/√n", style)
    _legend(ax_conv, style)

    _style_axes(ax_dist, style)
    terminal = result.terminal_prices[:200_000]
    ax_dist.hist(terminal, bins=80, density=True, color=style.line, alpha=0.75,
                 edgecolor=style.surface, linewidth=0.4)
    _reference_line(ax_dist, result.inputs.strike, "strike", style)
    ax_dist.set_yticks([])
    ax_dist.grid(False)
    _title(ax_dist, "risk-neutral terminal price", style)


# --------------------------------------------------------------------------
# Volatility Lab
# --------------------------------------------------------------------------

def plot_smiles(
    fig: Figure, curves: list[SmileCurves], style: ChartStyle | None = None, max_panels: int = 6
) -> None:
    """Small multiples: one smile per expiry (implied vol vs log-moneyness).

    Per panel: bid–ask IV range (muted bars), mid IVs (dots), fitted SVI
    (series line), and the true smile when known (dashed). Each panel owns
    its y-scale: short expiries sit far higher, and a shared scale would
    flatten them.
    """
    style = style or ChartStyle()
    fig.clear()
    shown = curves[:max_panels]
    if not shown:
        plot_message(fig, "no usable quotes", style)
        return
    cols = min(3, len(shown))
    rows = math.ceil(len(shown) / cols)
    for i, curve in enumerate(shown):
        ax = fig.add_subplot(rows, cols, i + 1)
        _style_axes(ax, style)
        low = np.where(np.isnan(curve.quote_iv_bid), curve.quote_iv, curve.quote_iv_bid)
        high = np.where(np.isnan(curve.quote_iv_ask), curve.quote_iv, curve.quote_iv_ask)
        ax.vlines(curve.quote_k, low, high, color=style.muted, linewidth=1.0,
                  label="bid–ask IV" if i == 0 else None)
        ax.plot(curve.quote_k, curve.quote_iv, "o", markersize=3.5, color=style.text,
                markeredgewidth=0, label="mid IV" if i == 0 else None)
        if curve.true_iv is not None:
            ax.plot(curve.k_grid, curve.true_iv, color=style.text_secondary, linewidth=1.0,
                    linestyle="--", label="true" if i == 0 else None)
        if curve.fitted_iv is not None:
            ax.plot(curve.k_grid, curve.fitted_iv, color=style.line, linewidth=1.8,
                    label="SVI fit" if i == 0 else None)
        ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
        ax.axvline(0.0, color=style.baseline, linewidth=0.8)
        _title(ax, f"{curve.dte:g}d", style)
        if i == 0:
            # Equity skew leaves the upper right (OTM calls) emptiest.
            _legend(ax, style, loc="upper right")
    fig.supxlabel("log-moneyness  ln(K/F)", color=style.muted, fontsize=8,
                  fontfamily=style.font_family)


def plot_vol_term_structure(
    fig: Figure,
    fitted_days: np.ndarray,
    fitted_atm: np.ndarray,
    curve_days: np.ndarray | None = None,
    true_atm: np.ndarray | None = None,
    garch_vol: np.ndarray | None = None,
    garch_label: str = "",
    realized: list[tuple[float, float]] | None = None,
    style: ChartStyle | None = None,
) -> None:
    """ATM implied vol by expiry, against the true curve and model forecasts.

    ``curve_days`` is the shared x-grid for ``true_atm``/``garch_vol``;
    ``realized`` is ``[(calendar days, vol)]`` trailing realized vols.
    """
    style = style or ChartStyle()
    fig.clear()
    ax = fig.add_subplot()
    _style_axes(ax, style, right_ticks=True)
    if curve_days is not None and true_atm is not None:
        ax.plot(curve_days, true_atm, color=style.text_secondary, linewidth=1.0, linestyle="--",
                label="true ATM vol")
    if curve_days is not None and garch_vol is not None:
        ax.plot(curve_days, garch_vol, color=style.ramp[2], linewidth=1.6, label=garch_label)
    if realized:
        days, vols = zip(*realized)
        ax.plot(days, vols, linestyle="none", marker="D", markersize=7, color=style.ramp[3],
                markeredgecolor=style.surface, markeredgewidth=1.5, label="realized (trailing)",
                zorder=4)
    ax.plot(fitted_days, fitted_atm, color=style.line, linewidth=1.8, marker="o", markersize=6,
            markeredgecolor=style.surface, markeredgewidth=1.5, label="ATM IV (SVI fit)")
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
    ax.set_xlabel("days to expiry", color=style.muted, fontsize=8)
    _title(ax, "at-the-money volatility term structure", style)
    _legend(ax, style, loc="best")


def plot_vol_heatmap(
    fig: Figure, z: np.ndarray, dte: np.ndarray, iv: np.ndarray, style: ChartStyle | None = None
) -> None:
    """Fitted implied vol over (standardized moneyness, expiry)."""
    style = style or ChartStyle()
    fig.clear()
    ax = fig.add_subplot()
    _style_axes(ax, style)
    ax.grid(False)
    cmap = LinearSegmentedColormap.from_list("iv", list(style.sequential))
    # Rows are expiries as ordered categories (equal height), columns z.
    mesh = ax.pcolormesh(z, np.arange(len(dte)), iv, cmap=cmap, shading="nearest")
    ax.set_yticks(np.arange(len(dte)))
    ax.set_yticklabels([f"{d:g}d" for d in dte])
    ax.axvline(0.0, color=style.surface, linewidth=1.0)
    ax.set_xlabel("moneyness in ATM standard deviations  k/√θ", color=style.muted, fontsize=8)
    bar = fig.colorbar(mesh, ax=ax, format=PercentFormatter(xmax=1.0, decimals=0), pad=0.02)
    bar.outline.set_visible(False)
    bar.ax.tick_params(colors=style.muted, labelsize=8, length=0)
    _title(ax, "fitted implied volatility surface", style)
