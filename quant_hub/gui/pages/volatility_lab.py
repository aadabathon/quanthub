"""Volatility Lab: fit an implied-volatility surface to a chain and score it.

Flow: the experiment card → one TaskRunner job (quote a chain off a known
SSVI surface → invert mids to IV → fit raw SVI per expiry → arbitrage
checks) → one immutable ``SurfaceFit`` that every view and the diagnostics
render from. As with the Simulator's D15 invariant, nothing on screen can
describe a different draw.

The fit consumes an ``OptionChain``, so a real IBKR chain slots into the
same job once chain data exists. The *model* side of the term structure
already uses real history: "Compare with history" loads a source, fits
GARCH, and overlays its expected-vol curve and trailing realized vols on the
ATM IV term structure (``services/vol_context.py``, D30).

**Market chains (C2).** The card's Market chain mode fetches a real chain
by symbol from a named source (D23/D34) through ``services/option_chains``,
which saves every live fetch as a snapshot (D33); "stored" reloads the
newest snapshot without a broker. A market chain has no truth, so the job
first estimates each expiry's forward and discount factor from put–call
parity (``models/carry.py``) and then runs the same fit. The table's TRUE
column becomes the provider's own model IV at the money, and the Carry card
shows each expiry's forward and how it was estimated. Short-dated implied
dividend yields are not shown: they are measured against the stale delayed
spot, which makes them noise.

**Fit method (C3, D35).** The Fit card toggles between independent raw SVI
per expiry (the default) and one joint SSVI surface (``models/ssvi_fit``).
SSVI has fewer parameters, no static arbitrage by construction, and is far
steadier on noisy chains. But live SPY/SPX smiles lie outside the SSVI
slice family, so on liquid chains independent SVI fits much better.
Switching refits the chain already on the page; it never refetches.

**The variance premium (VARIANCE I4, D77).** Each fit job also reads the
fit's model-free implied variance (``models/implied_variance``, 26 ms on the
default chain). The Term structure view draws √K_var per expiry beside ATM
IV, hollow where over half of it comes from the extrapolated wings, with the
quote sum's bid–ask band. The Variance premium and Premium history cards
price *this* fit against the history that "Compare with history" loads
(``services/vol_context.load_variance_premium``). That job's GARCH is fitted
on bars up to the chain's day, and the overlay draws the same fit, so the
chart and the cards agree, and a stored chain from last month isn't set
against today's GARCH. (Loaded before any fit, the overlay is as of now.)
Once loaded, every new fit is repriced in a job of its own. The cards and
the overlay are blank until it lands, so they never describe an earlier fit
(D15), and a late result for a fit since replaced is dropped. A market
chain's history is its stored snapshots; a synthetic chain has none.

The Surface view switches between a heatmap and a rotatable 3D surface
(``gui/surface3d.py``, Plotly, D32). The same job builds the 3D mesh
(``vol_surface.surface_mesh``) from the fit. The 3D view embeds Chromium,
whose first construction costs ~0.7 s of GUI time. The composition root
prepares this page and its browser before showing the main window: adding
the first WebEngine widget later makes Qt recreate the native window.
Only the widgets are prepared; the fit still starts on the first visit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.data.options import ChainRequest
from quant_hub.data.stats import TRADING_DAYS_PER_YEAR
from quant_hub.gui.charts import (
    Curve,
    CurveChart,
    Heatmap,
    Panel,
    Series,
    SmileGrid,
    StackedTimeSeries,
)
from quant_hub.gui.components import (
    Card,
    CardGrid,
    DataTable,
    EmptyState,
    Field,
    MetricTile,
    PageHeader,
    PageStack,
    SegmentedControl,
    Select,
    SelectField,
    StatTable,
    button,
    label,
    metric_row,
)
from quant_hub.gui.pages.base import ScrollPage, saved_grid
from quant_hub.gui.surface3d import Surface3D
from quant_hub.gui.theme import (
    FIELD_L,
    FIELD_M,
    FIELD_S,
    PALETTE,
    SIDE_PANEL_WIDTH,
    SPACE_M,
)
from quant_hub.models.carry import FIXED_RATE, NONE, REGRESSION, CarryFit, implied_carry
from quant_hub.models.implied_variance import (
    HEADLINE_DAYS,
    VarianceTermStructure,
    implied_variance,
)
from quant_hub.models.svi import SSVISurface
from quant_hub.models.variance_premium import (
    MIN_INDEPENDENT_WINDOWS,
    MIN_PERCENTILE_HISTORY,
    Premium,
)
from quant_hub.models.vol_surface import (
    SurfaceFit,
    SurfaceMesh,
    SyntheticChainSpec,
    fit_surface,
    smile_curves,
    surface_grid,
    surface_mesh,
    synthetic_chain,
)
from quant_hub.services.market_service import MarketService
from quant_hub.services.option_chains import ChainService
from quant_hub.services.vol_context import (
    VariancePremiumView,
    VolContext,
    load_variance_premium,
    load_vol_context,
)
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.tasks import TaskRunner

_logger = logging.getLogger(__name__)

_STATE_KEY = "volatility_lab"     # shared with the Tk page
_DEFAULTS = {
    "spot": "100", "rate": "4", "div": "0", "expiries": "7,30,60,91,182,365",
    "strikes": "21", "spread": "4", "noise": "0.5", "seed": "7",
    "v0": "25", "v_inf": "20", "kappa": "2", "rho": "-0.6", "eta": "1.2",
    "source": "synthetic", "symbol": "SPY", "lookback": "1825",
    "mode": "Synthetic", "chain_source": "ibkr", "chain_symbol": "SPY",
    "targets": "7,30,60,91,182,365", "chain_strikes": "21", "chain_rate": "4",
    "fit_method": "SVI",
}
_METHOD_KEYS = {"SVI": "svi", "SSVI": "ssvi"}
_SYNTHETIC, _MARKET = "Synthetic", "Market chain"
_CARD_SUBTITLES = {
    _SYNTHETIC: "A synthetic chain quoted off a known arbitrage-free SSVI surface, with "
                "bid–ask spreads and mid noise",
    _MARKET: "A real chain from a named source, saved as a snapshot (or the latest saved "
             "one); forwards come from put–call parity",
}
_CARRY_LABELS = {REGRESSION: "parity F, D", FIXED_RATE: "parity F", NONE: "flat"}


@dataclass(frozen=True)
class _Result:
    """One job's output: everything on the page describes this (D15)."""

    truth: SSVISurface | None
    fit: SurfaceFit
    mesh: SurfaceMesh | None
    mesh_problem: str
    carry: CarryFit | None = None
    snapshot: int | None = None
    variance: VarianceTermStructure | None = None


