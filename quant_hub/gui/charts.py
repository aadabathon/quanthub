"""Interactive charts on pyqtgraph: price + volume, sparkline, forecast fan,
stacked time series, and histogram with a density overlay.

Same rules as ``visualization/charts.py``: these widgets render, they never
compute. Every series arrives precomputed (closes, volumes, percentile
bands); the only arithmetic here is presentation — axis limits, converting
dates to epoch seconds, subsampling paths for legibility, and snapping the
crosshair to the nearest bar.

Why pyqtgraph (D31): it draws through Qt's scene graph, so a redraw costs a
few milliseconds instead of matplotlib's ~100 ms Agg rasterization. That is
what makes the crosshair track the mouse at frame rate.

Hover layer: every chart with a plot gets a snapped crosshair and a readout
line above the plot (date + values in text ink), per the data-viz method.
Charts are view-only — range is driven by the page's controls, so mouse pan/
zoom and pyqtgraph's context menu are disabled.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGradient, QLinearGradient, QPen
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from quant_hub.gui.components import label
from quant_hub.gui.theme import PALETTE, SPACE_XS, font_family, FONT_UI

_DAY_S = 86_400
_TICK_FONT_PX = 11
_SHADE_ALPHA = 0.32   # a background tint's opacity at value 1 (Shade)
_STACK_ALPHA = 0.5    # fill opacity of a stacked panel's areas


# ----------------------------------------------------------------- helpers

def epoch_seconds(index: pd.DatetimeIndex) -> np.ndarray:
    """Naive session dates -> float seconds, read as UTC so the date axis
    (built with ``utcOffset=0``) shows the same calendar day back."""
    return index.values.astype("datetime64[s]").astype(np.int64).astype(float)


def compact_number(value: float) -> str:
    for threshold, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= threshold:
            return f"{value / threshold:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.0f}"


def _color(hex_color: str, alpha: float = 1.0) -> QColor:
    color = QColor(hex_color)
    color.setAlphaF(alpha)
    return color


def _vertical_gradient(hex_color: str, top_alpha: float) -> QBrush:
    """Fill that fades from ``top_alpha`` at the series' high to clear at its
    low. ObjectBoundingMode spans the fill path's own bounding box; in item
    coordinates y grows with price, so stop 1 is the top on screen."""
    gradient = QLinearGradient(0, 0, 0, 1)
    gradient.setCoordinateMode(QGradient.ObjectBoundingMode)
    gradient.setColorAt(0.0, _color(hex_color, 0.0))
    gradient.setColorAt(1.0, _color(hex_color, top_alpha))
    return QBrush(gradient)


class _ValueAxis(pg.AxisItem):
    """Value axis formatting: ``price`` (1,234.5), ``money`` ($1,234),
    ``compact`` (300M) or ``percent`` (fractions shown as 12% / 1.5%)."""

    def __init__(self, orientation: str, kind: str = "price") -> None:
        super().__init__(orientation)
        self._kind = kind

    def logTickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph API)
        # Log mode passes exponents. Label whole decades only (10, 100, 1K):
        # the minor ticks between them would otherwise crowd the axis.
        return [compact_number(10 ** v) if abs(v - round(v)) < 1e-6 else "" for v in values]

    def tickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph API)
        if self.logMode:   # pyqtgraph's own tickStrings does this routing
            return self.logTickStrings(values, scale, spacing)
        if self._kind == "money":
            decimals = 0 if spacing >= 1 else 2
            return [f"${v:,.{decimals}f}" if v >= 0 else f"−${-v:,.{decimals}f}"
                    for v in values]
        if self._kind == "compact":
            return [compact_number(v) for v in values]
        if self._kind == "percent":
            decimals = 0 if spacing >= 0.01 else 1
            return [f"{v:.{decimals}%}" for v in values]
        decimals = 0 if spacing >= 1 else 2
        return [f"{v:,.{decimals}f}" for v in values]


def _style_axis(axis: pg.AxisItem) -> None:
    axis.setPen(pg.mkPen(PALETTE.grid))
    axis.setTextPen(pg.mkPen(PALETTE.muted))
    font = pg.QtGui.QFont(font_family(FONT_UI))
    font.setPixelSize(_TICK_FONT_PX)
    axis.setTickFont(font)
    axis.setStyle(tickLength=0, tickTextOffset=8)


def _view_only(plot: pg.PlotItem) -> None:
    plot.setMouseEnabled(x=False, y=False)
    plot.hideButtons()
    plot.setMenuEnabled(False)
    plot.vb.setDefaultPadding(0.0)
    for name in ("left", "right", "bottom", "top"):
        _style_axis(plot.getAxis(name))


def _crosshair_pen() -> QPen:
    pen = pg.mkPen(_color(PALETTE.text_secondary, 0.45), width=1)
    pen.setStyle(Qt.DashLine)
    return pen


def _readout_label() -> QLabel:
    """Hover readout above a plot. Rich text can't elide, so it may clip on
    a narrow card instead of widening it."""
    readout = label("", "readout")
    readout.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return readout


class _Canvas(pg.GraphicsLayoutWidget):
    """GraphicsLayoutWidget that reports when the mouse leaves it."""

    left = Signal()

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self.left.emit()
        super().leaveEvent(event)


# ------------------------------------------------------------ price chart

class PriceVolumeChart(QWidget):
    """Area chart of closes over linked volume bars, with a last-price tag
    and a snapped crosshair readout."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)

        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        self._canvas.ci.setSpacing(2)
        layout.addWidget(self._canvas, 1)

        self._price = self._canvas.addPlot(
            row=0, col=0,
            axisItems={"bottom": pg.DateAxisItem("bottom", utcOffset=0),
                       "right": _ValueAxis("right")},
        )
        self._volume = self._canvas.addPlot(
            row=1, col=0,
            axisItems={"bottom": pg.DateAxisItem("bottom", utcOffset=0),
                       "right": _ValueAxis("right", "compact")},
        )
        self._canvas.ci.layout.setRowStretchFactor(0, 4)
        self._canvas.ci.layout.setRowStretchFactor(1, 1)
        for plot in (self._price, self._volume):
            _view_only(plot)
            plot.hideAxis("left")
            plot.showAxis("right")
            plot.getAxis("right").setWidth(58)
            plot.showGrid(x=False, y=True, alpha=0.9)
        self._price.hideAxis("bottom")
        self._volume.setXLink(self._price)

        self._curve = pg.PlotDataItem(pen=pg.mkPen(PALETTE.series, width=2))
        self._price.addItem(self._curve)
        self._bars: pg.BarGraphItem | None = None
        self._last_line = pg.InfiniteLine(
            angle=0, movable=False,
            pen=pg.mkPen(_color(PALETTE.series, 0.55), width=1, style=Qt.DotLine),
            label="", labelOpts={"position": 1.0, "color": "#ffffff",
                                 "fill": PALETTE.series, "anchors": [(1, 0.5), (1, 0.5)]},
        )
        self._price.addItem(self._last_line, ignoreBounds=True)

        pen = _crosshair_pen()
        self._vline = pg.InfiniteLine(angle=90, pen=pen, movable=False)
        self._vline_vol = pg.InfiniteLine(angle=90, pen=pen, movable=False)
        self._hline = pg.InfiniteLine(angle=0, pen=pen, movable=False)
        self._marker = pg.ScatterPlotItem(size=9, brush=pg.mkBrush(PALETTE.series),
                                          pen=pg.mkPen(PALETTE.surface, width=2))
        for item, plot in ((self._vline, self._price), (self._hline, self._price),
                           (self._marker, self._price), (self._vline_vol, self._volume)):
            plot.addItem(item, ignoreBounds=True)
        self._set_crosshair_visible(False)

        self._x = np.empty(0)
        self._close = np.empty(0)
        self._vol: np.ndarray | None = None
        self._dates: pd.DatetimeIndex | None = None
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def set_series(self, dates: pd.DatetimeIndex, close: np.ndarray,
                   volume: np.ndarray | None) -> None:
        self._dates = dates
        self._x = epoch_seconds(dates)
        self._close = np.asarray(close, dtype=float)
        has_volume = volume is not None and np.isfinite(volume).any()
        self._vol = np.asarray(volume, dtype=float) if has_volume else None

        low, high = float(self._close.min()), float(self._close.max())
        pad = max((high - low) * 0.08, high * 0.002)
        floor = low - pad
        self._curve.setData(self._x, self._close, fillLevel=floor,
                            brush=_vertical_gradient(PALETTE.series, 0.38))
        self._price.setYRange(floor, high + pad, padding=0)
        self._price.setXRange(self._x[0], self._x[-1] + _DAY_S, padding=0)
        self._last_line.setPos(self._close[-1])
        self._last_line.label.setFormat(f" {self._close[-1]:,.2f} ")

        if self._bars is not None:
            self._volume.removeItem(self._bars)
            self._bars = None
        self._volume.setVisible(self._vol is not None)
        if self._vol is not None:
            span_days = max((self._x[-1] - self._x[0]) / _DAY_S, 1.0)
            width = _DAY_S * (0.7 if span_days < 400 else 1.2)
            self._bars = pg.BarGraphItem(
                x=self._x, height=np.nan_to_num(self._vol), width=width,
                brush=_color(PALETTE.series, 0.55), pen=pg.mkPen(None),
            )
            self._volume.addItem(self._bars)
            self._volume.setYRange(0, float(np.nanmax(self._vol)) * 1.1, padding=0)
        self._show_readout(len(self._x) - 1)

    # -- hover ---------------------------------------------------------
    def _on_mouse_moved(self, pos: QPointF) -> None:
        if len(self._x) == 0:
            return
        inside = self._price.sceneBoundingRect().contains(pos) or (
            self._vol is not None and self._volume.sceneBoundingRect().contains(pos))
        if not inside:
            self._on_leave()
            return
        x = self._price.vb.mapSceneToView(pos).x()
        i = int(np.clip(np.searchsorted(self._x, x), 1, len(self._x) - 1))
        i = i if abs(self._x[i] - x) < abs(self._x[i - 1] - x) else i - 1
        self._vline.setPos(self._x[i])
        self._vline_vol.setPos(self._x[i])
        self._hline.setPos(self._close[i])
        self._marker.setData([self._x[i]], [self._close[i]])
        self._set_crosshair_visible(True)
        self._show_readout(i)

    def _on_leave(self) -> None:
        self._set_crosshair_visible(False)
        if len(self._x):
            self._show_readout(len(self._x) - 1)

    def _set_crosshair_visible(self, visible: bool) -> None:
        for item in (self._vline, self._vline_vol, self._hline, self._marker):
            item.setVisible(visible)

    def _show_readout(self, i: int) -> None:
        assert self._dates is not None
        parts = [f"<b style='color:{PALETTE.text}'>{self._dates[i]:%b %d, %Y}</b>",
                 f"Close <b style='color:{PALETTE.text}'>{self._close[i]:,.2f}</b>"]
        if i > 0:
            change = self._close[i] / self._close[i - 1] - 1.0
            ink = PALETTE.good if change >= 0 else PALETTE.critical
            parts.append(f"<span style='color:{ink}'>{change:+.2%}</span>")
        if self._vol is not None and np.isfinite(self._vol[i]):
            parts.append(f"Vol <b style='color:{PALETTE.text}'>{compact_number(self._vol[i])}</b>")
        self._readout.setText("&nbsp;&nbsp;·&nbsp;&nbsp;".join(parts))


