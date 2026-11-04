r"""The literature timeline: papers on strand lanes over time, linked (Lit-2).

Like the charts in ``charts.py`` this widget renders and never computes the
literature: the page hands it nodes (one per paper, already placed on a
lane), the links between them and the era bands. What it does compute is
presentation: where on the time axis a year sits, where same-year papers
spread, and which labels fit. Those three pieces are pure functions below,
tested without a display.

The time axis
-------------
Papers are sparse before 1950 (Bachelier, Wiener, Cowles, Williams …) and
dense after, and one root (Bernoulli, 1738) is far older still. A linear
axis would spend most of the width on a handful of papers, so the axis
coordinate u squeezes the past in two steps: by c = 0.3 between E = 1900
and P = 1950, and by e = 0.05 before E:

.. math::

   u(y) = \begin{cases}
     y & y \ge P \\
     P - c\,(P - y) & E \le y < P \\
     P - c\,(P - E) - e\,(E - y) & y < E
   \end{cases}

Each piece is linear with a positive slope and the pieces meet at P and E,
so u is continuous and strictly increasing and inverts piece by piece. The
year axis labels ticks in years, at the spacing that fits.

Same-year papers
----------------
k papers in one lane and one year sit at :math:`y + j/(k+1)`, j = 1 … k,
so they never share a point and stay inside their year.

Labels, level of detail
-----------------------
Every paper is a dot; its label (a small chip) shows only where it fits.
Each lane has two label rows when lanes are tall enough (above and below
the dots), one when they're shorter, none when cramped. Labels are placed
greedily by priority, the selected paper and its links first, then
search matches, then the most-cited: a label takes the first row where it
overlaps nothing already placed. Zooming in spreads the papers, so more
labels fit; zooming out leaves the landmarks.

Interaction
-----------
Unlike the view-only charts, the timeline pans (drag) and zooms (wheel)
along time; the lanes stay fixed. Clicking a dot or a label selects the
paper (``selected``); double-clicking asks to open it (``opened``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsRectItem, QVBoxLayout, QWidget

from quant_hub.gui.charts import _Canvas, _color, _readout_label, _style_axis
from quant_hub.gui.theme import FONT_UI, PALETTE, SPACE_XS, font_family

PIVOT, SQUEEZE = 1950, 0.3
EARLY, EARLY_SQUEEZE = 1900, 0.05
FIRST_YEAR, LAST_YEAR = 1730, 2030   # the pannable range
_HEADER = 0.75          # lanes above y=0 reserved for the era names
_DOT = 9                # px
_DOT_SELECTED = 14
_LABEL_PX = 11
_LABEL_GAP = 6          # px between neighboring labels in a row
_HIT = 10               # px: how near a click must be to a dot
_LANE_AXIS_WIDTH = 168
# Lane height (px) for two / one label rows: a chip is ~17 px and sits
# _DOT/2 + 2 px off the dot, so each row needs ~23.5 px of the lane.
_TWO_ROWS, _ONE_ROW = 48, 26
_TICK_STEPS = (100, 50, 25, 10, 5, 2, 1)
_TICK_SPACING = 64      # px: the least room between year ticks


# --------------------------------------------------------------- pure helpers

_U_EARLY = PIVOT - SQUEEZE * (PIVOT - EARLY)   # u(E)


def to_axis(year: float) -> float:
    """u(y): the axis position of a (fractional) year (module docstring)."""
    if year >= PIVOT:
        return year
    if year >= EARLY:
        return PIVOT - SQUEEZE * (PIVOT - year)
    return _U_EARLY - EARLY_SQUEEZE * (EARLY - year)


def from_axis(u: float) -> float:
    """y(u), the inverse of ``to_axis``."""
    if u >= PIVOT:
        return u
    if u >= _U_EARLY:
        return PIVOT - (PIVOT - u) / SQUEEZE
    return EARLY - (_U_EARLY - u) / EARLY_SQUEEZE


def spread(keys: Sequence[tuple[int, int]]) -> list[float]:
    """Fractional years for papers keyed (lane, year): the j-th of k sharing
    a key sits at year + j/(k+1), in input order."""
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for key in keys:
        counts[key] += 1
    seen: dict[tuple[int, int], int] = defaultdict(int)
    out = []
    for key in keys:
        seen[key] += 1
        out.append(key[1] + seen[key] / (counts[key] + 1))
    return out


@dataclass(frozen=True)
class LabelBox:
    id: str
    lane: int
    center: float     # px along the axis
    width: float      # px
    priority: float   # higher places first


def pack_labels(boxes: Sequence[LabelBox], rows: int, gap: float,
                visible: tuple[float, float]) -> dict[str, tuple[int, float]]:
    """(row, left edge in px) of each label that fits, by id; row 0 is
    above the dots, 1 below.

    A label is centered on its dot, but slid inward to stay inside
    ``visible``. Greedy by priority (ties: earlier first): it takes the
    first of ``rows`` rows in its lane where it keeps ``gap`` px from every
    label already placed. Labels whose dot is outside ``visible`` are
    skipped."""
    placed: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    shown: dict[str, tuple[int, float]] = {}
    order = sorted(range(len(boxes)), key=lambda i: (-boxes[i].priority, boxes[i].center))
    for i in order:
        box = boxes[i]
        if not visible[0] <= box.center <= visible[1]:
            continue
        left = min(max(box.center - box.width / 2, visible[0]), visible[1] - box.width)
        right = left + box.width
        for row in range(rows):
            taken = placed[(box.lane, row)]
            if all(right + gap <= a or left - gap >= b for a, b in taken):
                taken.append((left, right))
                shown[box.id] = (row, left)
                break
    return shown


def year_ticks(u_low: float, u_high: float, px: float) -> list[int]:
    """Year ticks for the visible axis range: the finest step whose ticks
    stay ``_TICK_SPACING`` px apart, keeping any tick that crowds its
    predecessor out (the squeezed years get sparser ticks)."""
    if px <= 0 or u_high <= u_low:
        return []
    per_unit = px / (u_high - u_low)
    y_low, y_high = from_axis(u_low), from_axis(u_high)
    for step in reversed(_TICK_STEPS):
        if step * per_unit >= _TICK_SPACING:
            break
    years = range(int(y_low // step + 1) * step, int(y_high) + 1, step)
    ticks, last = [], None
    for year in years:
        if last is None or (to_axis(year) - to_axis(last)) * per_unit >= _TICK_SPACING:
            ticks.append(year)
            last = year
    return ticks


# ------------------------------------------------------------------- records

@dataclass(frozen=True)
class Node:
    id: str
    lane: int
    year: float          # fractional, after ``spread``
    label: str           # "Black–Scholes 1973"
    readout: str         # rich text for the hover line
    implemented: bool    # in your code: filled dot
    read: bool           # Read or Replicated: a check on the label
    priority: float      # label order when space is short


@dataclass(frozen=True)
class Edge:
    source: str          # the later paper
    target: str          # the earlier paper
    critique: bool       # corrects / challenges / fails to replicate
    independent: bool


@dataclass(frozen=True)
class Band:
    start: int
    end: int
    name: str          # short
    long: str = ""     # used when it fits


# -------------------------------------------------------------------- widget

class _YearAxis(pg.AxisItem):
    """Bottom axis in years over the squeezed coordinate u."""

    def tickValues(self, minVal, maxVal, size):  # noqa: N802 (pyqtgraph API)
        return [(1.0, [to_axis(y) for y in year_ticks(minVal, maxVal, size)])]

    def tickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph API)
        return [str(round(from_axis(v))) for v in values]


class _Label(pg.TextItem):
    """A paper's chip. Clicks select the paper; double-clicks open it."""

    def __init__(self, node: Node, chart: "TimelineChart", font: QFont) -> None:
        text = ("✓ " if node.read else "") + node.label
        super().__init__(text, color=PALETTE.text_secondary, anchor=(0.5, 1.0))
        self.node = node
        self._chart = chart
        self.setFont(font)
        self.textItem.document().setDocumentMargin(2.5)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptHoverEvents(True)
        self.width = self.textItem.boundingRect().width()
        self.height = self.textItem.boundingRect().height()
        self.style(False, False, True)

    def style(self, selected: bool, linked: bool, lit: bool) -> None:
        if selected:
            ink, edge, fill, z = PALETTE.text, PALETTE.accent_hover, PALETTE.accent_soft, 30
        elif linked:
            ink, edge, fill, z = PALETTE.text, PALETTE.accent_soft_border, PALETTE.raised, 20
        else:
            ink, edge, fill, z = PALETTE.text_secondary, PALETTE.border_strong, PALETTE.raised, 10
        alpha = 1.0 if lit else 0.35
        self.setColor(_color(ink, alpha))
        self.border = pg.mkPen(_color(edge, alpha), width=1)
        self.fill = pg.mkBrush(_color(fill, 0.94 * alpha))
        self.setZValue(z)
        self.update()

    def place(self, row: int, across: float) -> None:
        """Row 0 above the dot, 1 below; ``across`` is where the dot falls
        along the chip (0.5: centered), both in units of the chip's size."""
        gap = (_DOT / 2 + 2) / max(self.height, 1)
        self.setAnchor((across, 1.0 + gap) if row == 0 else (across, -gap))

    def mouseClickEvent(self, ev) -> None:  # noqa: N802 (pyqtgraph API)
        if ev.button() == Qt.LeftButton:
            ev.accept()
            self._chart._activate(self.node.id, ev.double())

    def hoverEvent(self, ev) -> None:  # noqa: N802 (pyqtgraph API)
        if not ev.isExit():
            self._chart._show_readout(self.node)
        else:
            self._chart._show_readout(None)


