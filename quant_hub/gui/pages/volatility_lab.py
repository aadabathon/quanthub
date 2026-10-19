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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Curve, CurveChart, Heatmap, SmileGrid
from quant_hub.gui.components import (
    Card,
    DataTable,
    EmptyState,
    Field,
    PageHeader,
    PageStack,
    SegmentedControl,
    Select,
    SelectField,
    StatTable,
    button,
    label,
)
from quant_hub.gui.pages.base import ScrollPage
from quant_hub.gui.surface3d import Surface3D
from quant_hub.gui.theme import (
    FIELD_L,
    FIELD_M,
    FIELD_S,
    PALETTE,
    SIDE_PANEL_WIDTH,
    SPACE_L,
    SPACE_M,
)
from quant_hub.data.options import ChainRequest
from quant_hub.models.carry import FIXED_RATE, NONE, REGRESSION, CarryFit, implied_carry
from quant_hub.models.svi import SSVISurface
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
from quant_hub.services.vol_context import VolContext, load_vol_context
from quant_hub.storage.state_store import ViewStateStore

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
_VIEWS = ("Smiles", "Term structure", "Surface")
_HEATMAP, _3D = "Heatmap", "3D"
_SUBTITLES = {
    "Smiles": "Implied vol vs log-moneyness ln(K/F), one panel per expiry, each on its own "
              "scale",
    "Term structure": "At-the-money vol by expiry: the SVI fit, the true surface when "
                      "known, and history",
    "Surface": "Fitted IV over moneyness in ATM standard deviations (k/√θ), expiries as rows",
    _3D: "Fitted IV over k/√θ and days to expiry: drag to rotate, scroll to zoom, hover "
         "for values",
}
_GROUP_WIDTH = 150


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
        self.body.addWidget(self._build_experiment(saved))

        main = QHBoxLayout()
        main.setSpacing(SPACE_L)
        self.body.addLayout(main, 1)
        main.addWidget(self._build_views(saved), 1)
        side = QWidget()
        side.setFixedWidth(SIDE_PANEL_WIDTH)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_L)
        column.addWidget(self._build_fit_table())
        column.addWidget(self._build_carry())
        column.addWidget(self._build_checks())
        column.addStretch(1)
        main.addWidget(side)
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
        self._context_label = label(
            "Overlay a GARCH expected-vol curve and trailing realized vols from any source "
            "(ibkr reads the local store first).", "muted")
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
        self._load_button.setEnabled(False)
        self._header.set_status(f"Loading {symbol} from {source} and fitting GARCH…")
        self._tasks.submit(lambda: load_vol_context(self._market, symbol, source, lookback),
                           on_done=self._on_context, on_error=self._on_context_error)

    def _on_context(self, context: VolContext) -> None:
        self._context = context
        self._load_button.setEnabled(True)
        p = context.garch
        long_run = float(np.sqrt(p.unconditional_variance * TRADING_DAYS_PER_YEAR))
        realized = " · ".join(f"{w}d {vol:.1%}" for w, vol in context.realized)
        self._context_label.setText(
            f"{context.symbol} · {context.source} · {context.returns_used:,} daily returns to "
            f"{context.history_end:%Y-%m-%d} · GARCH α {p.alpha:.3f} β {p.beta:.3f}, long-run "
            f"{long_run:.1%}" + (f" · realized {realized}" if realized else ""))
        self._header.set_status(f"Loaded {context.symbol} history")
        self._render_term_structure()

    def _on_context_error(self, exc: Exception) -> None:
        self._load_button.setEnabled(True)
        self._header.set_status(f"History load failed: {exc}", error=True)

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
        if context is not None and context.realized:
            curves.append(Curve(
                "Realized (trailing)",
                np.array([VolContext.calendar_days(w) for w, _ in context.realized]),
                np.array([vol for _, vol in context.realized]), PALETTE.ramp[3], 1.0,
                fmt="{:.2%}", marker="d", line=False, readout=False))
        self._term_chart.set_curves(curves)

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
    return _Result(truth, fit, mesh, problem)


def _refit_job(chain, truth: SSVISurface | None, method: str, carry: CarryFit | None,
               snapshot: int | None) -> _Result:
    """Worker thread: the same chain (carry included), another fit method."""
    fit = fit_surface(chain, method=method)
    mesh, problem = _mesh(fit, truth)
    return _Result(truth, fit, mesh, problem, carry, snapshot)


def _market_job(chains: ChainService, symbol: str, source: str,
                request: ChainRequest, method: str) -> _Result:
    """Worker thread: fetch (and save) the chain, imply each expiry's carry from
    parity, then fit and mesh exactly as for a synthetic chain."""
    chain, snapshot = chains.get_chain(symbol, source, request)
    carry = implied_carry(chain)
    fit = fit_surface(carry.chain, method=method)
    mesh, problem = _mesh(fit, None)
    return _Result(None, fit, mesh, problem, carry, snapshot)


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