# --------------------------------------------------------------- sparkline

class Sparkline(pg.PlotWidget):
    """A tiny axis-less trend line for table cells."""

    def __init__(self, values: np.ndarray, width: int = 120, height: int = 34) -> None:
        super().__init__(background=None)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        plot = self.getPlotItem()
        _view_only(plot)
        plot.hideAxis("left")
        plot.hideAxis("bottom")
        plot.setContentsMargins(0, 4, 0, 4)
        values = np.asarray(values, dtype=float)
        x = np.arange(len(values), dtype=float)
        floor = float(values.min())
        plot.plot(x, values, pen=pg.mkPen(PALETTE.series, width=1.5),
                  fillLevel=floor, brush=_vertical_gradient(PALETTE.series, 0.30))
        plot.setYRange(floor, float(values.max()), padding=0.05)


# ---------------------------------------------------------------- fan chart

class FanChart(QWidget):
    """A Monte Carlo fan: faint sample paths, 50% and 90% bands, and the
    median path. Optionally preceded by a history line (split by a dashed
    "now" line) and overlaid with named curves on the fan's dates (e.g. the
    closed-form mean, or one highlighted path)."""

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)
        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas, 1)
        self._plot = self._canvas.addPlot(
            axisItems={"bottom": pg.DateAxisItem("bottom", utcOffset=0),
                       "left": _ValueAxis("left")})
        _view_only(self._plot)
        self._plot.getAxis("left").setWidth(52)
        self._plot.showGrid(x=False, y=True, alpha=0.9)

        self._items: list[pg.GraphicsObject] = []
        self._vline = pg.InfiniteLine(angle=90, pen=_crosshair_pen(), movable=False)
        self._plot.addItem(self._vline, ignoreBounds=True)
        self._vline.setVisible(False)
        self._hist_x = np.empty(0)
        self._fan_x = np.empty(0)
        self._overlays: Sequence[Series] = ()
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def set_fan(self, fan_dates: pd.DatetimeIndex, paths: np.ndarray,
                bands: dict[int, np.ndarray], n_paths: int, *,
                hist_dates: pd.DatetimeIndex | None = None,
                hist_close: np.ndarray | None = None,
                overlays: Sequence[Series] = (), max_drawn: int = 160) -> None:
        for item in self._items:
            self._plot.removeItem(item)
        self._items = []
        has_history = hist_dates is not None and len(hist_dates) > 0
        self._hist_dates = hist_dates if has_history else None
        self._hist_x = epoch_seconds(hist_dates) if has_history else np.empty(0)
        self._hist_close = np.asarray(hist_close, dtype=float) if has_history else np.empty(0)
        self._fan_dates, self._fan_x = fan_dates, epoch_seconds(fan_dates)
        self._bands, self._overlays = bands, overlays

        # Sample paths: one curve item with breaks between paths (fast).
        shown = paths[:max_drawn]
        xs = np.tile(self._fan_x, len(shown))
        connect = np.ones(xs.size, dtype=bool)
        connect[len(self._fan_x) - 1::len(self._fan_x)] = False
        self._add(pg.PlotCurveItem(xs, shown.ravel(), connect=connect,
                                   pen=pg.mkPen(_color(PALETTE.series, 0.10), width=1)))
        for low, high, alpha in ((5, 95, 0.10), (25, 75, 0.16)):
            lo = pg.PlotCurveItem(self._fan_x, bands[low], pen=pg.mkPen(None))
            hi = pg.PlotCurveItem(self._fan_x, bands[high], pen=pg.mkPen(None))
            self._add(lo)
            self._add(hi)
            self._add(pg.FillBetweenItem(lo, hi, brush=_color(PALETTE.series, alpha)))
        self._add(pg.PlotCurveItem(self._fan_x, bands[50],
                                   pen=pg.mkPen(PALETTE.series_light, width=1.8)))
        for series in overlays:
            self._add(pg.PlotCurveItem(self._fan_x, series.values, pen=pg.mkPen(
                series.color, width=series.width,
                style=Qt.DashLine if series.dashed else Qt.SolidLine)))
        if has_history:
            self._add(pg.PlotCurveItem(self._hist_x, self._hist_close,
                                       pen=pg.mkPen(PALETTE.series, width=2)))

        top = float(max(np.max(bands[95]), *(np.nanmax(o.values) for o in overlays),
                        self._hist_close.max() if has_history else -np.inf))
        bottom = float(min(np.min(bands[5]), *(np.nanmin(o.values) for o in overlays),
                           self._hist_close.min() if has_history else np.inf))
        pad = (top - bottom) * 0.06
        self._plot.setYRange(max(bottom - pad, 0.0), top + pad * (3 if has_history else 1),
                             padding=0)
        first = self._hist_x[0] if has_history else self._fan_x[0]
        self._plot.setXRange(first, self._fan_x[-1], padding=0.01)

        if has_history:
            now = pg.InfiniteLine(pos=self._fan_x[0], angle=90, movable=False,
                                  pen=pg.mkPen(_color(PALETTE.text_secondary, 0.6), width=1,
                                               style=Qt.DashLine))
            self._add(now, ignore_bounds=True)
            for text, anchor in (("Historical  ", (1, 0)),
                                 (f"  Forecast · {n_paths:,} paths", (0, 0))):
                tag = pg.TextItem(text, color=PALETTE.text_secondary, anchor=anchor)
                tag.setPos(self._fan_x[0], top + pad * 3)
                self._add(tag, ignore_bounds=True)
        self._show_readout(None)

    def _add(self, item, ignore_bounds: bool = False) -> None:
        self._plot.addItem(item, ignoreBounds=ignore_bounds)
        self._items.append(item)

    def _on_mouse_moved(self, pos: QPointF) -> None:
        if len(self._fan_x) == 0 or not self._plot.sceneBoundingRect().contains(pos):
            self._on_leave()
            return
        x = self._plot.vb.mapSceneToView(pos).x()
        if len(self._hist_x) and x <= self._fan_x[0]:
            i = int(np.clip(np.searchsorted(self._hist_x, x), 0, len(self._hist_x) - 1))
            self._vline.setPos(self._hist_x[i])
            self._show_readout(("hist", i))
        else:
            i = int(np.clip(np.searchsorted(self._fan_x, x), 0, len(self._fan_x) - 1))
            self._vline.setPos(self._fan_x[i])
            self._show_readout(("fan", i))
        self._vline.setVisible(True)

    def _on_leave(self) -> None:
        self._vline.setVisible(False)
        if len(self._fan_x):
            self._show_readout(None)

    def _show_readout(self, at: tuple[str, int] | None) -> None:
        strong = f"color:{PALETTE.text}"
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        if at is None:
            at = ("fan", len(self._fan_x) - 1)
        kind, i = at
        if kind == "hist":
            self._readout.setText(
                f"<b style='{strong}'>{self._hist_dates[i]:%b %d, %Y}</b>{sep}"
                f"Close <b style='{strong}'>{self._hist_close[i]:,.2f}</b>")
            return
        b = self._bands
        parts = [f"<b style='{strong}'>{self._fan_dates[i]:%b %d, %Y}</b>",
                 f"<span style='color:{PALETTE.series_light}'>━</span> Median "
                 f"<b style='{strong}'>{b[50][i]:,.2f}</b>",
                 f"90% band <b style='{strong}'>{b[5][i]:,.2f} – {b[95][i]:,.2f}</b>"]
        for series in self._overlays:
            parts.append(f"{_swatch(series)} {series.name} "
                         f"<b style='{strong}'>{series.fmt.format(series.values[i])}</b>")
        self._readout.setText(sep.join(parts))

    def clear(self) -> None:
        for item in self._items:
            self._plot.removeItem(item)
        self._items = []
        self._hist_x = self._fan_x = np.empty(0)
        self._readout.setText("")