@dataclass(frozen=True)
class _Priced:
    """One premium job's output, for the fit it priced (D15)."""

    fit: SurfaceFit
    view: VariancePremiumView | None
    problem: str = ""


@dataclass(frozen=True)
class _History:
    """A history load: the fit priced against it (its view carries the
    overlay's context, as of the chain), or with no fit yet the overlay alone."""

    inputs: tuple[str, str, int]
    """``(symbol, source, lookback days)`` as loaded, for repricing later fits."""
    priced: _Priced | None
    context: VolContext | None = None


_MOSTLY_EXTRAPOLATED = 0.5
"""A K_var with more than this share from the wings is drawn hollow."""
_VIEWS = ("Smiles", "Term structure", "Surface")
_HEATMAP, _3D = "Heatmap", "3D"
_SUBTITLES = {
    "Smiles": "Implied vol vs log-moneyness ln(K/F), one panel per expiry, each on its own "
              "scale",
    "Term structure": "ATM IV and √K_var by expiry (hollow: over half from the wings), "
                      "the quote sum's bid–ask band, the true surface when known, and history",
    "Surface": "Fitted IV over moneyness in ATM standard deviations (k/√θ), expiries as rows",
    _3D: "Fitted IV over k/√θ and days to expiry: drag to rotate, scroll to zoom, hover "
         "for values",
}
_GROUP_WIDTH = 150
_CONTEXT_HINT = ("Overlay a GARCH expected-vol curve and trailing realized vols from any source "
                 "(ibkr reads the local store first), as of the chain. The variance premium "
                 "cards use the same fit.")
_GARCH_TIP = ("GARCH's expected vol over the window's own NYSE sessions, per calendar year, as "
              "implied variance is quoted. The chart's GARCH curve is the same fit but assumes "
              "252 sessions a year, so a window short of sessions (a holiday month) reads lower "
              "here.")
_PREMIUM_SUBTITLE = ("This fit's implied variance against GARCH and realized, from the "
                     "history Compare with history loads")
_HISTORY_SUBTITLE = (f"{HEADLINE_DAYS:g}-day implied (each stored day's first snapshot, by its "
                     "quote sums) against the realized vol after and before it")
_NOT_LOADED = ("No comparison yet",
               ("Press Load history. It uses the source, symbol and lookback of Compare with "
                "history, on the Term structure view: for SPX chains, SPY's bars."))


