"""Simulator: controlled stochastic experiments (GBM + Monte Carlo).

Moved from the old Dashboard in M6 — the laboratory is a research tool, not
the application's overview. Synthetic only: there are deliberately no
market-data controls here; real instruments live on the Markets page.

One piece of state drives everything: the latest ``(simulation, analysis,
monte-carlo summary)`` triple, produced in a single TaskRunner job so every
chart and every stat describes the same draw (the D15 invariant, inherited).
True parameters are known, so the stats panel shows TRUE vs ESTIMATE and
the rolling-vol chart gets a σ reference line.

No math happens in this file — only input parsing, task submission, and
formatting. Analytics come from ``data.stats``/``data.simulation``; rendering
from ``visualization.charts``. GBM is the only process today; when a second
process lands (OU, jump diffusion, …) the process card grows a selector —
nothing in the page's structure assumes GBM beyond that card.
"""

from __future__ import annotations

import logging
import math
import tkinter as tk
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from quant_hub.config import AppConfig
from quant_hub.data.simulation import (
    GBMSimulationResult,
    MonteCarloSummary,
    monte_carlo_summary,
    run_gbm_simulation,
)
from quant_hub.data.stats import SeriesAnalysis, analyze_history
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import (
    FONT_MONO,
    FONT_MONO_LG,
    FONT_MONO_SM,
    FONT_SMALL,
    PALETTE,
    SPACE_L,
    SPACE_M,
    SPACE_S,
    SPACE_XS,
)
from quant_hub.gui.views.base import View
from quant_hub.visualization.charts import (
    ChartStyle,
    create_figure,
    plot_lab_overview,
    plot_message,
    plot_monte_carlo,
)

_logger = logging.getLogger(__name__)

# Map the GUI theme onto the chart's style contract once, here.
_CHART_STYLE = ChartStyle(
    surface=PALETTE.surface,
    text=PALETTE.text,
    text_secondary=PALETTE.text_secondary,
    muted=PALETTE.muted,
    grid=PALETTE.grid,
    baseline=PALETTE.border,
    line=PALETTE.accent,
)

# Stats-panel rows: (attribute of ReturnStats, label, format)
_SERIES_ROWS: list[tuple[str, str, str]] = [
    ("start_price", "Start price", "price"),
    ("end_price", "End price", "price"),
    ("total_return", "Total return", "signed_pct"),
    ("cagr", "CAGR", "signed_pct"),
    ("annualized_vol", "Volatility (ann.)", "pct"),
    ("sharpe", "Sharpe (rf=0)", "signed_num"),
    ("max_drawdown", "Max drawdown", "signed_pct"),
    ("skewness", "Skewness (log r)", "signed_num"),
    ("excess_kurtosis", "Excess kurtosis", "signed_num"),
    ("n_observations", "Observations", "int"),
]


def _fmt(value: float, kind: str) -> str:
    if kind == "int":
        return str(int(value))
    if isinstance(value, float) and math.isnan(value):
        return "n/a"
    if kind == "price":
        return f"{value:,.2f}"
    if kind == "pct":
        return f"{value:.2%}"
    if kind == "signed_pct":
        return f"{value:+.2%}"
    if kind == "signed_num":
        return f"{value:+.2f}"
    raise ValueError(f"unknown format {kind!r}")


def _signed_color(value: float) -> str:
    if isinstance(value, float) and (math.isnan(value) or value == 0):
        return PALETTE.text
    return PALETTE.good if value > 0 else PALETTE.critical


