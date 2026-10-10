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
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

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
    StatTable,
    button,
    label,
)
from quant_hub.gui.pages.base import ScrollPage
from quant_hub.gui.theme import (
    FIELD_L,
    FIELD_M,
    FIELD_S,
    PALETTE,
    SIDE_PANEL_WIDTH,
    SPACE_L,
    SPACE_M,
)
from quant_hub.models.svi import SSVISurface
from quant_hub.models.vol_surface import (
    SurfaceFit,
    SyntheticChainSpec,
    fit_surface,
    smile_curves,
    surface_grid,
    synthetic_chain,
)
from quant_hub.services.market_service import MarketService
from quant_hub.services.vol_context import VolContext, load_vol_context
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "volatility_lab"     # shared with the Tk page
_DEFAULTS = {
    "spot": "100", "rate": "4", "div": "0", "expiries": "7,30,60,91,182,365",
    "strikes": "21", "spread": "4", "noise": "0.5", "seed": "7",
    "v0": "25", "v_inf": "20", "kappa": "2", "rho": "-0.6", "eta": "1.2",
    "source": "synthetic", "symbol": "SPY", "lookback": "1825",
}
_VIEWS = ("Smiles", "Term structure", "Surface")
_SUBTITLES = {
    "Smiles": "Implied vol vs log-moneyness ln(K/F), one panel per expiry, each on its own "
              "scale",
    "Term structure": "At-the-money vol by expiry: SVI fit vs the true surface, and vs history",
    "Surface": "Fitted IV over moneyness in ATM standard deviations (k/√θ), expiries as rows",
}
_GROUP_WIDTH = 150