# ------------------------------------------------------ stacked time series

@dataclass(frozen=True)
class Series:
    """One line in a ``Panel``. ``fmt`` formats its value in the readout."""

    name: str
    values: np.ndarray
    color: str = PALETTE.series
    width: float = 1.6
    dashed: bool = False
    fmt: str = "{:,.2f}"
    fill: bool = False      # shade between the line and zero (drawdowns)


@dataclass(frozen=True)
class Band:
    """A shaded interval in a ``Panel`` (a confidence band), drawn under the
    lines and read out as ``lower – upper``."""

    name: str
    lower: np.ndarray
    upper: np.ndarray
    color: str = PALETTE.series
    fmt: str = "{:,.2f}"


@dataclass(frozen=True)
class Shade:
    """A background tint in a ``Panel``: each date's column is ``color`` at
    an opacity proportional to its value in ``values`` (0–1), such as a
    regime's probability. Tints of several shades blend. Not in the
    readout: another panel carries the values."""

    values: np.ndarray
    color: str


@dataclass(frozen=True)
class Panel:
    """One row of a ``StackedTimeSeries``: a title, its lines, the axis
    format, a relative height, and optional horizontal reference lines
    ``(label, value)`` drawn dotted with their label at the right.

    ``y_range`` fixes the value axis instead of fitting everything drawn,
    for series whose early values would flatten the rest (a filter's
    diffuse start).

    ``stacked`` draws the series as stacked areas, each on top of the ones
    before (shares that sum to one, such as regime probabilities), while
    the readout still shows each series' own value. ``shades`` tint the
    background behind everything."""

    title: str
    series: Sequence[Series]
    axis: str = "price"
    stretch: int = 1
    zero_line: bool = False
    references: Sequence[tuple[str, float]] = ()
    bands: Sequence[Band] = ()
    y_range: tuple[float, float] | None = None
    stacked: bool = False
    shades: Sequence[Shade] = ()