class VolatilityLabPage(ScrollPage):
    def __init__(self, market_service: MarketService, chains: ChainService,
                 tasks: TaskRunner, state_store: ViewStateStore) -> None:
        super().__init__()
        self._market, self._tasks, self._state_store = market_service, tasks, state_store
        self._chains = chains
        self._carry: CarryFit | None = None
        self._snapshot: int | None = None
        self._fit: SurfaceFit | None = None
        self._truth: SSVISurface | None = None
        self._context: VolContext | None = None
        self._variance: VarianceTermStructure | None = None
        self._premium: VariancePremiumView | None = None
        self._premium_inputs: tuple[str, str, int] | None = None
        self._mesh: SurfaceMesh | None = None
        self._mesh_problem = ""
        self._surface3d: Surface3D | None = None
        saved = {**_DEFAULTS, **{k: str(v) for k, v in state_store.load(_STATE_KEY).items()}}
        if saved["source"] not in market_service.source_names:
            saved["source"] = market_service.source_names[0]
        if saved["chain_source"] not in chains.source_names:
            saved["chain_source"] = chains.source_names[0]
        self._mode = _MARKET if saved["mode"] == _MARKET else _SYNTHETIC
        self._method = saved["fit_method"] if saved["fit_method"] in _METHOD_KEYS else "SVI"
        view = saved.get("tab", _VIEWS[0])
        self._view = view if view in _VIEWS else _VIEWS[0]
        self._surface_mode = _3D if saved.get("surface_mode") == _3D else _HEATMAP

        self._header = PageHeader(
            "Volatility Lab",
            "Implied-vol surfaces from a known SSVI world or a market chain: IVs, SVI fits, "
            "arbitrage checks")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add("chain", self._build_experiment(saved), 0, 0, 12)
        grid.add("views", self._build_views(saved), 0, 7, 9, 20)
        grid.add_column([("fit", self._build_fit_table(), None),
                         ("carry", self._build_carry(), None),
                         ("checks", self._build_checks(), None)],
                        x=9, w=3, y=7, min_width=SIDE_PANEL_WIDTH)
        grid.add_column([("premium", self._build_premium(), None),
                         ("premium_history", self._build_premium_history(), 20)],
                        x=0, w=9, y=27)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)
        self._ensure_surface3d()

    # ------------------------------------------------------------ building
    def _build_experiment(self, saved: dict) -> Card:
        card = self._experiment_card = Card("Chain", _CARD_SUBTITLES[self._mode])
        modes = SegmentedControl([_SYNTHETIC, _MARKET], self._mode)
        modes.changed.connect(self._on_mode)
        card.add_action(modes)
        self._fields: dict[str, Field] = {}

        def field(key: str, caption: str, width: int = FIELD_S, placeholder: str = "") -> Field:
            self._fields[key] = Field(caption, saved[key], width, placeholder=placeholder)
            self._fields[key].committed.connect(self._run)
            return self._fields[key]

        def group(column: QVBoxLayout, title: str, *widgets: QWidget) -> QHBoxLayout:
            row = QHBoxLayout()
            row.setSpacing(SPACE_M)
            row.addWidget(_group_title(title), 0, Qt.AlignBottom)
            for widget in widgets:
                row.addWidget(widget, 0, Qt.AlignBottom)
            row.addStretch(1)
            column.addLayout(row)
            return row

        def run_button(text: str) -> QWidget:
            widget = button(text, "primary", "ph.lightning", on_click=self._run)
            widget.setFixedWidth(FIELD_L)
            return widget

        # The widest row sets the page's minimum width, so the quote-noise
        # inputs get their own row (one long CHAIN row clipped at 1280 px).
        synthetic = QWidget()
        rows = QVBoxLayout(synthetic)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(SPACE_M)
        group(rows, "CHAIN", field("spot", "Spot"), field("rate", "Rate %"),
              field("div", "Div %"), field("expiries", "Expiries (days)", FIELD_L),
              field("strikes", "Strikes / expiry"))
        group(rows, "QUOTE NOISE", field("spread", "Spread %"),
              field("noise", "Noise × ½ spread", FIELD_M),
              field("seed", "Seed", placeholder="random"))
        surface = group(rows, "TRUE SURFACE · SSVI", field("v0", "ATM vol now %"),
                        field("v_inf", "Long-run %"), field("kappa", "κ / yr"),
                        field("rho", "Skew ρ"), field("eta", "Curvature η"))
        surface.addWidget(run_button("Generate & fit"), 0, Qt.AlignBottom)

        market = QWidget()
        rows = QVBoxLayout(market)
        rows.setContentsMargins(0, 0, 0, 0)
        self._chain_source = SelectField("Source", self._chains.source_names,
                                         saved["chain_source"], FIELD_S)
        row = group(rows, "MARKET CHAIN", self._chain_source,
                    field("chain_symbol", "Symbol"),
                    field("targets", "Target expiries (days)", FIELD_L),
                    field("chain_strikes", "Strikes / expiry"),
                    field("chain_rate", "Fallback rate %"))
        self._fields["chain_symbol"].edit.textEdited.connect(
            lambda text: self._fields["chain_symbol"].edit.setText(text.upper()))
        row.addWidget(run_button("Fetch & fit"), 0, Qt.AlignBottom)

        self._groups = {_SYNTHETIC: synthetic, _MARKET: market}
        for widget in self._groups.values():
            card.body.addWidget(widget)
        self._show_mode()
        return card

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._show_mode()

    def _show_mode(self) -> None:
        for mode, widget in self._groups.items():
            widget.setVisible(mode == self._mode)
        self._experiment_card.set_subtitle(_CARD_SUBTITLES[self._mode])

    def _build_views(self, saved: dict) -> Card:
        self._views_card = Card("Surface fit", self._subtitle())
        views = SegmentedControl(list(_VIEWS), self._view)
        views.changed.connect(self._on_view)
        self._views_card.add_action(views)
        self._stack = PageStack()
        self._smiles = SmileGrid()
        self._term = self._build_term(saved)
        self._surface_view = self._build_surface()
        self._empty = EmptyState("mdi6.sigma", "No fit yet",
                                 "Set up the experiment above and press Generate & fit.")
        for widget in (self._smiles, self._term, self._surface_view, self._empty):
            self._stack.addWidget(widget)
        self._stack.setCurrentWidget(self._empty)
        self._stack.setMinimumHeight(420)
        self._views_card.body.addWidget(self._stack, 1)
        return self._views_card

    def _build_term(self, saved: dict) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_M)
        controls = QHBoxLayout()
        controls.setSpacing(SPACE_M)
        controls.addWidget(label("Compare with history", "groupTitle"))
        controls.addSpacing(SPACE_M)
        controls.addWidget(label("Source", "fieldCaption"))
        self._source = Select(min_width=FIELD_M)
        self._source.setFixedWidth(FIELD_M)
        self._source.set_items(self._market.source_names, saved["source"])
        controls.addWidget(self._source)
        self._symbol = Field("Symbol", saved["symbol"], FIELD_S, uppercase=True, inline=True)
        self._lookback = Field("Lookback (days)", saved["lookback"], FIELD_S, inline=True)
        for widget in (self._symbol, self._lookback):
            widget.committed.connect(self._load_history)
            controls.addWidget(widget)
        self._load_button = button("Load", icon_name="ph.download-simple",
                                   on_click=self._load_history)
        controls.addWidget(self._load_button)
        controls.addStretch(1)
        column.addLayout(controls)
        self._context_label = label(_CONTEXT_HINT, "muted")
        column.addWidget(self._context_label)
        self._term_chart = CurveChart(x_axis="price", y_axis="percent", x_name="Days",
                                      x_fmt="{:,.0f}")
        column.addWidget(self._term_chart, 1)
        return holder

    def _build_surface(self) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_M)
        controls = QHBoxLayout()
        controls.setSpacing(SPACE_M)
        self._surface_modes = SegmentedControl([_HEATMAP, _3D], self._surface_mode)
        self._surface_modes.changed.connect(self._on_surface_mode)
        controls.addWidget(self._surface_modes, 0, Qt.AlignTop)
        self._mesh_label = label("", "muted", wrap=True)
        controls.addWidget(self._mesh_label, 1)
        column.addLayout(controls)
        self._surface_stack = QStackedWidget()
        self._surface = Heatmap(x_name="k/√θ", x_fmt="{:+.2f}", value_fmt="{:.2%}")
        self._surface_empty = EmptyState("ph.cube", "No 3D surface", "")
        self._surface_stack.addWidget(self._surface)
        self._surface_stack.addWidget(self._surface_empty)
        column.addWidget(self._surface_stack, 1)
        return holder

    def _build_fit_table(self) -> Card:
        card = self._fit_card = Card("Fit by expiry", "ATM vol: SVI fit vs the true surface")
        self._methods = SegmentedControl(list(_METHOD_KEYS), self._method)
        self._methods.setToolTip("SVI: each expiry fitted on its own (5 parameters each). "
                                 "SSVI: one arbitrage-free surface through every expiry "
                                 "(N + 3 parameters).")
        self._methods.changed.connect(self._on_method)
        card.add_action(self._methods)
        self._table = DataTable([("Expiry", "left"), ("SVI", "right"), ("True", "right"),
                                 ("χ² / pt", "right")], row_height=32)
        self._table.balance_columns()
        self._table.setMinimumHeight(6 * 32 + 36)
        card.body.addWidget(self._table)
        return card

    def _build_carry(self) -> Card:
        card = self._carry_card = Card(
            "Carry by expiry", "From put–call parity; the delayed spot isn't used")
        self._carry_table = DataTable([("Expiry", "left"), ("Forward", "right"),
                                       ("±", "right"), ("From", "right")], row_height=32)
        self._carry_table.balance_columns()
        self._carry_table.setMinimumHeight(6 * 32 + 36)
        card.body.addWidget(self._carry_table)
        card.setVisible(False)
        return card

    def _build_checks(self) -> Card:
        card = Card("Checks")
        self._checks = StatTable([
            ("Arbitrage", (("butterfly", "Butterfly  (min g)"), ("calendar", "Calendar  (min Δw)"))),
            ("Between expiries", (("mesh_butterfly", "Butterfly  (min g)"),
                                  ("mesh_calendar", "Calendar  (min Δw)"))),
            ("Quotes", (("used", "Used in fits"), ("no_bid", "Excluded: no bid"),
                        ("bounds", "Excluded: outside bounds"),
                        ("itm", "Excluded: in the money"))),
        ], value_header="")
        card.body.addWidget(self._checks)
        self._note = label("", "muted", wrap=True)
        card.body.addWidget(self._note)
        return card

    def _build_premium(self) -> Card:
        card = self._premium_card = Card("Variance premium", _PREMIUM_SUBTITLE)
        self._premium_load = button("Load history", icon_name="ph.download-simple",
                                    on_click=self._load_history)
        card.add_action(self._premium_load)
        self._premium_stack = PageStack()
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_M)
        days = f"{HEADLINE_DAYS:g}d"
        self._premium_tiles = {
            "kvar": MetricTile(f"√K_var · {days}"), "quotes": MetricTile(f"Quote sum · {days}"),
            "garch": MetricTile("Against GARCH"), "trailing": MetricTile("Against realized")}
        self._premium_tiles["kvar"].setToolTip(
            "The fitted smile's model-free implied variance, wings included, interpolated "
            "to the tenor in total variance")
        self._premium_tiles["quotes"].setToolTip(
            "The quotes alone, summed as the VIX is (CBOE's strike rules), at the tenor: the "
            "number the history uses")
        self._premium_tiles["garch"].setToolTip(_GARCH_TIP)
        self._premium_tiles["trailing"].setToolTip(
            "The quote sum less the realized vol over the same number of calendar days "
            "before the chain")
        column.addLayout(metric_row(list(self._premium_tiles.values())))
        self._premium_table = DataTable(
            [("Expiry", "left"), ("Sessions", "right"), ("√K_var", "right"),
             ("ATM IV", "right"), ("Wings", "right"), ("GARCH", "right"),
             ("vs GARCH", "right"), ("Realized", "right"), ("vs realized", "right")],
            row_height=32)
        self._premium_table.balance_columns()
        self._premium_table.setMinimumHeight(6 * 32 + 36)
        for col, tip in ((1, "NYSE sessions between the chain and the expiry (decision 6)"),
                         (4, "The share of K_var from the extrapolated wings, past the quotes"),
                         (5, _GARCH_TIP),
                         (7, "Realized vol over as many calendar days before the chain")):
            self._premium_table.horizontalHeaderItem(col).setToolTip(tip)
        column.addWidget(self._premium_table)
        self._premium_notes = label("", "muted", wrap=True)
        column.addWidget(self._premium_notes)
        self._premium_empty = EmptyState("ph.activity", *_NOT_LOADED)
        self._premium_stack.addWidget(body)
        self._premium_stack.addWidget(self._premium_empty)
        self._premium_body = body
        self._premium_stack.setCurrentWidget(self._premium_empty)
        card.body.addWidget(self._premium_stack, 1)
        return card

    def _build_premium_history(self) -> Card:
        card = self._history_card = Card("Premium history", _HISTORY_SUBTITLE)
        self._history_stack = PageStack()
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_M)
        self._history_tiles = {"mean": MetricTile("Mean premium · ex post"),
                               "percentile": MetricTile("Today's percentile"),
                               "windows": MetricTile("Windows")}
        self._history_tiles["mean"].setToolTip(
            "Averaged in variance; shown as √ of the mean implied less √ of the mean realized. "
            "z is the mean over its Newey–West error, which runs small on a short history "
            "(about 85% coverage at one year).")
        self._history_tiles["percentile"].setToolTip(
            "Today's quote sum less trailing realized, among every earlier stored day's")
        column.addLayout(metric_row(list(self._history_tiles.values())))
        self._history_chart = StackedTimeSeries(readout_per_panel=True)
        self._history_chart.setMinimumHeight(260)
        column.addWidget(self._history_chart, 1)
        self._history_empty = EmptyState("ph.chart-line", *_NOT_LOADED)
        self._history_stack.addWidget(body)
        self._history_stack.addWidget(self._history_empty)
        self._history_body = body
        self._history_stack.setCurrentWidget(self._history_empty)
        card.body.addWidget(self._history_stack, 1)
        return card

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if self._fit is None:
            self._run()

    def on_hide(self) -> None:
        state = {key: field.text() for key, field in self._fields.items()}
        state |= {"source": self._source.current(), "symbol": self._symbol.text(),
                  "lookback": self._lookback.text(), "tab": self._view,
                  "surface_mode": self._surface_mode, "mode": self._mode,
                  "chain_source": self._chain_source.current(),
                  "fit_method": self._method}
        self._state_store.save(_STATE_KEY, state)

    # ----------------------------------------------------------------- jobs
    def _run(self) -> None:
        try:
            if self._mode == _MARKET:
                symbol, source, request = self._read_market_inputs()
                method = _METHOD_KEYS[self._method]
                work = lambda: _market_job(self._chains, symbol, source, request,  # noqa: E731
                                           method)
                status = (f"Fetching {symbol} options from {source}…"
                          + (" The day's first IBKR chain also lists each expiry's strikes "
                             "(about 40 s)." if source == "ibkr" else ""))
            else:
                truth, spec = self._read_inputs()
                method = _METHOD_KEYS[self._method]
                work = lambda: _fit_job(truth, spec, method)  # noqa: E731
                status = "Quoting the chain and fitting SVI…"
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        for widget in self._run_buttons():
            widget.setEnabled(False)
        self._header.set_status(status)
        self._tasks.submit(work, on_done=self._on_fit, on_error=self._on_error)

    def _on_method(self, method: str) -> None:
        self._method = method
        if self._fit is None:
            return
        # Refit the chain already on the page (with its carry): no refetch.
        chain, truth = self._fit.chain, self._truth
        carry, snapshot = self._carry, self._snapshot
        key = _METHOD_KEYS[method]
        for widget in self._run_buttons():
            widget.setEnabled(False)
        self._header.set_status(f"Refitting with {method}…")
        self._tasks.submit(lambda: _refit_job(chain, truth, key, carry, snapshot),
                           on_done=self._on_fit, on_error=self._on_error)

    def _run_buttons(self) -> list[QWidget]:
        return [w for group in self._groups.values() for w in group.findChildren(QWidget)
                if w.property("variant") == "primary"]

    def _parse_days(self, key: str) -> tuple[float, ...]:
        field = self._fields[key]
        text = field.text().replace(" ", "")
        try:
            days = tuple(sorted({float(x) for x in text.split(",") if x}))
        except ValueError:
            raise field.fail(f"{key.capitalize()} must be comma-separated days, "
                             f"e.g. 7,30,91") from None
        if not days or min(days) <= 0:
            raise field.fail("Expiries must be positive days")
        return days

    def _read_market_inputs(self) -> tuple[str, str, ChainRequest]:
        f = self._fields
        symbol = f["chain_symbol"].required_text().upper()
        request = ChainRequest(
            target_dtes=self._parse_days("targets"),
            strikes_per_expiry=f["chain_strikes"].number(integer=True, minimum=6),
            rate=f["chain_rate"].number(scale=0.01))
        return symbol, self._chain_source.current(), request

    def _read_inputs(self) -> tuple[SSVISurface, SyntheticChainSpec]:
        f = self._fields
        expiries = self._parse_days("expiries")
        truth = SSVISurface(
            v0=f["v0"].number(scale=0.01) ** 2, v_inf=f["v_inf"].number(scale=0.01) ** 2,
            kappa=f["kappa"].number(), rho=f["rho"].number(), eta=f["eta"].number())
        spec = SyntheticChainSpec(
            spot=f["spot"].number(minimum=0.01), rate=f["rate"].number(scale=0.01),
            dividend_yield=f["div"].number(scale=0.01), expiries=expiries,
            strikes_per_expiry=f["strikes"].number(integer=True, minimum=3),
            relative_spread=f["spread"].number(scale=0.01, minimum=0.0),
            noise=f["noise"].number(minimum=0.0),
            seed=f["seed"].number(integer=True, allow_blank=True))
        return truth, spec

    def _on_fit(self, result: _Result) -> None:
        self._truth, self._fit, self._mesh = result.truth, result.fit, result.mesh
        self._mesh_problem, self._carry, self._snapshot = (result.mesh_problem, result.carry,
                                                            result.snapshot)
        self._variance = result.variance
        for widget in self._run_buttons():
            widget.setEnabled(True)
        fit = self._fit
        chain = fit.chain
        if self._snapshot is not None:
            self._header.set_status(
                f"{chain.symbol} · {chain.source} as of {chain.as_of:%Y-%m-%d %H:%M} · "
                f"snapshot #{self._snapshot} · {len(chain.quotes)} quotes, "
                f"{len(fit.slices)} expiries fitted")
        else:
            self._header.set_status(f"Fitted {len(fit.slices)} expiries from "
                                    f"{len(chain.quotes)} quotes")
        self._render()
        self._reprice()

    def _on_error(self, exc: Exception) -> None:
        for widget in self._run_buttons():
            widget.setEnabled(True)
        self._header.set_status(f"Fit failed: {exc}", error=True)
        _logger.error("volatility lab fit failed: %s", exc)

    def _load_history(self) -> None:
        try:
            symbol = self._symbol.required_text()
            lookback = self._lookback.number(integer=True, minimum=60)
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        source = self._source.current()
        inputs = (symbol, source, lookback)
        fit, market_chain = self._fit, self._truth is None
        for widget in (self._load_button, self._premium_load):
            widget.setEnabled(False)
        self._header.set_status(f"Loading {symbol} from {source} and fitting GARCH…")
        if fit is not None:
            self._show_premium_state("Pricing the variance premium…",
                                     f"Against {symbol} bars from {source}.")
        self._tasks.submit(
            lambda: _history_job(self._market, self._chains, fit, market_chain, inputs),
            on_done=self._on_context, on_error=self._on_context_error)

    def _on_context(self, history: _History) -> None:
        self._premium_inputs = history.inputs
        for widget in (self._load_button, self._premium_load):
            widget.setEnabled(True)
        symbol, priced = history.inputs[0], history.priced
        if priced is None:                   # loaded before any fit: as of now
            self._show_context(history.context)
            self._header.set_status(f"Loaded {symbol} history")
            self._reprice()                  # a fit that landed during the load
            return
        if priced.fit is self._fit:
            self._on_priced(priced)
        else:
            self._reprice()                  # a fit that landed during the load
        if priced.view is None:
            self._header.set_status(f"History load failed: {priced.problem}", error=True)
        else:
            self._header.set_status(f"Loaded {symbol} history as of {priced.view.as_of:%Y-%m-%d}")

    def _show_context(self, context: VolContext | None, why: str = "") -> None:
        """The overlay: GARCH's curve and the trailing vols, as of the chain once
        a fit is priced, so the chart and the cards share one GARCH fit."""
        self._context = context
        if context is None:
            self._context_label.setText(why or _CONTEXT_HINT)
        else:
            p = context.garch
            long_run = float(np.sqrt(p.unconditional_variance * TRADING_DAYS_PER_YEAR))
            realized = " · ".join(f"{w}d {vol:.1%}" for w, vol in context.realized)
            self._context_label.setText(
                f"{context.symbol} · {context.source} · {context.returns_used:,} daily returns "
                f"to {context.history_end:%Y-%m-%d} · GARCH α {p.alpha:.3f} β {p.beta:.3f}, "
                f"long-run {long_run:.1%}" + (f" · realized {realized}" if realized else ""))
        self._render_term_structure()

    def _on_context_error(self, exc: Exception) -> None:
        for widget in (self._load_button, self._premium_load):
            widget.setEnabled(True)
        self._header.set_status(f"History load failed: {exc}", error=True)
        if self._premium is not None:
            self._render_premium()          # the fit's earlier pricing still holds
        else:
            self._show_premium_state(*_NOT_LOADED)

    def _reprice(self) -> None:
        """Price the fit on the page against the history last loaded, if any.
        The cards and the overlay are blank until it lands, so they never show
        an earlier fit, or another day's GARCH."""
        if self._premium_inputs is None or self._fit is None:
            return
        fit, market_chain, inputs = self._fit, self._truth is None, self._premium_inputs
        self._premium = None
        pricing = f"Pricing this fit against {inputs[0]} bars from {inputs[1]}…"
        self._show_premium_state("Pricing the variance premium…", pricing)
        self._show_context(None, pricing)
        self._tasks.submit(
            lambda: _premium_job(self._market, self._chains, fit, market_chain, inputs),
            on_done=self._on_priced, on_error=self._on_premium_error)

    def _on_priced(self, priced: _Priced) -> None:
        if priced.fit is not self._fit:
            return           # a fit since replaced; its own job is queued behind this one
        self._premium = view = priced.view
        if view is None:
            self._show_premium_state("No comparison", priced.problem)
            self._show_context(None, f"No comparison: {priced.problem}")
            return
        self._show_context(view.context, next(
            (n for n in view.notes if n.startswith("No GARCH")), ""))
        self._render_premium()

    def _on_premium_error(self, exc: Exception) -> None:
        self._show_premium_state("No comparison", f"Pricing failed: {exc}")
        self._show_context(None, f"Pricing failed: {exc}")
        _logger.error("variance premium failed: %s", exc)

    # --------------------------------------------------------------- render
    def _on_view(self, view: str) -> None:
        self._view = view
        self._views_card.set_subtitle(self._subtitle())
        self._show_view()

    def _on_surface_mode(self, mode: str) -> None:
        self._surface_mode = mode
        self._views_card.set_subtitle(self._subtitle())
        self._show_surface()

    def _subtitle(self) -> str:
        if self._view == "Surface" and self._surface_mode == _3D:
            return _SUBTITLES[_3D]
        return _SUBTITLES[self._view]

    def _ensure_surface3d(self) -> Surface3D:
        if self._surface3d is None:
            self._surface3d = Surface3D()
            self._surface_stack.addWidget(self._surface3d)
            if self._mesh is not None:
                self._surface3d.set_mesh(self._mesh)
        return self._surface3d

    def _show_surface(self) -> None:
        if self._surface_mode == _HEATMAP:
            self._surface_stack.setCurrentWidget(self._surface)
            self._mesh_label.setText("")
            return
        if self._mesh is None:
            self._surface_empty.set_text("No 3D surface", self._mesh_problem)
            self._surface_stack.setCurrentWidget(self._surface_empty)
            self._mesh_label.setText("")
            return
        self._surface_stack.setCurrentWidget(self._ensure_surface3d())
        share = float(self._mesh.extrapolated.mean())
        self._mesh_label.setText(
            "White lines are the fitted expiries; between them, total variance is "
            "interpolated linearly in T at fixed k"
            + (f", and {share:.0%} of the mesh lies past a nearer expiry's quotes "
               "(SVI wings, extrapolated)" if share else "")
            + (". Orange wireframe: the true surface." if self._mesh.true_iv is not None
               else "."))

    def _show_view(self) -> None:
        if self._fit is None:
            return
        if not self._fit.slices and self._view != "Smiles":
            self._empty.set_text("No fitted expiries",
                                 "No expiry had enough usable quotes to fit.")
            self._stack.setCurrentWidget(self._empty)
            return
        self._stack.setCurrentWidget(
            {"Smiles": self._smiles, "Term structure": self._term,
             "Surface": self._surface_view}[self._view])
        if self._view == "Surface":
            self._show_surface()

    def _render(self) -> None:
        fit = self._fit
        self._smiles.set_smiles(smile_curves(fit, self._truth))
        if fit.slices:
            z, dte, iv = surface_grid(fit)
            self._surface.set_grid(z, [f"{d:.0f}d" for d in dte], iv)
        if self._mesh is not None and self._surface3d is not None:
            self._surface3d.set_mesh(self._mesh)
        self._render_term_structure()
        self._render_diagnostics()
        self._show_view()

    def _render_term_structure(self) -> None:
        fit, truth, context = self._fit, self._truth, self._context
        if fit is None or not fit.slices:
            return
        days = np.array([s.dte for s in fit.slices], dtype=float)
        atm = np.array([s.atm_vol for s in fit.slices])
        grid = np.linspace(1.0, max(days.max(), 365.0), 200)
        curves = []
        if truth is not None:
            curves.append(Curve("True ATM vol", grid,
                                np.array([truth.atm_vol(d / 365.0) for d in grid]),
                                PALETTE.text_secondary, 1.2, dashed=True, fmt="{:.2%}"))
        if context is not None:
            curves.append(Curve(f"GARCH forecast · {context.symbol}", grid,
                                context.garch_vol(grid), PALETTE.ramp[2], 1.6, fmt="{:.2%}"))
        curves.append(Curve("ATM IV (SVI fit)", days, atm, PALETTE.series, 1.8, fmt="{:.2%}",
                            marker="o"))
        band = None
        if self._variance is not None:
            expiries = self._variance.expiries
            curves.append(Curve(
                "√K_var (fit)", np.array([e.dte for e in expiries], dtype=float),
                np.array([e.smile.vol for e in expiries]), PALETTE.series_2, 1.6,
                fmt="{:.2%}", marker="o",
                hollow=tuple(e.smile.extrapolated_share > _MOSTLY_EXTRAPOLATED
                             for e in expiries)))
            quoted = [e for e in expiries if e.quotes is not None]
            if len(quoted) >= 2:
                band = (np.array([e.dte for e in quoted], dtype=float),
                        np.array([e.quotes.vol_band[0] for e in quoted]),
                        np.array([e.quotes.vol_band[1] for e in quoted]), "Quote sum bid–ask")
        if context is not None and context.realized:
            curves.append(Curve(
                "Realized (trailing)",
                np.array([VolContext.calendar_days(w) for w, _ in context.realized]),
                np.array([vol for _, vol in context.realized]), PALETTE.ramp[3], 1.0,
                fmt="{:.2%}", marker="d", line=False, readout=False))
        self._term_chart.set_curves(curves, band=band, band_fmt="{:.2%}",
                                    band_color=PALETTE.series_2)

    def _show_premium_state(self, title: str, body: str) -> None:
        """Both premium cards blank, saying why (not loaded, pricing, a problem)."""
        for empty, stack in ((self._premium_empty, self._premium_stack),
                             (self._history_empty, self._history_stack)):
            empty.set_text(title, body)
            stack.setCurrentWidget(empty)
        self._premium_card.set_subtitle(_PREMIUM_SUBTITLE)
        self._history_card.set_subtitle(_HISTORY_SUBTITLE)

    def _render_premium(self) -> None:
        view = self._premium
        tiles = self._premium_tiles
        days = f"{view.tenor_days:g}d"
        self._premium_card.set_subtitle(
            f"{view.symbol} as of {view.as_of:%Y-%m-%d %H:%M} against {view.realized_symbol} "
            f"bars from {view.source}")
        tenor, quotes = view.tenor, view.quote_tenor
        if tenor is not None:
            tiles["kvar"].set_value(f"{tenor.vol:.1%}", hint=(
                f"ATM {tenor.smile.atm_vol:.1%} · wings {tenor.smile.extrapolated_share:.0%}"))
        else:
            tiles["kvar"].set_value("—", hint=f"no two fitted expiries bracket {days}")
        if quotes is not None:
            low, high = quotes.vol_band
            tiles["quotes"].set_value(f"{quotes.vol:.1%}", hint=f"bid–ask {low:.1%}–{high:.1%}")
        else:
            tiles["quotes"].set_value("—", hint=f"no two quote sums bracket {days}")
        garch, trailing = view.garch_tenor, view.trailing
        tiles["garch"].set_value(_points(garch), hint=(
            f"GARCH {garch.against_vol:.1%}, {view.tenor_trading_days} sessions" if garch
            else "no GARCH comparison"))
        tiles["trailing"].set_value(_points(trailing), hint=(
            f"realized {trailing.against_vol:.1%}, {days} before" if trailing
            else "no trailing window"))
        self._premium_table.set_rows([
            [f"{e.dte:.0f}d", (str(e.trading_days), PALETTE.text_secondary),
             f"{e.implied.vol:.1%}", f"{e.implied.atm_vol:.1%}",
             (f"{e.implied.extrapolated_share:.0%}",
              PALETTE.warning if e.implied.extrapolated_share > _MOSTLY_EXTRAPOLATED
              else PALETTE.text_secondary),
             f"{e.garch.against_vol:.1%}" if e.garch else "—", _points(e.garch),
             f"{e.trailing.against_vol:.1%}" if e.trailing else "—", _points(e.trailing)]
            for e in view.expiries])
        self._premium_notes.setText(" ".join(view.notes))
        self._premium_notes.setVisible(bool(view.notes))
        self._premium_stack.setCurrentWidget(self._premium_body)
        self._render_history()

    def _render_history(self) -> None:
        view = self._premium
        ex_post, trailing = view.ex_post, view.trailing_history
        days = sorted({p.as_of.date() for p in (*ex_post.points, *trailing.points)})
        if len(days) < 2:
            self._history_empty.set_text("No history yet", self._no_history_reason(view))
            self._history_stack.setCurrentWidget(self._history_empty)
            self._history_card.set_subtitle(_HISTORY_SUBTITLE)
            return
        self._history_card.set_subtitle(
            f"{view.symbol}: {view.tenor_days:g}-day implied on {len(days)} stored days, by "
            f"their quote sums, against {view.realized_symbol}'s realized vol after and before")
        summary = ex_post.summary()
        tiles = self._history_tiles
        if summary is not None:
            z = summary.mean / summary.error if summary.error > 0 else float("nan")
            tiles["mean"].set_value(
                f"{summary.vol_points * 100:+.1f} pts",
                hint=f"z {z:+.1f} · {summary.windows} windows, {summary.independent} "
                     "independent")
        else:
            tiles["mean"].set_value("—", hint=(
                f"from {MIN_INDEPENDENT_WINDOWS} windows that don't overlap"))
        earlier = sum(p.as_of.date() < view.as_of.date() for p in trailing.points)
        if view.trailing_percentile is not None:
            tiles["percentile"].set_value(_ordinal(round(100 * view.trailing_percentile)),
                                          hint=f"against realized, of {earlier} earlier days")
        else:
            tiles["percentile"].set_value("—", hint=(
                f"{earlier} earlier days; ranks from {MIN_PERCENTILE_HISTORY}"))
        tiles["windows"].set_value(
            f"{len(ex_post.points)} complete",
            hint=f"{ex_post.pending} pending · {len(ex_post.excluded)} left out")
        tiles["windows"].setToolTip("\n".join(
            f"{e.as_of:%Y-%m-%d}: {e.reason}" for e in ex_post.excluded[:20]))

        after = {p.as_of.date(): p.premium for p in ex_post.points}
        before = {p.as_of.date(): p.premium for p in trailing.points}
        implied = {d: (before.get(d) or after[d]).implied_vol for d in days}

        def series(premiums: dict, value) -> np.ndarray:
            return np.array([value(premiums[d]) if d in premiums else np.nan for d in days])

        def vol_points(premium: Premium) -> float:
            return 100 * premium.vol_points

        self._history_chart.set_panels(pd.DatetimeIndex(days), [
            Panel(f"{view.tenor_days:g}-day vol", [
                Series("Implied (quote sum)", np.array([implied[d] for d in days]),
                       PALETTE.series_2, fmt="{:.1%}"),
                Series("Realized after", series(after, lambda p: p.against_vol),
                       PALETTE.series, fmt="{:.1%}"),
                Series("Realized before", series(before, lambda p: p.against_vol),
                       PALETTE.ramp[2], 1.2, dashed=True, fmt="{:.1%}")],
                  axis="percent", stretch=3),
            Panel("Premium, vol points", [
                Series("Ex post", series(after, vol_points), PALETTE.series, fmt="{:+.1f}"),
                Series("Against trailing", series(before, vol_points), PALETTE.ramp[2], 1.2,
                       dashed=True, fmt="{:+.1f}")],
                  stretch=2, zero_line=True,
                  references=([(f"mean {100 * summary.vol_points:+.1f}",
                                100 * summary.vol_points)] if summary is not None else ())),
        ])
        self._history_stack.setCurrentWidget(self._history_body)

    def _no_history_reason(self, view: VariancePremiumView) -> str:
        if self._truth is not None:
            return ("A synthetic chain has no history. A market chain's is its stored "
                    "snapshots, one a day (D63).")
        if view.days_with_snapshots == 0:
            return next((n for n in view.notes if n.startswith("No stored")),
                        f"No stored {view.symbol} chains.")
        return (f"{view.days_with_snapshots} stored day(s) of {view.symbol} so far. The chart "
                "needs two, and each day's window completes "
                f"{view.tenor_days:g} calendar days later.")

    def _render_diagnostics(self) -> None:
        fit, truth = self._fit, self._truth
        market = truth is None
        against = "the provider's model IV" if market else "the true surface"
        joint = fit.joint
        self._fit_card.set_subtitle(
            f"Joint SSVI · ρ {joint.params.rho:+.2f} · η {joint.params.eta:.2f} · "
            f"γ {joint.params.gamma:.2f} · ATM vs {against}" if joint is not None
            else f"Independent SVI · ATM vs {against}")
        self._table.horizontalHeaderItem(2).setText("IBKR" if market else "True")
        self._table.horizontalHeaderItem(2).setToolTip(
            "Median of the provider's own model IVs for the four quotes nearest the "
            "forward" if market
            else "ATM vol of the true SSVI surface")

        def reference(s) -> str:
            if not market:
                return f"{truth.atm_vol(s.years):.2%}"
            iv = _reference_atm_iv(fit.chain, s.dte)
            return f"{iv:.2%}" if iv is not None else "—"

        self._table.set_rows([
            [f"{s.dte:.0f}d", f"{s.atm_vol:.2%}", (reference(s), PALETTE.text_secondary),
             f"{s.fit.chi2_per_point:.2f}"]
            for s in fit.slices])
        carry = self._carry
        self._carry_card.setVisible(carry is not None)
        if carry is not None:
            self._carry_table.set_rows([
                [f"{e.dte:.0f}d", f"{e.forward:,.2f}" if e.forward else "—",
                 f"{e.forward_se:.3f}" if e.forward_se is not None else "—",
                 _CARRY_LABELS[e.method]]
                for e in carry.expiries])
        g = min((s.min_butterfly_g for s in fit.slices), default=float("nan"))
        gap = min(fit.calendar_gaps, default=float("inf"))
        self._checks.set_value("butterfly",
                               f"{'ok' if fit.butterfly_free else 'VIOLATED'} · {g:.3f}",
                               tone="good" if fit.butterfly_free else "critical")
        self._checks.set_value("calendar",
                               ("ok" if fit.calendar_free else "VIOLATED")
                               + (f" · {gap:.5f}" if np.isfinite(gap) else ""),
                               tone="good" if fit.calendar_free else "critical")
        mesh = self._mesh
        if mesh is None:
            self._checks.set_value("mesh_butterfly", "—")
            self._checks.set_value("mesh_calendar", "—")
        else:
            butterfly_ok, calendar_ok = mesh.min_butterfly_g >= 0, mesh.min_calendar_gap >= 0
            self._checks.set_value(
                "mesh_butterfly",
                f"{'ok' if butterfly_ok else 'VIOLATED'} · {mesh.min_butterfly_g:.3f}",
                tone="good" if butterfly_ok else "critical")
            self._checks.set_value(
                "mesh_calendar",
                f"{'ok' if calendar_ok else 'VIOLATED'} · {mesh.min_calendar_gap:.5f}",
                tone="good" if calendar_ok else "critical")
        ivs = fit.ivs
        self._checks.set_value("used", f"{int(ivs.usable.sum())} / {len(ivs.usable)}")
        self._checks.set_value("no_bid", str(ivs.excluded_no_bid))
        self._checks.set_value("bounds", str(ivs.excluded_out_of_bounds))
        self._checks.set_value("itm", str(ivs.excluded_in_the_money))
        skipped = ", ".join(f"{d:g}d" for d in fit.skipped_expiries)
        self._note.setText((f"Skipped (under 6 usable quotes): {skipped}. " if skipped else "")
                           + "χ²/pt ≈ noise² when residuals are pure quote noise.")


