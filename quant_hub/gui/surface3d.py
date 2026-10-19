"""Interactive 3D implied-vol surface: Plotly (WebGL) in embedded Chromium (D32).

Render-only, like ``charts.py``: it takes a ``models.vol_surface.SurfaceMesh``
(plot-ready arrays) and turns it into a Plotly figure, data and layout as
JSON, which plotly.js draws. No math happens here.

How it loads:

* plotly.js comes from the installed ``plotly`` package (``plotly.min.js``
  in its ``package_data``), so nothing touches the network. The Python
  side of plotly is never imported; the figure is a plain dict.
* The page is set once, with that folder as its base URL. Every update calls
  ``Plotly.react`` with the new figure, which redraws in place and keeps
  the camera (``uirevision``); that is tens of milliseconds, not a reload.
* Chromium starts in other processes, so the GUI thread only pays for
  constructing the view (~0.7 s the first time in a process). The app
  builds the Volatility Lab and this widget before showing the main window,
  so Qt chooses its graphics surface without recreating a visible window.
  An update sent before the page has loaded waits for it.
* The first 3D draw in a page also pays ~2.5 s of one-time WebGL and JIT
  setup. The page draws a throwaway plot as it loads, while still hidden,
  so the first real draw takes ~0.2 s and later ones ~0.1 s (measured on
  an RTX 4060 Ti through ANGLE/D3D11).

Threading: like every widget, GUI thread only. ``runJavaScript`` is
asynchronous; ``rendered`` fires when plotly.js has finished drawing.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QFrame, QStackedLayout, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.gui.components import EmptyState, set_role
from quant_hub.gui.theme import FONT_UI, PALETTE, font_family
from quant_hub.models.vol_surface import SurfaceMesh

# The truth wireframe: every Nth mesh row and column, translucent and thin,
# since it lies almost on the fit and would otherwise hide it.
_WIRE_ROW_STEP, _WIRE_COL_STEP = 4, 8
_WIRE_ALPHA = 0.55

_PAGE = """<!doctype html>
<html><head><meta charset="utf-8">
<script src="plotly.min.js"></script>
<style>
  html, body {{ margin: 0; height: 100%; overflow: hidden; background: {background}; }}
  #plot {{ width: 100%; height: 100%; }}
</style></head>
<body><div id="plot"></div>
<script>
  const config = {{displaylogo: false, responsive: true,
                   modeBarButtonsToRemove: ["toImage", "resetCameraLastSave3d"]}};
  // 3D plots need WebGL, which some sessions lack (e.g. Remote Desktop).
  const webgl = !!document.createElement("canvas").getContext("webgl");
  window.render = function (figure) {{
    if (!webgl) return "no-webgl";
    try {{
      Plotly.react("plot", figure.data, figure.layout, config);
      return "ok";
    }} catch (error) {{
      return "error: " + error.message;
    }}
  }};
  // Warm-up: the first 3D draw in a page pays ~2.5 s of one-time WebGL and
  // JIT setup. Paying it on a throwaway plot now, while the view is hidden
  // and the load is still running, makes the first real draw ~0.2 s.
  if (webgl) Plotly.newPlot("plot", [
    {{type: "surface", z: [[0, 1], [1, 2]], showscale: false}},
    {{type: "scatter3d", mode: "lines+markers", x: [0, 1], y: [0, 1], z: [0, 1]}},
  ], {{paper_bgcolor: "{background}"}}, config);
</script></body></html>
"""


def plotly_js_folder() -> Path:
    """The installed plotly package's folder holding ``plotly.min.js``."""
    spec = importlib.util.find_spec("plotly")
    if spec is None or spec.origin is None:
        raise RuntimeError("the plotly package is not installed (pip install plotly)")
    return Path(spec.origin).parent / "package_data"


def _rgba(hex_color: str, alpha: float) -> str:
    red, green, blue = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{alpha})"


def _values(array: np.ndarray) -> list:
    """Floats for JSON, with NaN as null (a gap in plotly)."""
    return [round(float(v), 7) if math.isfinite(v) else None for v in np.ravel(array)]


def _grid(array: np.ndarray) -> list[list]:
    return [_values(row) for row in array]