def _days(dte: float) -> str:
    """Days to expiry as a label: real expiries are fractional (they run to
    16:00 New York), so 6.93 shows as "7d"."""
    return f"{dte:.0f}d"


def _swatch(series: Series) -> str:
    glyph = "╍" if series.dashed else "━"
    return f"<span style='color:{series.color}'>{glyph}</span>"


class StackedTimeSeries(QWidget):
    """Panels sharing one date axis, with a single crosshair across all of
    them and a readout of every series at the hovered date.

    ``readout_per_panel`` gives each panel its own readout line, for charts
    with more values than one line holds."""

    def __init__(self, readout_per_panel: bool = False) -> None:
        super().__init__()
        self._per_panel = readout_per_panel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)
        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        self._canvas.ci.setSpacing(6)
        layout.addWidget(self._canvas, 1)
        self._plots: list[pg.PlotItem] = []
        self._vlines: list[pg.InfiniteLine] = []
        self._panels: Sequence[Panel] = ()
        self._x = np.empty(0)
        self._dates: pd.DatetimeIndex | None = None
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def set_panels(self, dates: pd.DatetimeIndex, panels: Sequence[Panel]) -> None:
        self._canvas.ci.clear()
        self._plots, self._vlines = [], []
        self._panels, self._dates, self._x = panels, dates, epoch_seconds(dates)
        last = len(panels) - 1
        for row, panel in enumerate(panels):
            plot = self._canvas.addPlot(
                row=row, col=0,
                axisItems={"bottom": pg.DateAxisItem("bottom", utcOffset=0),
                           "right": _ValueAxis("right", panel.axis)})
            _view_only(plot)
            plot.vb.setDefaultPadding(0.06)
            plot.hideAxis("left")
            plot.showAxis("right")
            plot.getAxis("right").setWidth(58)
            plot.showGrid(x=False, y=True, alpha=0.9)
            plot.setTitle(panel.title, color=PALETTE.text_secondary, size="9pt", justify="left")
            if row != last:
                plot.hideAxis("bottom")
            if row > 0:
                plot.setXLink(self._plots[0])
            if panel.zero_line:
                plot.addItem(pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen(PALETTE.border_strong)),
                             ignoreBounds=True)
            self._add_shades(plot, panel)
            for band in panel.bands:
                lower = pg.PlotCurveItem(self._x, band.lower, pen=pg.mkPen(None))
                upper = pg.PlotCurveItem(self._x, band.upper, pen=pg.mkPen(None))
                plot.addItem(lower)
                plot.addItem(upper)
                plot.addItem(pg.FillBetweenItem(lower, upper, brush=_color(band.color, 0.18)))
            if panel.stacked:
                self._add_stack(plot, panel)
            for series in () if panel.stacked else panel.series:
                pen = pg.mkPen(series.color, width=series.width,
                               style=Qt.DashLine if series.dashed else Qt.SolidLine)
                curve = pg.PlotDataItem(self._x, series.values, pen=pen)
                if series.fill:
                    curve.setFillLevel(0.0)
                    curve.setBrush(_color(series.color, 0.22))
                plot.addItem(curve)
                # Enable clipping/downsampling only once parented: pyqtgraph
                # 0.14 adds an item to the scene before its ViewBox, and a
                # clipToView item queried in that window crashes.
                curve.setClipToView(True)
                curve.setDownsampling(auto=True, method="peak")
            # References after the series, so their labels paint on top.
            for name, value in panel.references:
                plot.addItem(pg.InfiniteLine(
                    pos=value, angle=0, movable=False,
                    pen=pg.mkPen(_color(PALETTE.text_secondary, 0.7), width=1, style=Qt.DotLine),
                    label=name, labelOpts={"position": 0.99, "color": PALETTE.text_secondary,
                                           "fill": _color(PALETTE.surface, 0.85),
                                           "anchors": [(1, 1), (1, 1)]}))
            if panel.y_range is not None:
                plot.setYRange(*panel.y_range, padding=0.0)
            vline = pg.InfiniteLine(angle=90, pen=_crosshair_pen(), movable=False)
            vline.setVisible(False)
            plot.addItem(vline, ignoreBounds=True)
            self._plots.append(plot)
            self._vlines.append(vline)
            self._canvas.ci.layout.setRowStretchFactor(row, panel.stretch)
        if self._plots:
            self._plots[0].setXRange(self._x[0], self._x[-1], padding=0.01)
        self._show_readout(len(self._x) - 1)

    def _add_shades(self, plot: pg.PlotItem, panel: Panel) -> None:
        """One image with a pixel per calendar day, each holding its latest
        date's tint, so weekends and holidays don't shift columns and no
        seams show between them. The shades composite in order ("over"),
        each at ``_SHADE_ALPHA`` × its value. The image is as tall as the
        panel's data and then some: the view clips it, and it doesn't count
        toward the autorange."""
        if not panel.shades or not len(self._x):
            return
        x = self._x
        days = int(round((x[-1] - x[0]) / _DAY_S)) + 1
        grid = x[0] + np.arange(days) * _DAY_S
        latest = np.clip(np.searchsorted(x, grid + _DAY_S / 2, side="right") - 1, 0, len(x) - 1)
        premultiplied, alpha = np.zeros((days, 3)), np.zeros(days)
        for shade in panel.shades:
            values = np.clip(np.nan_to_num(np.asarray(shade.values, dtype=float)), 0, 1)
            a = (_SHADE_ALPHA * values[latest])[:, None]
            rgb = np.array(_color(shade.color).getRgbF()[:3])
            premultiplied = rgb * a + premultiplied * (1 - a)
            alpha = a[:, 0] + alpha * (1 - a[:, 0])
        rgba = np.zeros((days, 1, 4), dtype=np.ubyte)
        rgba[:, 0, :3] = np.round(255 * np.divide(
            premultiplied, alpha[:, None], out=np.zeros_like(premultiplied),
            where=alpha[:, None] > 0))
        rgba[:, 0, 3] = np.round(255 * alpha)
        if panel.y_range is not None:
            low, high = panel.y_range
        else:
            values = np.concatenate([np.asarray(s.values, dtype=float) for s in panel.series])
            values = values[np.isfinite(values)]
            low, high = (float(values.min()), float(values.max())) if len(values) else (0.0, 1.0)
        span = max(high - low, abs(high) * 1e-6, 1e-12)
        image = pg.ImageItem(rgba, levels=(0, 255))   # image[x, y]: one row, a column a day
        image.setRect(QRectF(grid[0] - _DAY_S / 2, low - span, days * _DAY_S,
                             high - low + 2 * span))
        plot.addItem(image, ignoreBounds=True)

    def _add_stack(self, plot: pg.PlotItem, panel: Panel) -> None:
        base = np.zeros(len(self._x))
        for series in panel.series:
            top = base + np.nan_to_num(np.asarray(series.values, dtype=float))
            lower = pg.PlotCurveItem(self._x, base, pen=pg.mkPen(None))
            upper = pg.PlotCurveItem(self._x, top, pen=pg.mkPen(series.color, width=1))
            plot.addItem(lower)
            plot.addItem(upper)
            fill = _color(series.color, _STACK_ALPHA)
            plot.addItem(pg.FillBetweenItem(lower, upper, brush=fill))
            base = top

    def clear(self) -> None:
        self._canvas.ci.clear()
        self._plots, self._vlines, self._panels = [], [], ()
        self._x = np.empty(0)
        self._readout.setText("")

    def _on_mouse_moved(self, pos: QPointF) -> None:
        plot = next((p for p in self._plots if p.sceneBoundingRect().contains(pos)), None)
        if plot is None or len(self._x) == 0:
            self._on_leave()
            return
        x = plot.vb.mapSceneToView(pos).x()
        i = int(np.clip(np.searchsorted(self._x, x), 1, len(self._x) - 1))
        i = i if abs(self._x[i] - x) < abs(self._x[i - 1] - x) else i - 1
        for vline in self._vlines:
            vline.setPos(self._x[i])
            vline.setVisible(True)
        self._show_readout(i)

    def _on_leave(self) -> None:
        for vline in self._vlines:
            vline.setVisible(False)
        if len(self._x):
            self._show_readout(len(self._x) - 1)

    def _show_readout(self, i: int) -> None:
        if self._dates is None or not len(self._x):
            return
        strong = f"color:{PALETTE.text}"
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        lines = []
        for panel in self._panels:
            parts = []
            for series in panel.series:
                value = series.values[i]
                text = "—" if not np.isfinite(value) else series.fmt.format(value)
                parts.append(f"{_swatch(series)} {series.name} <b style='{strong}'>{text}</b>")
            for band in panel.bands:
                parts.append(f"<span style='color:{band.color}'>▮</span> {band.name} "
                             f"<b style='{strong}'>{band.fmt.format(band.lower[i])} – "
                             f"{band.fmt.format(band.upper[i])}</b>")
            lines.append(sep.join(parts))
        date = f"<b style='{strong}'>{self._dates[i]:%b %d, %Y}</b>{sep}"
        self._readout.setText(date + ("<br>" if self._per_panel else sep).join(lines))