def _fit_job(truth: SSVISurface, spec: SyntheticChainSpec, method: str) -> _Result:
    """Worker thread: quote, fit, and mesh the surface (or say why it can't)."""
    fit = fit_surface(synthetic_chain(truth, spec), method=method)
    mesh, problem = _mesh(fit, truth)
    return _Result(truth, fit, mesh, problem, variance=_variance(fit))


def _refit_job(chain, truth: SSVISurface | None, method: str, carry: CarryFit | None,
               snapshot: int | None) -> _Result:
    """Worker thread: the same chain (carry included), another fit method."""
    fit = fit_surface(chain, method=method)
    mesh, problem = _mesh(fit, truth)
    return _Result(truth, fit, mesh, problem, carry, snapshot, _variance(fit))


def _market_job(chains: ChainService, symbol: str, source: str,
                request: ChainRequest, method: str) -> _Result:
    """Worker thread: fetch (and save) the chain, imply each expiry's carry from
    parity, then fit and mesh exactly as for a synthetic chain."""
    chain, snapshot = chains.get_chain(symbol, source, request)
    carry = implied_carry(chain)
    fit = fit_surface(carry.chain, method=method)
    mesh, problem = _mesh(fit, None)
    return _Result(None, fit, mesh, problem, carry, snapshot, _variance(fit))