def _polylines(xs: list[np.ndarray], ys: list[np.ndarray], zs: list[np.ndarray]) -> dict:
    """Several 3D polylines as one scatter trace, separated by nulls."""
    out: dict[str, list] = {"x": [], "y": [], "z": []}
    for x, y, z in zip(xs, ys, zs):
        for key, values in (("x", x), ("y", y), ("z", z)):
            out[key] += _values(values) + [None]
    return out


def surface_figure(mesh: SurfaceMesh) -> dict:
    """The Plotly figure for one mesh: the fitted surface, the fitted expiries
    as lines on it, the true surface as a wireframe (when known), and the
    quotes with their bid–ask ranges."""
    p = PALETTE
    days = np.asarray(mesh.dte, dtype=float)
    steps = len(p.sequential) - 1
    data: list[dict] = [{
        "type": "surface", "name": "SVI surface", "x": _values(mesh.z), "y": _values(days),
        "z": _grid(mesh.iv), "colorscale": [[i / steps, c] for i, c in enumerate(p.sequential)],
        "opacity": 0.97, "showlegend": True,
        "colorbar": {"title": {"text": "IV", "font": {"color": p.muted}}, "tickformat": ".0%",
                     "tickfont": {"color": p.muted}, "thickness": 12, "len": 0.6,
                     "outlinewidth": 0, "x": 0.98},
        "lighting": {"ambient": 0.65, "diffuse": 0.55, "specular": 0.12, "roughness": 0.7},
        "contours": {"z": {"show": True, "usecolormap": True, "project": {"z": True},
                           "width": 1}},
        "hovertemplate": "k/√θ %{x:+.2f}<br>%{y:.0f} days<br>IV %{z:.2%}<extra>surface</extra>",
    }]

    rows = [int(r) for r in mesh.node_rows]
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "Fitted expiries", "hoverinfo": "skip",
        "line": {"color": p.text, "width": 3},
        **_polylines([mesh.z] * len(rows), [np.full(len(mesh.z), days[r]) for r in rows],
                     [mesh.iv[r] for r in rows]),
    })

    if mesh.true_iv is not None:
        row_ids = range(0, len(days), _WIRE_ROW_STEP)
        col_ids = range(0, len(mesh.z), _WIRE_COL_STEP)
        data.append({
            "type": "scatter3d", "mode": "lines", "name": "True SSVI", "hoverinfo": "skip",
            "line": {"color": _rgba(p.series_2, _WIRE_ALPHA), "width": 1.5},
            **_polylines([mesh.z for _ in row_ids] + [np.full(len(days), mesh.z[c]) for c in col_ids],
                         [np.full(len(mesh.z), days[r]) for r in row_ids] + [days for _ in col_ids],
                         [mesh.true_iv[r] for r in row_ids] + [mesh.true_iv[:, c] for c in col_ids]),
        })

    # Bid–ask whiskers (a missing side collapses onto the mid), then the mids.
    low = np.where(np.isfinite(mesh.quote_iv_bid), mesh.quote_iv_bid, mesh.quote_iv)
    high = np.where(np.isfinite(mesh.quote_iv_ask), mesh.quote_iv_ask, mesh.quote_iv)
    data.append({
        "type": "scatter3d", "mode": "lines", "name": "Bid–ask", "hoverinfo": "skip",
        "line": {"color": p.muted, "width": 3},
        **_polylines([np.array([x, x]) for x in mesh.quote_z],
                     [np.array([d, d]) for d in mesh.quote_dte],
                     [np.array([lo, hi]) for lo, hi in zip(low, high)]),
    })
    data.append({
        "type": "scatter3d", "mode": "markers", "name": "Quotes (mid)",
        "x": _values(mesh.quote_z), "y": _values(mesh.quote_dte), "z": _values(mesh.quote_iv),
        "marker": {"size": 2.5, "color": p.text},
        "hovertemplate": "quote · k/√θ %{x:+.2f}<br>%{y:.0f} days<br>mid IV %{z:.2%}"
                         "<extra></extra>",
    })

    axis = {"backgroundcolor": p.surface, "gridcolor": p.border, "zerolinecolor": p.border,
            "showbackground": True, "color": p.muted, "title": {"font": {"color": p.muted}}}
    layout = {
        "paper_bgcolor": p.surface, "plot_bgcolor": p.surface,
        "margin": {"l": 0, "r": 0, "t": 0, "b": 0},
        "font": {"family": font_family(FONT_UI), "color": p.muted, "size": 11},
        "legend": {"x": 0.01, "y": 0.98, "bgcolor": "rgba(0,0,0,0)",
                   "font": {"color": p.text_secondary}},
        "hoverlabel": {"bgcolor": p.raised, "bordercolor": p.border_strong,
                       "font": {"color": p.text}},
        "modebar": {"bgcolor": "rgba(0,0,0,0)", "color": p.muted, "activecolor": p.text},
        # A constant uirevision keeps the user's camera across updates.
        "uirevision": "surface",
        "scene": {
            "uirevision": "surface",
            "xaxis": {**axis, "title": {**axis["title"], "text": "k / √θ"}},
            "yaxis": {**axis, "title": {**axis["title"], "text": "days to expiry"}},
            "zaxis": {**axis, "title": {**axis["title"], "text": "implied vol"},
                      "tickformat": ".0%"},
            "camera": {"eye": {"x": 1.35, "y": -1.45, "z": 0.8}},
            "aspectmode": "manual", "aspectratio": {"x": 1.2, "y": 1.2, "z": 0.7},
        },
    }
    return {"data": data, "layout": layout}