# --------------------------------------------------------------- histogram

class Histogram(QWidget):
    """Density histogram with an optional normal-density overlay.

    Presentation math only: binning (``np.histogram``) and the normal pdf
    drawn from supplied moments, exactly as ``visualization.charts`` does.
    """

    def __init__(self, axis: str = "number", bin_fmt: str = "{:+.2f}") -> None:
        """``axis``: ``number``, ``percent`` or ``price`` tick labels;
        ``bin_fmt`` formats bin edges in the hover readout."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)
        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas, 1)
        self._bin_fmt = bin_fmt
        self._markers: Sequence[tuple[Series, float]] = ()
        bottom = pg.AxisItem("bottom") if axis == "number" else _ValueAxis("bottom", axis)
        self._plot = self._canvas.addPlot(axisItems={"bottom": bottom})
        _view_only(self._plot)
        self._plot.vb.setDefaultPadding(0.02)
        self._plot.hideAxis("left")
        self._items: list[pg.GraphicsObject] = []
        self._edges = np.empty(0)
        self._density = np.empty(0)
        self._counts = np.empty(0)
        self._normal: tuple[float, float] | None = None
        self._normal_name = ""
        self._highlight = pg.BarGraphItem(x0=[0], x1=[0], height=[0],
                                          brush=_color(PALETTE.series_light, 0.95), pen=pg.mkPen(None))
        self._highlight.setVisible(False)
        self._plot.addItem(self._highlight)
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def set_data(self, values: np.ndarray, normal: tuple[float, float] | None = None,
                 normal_name: str = "Normal", bins: int | None = None,
                 markers: Sequence[tuple[Series, float]] = ()) -> None:
        """``markers``: vertical reference lines ``(style, x)``; the style's
        name and ``fmt`` label it in the readout legend, not on the plot, so
        close markers (mean vs. expectation) never collide."""
        for item in self._items:
            self._plot.removeItem(item)
        self._items = []
        values = np.asarray(values, dtype=float)
        values = values[np.isfinite(values)]
        bins = bins or min(60, max(10, len(values) // 8))
        self._density, self._edges = np.histogram(values, bins=bins, density=True)
        self._counts = np.histogram(values, bins=self._edges)[0]
        self._normal, self._normal_name = normal, normal_name
        width = self._edges[1] - self._edges[0]
        gap = width * 0.08     # a hairline of surface between adjacent bars
        bars = pg.BarGraphItem(x0=self._edges[:-1] + gap / 2, x1=self._edges[1:] - gap / 2,
                               height=self._density, brush=_color(PALETTE.series, 0.85),
                               pen=pg.mkPen(None))
        self._plot.addItem(bars)
        self._items.append(bars)
        if normal is not None:
            mean, sd = normal
            x = np.linspace(self._edges[0], self._edges[-1], 240)
            pdf = np.exp(-0.5 * ((x - mean) / sd) ** 2) / (sd * math.sqrt(2.0 * math.pi))
            curve = pg.PlotDataItem(x, pdf, pen=pg.mkPen(PALETTE.text_secondary, width=1.5,
                                                         style=Qt.DashLine))
            self._plot.addItem(curve)
            self._items.append(curve)
        self._markers = markers
        for style, x in markers:
            line = pg.InfiniteLine(pos=x, angle=90, movable=False, pen=pg.mkPen(
                style.color, width=style.width,
                style=Qt.DashLine if style.dashed else Qt.SolidLine))
            self._plot.addItem(line)
            self._items.append(line)
        self._plot.setXRange(self._edges[0], self._edges[-1], padding=0.02)
        self._plot.setYRange(0, float(self._density.max()) * 1.12, padding=0)
        self._show_readout(None)

    def _on_mouse_moved(self, pos: QPointF) -> None:
        if not len(self._edges) or not self._plot.sceneBoundingRect().contains(pos):
            self._on_leave()
            return
        x = self._plot.vb.mapSceneToView(pos).x()
        i = int(np.clip(np.searchsorted(self._edges, x) - 1, 0, len(self._density) - 1))
        self._highlight.setOpts(x0=[self._edges[i]], x1=[self._edges[i + 1]],
                                height=[self._density[i]])
        self._highlight.setVisible(True)
        self._show_readout(i)

    def _on_leave(self) -> None:
        self._highlight.setVisible(False)
        if len(self._edges):
            self._show_readout(None)

    def _show_readout(self, i: int | None) -> None:
        strong = f"color:{PALETTE.text}"
        total = int(self._counts.sum())
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        if i is None:
            # With markers the legend needs the room; the card names the sample.
            parts = [] if self._markers else [f"{total:,} observations · hover a bar"]
            if self._normal is not None:
                parts.append(f"<span style='color:{PALETTE.text_secondary}'>╍</span> "
                             f"{self._normal_name}")
            for style, x in self._markers:
                parts.append(f"{_swatch(style)} {style.name} "
                             f"<b style='{strong}'>{style.fmt.format(x)}</b>")
            self._readout.setText(sep.join(parts))
            return
        lo, hi = self._edges[i], self._edges[i + 1]
        share = self._counts[i] / total if total else 0.0
        lo_text, hi_text = self._bin_fmt.format(lo), self._bin_fmt.format(hi)
        self._readout.setText(
            f"<b style='{strong}'>[{lo_text}, {hi_text})</b>&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"<b style='{strong}'>{int(self._counts[i]):,}</b> obs ({share:.1%})&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"density <b style='{strong}'>{self._density[i]:.3f}</b>")


# ------------------------------------------------------------ curve chart

@dataclass(frozen=True)
class Curve:
    """One y(x) line. ``fill``: None, ``"zero"`` (shade to zero in the line's
    color) or ``"signed"`` (profit green above zero, loss red below)."""

    name: str
    x: np.ndarray
    y: np.ndarray
    color: str = PALETTE.series
    width: float = 1.8
    dashed: bool = False
    fmt: str = "{:,.2f}"
    fill: str | None = None
    marker: str | None = None   # pyqtgraph symbol at each point ("o", "d")
    line: bool = True           # False: points only
    readout: bool = True        # False: leave out of the hover readout
                                # (sparse points that shouldn't be interpolated)


class CurveChart(QWidget):
    """Lines over a numeric x axis (spot, path count): option profiles, P&L
    diagrams, Monte Carlo convergence. Hover snaps to the x grid of the first
    curve and reads every curve there (interpolated when grids differ).

    Extras: labelled vertical reference lines (spot, strike), a zero line,
    labelled points on the axis (breakevens), and a shaded band between two
    y arrays (a confidence interval)."""

    def __init__(self, x_axis: str = "price", y_axis: str = "price", log_x: bool = False,
                 x_name: str = "S", x_fmt: str = "{:,.2f}") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)
        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas, 1)
        self._plot = self._canvas.addPlot(axisItems={
            "bottom": _ValueAxis("bottom", x_axis), "right": _ValueAxis("right", y_axis)})
        _view_only(self._plot)
        self._plot.vb.setDefaultPadding(0.02)
        self._plot.hideAxis("left")
        self._plot.showAxis("right")
        self._plot.getAxis("right").setWidth(64)
        self._plot.showGrid(x=False, y=True, alpha=0.9)
        self._log_x = log_x
        if log_x:
            self._plot.setLogMode(x=True, y=False)
        self._x_name, self._x_fmt = x_name, x_fmt
        self._items: list[pg.GraphicsObject] = []
        self._curves: Sequence[Curve] = ()
        self._band: tuple[np.ndarray, np.ndarray, np.ndarray, str] | None = None
        self._vline = pg.InfiniteLine(angle=90, pen=_crosshair_pen(), movable=False)
        self._vline.setVisible(False)
        self._plot.addItem(self._vline, ignoreBounds=True)
        self._dots = pg.ScatterPlotItem(size=8, pen=pg.mkPen(PALETTE.surface, width=2))
        self._plot.addItem(self._dots, ignoreBounds=True)
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def _xv(self, x):
        """Data x -> view x (log10 in log mode)."""
        return np.log10(x) if self._log_x else x

    def set_curves(self, curves: Sequence[Curve], *, vlines: Sequence[tuple[str, float]] = (),
                   zero_line: bool = False, points: Sequence[tuple[float, str]] = (),
                   band: tuple[np.ndarray, np.ndarray, np.ndarray, str] | None = None) -> None:
        for item in self._items:
            self._plot.removeItem(item)
        self._items = []
        self._curves, self._band = curves, band
        if zero_line:
            self._add(pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen(PALETTE.border_strong)), True)
        if band is not None:
            bx, lo, hi, _name = band
            # PlotCurveItem (unlike PlotDataItem) ignores the plot's log mode,
            # so the band's x goes in already transformed.
            lo_item = pg.PlotCurveItem(self._xv(bx), lo, pen=pg.mkPen(None))
            hi_item = pg.PlotCurveItem(self._xv(bx), hi, pen=pg.mkPen(None))
            self._add(lo_item)
            self._add(hi_item)
            self._add(pg.FillBetweenItem(lo_item, hi_item, brush=_color(PALETTE.series, 0.18)))
        for curve in curves:
            if curve.fill == "zero":
                self._add(pg.PlotDataItem(curve.x, curve.y, pen=pg.mkPen(None), fillLevel=0.0,
                                          brush=_color(curve.color, 0.14)))
            elif curve.fill == "signed":
                for part, ink in ((np.maximum(curve.y, 0.0), PALETTE.good),
                                  (np.minimum(curve.y, 0.0), PALETTE.critical)):
                    self._add(pg.PlotDataItem(curve.x, part, pen=pg.mkPen(None), fillLevel=0.0,
                                              brush=_color(ink, 0.13)))
            if curve.line:
                self._add(pg.PlotDataItem(curve.x, curve.y, pen=pg.mkPen(
                    curve.color, width=curve.width,
                    style=Qt.DashLine if curve.dashed else Qt.SolidLine)))
            if curve.marker:
                self._add(pg.ScatterPlotItem(self._xv(np.asarray(curve.x, dtype=float)), curve.y,
                                             symbol=curve.marker, size=9,
                                             brush=pg.mkBrush(curve.color),
                                             pen=pg.mkPen(PALETTE.surface, width=1.5)))
        for name, x in vlines:
            self._add(pg.InfiniteLine(
                pos=self._xv(x), angle=90, movable=False,
                pen=pg.mkPen(_color(PALETTE.text_secondary, 0.55), width=1, style=Qt.DotLine),
                label=name, labelOpts={"position": 0.9, "color": PALETTE.text_secondary,
                                       "fill": _color(PALETTE.surface, 0.85),
                                       "anchors": [(0.5, 1), (0.5, 1)]}), True)
        if points:
            xs = np.array([x for x, _ in points], dtype=float)
            dots = pg.ScatterPlotItem(self._xv(xs), np.zeros_like(xs), size=9,
                                      brush=pg.mkBrush(PALETTE.text),
                                      pen=pg.mkPen(PALETTE.surface, width=2))
            self._add(dots, True)
            for x, text in points:
                tag = pg.TextItem(text, color=PALETTE.text_secondary, anchor=(0.5, -0.4))
                tag.setPos(self._xv(x), 0.0)
                self._add(tag, True)
        first = curves[0].x if curves else (band[0] if band is not None else np.array([0, 1]))
        self._plot.setXRange(self._xv(first[0]), self._xv(first[-1]), padding=0.01)
        self._plot.enableAutoRange(axis="y")
        self._show_readout(None)

    def _add(self, item, ignore_bounds: bool = False) -> None:
        self._plot.addItem(item, ignoreBounds=ignore_bounds)
        self._items.append(item)

    def _grid(self) -> np.ndarray:
        if self._curves:
            return self._curves[0].x
        return self._band[0] if self._band is not None else np.empty(0)

    def _on_mouse_moved(self, pos: QPointF) -> None:
        grid = self._grid()
        if not len(grid) or not self._plot.sceneBoundingRect().contains(pos):
            self._on_leave()
            return
        xv = self._plot.vb.mapSceneToView(pos).x()
        x = 10 ** xv if self._log_x else xv
        i = int(np.clip(np.searchsorted(grid, x), 1, len(grid) - 1))
        i = i if abs(grid[i] - x) < abs(grid[i - 1] - x) else i - 1
        self._vline.setPos(self._xv(grid[i]))
        self._vline.setVisible(True)
        shown = [c for c in self._curves if c.readout]
        ys = [float(np.interp(grid[i], c.x, c.y)) for c in shown]
        self._dots.setData([self._xv(grid[i])] * len(ys), ys,
                           brush=[pg.mkBrush(c.color) for c in shown])
        self._show_readout(i)

    def _on_leave(self) -> None:
        self._vline.setVisible(False)
        self._dots.setData([], [])
        if len(self._grid()):
            self._show_readout(None)

    def _show_readout(self, i: int | None) -> None:
        strong = f"color:{PALETTE.text}"
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        grid = self._grid()
        if i is None:
            parts = ["hover for values"]
            parts += [f"{_curve_swatch(c)} {c.name}" for c in self._curves]
            self._readout.setText(sep.join(parts))
            return
        x = grid[i]
        parts = [f"{self._x_name} <b style='{strong}'>{self._x_fmt.format(x)}</b>"]
        for curve in (c for c in self._curves if c.readout):
            y = float(np.interp(x, curve.x, curve.y))
            parts.append(f"{_curve_swatch(curve)} {curve.name} "
                         f"<b style='{strong}'>{curve.fmt.format(y)}</b>")
        if self._band is not None:
            bx, lo, hi, name = self._band
            parts.append(f"{name} <b style='{strong}'>{float(np.interp(x, bx, lo)):,.4f} – "
                         f"{float(np.interp(x, bx, hi)):,.4f}</b>")
        self._readout.setText(sep.join(parts))

    def clear(self) -> None:
        for item in self._items:
            self._plot.removeItem(item)
        self._items, self._curves, self._band = [], (), None
        self._readout.setText("")


def _curve_swatch(curve: Curve) -> str:
    if not curve.line:   # points only: show the marker, not a line
        glyph = "◆" if curve.marker == "d" else "●"
    else:
        glyph = "╍" if curve.dashed else "━"
    return f"<span style='color:{curve.color}'>{glyph}</span>"


# -------------------------------------------------------------- smile grid

class SmileGrid(QWidget):
    """Small multiples, one smile per expiry (implied vol vs log-moneyness).

    Per panel: the bid–ask IV range (muted bars), mid IVs (dots), the true
    smile when known (dashed) and the fitted SVI smile (series line). Each
    panel owns its y-scale: short expiries sit far higher, and a shared scale
    would flatten them. Takes ``models.vol_surface.SmileCurves``.
    """

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)
        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        self._canvas.ci.setSpacing(10)
        layout.addWidget(self._canvas, 1)
        self._panels: list[tuple[pg.PlotItem, pg.InfiniteLine, object]] = []
        self._has_truth = False   # whether any panel draws a true smile
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def set_smiles(self, curves: Sequence, max_panels: int = 6) -> None:
        self._canvas.ci.clear()
        self._panels = []
        shown = list(curves)[:max_panels]
        self._has_truth = any(curve.true_iv is not None for curve in shown)
        cols = min(3, len(shown)) or 1
        muted_pen = pg.mkPen(_color(PALETTE.muted, 0.9), width=1.6)
        for i, curve in enumerate(shown):
            plot = self._canvas.addPlot(row=i // cols, col=i % cols,
                                        axisItems={"left": _ValueAxis("left", "percent")})
            _view_only(plot)
            plot.vb.setDefaultPadding(0.06)
            plot.getAxis("left").setWidth(42)
            plot.showGrid(x=False, y=True, alpha=0.9)
            plot.setTitle(_days(curve.dte), color=PALETTE.text_secondary, size="9pt",
                          justify="left")
            plot.addItem(pg.InfiniteLine(pos=0.0, angle=90, pen=pg.mkPen(PALETTE.border_strong)),
                         ignoreBounds=True)
            low = np.where(np.isnan(curve.quote_iv_bid), curve.quote_iv, curve.quote_iv_bid)
            high = np.where(np.isnan(curve.quote_iv_ask), curve.quote_iv, curve.quote_iv_ask)
            bars_x = np.repeat(curve.quote_k, 2)
            bars_y = np.column_stack([low, high]).ravel()
            plot.addItem(pg.PlotCurveItem(bars_x, bars_y, connect="pairs", pen=muted_pen))
            if curve.true_iv is not None:
                plot.addItem(pg.PlotDataItem(curve.k_grid, curve.true_iv, pen=pg.mkPen(
                    PALETTE.text_secondary, width=1.2, style=Qt.DashLine)))
            if curve.fitted_iv is not None:
                plot.addItem(pg.PlotDataItem(curve.k_grid, curve.fitted_iv,
                                             pen=pg.mkPen(PALETTE.series, width=2.0)))
            plot.addItem(pg.ScatterPlotItem(curve.quote_k, curve.quote_iv, size=5,
                                            brush=pg.mkBrush(PALETTE.text), pen=pg.mkPen(None)))
            vline = pg.InfiniteLine(angle=90, pen=_crosshair_pen(), movable=False)
            vline.setVisible(False)
            plot.addItem(vline, ignoreBounds=True)
            self._panels.append((plot, vline, curve))
        self._show_readout(None, 0)

    def _on_mouse_moved(self, pos: QPointF) -> None:
        hit = next(((p, v, c) for p, v, c in self._panels if p.sceneBoundingRect().contains(pos)),
                   None)
        if hit is None:
            self._on_leave()
            return
        plot, vline, curve = hit
        k = plot.vb.mapSceneToView(pos).x()
        i = int(np.clip(np.searchsorted(curve.k_grid, k), 0, len(curve.k_grid) - 1))
        for _p, other, _c in self._panels:
            other.setVisible(other is vline)
        vline.setPos(curve.k_grid[i])
        self._show_readout(curve, i)

    def _on_leave(self) -> None:
        for _p, vline, _c in self._panels:
            vline.setVisible(False)
        self._show_readout(None, 0)

    def _show_readout(self, curve, i: int) -> None:
        strong = f"color:{PALETTE.text}"
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        legend = [f"<span style='color:{PALETTE.muted}'>┃</span> bid–ask IV",
                  f"<span style='color:{PALETTE.text}'>●</span> mid IV",
                  *([f"<span style='color:{PALETTE.text_secondary}'>╍</span> true"]
                    if self._has_truth else []),
                  f"<span style='color:{PALETTE.series}'>━</span> SVI fit"]
        if curve is None:
            self._readout.setText(sep.join(["hover a smile"] + legend))
            return
        k = curve.k_grid[i]
        parts = [f"<b style='{strong}'>{_days(curve.dte)}</b>",
                 f"k <b style='{strong}'>{k:+.3f}</b>"]
        if curve.fitted_iv is not None:
            parts.append(f"<span style='color:{PALETTE.series}'>━</span> SVI "
                         f"<b style='{strong}'>{curve.fitted_iv[i]:.2%}</b>")
        if curve.true_iv is not None:
            parts.append(f"<span style='color:{PALETTE.text_secondary}'>╍</span> true "
                         f"<b style='{strong}'>{curve.true_iv[i]:.2%}</b>")
        j = int(np.argmin(np.abs(curve.quote_k - k)))
        parts.append(f"nearest quote k {curve.quote_k[j]:+.3f}: mid "
                     f"<b style='{strong}'>{curve.quote_iv[j]:.2%}</b>")
        self._readout.setText(sep.join(parts))

    def clear(self) -> None:
        self._canvas.ci.clear()
        self._panels = []
        self._readout.setText("")


# ----------------------------------------------------------------- heatmap

class Heatmap(QWidget):
    """Values over (numeric x, ordered row categories), colored along the
    validated sequential ramp, with a color bar and a hover readout."""

    def __init__(self, x_name: str = "x", x_fmt: str = "{:+.2f}",
                 value_fmt: str = "{:.2%}") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_XS)
        self._readout = _readout_label()
        layout.addWidget(self._readout)
        self._canvas = _Canvas()
        self._canvas.setBackground(PALETTE.surface)
        self._canvas.ci.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._canvas, 1)
        self._x_name, self._x_fmt, self._value_fmt = x_name, x_fmt, value_fmt
        self._plot = self._canvas.addPlot(row=0, col=0)
        _view_only(self._plot)
        self._bar = self._canvas.addPlot(row=0, col=1,
                                         axisItems={"right": _ValueAxis("right", "percent")})
        _view_only(self._bar)
        self._bar.hideAxis("left")
        self._bar.hideAxis("bottom")
        self._bar.showAxis("right")
        self._bar.setFixedWidth(64)
        self._canvas.ci.layout.setColumnStretchFactor(0, 1)
        colors = [QColor(c) for c in PALETTE.sequential]
        self._cmap = pg.ColorMap(np.linspace(0.0, 1.0, len(colors)), colors)
        self._image = pg.ImageItem()
        self._image.setLookupTable(self._cmap.getLookupTable(nPts=256))
        self._plot.addItem(self._image)
        self._bar_image = pg.ImageItem()
        self._bar_image.setLookupTable(self._cmap.getLookupTable(nPts=256))
        self._bar.addItem(self._bar_image)
        self._zero = pg.InfiniteLine(pos=0.0, angle=90, pen=pg.mkPen(PALETTE.surface, width=1.5))
        self._plot.addItem(self._zero, ignoreBounds=True)
        self._cell = pg.PlotCurveItem(pen=pg.mkPen(PALETTE.text, width=1.5))
        self._plot.addItem(self._cell, ignoreBounds=True)
        self._x = np.empty(0)
        self._rows: Sequence[str] = ()
        self._values = np.empty((0, 0))
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.left.connect(self._on_leave)

    def set_grid(self, x: np.ndarray, rows: Sequence[str], values: np.ndarray) -> None:
        """``values[r, j]`` is the value of row ``r`` at ``x[j]``."""
        self._x, self._rows, self._values = np.asarray(x, dtype=float), list(rows), values
        low, high = float(np.nanmin(values)), float(np.nanmax(values))
        if high <= low:
            high = low + 1e-9
        dx = (self._x[-1] - self._x[0]) / max(len(self._x) - 1, 1)
        # ImageItem indexes image[x, y]; rows are equal-height categories.
        self._image.setImage(values.T, levels=(low, high))
        self._image.setRect(QRectF(self._x[0] - dx / 2, -0.5, self._x[-1] - self._x[0] + dx,
                                   len(self._rows)))
        self._plot.getAxis("left").setTicks([[(i, r) for i, r in enumerate(self._rows)]])
        self._plot.setXRange(self._x[0] - dx / 2, self._x[-1] + dx / 2, padding=0)
        self._plot.setYRange(-0.5, len(self._rows) - 0.5, padding=0)
        ramp = np.linspace(low, high, 256).reshape(1, -1)
        self._bar_image.setImage(ramp, levels=(low, high))
        self._bar_image.setRect(QRectF(0, low, 1, high - low))
        self._bar.setXRange(0, 1, padding=0)
        self._bar.setYRange(low, high, padding=0)
        self._show_readout(None)

    def _on_mouse_moved(self, pos: QPointF) -> None:
        if not len(self._x) or not self._plot.sceneBoundingRect().contains(pos):
            self._on_leave()
            return
        point = self._plot.vb.mapSceneToView(pos)
        r = int(np.clip(round(point.y()), 0, len(self._rows) - 1))
        j = int(np.clip(np.searchsorted(self._x, point.x()), 0, len(self._x) - 1))
        dx = (self._x[-1] - self._x[0]) / max(len(self._x) - 1, 1)
        x0, x1, y0, y1 = self._x[j] - dx / 2, self._x[j] + dx / 2, r - 0.5, r + 0.5
        self._cell.setData([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])
        self._cell.setVisible(True)
        self._show_readout((r, j))

    def _on_leave(self) -> None:
        self._cell.setVisible(False)
        if len(self._x):
            self._show_readout(None)

    def _show_readout(self, at: tuple[int, int] | None) -> None:
        strong = f"color:{PALETTE.text}"
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        if at is None:
            low, high = float(np.nanmin(self._values)), float(np.nanmax(self._values))
            self._readout.setText(
                f"hover a cell{sep}range <b style='{strong}'>{self._value_fmt.format(low)}</b> – "
                f"<b style='{strong}'>{self._value_fmt.format(high)}</b>")
            return
        r, j = at
        self._readout.setText(
            f"<b style='{strong}'>{self._rows[r]}</b>{sep}{self._x_name} "
            f"<b style='{strong}'>{self._x_fmt.format(self._x[j])}</b>{sep}"
            f"IV <b style='{strong}'>{self._value_fmt.format(self._values[r, j])}</b>")