def _variance(fit: SurfaceFit) -> VarianceTermStructure | None:
    """The fit's model-free implied variance, or None with no fitted expiry."""
    return implied_variance(fit) if fit.slices else None


def _premium_job(market: MarketService, chains: ChainService, fit: SurfaceFit,
                 market_chain: bool, inputs: tuple[str, str, int]) -> _Priced:
    """Worker thread: this fit's variance against the history (``inputs``).
    A market chain's stored snapshots are its history; a synthetic one has none."""
    symbol, source, lookback = inputs
    if not fit.slices:
        return _Priced(fit, None, "No fitted expiries to price.")
    try:
        view = load_variance_premium(market, chains, fit, source, symbol,
                                     lookback_days=lookback, history=market_chain)
    except ValueError as exc:
        return _Priced(fit, None, str(exc))
    return _Priced(fit, view)


def _history_job(market: MarketService, chains: ChainService, fit: SurfaceFit | None,
                 market_chain: bool, inputs: tuple[str, str, int]) -> _History:
    """Worker thread: the fit priced against the history, which carries the
    overlay's GARCH as of the chain; with no fit yet, the overlay as of now."""
    if fit is None:
        symbol, source, lookback = inputs
        return _History(inputs, None, load_vol_context(market, symbol, source, lookback))
    return _History(inputs, _premium_job(market, chains, fit, market_chain, inputs))


def _points(premium: Premium | None) -> str:
    """A premium in vol points, for reading (its average is in variance)."""
    return "—" if premium is None else f"{100 * premium.vol_points:+.1f} pts"


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _mesh(fit: SurfaceFit, truth: SSVISurface | None) -> tuple[SurfaceMesh | None, str]:
    try:
        return surface_mesh(fit, truth), ""
    except ValueError as exc:
        return None, f"Can't draw it: {exc}."


def _reference_atm_iv(chain, dte: float, nearest: int = 4) -> float | None:
    """The median of the provider's model IVs for the quotes nearest this
    expiry's forward. One quote's delayed model value can be stale (live:
    SPY 92d showed 24.8% against a 13.8% smile), and a median of a few
    near-the-money values isn't swayed by one."""
    forward = chain.forward(dte)
    quotes = sorted((q for q in chain.quotes if q.dte == dte and q.reference_iv is not None),
                    key=lambda q: abs(q.strike - forward))[:nearest]
    if not quotes:
        return None
    return float(np.median([q.reference_iv for q in quotes]))


def _group_title(text: str):
    title = label(text, "groupTitle")
    title.setFixedWidth(_GROUP_WIDTH)
    title.setMinimumHeight(34)
    title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
    return title