class Surface3D(QWidget):
    """A rotatable 3D surface; shows a loading cover until the first draw.

    Chromium stalls drawing in a hidden view, so updates wait until this
    widget is shown, and the loading cover sits *over* the web view (both
    visible, cover on top) rather than hiding it.
    """

    rendered = Signal()

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget()
        self._stack.layout().setStackingMode(QStackedLayout.StackAll)
        layout.addWidget(self._stack)
        self._view = QWebEngineView()
        self._view.setContextMenuPolicy(Qt.NoContextMenu)
        # Paint the surface color before the page loads, not a white flash.
        self._view.page().setBackgroundColor(QColor(PALETTE.surface))
        self._cover = QFrame()
        set_role(self._cover, "cover")
        cover_layout = QVBoxLayout(self._cover)
        self._loading = EmptyState("ph.cube", "Loading the 3D view…",
                                   "Starting the embedded browser that draws it.")
        cover_layout.addWidget(self._loading)
        self._stack.addWidget(self._view)
        self._stack.addWidget(self._cover)
        self._stack.setCurrentWidget(self._cover)   # raised over the view
        self._ready = False
        self._pending: str | None = None
        self._view.loadFinished.connect(self._on_loaded)
        self._view.setHtml(_PAGE.format(background=PALETTE.surface),
                           QUrl.fromLocalFile(str(plotly_js_folder()) + "/"))

    @property
    def ready(self) -> bool:
        return self._ready

    def set_mesh(self, mesh: SurfaceMesh) -> None:
        self._pending = f"window.render({json.dumps(surface_figure(mesh))})"
        self._flush()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().showEvent(event)
        self._flush()

    def _flush(self) -> None:
        """Send the latest figure once the page has loaded and is on screen."""
        if self._ready and self._pending is not None and self.isVisible():
            self._view.page().runJavaScript(self._pending, 0, self._on_rendered)
            self._pending = None

    def _on_loaded(self, ok: bool) -> None:
        if not ok:
            self._loading.set_text("The 3D view failed to load",
                                   "plotly.js couldn't be loaded from the plotly package.")
            return
        self._ready = True
        self._flush()

    def _on_rendered(self, result) -> None:
        try:
            if result != "ok":
                self._loading.set_text(
                    "The 3D view can't draw here",
                    "It needs WebGL (GPU drawing in the embedded browser), which this "
                    "session doesn't provide. The heatmap shows the same surface."
                    if result == "no-webgl" else f"plotly.js reported: {result}")
                return
            self._stack.setCurrentWidget(self._view)
            self._cover.hide()
            self.rendered.emit()
        except RuntimeError:
            pass   # the widget was deleted while the script ran (asynchronous)