class VolatilityLabPage(ScrollPage):
    def __init__(self, market_service: MarketService, tasks: TaskRunner,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._market, self._tasks, self._state_store = market_service, tasks, state_store
        self._fit: SurfaceFit | None = None
        self._truth: SSVISurface | None = None
        self._context: VolContext | None = None
        saved = {**_DEFAULTS, **{k: str(v) for k, v in state_store.load(_STATE_KEY).items()}}
        if saved["source"] not in market_service.source_names:
            saved["source"] = market_service.source_names[0]
        view = saved.get("tab", _VIEWS[0])
        self._view = view if view in _VIEWS else _VIEWS[0]

        self._header = PageHeader(
            "Volatility Lab",
            "Implied-vol surface from an option chain: quote a known SSVI world, invert to IV, "
            "fit SVI per expiry, and test for arbitrage")
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
        column.addWidget(self._build_checks())
        column.addStretch(1)
        main.addWidget(side)

    # ------------------------------------------------------------ building
    def _build_experiment(self, saved: dict) -> Card:
        card = Card("Experiment", "A synthetic chain quoted off a known arbitrage-free SSVI "
                                  "surface, with bid–ask spreads and mid noise")
        self._fields: dict[str, Field] = {}

        def field(key: str, caption: str, width: int = FIELD_S, placeholder: str = "") -> Field:
            self._fields[key] = Field(caption, saved[key], width, placeholder=placeholder)
            self._fields[key].committed.connect(self._run)
            return self._fields[key]

        chain = QHBoxLayout()
        chain.setSpacing(SPACE_M)
        chain.addWidget(_group_title("CHAIN"), 0, Qt.AlignBottom)
        for widget in (field("spot", "Spot"), field("rate", "Rate %"), field("div", "Div %"),
                       field("expiries", "Expiries (days)", FIELD_L),
                       field("strikes", "Strikes / expiry"), field("spread", "Spread %"),
                       field("noise", "Noise × ½ spread", FIELD_M),
                       field("seed", "Seed", placeholder="random")):
            chain.addWidget(widget, 0, Qt.AlignBottom)
        chain.addStretch(1)
        card.body.addLayout(chain)

        surface = QHBoxLayout()
        surface.setSpacing(SPACE_M)
        surface.addWidget(_group_title("TRUE SURFACE · SSVI"), 0, Qt.AlignBottom)
        for widget in (field("v0", "ATM vol now %"), field("v_inf", "Long-run %"),
                       field("kappa", "κ / yr"), field("rho", "Skew ρ"),
                       field("eta", "Curvature η")):
            surface.addWidget(widget, 0, Qt.AlignBottom)
        surface.addStretch(1)
        self._run_button = button("Generate & fit", "primary", "ph.lightning",
                                  on_click=self._run)
        self._run_button.setFixedWidth(FIELD_L)
        surface.addWidget(self._run_button, 0, Qt.AlignBottom)
        card.body.addLayout(surface)
        return card

    def _build_views(self, saved: dict) -> Card:
        self._views_card = Card("Surface fit", _SUBTITLES[self._view])
        views = SegmentedControl(list(_VIEWS), self._view)
        views.changed.connect(self._on_view)
        self._views_card.add_action(views)
        self._stack = PageStack()
        self._smiles = SmileGrid()
        self._term = self._build_term(saved)
        self._surface = Heatmap(x_name="k/√θ", x_fmt="{:+.2f}", value_fmt="{:.2%}")
        self._empty = EmptyState("mdi6.sigma", "No fit yet",
                                 "Set up the experiment above and press Generate & fit.")
        for widget in (self._smiles, self._term, self._surface, self._empty):
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

    def _build_fit_table(self) -> Card:
        card = Card("Fit by expiry", "ATM vol: SVI fit vs the true surface")
        self._table = DataTable([("Expiry", "left"), ("SVI", "right"), ("True", "right"),
                                 ("χ² / pt", "right")], row_height=32)
        self._table.balance_columns()
        self._table.setMinimumHeight(6 * 32 + 36)
        card.body.addWidget(self._table)
        return card

    def _build_checks(self) -> Card:
        card = Card("Checks")
        self._checks = StatTable([
            ("Arbitrage", (("butterfly", "Butterfly  (min g)"), ("calendar", "Calendar  (min Δw)"))),
            ("Quotes", (("used", "Used in fits"), ("no_bid", "Excluded: no bid"),
                        ("bounds", "Excluded: outside bounds"))),
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
                  "lookback": self._lookback.text(), "tab": self._view}
        self._state_store.save(_STATE_KEY, state)

    # ----------------------------------------------------------------- jobs
    def _run(self) -> None:
        try:
            truth, spec = self._read_inputs()
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._run_button.setEnabled(False)
        self._header.set_status("Quoting the chain and fitting SVI…")
        self._tasks.submit(lambda: (truth, fit_surface(synthetic_chain(truth, spec))),
                           on_done=self._on_fit, on_error=self._on_error)

    def _read_inputs(self) -> tuple[SSVISurface, SyntheticChainSpec]:
        f = self._fields
        text = f["expiries"].text().replace(" ", "")
        try:
            expiries = tuple(sorted({float(x) for x in text.split(",") if x}))
        except ValueError:
            raise f["expiries"].fail("Expiries must be comma-separated days, e.g. 7,30,91") \
                from None
        if not expiries or min(expiries) <= 0:
            raise f["expiries"].fail("Expiries must be positive days")
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

    def _on_fit(self, result: tuple[SSVISurface, SurfaceFit]) -> None:
        self._truth, self._fit = result
        self._run_button.setEnabled(True)
        fit = self._fit
        self._header.set_status(f"Fitted {len(fit.slices)} expiries from "
                                f"{len(fit.chain.quotes)} quotes")
        self._render()

    def _on_error(self, exc: Exception) -> None:
        self._run_button.setEnabled(True)
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
        self._views_card.set_subtitle(_SUBTITLES[view])
        self._show_view()

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
             "Surface": self._surface}[self._view])

    def _render(self) -> None:
        fit = self._fit
        self._smiles.set_smiles(smile_curves(fit, self._truth))
        if fit.slices:
            z, dte, iv = surface_grid(fit)
            self._surface.set_grid(z, [f"{d:g}d" for d in dte], iv)
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
        self._table.set_rows([
            [f"{s.dte:g}d", f"{s.atm_vol:.2%}",
             (f"{truth.atm_vol(s.years):.2%}" if truth else "—", PALETTE.text_secondary),
             f"{s.fit.chi2_per_point:.2f}"]
            for s in fit.slices])
        g = min((s.min_butterfly_g for s in fit.slices), default=float("nan"))
        gap = min(fit.calendar_gaps, default=float("inf"))
        self._checks.set_value("butterfly",
                               f"{'ok' if fit.butterfly_free else 'VIOLATED'} · {g:.3f}",
                               tone="good" if fit.butterfly_free else "critical")
        self._checks.set_value("calendar",
                               ("ok" if fit.calendar_free else "VIOLATED")
                               + (f" · {gap:.5f}" if np.isfinite(gap) else ""),
                               tone="good" if fit.calendar_free else "critical")
        ivs = fit.ivs
        self._checks.set_value("used", f"{int(ivs.usable.sum())} / {len(ivs.usable)}")
        self._checks.set_value("no_bid", str(ivs.excluded_no_bid))
        self._checks.set_value("bounds", str(ivs.excluded_out_of_bounds))
        skipped = ", ".join(f"{d:g}d" for d in fit.skipped_expiries)
        self._note.setText((f"Skipped (under 6 usable quotes): {skipped}. " if skipped else "")
                           + "χ²/pt ≈ noise² when residuals are pure quote noise.")


def _group_title(text: str):
    title = label(text, "groupTitle")
    title.setFixedWidth(_GROUP_WIDTH)
    title.setMinimumHeight(34)
    title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
    return title