class TimelineChart(QWidget):
    """Papers as dots on strand lanes over a squeezed year axis, with chips
    where they fit and the selected paper's links drawn as curves."""

    selected = Signal(str)
    opened = Signal(str)

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
        self._plot = self._canvas.addPlot(row=0, col=0, axisItems={"bottom": _YearAxis("bottom")})
        plot, vb = self._plot, self._plot.vb
        plot.hideButtons()
        plot.setMenuEnabled(False)
        for name in ("left", "bottom"):
            _style_axis(plot.getAxis(name))
        plot.getAxis("left").setWidth(_LANE_AXIS_WIDTH)
        plot.getAxis("left").setTextPen(pg.mkPen(PALETTE.text_secondary))
        vb.setDefaultPadding(0.0)
        vb.invertY(True)
        vb.setMouseEnabled(x=True, y=False)
        vb.setLimits(xMin=to_axis(FIRST_YEAR), xMax=to_axis(LAST_YEAR), minXRange=8)
        self._font = QFont(font_family(FONT_UI))
        self._font.setPixelSize(_LABEL_PX)
        self._metrics = QFontMetricsF(self._font)
        self._dots = pg.ScatterPlotItem(pxMode=True)
        self._dots.setZValue(5)
        plot.addItem(self._dots)
        self._faint = self._path_item(_color(PALETTE.series_light, 0.09), 1.0, Qt.SolidLine, -10)
        self._builds = self._path_item(_color(PALETTE.series_light, 0.95), 1.6, Qt.SolidLine, -4)
        self._critiques = self._path_item(_color(PALETTE.series_2, 0.95), 1.6, Qt.DashLine, -4)
        self._independents = self._path_item(_color(PALETTE.text_secondary, 0.9), 1.4, Qt.DotLine, -4)
        self._today = pg.InfiniteLine(angle=90, pen=self._pen(_color(PALETTE.muted, 0.6), 1, Qt.DashLine))
        self._today.setZValue(-6)
        plot.addItem(self._today, ignoreBounds=True)
        self._decor: list = []
        self._era_labels: list[tuple[pg.TextItem, float, float, list[tuple[str, float]]]] = []
        self._nodes: list[Node] = []
        self._by_id: dict[str, Node] = {}
        self._labels: dict[str, _Label] = {}
        self._edges: list[Edge] = []
        self._lanes = 0
        self._current: str | None = None
        self._lit: set[str] | None = None
        self._show_readout(None)
        vb.sigRangeChanged.connect(lambda *_: self._relayout())
        vb.sigResized.connect(lambda *_: self._relayout())
        self._canvas.scene().sigMouseMoved.connect(self._on_mouse_moved)
        self._canvas.scene().sigMouseClicked.connect(self._on_clicked)
        self._canvas.left.connect(lambda: self._show_readout(None))

    # -- building -----------------------------------------------------------
    def set_timeline(self, lanes: Sequence[str], nodes: Sequence[Node], edges: Sequence[Edge],
                     bands: Sequence[Band], today: float) -> None:
        plot = self._plot
        first = not self._nodes   # later loads keep the reader's place in time
        for item in self._decor:
            plot.removeItem(item)
        for label in self._labels.values():
            plot.removeItem(label)
        self._decor, self._era_labels, self._labels = [], [], {}
        self._nodes, self._edges, self._lanes = list(nodes), list(edges), len(lanes)
        self._by_id = {n.id: n for n in nodes}
        if self._current not in self._by_id:
            self._current = None
        plot.getAxis("left").setTicks([[(i + 0.5, name) for i, name in enumerate(lanes)]])
        x0, x1 = to_axis(FIRST_YEAR), to_axis(LAST_YEAR)
        for i in range(len(lanes) + 1):
            line = pg.PlotCurveItem([x0, x1], [i, i], pen=pg.mkPen(PALETTE.grid, width=1))
            line.setZValue(-15)
            self._add(line)
        for k, band in enumerate(bands):
            left, right = to_axis(band.start), to_axis(band.end + 1)
            if k % 2 == 0:
                rect = QGraphicsRectItem(QRectF(left, -_HEADER, right - left, len(lanes) + _HEADER))
                rect.setPen(QPen(Qt.NoPen))
                rect.setBrush(pg.mkBrush(_color(PALETTE.raised, 0.7)))
                rect.setZValue(-20)
                self._add(rect)
            name = pg.TextItem(band.name, color=PALETTE.muted, anchor=(0.5, 0.5))
            name.setFont(self._font)
            name.setPos((left + right) / 2, -_HEADER / 2)
            self._add(name)
            # Longest first: the band shows the longest name that fits.
            texts = [t for t in (band.long, band.name) if t]
            self._era_labels.append((name, left, right,
                                     [(t, self._metrics.horizontalAdvance(t) + 8) for t in texts]))
        self._today.setPos(to_axis(today))
        for node in nodes:
            label = _Label(node, self, self._font)
            label.setPos(to_axis(node.year), node.lane + 0.5)
            plot.addItem(label, ignoreBounds=True)
            self._labels[node.id] = label
        self._faint.setPath(self._curves(self._edges))
        plot.setYRange(-_HEADER, len(lanes), padding=0)
        if first:
            self.fit()
        self._restyle()

    def _add(self, item) -> None:
        self._plot.addItem(item, ignoreBounds=True)
        self._decor.append(item)

    def _pen(self, color, width: float, style) -> QPen:
        pen = pg.mkPen(color, width=width)
        pen.setStyle(style)
        pen.setCosmetic(True)
        return pen

    def _path_item(self, color, width: float, style, z: float) -> QGraphicsPathItem:
        item = QGraphicsPathItem()
        item.setPen(self._pen(color, width, style))
        item.setZValue(z)
        self._plot.addItem(item, ignoreBounds=True)
        return item

    def _curves(self, edges: Sequence[Edge]) -> QPainterPath:
        """One path of S-curves (across lanes) and arcs (within a lane)."""
        path = QPainterPath()
        for edge in edges:
            a, b = self._by_id.get(edge.source), self._by_id.get(edge.target)
            if a is None or b is None:
                continue
            x1, y1 = to_axis(a.year), a.lane + 0.5
            x2, y2 = to_axis(b.year), b.lane + 0.5
            path.moveTo(x1, y1)
            if a.lane == b.lane:
                path.cubicTo(x1, y1 - 0.45, x2, y2 - 0.45, x2, y2)
            else:
                mid = (x1 + x2) / 2
                path.cubicTo(mid, y1, mid, y2, x2, y2)
        return path

    # -- public controls ----------------------------------------------------
    def fit(self) -> None:
        self._plot.setXRange(to_axis(FIRST_YEAR), to_axis(2027), padding=0)

    def select(self, paper_id: str | None, reveal: bool = True) -> None:
        self._current = paper_id if paper_id in self._by_id else None
        if self._current and reveal:
            u = to_axis(self._by_id[self._current].year)
            low, high = self._plot.vb.viewRange()[0]
            if not low <= u <= high:
                half = (high - low) / 2
                self._plot.setXRange(u - half, u + half, padding=0)
        self._restyle()

    def set_lit(self, ids: set[str] | None) -> None:
        """Highlight ``ids`` (search matches) and dim the rest; None: all lit."""
        self._lit = ids
        self._restyle()

    def current(self) -> str | None:
        return self._current

    # -- drawing state ------------------------------------------------------
    def _neighbors(self) -> set[str]:
        if self._current is None:
            return set()
        return ({e.target for e in self._edges if e.source == self._current}
                | {e.source for e in self._edges if e.target == self._current})

    def _restyle(self) -> None:
        near = self._neighbors()
        spots = []
        for node in self._nodes:
            lit = self._lit is None or node.id in self._lit or node.id == self._current
            alpha = 1.0 if lit else 0.25
            chosen = node.id == self._current
            ink = PALETTE.text if chosen else PALETTE.series
            spots.append({
                "pos": (to_axis(node.year), node.lane + 0.5), "data": node.id,
                "size": _DOT_SELECTED if chosen else _DOT,
                "pen": pg.mkPen(_color(ink, alpha), width=2 if chosen else 1.5),
                "brush": pg.mkBrush(_color(PALETTE.series, alpha) if node.implemented
                                    else _color(PALETTE.surface, alpha)),
            })
            self._labels[node.id].style(chosen, node.id in near, lit)
        self._dots.setData(spots)
        mine = [e for e in self._edges if self._current in (e.source, e.target)]
        self._builds.setPath(self._curves([e for e in mine if not (e.critique or e.independent)]))
        self._critiques.setPath(self._curves([e for e in mine if e.critique]))
        self._independents.setPath(self._curves([e for e in mine if e.independent]))
        self._faint.setVisible(self._current is None)
        self._relayout()

    def _relayout(self) -> None:
        if not self._nodes:
            return
        vb = self._plot.vb
        (low, high), width = vb.viewRange()[0], vb.width()
        if width <= 0 or high <= low:
            return
        per_unit = width / (high - low)
        lane_px = vb.height() / (self._lanes + _HEADER)
        rows = 2 if lane_px >= _TWO_ROWS else 1 if lane_px >= _ONE_ROW else 0
        near = self._neighbors()
        boxes = []
        for node in self._nodes:
            priority = node.priority
            if node.id == self._current:
                priority += 1e6
            elif node.id in near:
                priority += 1e4
            if self._lit is not None and node.id in self._lit:
                priority += 1e3
            boxes.append(LabelBox(node.id, node.lane, (to_axis(node.year) - low) * per_unit,
                                  self._labels[node.id].width, priority))
        shown = pack_labels(boxes, rows, _LABEL_GAP, (0.0, width))
        for box in boxes:
            label, spot = self._labels[box.id], shown.get(box.id)
            if spot is not None and self._lit is not None and box.id not in self._lit \
                    and box.id != self._current and box.id not in near:
                spot = None   # while searching, only matches and the selection speak
            label.setVisible(spot is not None)
            if spot is not None:
                row, left = spot
                label.place(row, (box.center - left) / max(box.width, 1))
        for name, left, right, texts in self._era_labels:
            room = (min(right, high) - max(left, low)) * per_unit
            fits = next((text for text, need in texts if need <= room), None)
            name.setVisible(fits is not None)
            if fits is not None:
                if name.textItem.toPlainText() != fits:
                    name.setText(fits)
                name.setPos((max(left, low) + min(right, high)) / 2, -_HEADER / 2)

    # -- mouse --------------------------------------------------------------
    def _node_at(self, scene_pos: QPointF) -> Node | None:
        vb = self._plot.vb
        if not self._nodes or not vb.sceneBoundingRect().contains(scene_pos):
            return None
        best, best_d = None, _HIT ** 2
        for node in self._nodes:
            p = vb.mapViewToScene(QPointF(to_axis(node.year), node.lane + 0.5))
            d = (p.x() - scene_pos.x()) ** 2 + (p.y() - scene_pos.y()) ** 2
            if d <= best_d:
                best, best_d = node, d
        return best

    def _on_mouse_moved(self, pos: QPointF) -> None:
        node = self._node_at(pos)
        self._canvas.setCursor(Qt.PointingHandCursor if node else Qt.ArrowCursor)
        self._show_readout(node)

    def _on_clicked(self, ev) -> None:
        if ev.button() != Qt.LeftButton or ev.isAccepted():
            return
        node = self._node_at(ev.scenePos())
        if node is not None:
            self._activate(node.id, ev.double())

    def _activate(self, paper_id: str, double: bool) -> None:
        if double:
            self.opened.emit(paper_id)
            return
        self.select(paper_id, reveal=False)
        self.selected.emit(paper_id)

    def _show_readout(self, node: Node | None) -> None:
        if node is not None:
            self._readout.setText(node.readout)
            return
        dim = f"color:{PALETTE.muted}"
        self._readout.setText(
            f"<span style='{dim}'>drag to move through time · scroll to zoom · click a paper</span>"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;● in your code&nbsp;&nbsp;○ not yet&nbsp;&nbsp;✓ read"
            f"&nbsp;&nbsp;·&nbsp;&nbsp;<span style='color:{PALETTE.series_2}'>- - -</span> challenges")