class SimulatorView(View):
    def __init__(
        self,
        parent: tk.Misc,
        tasks: TaskRunner,
        config: AppConfig,
    ) -> None:
        super().__init__(parent)
        self._tasks = tasks
        self._config = config

        # The single dashboard state; every render reads only these three.
        self._sim: GBMSimulationResult | None = None
        self._analysis: SeriesAnalysis | None = None
        self._mc: MonteCarloSummary | None = None

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=300)
        self.rowconfigure(2, weight=1)

        self._build_header()
        self._build_controls()
        self._build_charts()
        self._build_stats_panel()

    # ------------------------------------------------------------------ layout
    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Surface.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew",
                    padx=SPACE_L, pady=(SPACE_M, SPACE_XS))
        self._symbol_label = ttk.Label(header, text="—", style="ViewTitle.TLabel")
        self._symbol_label.pack(side="left")
        self._price_label = tk.Label(
            header, text="", font=FONT_MONO_LG,
            background=PALETTE.surface, foreground=PALETTE.text,
        )
        self._price_label.pack(side="left", padx=(SPACE_L, SPACE_S))
        self._meta_label = ttk.Label(header, text="", style="Muted.TLabel")
        self._meta_label.pack(side="left", pady=(6, 0))
        self._message = ttk.Label(header, text="", style="Muted.TLabel")
        self._message.pack(side="right")

    def _field(
        self, parent: ttk.Frame, caption: str, initial: str,
        width: int, row: int, column: int,
    ) -> tk.StringVar:
        """A captioned mono entry inside a control card grid."""
        ttk.Label(parent, text=caption, style="Field.TLabel").grid(
            row=row, column=column, sticky="w", padx=(SPACE_S, 2), pady=2
        )
        var = tk.StringVar(value=initial)
        entry = ttk.Entry(parent, textvariable=var, width=width, font=FONT_MONO)
        entry.grid(row=row, column=column + 1, sticky="w", padx=(0, SPACE_XS), pady=2)
        return var

    def _build_controls(self) -> None:
        c = self._config
        bar = ttk.Frame(self, style="Surface.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew",
                 padx=SPACE_L, pady=(0, SPACE_S))

        # -- simulation card ----------------------------------------------
        sim = ttk.Frame(bar, style="Card.TFrame", padding=(SPACE_S, SPACE_XS, SPACE_S, SPACE_S))
        sim.pack(side="left", fill="y")
        ttk.Label(sim, text="GBM PROCESS", style="CardHeading.TLabel").grid(
            row=0, column=0, columnspan=8, sticky="w", padx=SPACE_S, pady=(2, 4)
        )
        self._s0_var = self._field(sim, "S₀", f"{c.default_initial_price:g}", 7, 1, 0)
        self._mu_var = self._field(sim, "μ %/yr", f"{c.default_mu * 100:g}", 6, 1, 2)
        self._sigma_var = self._field(sim, "σ %/yr", f"{c.default_sigma * 100:g}", 6, 1, 4)
        self._days_var = self._field(sim, "days", str(c.default_sim_days), 6, 1, 6)
        self._seed_var = self._field(sim, "seed", str(c.default_seed), 7, 2, 0)
        self._paths_var = self._field(sim, "paths", str(c.default_n_paths), 6, 2, 2)
        self._run_button = ttk.Button(
            sim, text="Run Simulation", style="Accent.TButton",
            command=self._run_simulation,
        )
        self._run_button.grid(row=2, column=4, columnspan=4, sticky="e",
                              padx=SPACE_S, pady=2)

        # -- analysis card --------------------------------------------------
        analysis = ttk.Frame(bar, style="Card.TFrame",
                             padding=(SPACE_S, SPACE_XS, SPACE_S, SPACE_S))
        analysis.pack(side="left", fill="y", padx=(SPACE_S, 0))
        ttk.Label(analysis, text="ANALYSIS", style="CardHeading.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=SPACE_S, pady=(2, 4)
        )
        ttk.Label(analysis, text="vol window", style="Field.TLabel").grid(
            row=1, column=0, sticky="w", padx=(SPACE_S, 2), pady=2
        )
        self._window_var = tk.StringVar(value=str(c.default_vol_window))
        window_spin = ttk.Spinbox(
            analysis, textvariable=self._window_var, from_=2, to=252,
            width=5, font=FONT_MONO, command=self._reanalyze,
        )
        window_spin.grid(row=1, column=1, sticky="w", pady=2)
        window_spin.bind("<Return>", lambda _e: self._reanalyze())
        ttk.Label(analysis, text="histogram", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", padx=(SPACE_S, 2), pady=2
        )
        self._returns_var = tk.StringVar(value="log")
        returns_box = ttk.Combobox(
            analysis, textvariable=self._returns_var,
            values=["log", "simple"], state="readonly", width=7,
        )
        returns_box.grid(row=2, column=1, sticky="w", pady=2)
        returns_box.bind("<<ComboboxSelected>>", lambda _e: self._render_all())

    def _build_charts(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.grid(row=2, column=0, sticky="nsew",
                      padx=(SPACE_L, SPACE_S), pady=(0, SPACE_L))

        def tab(title: str) -> tuple[ttk.Frame, FigureCanvasTkAgg]:
            frame = ttk.Frame(notebook, style="Surface.TFrame")
            notebook.add(frame, text=title)
            figure = create_figure(_CHART_STYLE)
            canvas = FigureCanvasTkAgg(figure, master=frame)
            widget = canvas.get_tk_widget()
            widget.configure(background=PALETTE.surface, highlightthickness=0)
            widget.pack(fill="both", expand=True)
            return figure, canvas

        self._fig_overview, self._overview_canvas = tab("Path Analysis")
        self._fig_mc, self._mc_canvas = tab("Monte Carlo")
        plot_message(self._fig_overview, "run a simulation or load a symbol", _CHART_STYLE)
        plot_message(self._fig_mc, "run a simulation to populate Monte Carlo", _CHART_STYLE)

    def _build_stats_panel(self) -> None:
        panel = ttk.Frame(self, style="Card.TFrame", padding=SPACE_S)
        panel.grid(row=2, column=1, sticky="nsew", padx=(0, SPACE_L), pady=(0, SPACE_L))
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=1)
        self._stat_values: dict[str, tk.Label] = {}
        row = 0

        def value_label(r: int, col: int, key: str, columnspan: int = 1) -> None:
            label = tk.Label(
                panel, text="—", font=FONT_MONO_SM, anchor="e",
                background=PALETTE.surface, foreground=PALETTE.text,
            )
            label.grid(row=r, column=col, columnspan=columnspan, sticky="e",
                       padx=(SPACE_S, 0), pady=1)
            self._stat_values[key] = label

        # -- process: true vs estimate ------------------------------------
        ttk.Label(panel, text="PROCESS", style="CardHeading.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        for col, caption in ((1, "TRUE"), (2, "ESTIMATE")):
            tk.Label(
                panel, text=caption, font=FONT_MONO_SM, anchor="e",
                background=PALETTE.surface, foreground=PALETTE.muted,
            ).grid(row=row, column=col, sticky="e", padx=(SPACE_S, 0))
        row += 1
        for key, caption in (
            ("mu", "μ  drift"),
            ("sigma", "σ  volatility"),
            ("log_drift", "μ − σ²/2"),
        ):
            ttk.Label(panel, text=caption, style="Secondary.TLabel", font=FONT_SMALL).grid(
                row=row, column=0, sticky="w", pady=1
            )
            value_label(row, 1, f"true_{key}")
            value_label(row, 2, f"est_{key}")
            row += 1

        ttk.Separator(panel).grid(row=row, column=0, columnspan=3, sticky="ew",
                                  pady=SPACE_XS)
        row += 1

        # -- series metrics ------------------------------------------------
        ttk.Label(panel, text="SERIES", style="CardHeading.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        row += 1
        for key, caption, _fmt_kind in _SERIES_ROWS:
            ttk.Label(panel, text=caption, style="Secondary.TLabel", font=FONT_SMALL).grid(
                row=row, column=0, sticky="w", pady=1
            )
            value_label(row, 1, key, columnspan=2)
            row += 1

        ttk.Separator(panel).grid(row=row, column=0, columnspan=3, sticky="ew",
                                  pady=SPACE_XS)
        row += 1

        # -- Monte Carlo terminal distribution ----------------------------
        ttk.Label(panel, text="TERMINAL S(T)", style="CardHeading.TLabel").grid(
            row=row, column=0, sticky="w", pady=(0, 2)
        )
        row += 1
        for key, caption in (
            ("mc_expected", "E[S(T)] theory"),
            ("mc_mean", "Mean (sample)"),
            ("mc_median", "Median (sample)"),
            ("mc_band", "5–95% band"),
        ):
            ttk.Label(panel, text=caption, style="Secondary.TLabel", font=FONT_SMALL).grid(
                row=row, column=0, sticky="w", pady=1
            )
            value_label(row, 1, key, columnspan=2)
            row += 1

    # ------------------------------------------------------------------ events
    def on_show(self) -> None:
        if self._analysis is None:
            self._run_simulation()

    def _set_busy(self, busy: bool) -> None:
        self._run_button.configure(state="disabled" if busy else "normal")

    def _show_message(self, text: str, error: bool = False) -> None:
        self._message.configure(
            text=text, foreground=PALETTE.critical if error else PALETTE.muted
        )

    # ------------------------------------------------------------------ actions
    def _parse_window(self) -> int:
        return int(self._window_var.get())

    def _run_simulation(self) -> None:
        try:
            s0 = float(self._s0_var.get())
            mu = float(self._mu_var.get()) / 100.0
            sigma = float(self._sigma_var.get()) / 100.0
            days = int(self._days_var.get())
            n_paths = int(self._paths_var.get())
            seed_text = self._seed_var.get().strip()
            seed = int(seed_text) if seed_text else None
            window = self._parse_window()
        except ValueError:
            self._show_message("inputs must be numeric (seed may be blank)", error=True)
            return

        self._set_busy(True)
        self._show_message("simulating…")

        def work() -> tuple[GBMSimulationResult, SeriesAnalysis, MonteCarloSummary]:
            sim = run_gbm_simulation(
                initial_price=s0, mu=mu, sigma=sigma,
                n_days=days, seed=seed, n_paths=n_paths,
            )
            return sim, analyze_history(sim.history, vol_window=window), monte_carlo_summary(sim)

        self._tasks.submit(work, on_done=self._on_sim_done, on_error=self._on_error)

    def _reanalyze(self) -> None:
        """Vol-window changed: recompute analytics on the current series
        without re-simulating (the draw stays fixed; only the estimator
        window moves)."""
        if self._analysis is None:
            return
        try:
            window = self._parse_window()
        except ValueError:
            self._show_message("vol window must be an integer", error=True)
            return
        history = self._analysis.history
        self._tasks.submit(
            lambda: analyze_history(history, vol_window=window),
            on_done=self._on_reanalyzed,
            on_error=self._on_error,
        )

    # ------------------------------------------------------------------ results
    def _on_sim_done(
        self, result: tuple[GBMSimulationResult, SeriesAnalysis, MonteCarloSummary]
    ) -> None:
        self._sim, self._analysis, self._mc = result
        self._set_busy(False)
        self._show_message("")
        self._render_all()

    def _on_reanalyzed(self, analysis: SeriesAnalysis) -> None:
        self._analysis = analysis
        self._show_message("")
        self._render_all()

    def _on_error(self, exc: Exception) -> None:
        self._set_busy(False)
        self._show_message(str(exc), error=True)
        _logger.error("dashboard task failed: %s", exc)

    # ------------------------------------------------------------------ render
    def _render_all(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
        sim = self._sim

        plot_lab_overview(
            self._fig_overview,
            analysis,
            _CHART_STYLE,
            return_kind=self._returns_var.get(),
            true_sigma=sim.params.sigma if sim else None,
        )
        self._overview_canvas.draw_idle()

        if sim is not None and self._mc is not None:
            plot_monte_carlo(self._fig_mc, sim, self._mc, _CHART_STYLE)
        else:
            plot_message(self._fig_mc, "Monte Carlo requires a simulation run", _CHART_STYLE)
        self._mc_canvas.draw_idle()

        self._render_header(analysis)
        self._render_stats(analysis)

    def _render_header(self, analysis: SeriesAnalysis) -> None:
        history = analysis.history
        self._symbol_label.configure(text=history.symbol)
        self._price_label.configure(text=f"{analysis.stats.end_price:,.2f}")
        if self._sim is not None:
            p = self._sim.params
            seed = "random" if p.seed is None else str(p.seed)
            meta = (
                f"μ {p.mu:.2%} · σ {p.sigma:.2%} · T {self._sim.horizon_years:.2f}y"
                f" · {self._sim.n_paths} paths · seed {seed}"
            )
        else:
            meta = f"last close {history.end:%Y-%m-%d} · source {history.source}"
        self._meta_label.configure(text=meta)

    def _render_stats(self, analysis: SeriesAnalysis) -> None:
        stats, est = analysis.stats, analysis.gbm

        # Process: true column only defined for simulations.
        if self._sim is not None:
            p = self._sim.params
            true_values = {
                "true_mu": _fmt(p.mu, "signed_pct"),
                "true_sigma": _fmt(p.sigma, "pct"),
                "true_log_drift": _fmt(p.mu - 0.5 * p.sigma**2, "signed_pct"),
            }
        else:
            true_values = {k: "—" for k in ("true_mu", "true_sigma", "true_log_drift")}
        for key, text in true_values.items():
            self._stat_values[key].configure(text=text, foreground=PALETTE.text_secondary)
        self._stat_values["est_mu"].configure(text=_fmt(est.mu_hat, "signed_pct"))
        self._stat_values["est_sigma"].configure(text=_fmt(est.sigma_hat, "pct"))
        self._stat_values["est_log_drift"].configure(
            text=_fmt(est.log_drift_hat, "signed_pct")
        )

        signed_keys = {"total_return", "cagr", "sharpe", "max_drawdown"}
        for key, _caption, fmt_kind in _SERIES_ROWS:
            value = getattr(stats, key)
            color = _signed_color(value) if key in signed_keys else PALETTE.text
            self._stat_values[key].configure(text=_fmt(value, fmt_kind), foreground=color)

        mc = self._mc
        mc_values = {
            "mc_expected": _fmt(mc.expected, "price") if mc else "—",
            "mc_mean": _fmt(mc.mean, "price") if mc else "—",
            "mc_median": _fmt(mc.median, "price") if mc else "—",
            "mc_band": (
                f"{mc.percentiles[5]:,.2f} – {mc.percentiles[95]:,.2f}" if mc else "—"
            ),
        }
        for key, text in mc_values.items():
            self._stat_values[key].configure(text=text)
