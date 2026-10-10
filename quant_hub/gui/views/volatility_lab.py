"""Volatility Lab: fit an implied-volatility surface to a chain and score it.

Flow: SYNTHETIC CHAIN card → one TaskRunner job (quote a chain off a known
SSVI surface → invert mids to IV → fit raw SVI per expiry → arbitrage
checks) → one immutable ``SurfaceFit`` that every tab and the diagnostics
panel render from. Like the Simulator's D15 invariant, nothing on screen can
describe a different draw.

The fit consumes an ``OptionChain``, so a real IBKR chain slots into the
same job once chain data exists. Until then the *model* side of the term
structure already uses real history: "compare with history" loads a source
(synthetic or ibkr/SQLite), fits GARCH, and overlays its expected-vol curve
and trailing realized vols on the ATM IV term structure
(``services/vol_context.py``).
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

import numpy as np

from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import PALETTE, SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from quant_hub.gui.views.base import View
from quant_hub.gui.views.lab_widgets import (
    CHART_STYLE, combo, embed_figure, entry, heading, parse_float, stat_rows,
)
from quant_hub.models.svi import SSVISurface
from quant_hub.models.vol_surface import (
    SurfaceFit, SyntheticChainSpec, fit_surface, smile_curves, surface_grid, synthetic_chain,
)
from quant_hub.services.market_service import MarketService
from quant_hub.services.vol_context import VolContext, load_vol_context
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.visualization.charts import (
    plot_message, plot_smiles, plot_vol_heatmap, plot_vol_term_structure,
)

_logger = logging.getLogger(__name__)
_STATE_KEY = "volatility_lab"

_DEFAULTS = {
    "spot": "100", "rate": "4", "div": "0", "expiries": "7,30,60,91,182,365",
    "strikes": "21", "spread": "4", "noise": "0.5", "seed": "7",
    "v0": "25", "v_inf": "20", "kappa": "2", "rho": "-0.6", "eta": "1.2",
    "source": "synthetic", "symbol": "SPY", "lookback": "1825",
}


class VolatilityLabView(View):
    def __init__(
        self, parent: tk.Misc, market_service: MarketService, tasks: TaskRunner,
        state_store: ViewStateStore,
    ) -> None:
        super().__init__(parent)
        self._market, self._tasks, self._state_store = market_service, tasks, state_store
        self._fit: SurfaceFit | None = None
        self._truth: SSVISurface | None = None
        self._context: VolContext | None = None
        saved = state_store.load(_STATE_KEY)
        self._vars = {key: tk.StringVar(value=str(saved.get(key, value)))
                      for key, value in _DEFAULTS.items()}
        if self._vars["source"].get() not in market_service.source_names:
            self._vars["source"].set(market_service.source_names[0])

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=290)
        self.rowconfigure(2, weight=1)
        header = ttk.Frame(self, style="Surface.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=SPACE_L,
                    pady=(SPACE_M, SPACE_XS))
        ttk.Label(header, text="VOLATILITY LAB", style="ViewTitle.TLabel").pack(side="left")
        self._message = ttk.Label(header, text="", style="Muted.TLabel")
        self._message.pack(side="right")
        self._build_controls()
        self._build_tabs()
        self._build_diagnostics()

    # -- layout ---------------------------------------------------------

    def _build_controls(self) -> None:
        card = ttk.Frame(self, style="Card.TFrame", padding=(SPACE_S, SPACE_XS, SPACE_S, SPACE_S))
        card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=SPACE_L, pady=(0, SPACE_S))
        ttk.Label(card, text="SYNTHETIC CHAIN", style="CardHeading.TLabel").grid(
            row=0, column=0, columnspan=6, sticky="w", padx=SPACE_S, pady=(2, 4))
        v = self._vars
        entry(card, "spot", v["spot"], 1, 0, width=7)
        entry(card, "rate %", v["rate"], 1, 2, width=5)
        entry(card, "div %", v["div"], 1, 4, width=5)
        entry(card, "expiries (days)", v["expiries"], 1, 6, width=22)
        entry(card, "strikes", v["strikes"], 1, 8, width=4)
        entry(card, "spread %", v["spread"], 1, 10, width=4)
        entry(card, "noise ×½spread", v["noise"], 1, 12, width=4)
        entry(card, "seed", v["seed"], 1, 14, width=5)
        ttk.Label(card, text="TRUE SURFACE (SSVI)", style="CardHeading.TLabel").grid(
            row=2, column=0, columnspan=6, sticky="w", padx=SPACE_S, pady=(SPACE_XS, 2))
        entry(card, "ATM vol now %", v["v0"], 3, 0, width=5)
        entry(card, "long-run %", v["v_inf"], 3, 2, width=5)
        entry(card, "κ / yr", v["kappa"], 3, 4, width=5)
        entry(card, "skew ρ", v["rho"], 3, 6, width=6)
        entry(card, "curvature η", v["eta"], 3, 8, width=4)
        self._run_button = ttk.Button(card, text="Generate & fit", style="Accent.TButton",
                                      command=self._run)
        self._run_button.grid(row=3, column=10, columnspan=6, sticky="ew", padx=SPACE_S)

    def _build_tabs(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew", padx=(SPACE_L, SPACE_S), pady=(0, SPACE_L))

        smiles = ttk.Frame(notebook, style="Surface.TFrame")
        notebook.add(smiles, text="Smiles")
        self._smile_fig, self._smile_canvas = embed_figure(smiles)

        term = ttk.Frame(notebook, style="Surface.TFrame")
        notebook.add(term, text="Term structure")
        bar = ttk.Frame(term, style="Card.TFrame", padding=(0, SPACE_XS))
        bar.pack(fill="x", pady=(SPACE_S, 0))
        ttk.Label(bar, text="compare with history", style="CardHeading.TLabel").grid(
            row=0, column=0, padx=SPACE_S)
        combo(bar, "source", self._vars["source"], self._market.source_names, 0, 1, width=9)
        entry(bar, "symbol", self._vars["symbol"], 0, 3, width=7, on_commit=self._load_history)
        entry(bar, "lookback days", self._vars["lookback"], 0, 5, width=5,
              on_commit=self._load_history)
        self._load_button = ttk.Button(bar, text="Load", command=self._load_history)
        self._load_button.grid(row=0, column=7, padx=SPACE_S)
        self._context_label = ttk.Label(term, text="GARCH forecast and realized vols appear "
                                        "after Load (works on ibkr history today)",
                                        style="Muted.TLabel")
        self._context_label.pack(anchor="w", pady=(2, 0))
        chart = ttk.Frame(term, style="Surface.TFrame")
        chart.pack(fill="both", expand=True)
        self._term_fig, self._term_canvas = embed_figure(chart)

        surface = ttk.Frame(notebook, style="Surface.TFrame")
        notebook.add(surface, text="Surface")
        self._surface_fig, self._surface_canvas = embed_figure(surface)

        for fig in (self._smile_fig, self._term_fig, self._surface_fig):
            plot_message(fig, "generate a chain to fit", CHART_STYLE)

    def _build_diagnostics(self) -> None:
        panel = ttk.Frame(self, style="Card.TFrame", padding=SPACE_S)
        panel.grid(row=2, column=1, sticky="nsew", padx=(0, SPACE_L), pady=(0, SPACE_L))
        heading(panel, "FIT BY EXPIRY  (ATM vol)")
        self._table = ttk.Treeview(panel, columns=("exp", "fit", "true", "chi2"),
                                   show="headings", height=6, selectmode="none")
        for col, text, width in (("exp", "expiry", 55), ("fit", "SVI", 70), ("true", "true", 70),
                                 ("chi2", "χ²/pt", 60)):
            self._table.heading(col, text=text)
            self._table.column(col, width=width, anchor="e", stretch=True)
        self._table.pack(fill="x", pady=(2, SPACE_S))
        heading(panel, "ARBITRAGE CHECKS")
        self._values = stat_rows(panel, [
            ("butterfly", "butterfly (min g)"), ("calendar", "calendar (min Δw)"),
        ])
        ttk.Separator(panel).pack(fill="x", pady=SPACE_XS)
        heading(panel, "QUOTES")
        self._values.update(stat_rows(panel, [
            ("used", "used in fits"), ("no_bid", "excluded: no bid"),
            ("bounds", "excluded: outside bounds"),
        ]))
        self._note = ttk.Label(panel, text="", style="Muted.TLabel", wraplength=270,
                               justify="left")
        self._note.pack(anchor="w", pady=(SPACE_S, 0))

    # -- lifecycle ------------------------------------------------------

    def on_show(self) -> None:
        if self._fit is None:
            self._run()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {key: var.get() for key, var in self._vars.items()})

    # -- jobs -----------------------------------------------------------

    def _run(self) -> None:
        try:
            truth, spec = self._read_inputs()
        except ValueError as exc:
            self._report(str(exc), True)
            return
        self._run_button.configure(state="disabled")
        self._report("quoting chain and fitting SVI…", False)
        self._tasks.submit(lambda: (truth, fit_surface(synthetic_chain(truth, spec))),
                           on_done=self._on_fit, on_error=self._on_error)

    def _read_inputs(self) -> tuple[SSVISurface, SyntheticChainSpec]:
        v = self._vars
        try:
            expiries = tuple(sorted({float(x) for x in v["expiries"].get().replace(" ", "").split(",") if x}))
            strikes = int(v["strikes"].get())
            seed_text = v["seed"].get().strip()
            seed = int(seed_text) if seed_text else None
        except ValueError:
            raise ValueError("expiries must be comma-separated days; strikes and seed whole numbers") from None
        truth = SSVISurface(
            v0=parse_float(v["v0"], "ATM vol now", 0.01) ** 2,
            v_inf=parse_float(v["v_inf"], "long-run vol", 0.01) ** 2,
            kappa=parse_float(v["kappa"], "κ"),
            rho=parse_float(v["rho"], "skew ρ"),
            eta=parse_float(v["eta"], "curvature η"),
        )
        spec = SyntheticChainSpec(
            spot=parse_float(v["spot"], "spot"), rate=parse_float(v["rate"], "rate", 0.01),
            dividend_yield=parse_float(v["div"], "dividend yield", 0.01), expiries=expiries,
            strikes_per_expiry=strikes, relative_spread=parse_float(v["spread"], "spread", 0.01),
            noise=parse_float(v["noise"], "noise"), seed=seed,
        )
        if spec.spot <= 0:
            raise ValueError("spot must be positive")
        return truth, spec

    def _on_fit(self, result: tuple[SSVISurface, SurfaceFit]) -> None:
        self._truth, self._fit = result
        self._run_button.configure(state="normal")
        fit = self._fit
        self._report(f"fitted {len(fit.slices)} expiries from {len(fit.chain.quotes)} quotes", False)
        self._render()

    def _load_history(self) -> None:
        v = self._vars
        try:
            lookback = int(v["lookback"].get())
        except ValueError:
            self._report("lookback days must be a whole number", True)
            return
        source, symbol = v["source"].get(), v["symbol"].get()
        self._load_button.configure(state="disabled")
        self._report(f"loading {symbol.strip().upper()} from {source} and fitting GARCH…", False)
        self._tasks.submit(lambda: load_vol_context(self._market, symbol, source, lookback),
                           on_done=self._on_context, on_error=self._on_context_error)

    def _on_context(self, context: VolContext) -> None:
        self._context = context
        self._load_button.configure(state="normal")
        p = context.garch
        self._context_label.configure(text=(
            f"{context.symbol} · {context.source} · {context.returns_used} daily returns to "
            f"{context.history_end:%Y-%m-%d} · GARCH α={p.alpha:.3f} β={p.beta:.3f} "
            f"long-run {np.sqrt(p.unconditional_variance * 252):.1%}"
        ))
        self._report(f"loaded {context.symbol} history", False)
        self._render_term_structure()

    def _on_context_error(self, exc: Exception) -> None:
        self._load_button.configure(state="normal")
        self._report(f"history load failed: {exc}", True)

    def _on_error(self, exc: Exception) -> None:
        self._run_button.configure(state="normal")
        self._report(f"fit failed: {exc}", True)
        _logger.error("volatility lab fit failed: %s", exc)

    # -- rendering ------------------------------------------------------

    def _render(self) -> None:
        fit, truth = self._fit, self._truth
        plot_smiles(self._smile_fig, smile_curves(fit, truth), CHART_STYLE)
        self._smile_canvas.draw_idle()
        if fit.slices:
            plot_vol_heatmap(self._surface_fig, *surface_grid(fit), style=CHART_STYLE)
        else:
            plot_message(self._surface_fig, "no expiry had enough usable quotes", CHART_STYLE)
        self._surface_canvas.draw_idle()
        self._render_term_structure()
        self._render_diagnostics()

    def _render_term_structure(self) -> None:
        fit = self._fit
        if fit is None or not fit.slices:
            return
        days = np.array([s.dte for s in fit.slices])
        grid = np.linspace(1.0, max(days.max(), 365.0), 200)
        context = self._context
        realized = None
        if context is not None:
            realized = [(VolContext.calendar_days(w), vol) for w, vol in context.realized]
        plot_vol_term_structure(
            self._term_fig, days, np.array([s.atm_vol for s in fit.slices]), grid,
            np.array([self._truth.atm_vol(d / 365.0) for d in grid]) if self._truth else None,
            context.garch_vol(grid) if context else None,
            f"GARCH forecast · {context.symbol}" if context else "", realized, CHART_STYLE,
        )
        self._term_canvas.draw_idle()

    def _render_diagnostics(self) -> None:
        fit, truth = self._fit, self._truth
        self._table.delete(*self._table.get_children())
        for s in fit.slices:
            true_atm = f"{truth.atm_vol(s.years):.2%}" if truth else "—"
            self._table.insert("", "end", values=(
                f"{s.dte:g}d", f"{s.atm_vol:.2%}", true_atm, f"{s.fit.chi2_per_point:.2f}"))
        g = min((s.min_butterfly_g for s in fit.slices), default=float("nan"))
        gap = min(fit.calendar_gaps, default=float("inf"))
        self._values["butterfly"].configure(
            text=f"{'ok' if fit.butterfly_free else 'VIOLATED'} · {g:.3f}",
            foreground=PALETTE.text if fit.butterfly_free else PALETTE.critical)
        self._values["calendar"].configure(
            text=("ok" if fit.calendar_free else "VIOLATED")
            + (f" · {gap:.5f}" if np.isfinite(gap) else ""),
            foreground=PALETTE.text if fit.calendar_free else PALETTE.critical)
        ivs = fit.ivs
        self._values["used"].configure(text=f"{int(ivs.usable.sum())} / {len(ivs.usable)}")
        self._values["no_bid"].configure(text=str(ivs.excluded_no_bid))
        self._values["bounds"].configure(text=str(ivs.excluded_out_of_bounds))
        skipped = ", ".join(f"{d:g}d" for d in fit.skipped_expiries)
        self._note.configure(text=(
            (f"Skipped (under 6 usable quotes): {skipped}. " if skipped else "")
            + "χ²/pt ≈ noise² if residuals are pure quote noise."
        ))

    def _report(self, message: str, is_error: bool) -> None:
        self._message.configure(text=message,
                                foreground=PALETTE.critical if is_error else PALETTE.muted)
