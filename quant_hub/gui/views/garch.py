"""GARCH Lab: fit conditional volatility to synthetic or explicitly sourced history."""

from __future__ import annotations

import logging
import math
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import FONT_MONO, FONT_MONO_LG, FONT_MONO_SM, PALETTE, SPACE_L, SPACE_M, SPACE_S, SPACE_XS
from quant_hub.gui.views.base import View
from quant_hub.models.garch import GARCH11Fit, GARCH11Parameters, fit_garch11, simulate_garch11
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.visualization.charts import ChartStyle, create_figure, plot_garch_overview, plot_message

_logger = logging.getLogger(__name__)
_STATE_KEY = "garch_lab"
_CHART_STYLE = ChartStyle(surface=PALETTE.surface, text=PALETTE.text, text_secondary=PALETTE.text_secondary, muted=PALETTE.muted, grid=PALETTE.grid, baseline=PALETTE.border, line=PALETTE.accent)


class GARCHLabView(View):
    """Interactive GARCH(1,1) fitting; all model and source work is off-thread."""

    def __init__(self, parent: tk.Misc, market_service: MarketService, tasks: TaskRunner, state_store: ViewStateStore) -> None:
        super().__init__(parent)
        self._market, self._tasks, self._state_store = market_service, tasks, state_store
        self._returns: pd.Series | None = None
        self._fit: GARCH11Fit | None = None
        saved = state_store.load(_STATE_KEY)
        names = market_service.source_names
        default_source = saved.get("source", "ibkr")
        if default_source not in names:
            default_source = names[0]
        self._mode_var = tk.StringVar(value=saved.get("mode", "Synthetic GARCH"))
        self._source_var = tk.StringVar(value=default_source)
        self._symbol_var = tk.StringVar(value=saved.get("symbol", "SPY"))
        self._lookback_var = tk.StringVar(value=saved.get("lookback", "1825"))
        self._mean_var = tk.StringVar(value=saved.get("mean", "0.02"))
        self._omega_var = tk.StringVar(value=saved.get("omega", "4"))
        self._alpha_var = tk.StringVar(value=saved.get("alpha", "0.08"))
        self._beta_var = tk.StringVar(value=saved.get("beta", "0.88"))
        self._days_var = tk.StringVar(value=saved.get("days", "2520"))
        self._seed_var = tk.StringVar(value=saved.get("seed", "42"))

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=285)
        self.rowconfigure(2, weight=1)
        self._build_header()
        self._build_controls(names)
        self._build_chart()
        self._build_stats()

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Surface.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=SPACE_L, pady=(SPACE_M, SPACE_XS))
        ttk.Label(header, text="GARCH(1,1) VOLATILITY LAB", style="ViewTitle.TLabel").pack(side="left")
        self._message = ttk.Label(header, text="", style="Muted.TLabel")
        self._message.pack(side="right")

    def _field(self, parent: ttk.Frame, text: str, var: tk.StringVar, row: int, col: int, width: int = 8) -> None:
        ttk.Label(parent, text=text, style="Field.TLabel").grid(row=row, column=col, sticky="w", padx=(SPACE_S, 2), pady=2)
        ttk.Entry(parent, textvariable=var, width=width, font=FONT_MONO).grid(row=row, column=col + 1, sticky="w", padx=(0, SPACE_XS), pady=2)

    def _build_controls(self, names: list[str]) -> None:
        bar = ttk.Frame(self, style="Surface.TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=SPACE_L, pady=(0, SPACE_S))
        source = ttk.Frame(bar, style="Card.TFrame", padding=(SPACE_S, SPACE_XS, SPACE_S, SPACE_S))
        source.pack(side="left", fill="y")
        ttk.Label(source, text="FIT INPUT", style="CardHeading.TLabel").grid(row=0, column=0, columnspan=6, sticky="w", padx=SPACE_S, pady=(2, 4))
        ttk.Label(source, text="mode", style="Field.TLabel").grid(row=1, column=0, sticky="w", padx=(SPACE_S, 2))
        mode = ttk.Combobox(source, textvariable=self._mode_var, values=["Synthetic GARCH", "Market history"], state="readonly", width=16)
        mode.grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Label(source, text="source", style="Field.TLabel").grid(row=1, column=3, sticky="w", padx=(SPACE_S, 2))
        ttk.Combobox(source, textvariable=self._source_var, values=names, state="readonly", width=9).grid(row=1, column=4, sticky="w")
        self._field(source, "symbol", self._symbol_var, 2, 0)
        self._field(source, "lookback days", self._lookback_var, 2, 2)
        ttk.Label(source, text="uses the selected source; ibkr reads SQLite cache first", style="Muted.TLabel").grid(row=3, column=0, columnspan=6, sticky="w", padx=SPACE_S, pady=(2, 0))

        synthetic = ttk.Frame(bar, style="Card.TFrame", padding=(SPACE_S, SPACE_XS, SPACE_S, SPACE_S))
        synthetic.pack(side="left", fill="y", padx=(SPACE_S, 0))
        ttk.Label(synthetic, text="SYNTHETIC PROCESS (PER DAY)", style="CardHeading.TLabel").grid(row=0, column=0, columnspan=8, sticky="w", padx=SPACE_S, pady=(2, 4))
        self._field(synthetic, "mean %", self._mean_var, 1, 0)
        self._field(synthetic, "omega ×10⁻⁶", self._omega_var, 1, 2)
        self._field(synthetic, "alpha", self._alpha_var, 1, 4)
        self._field(synthetic, "beta", self._beta_var, 1, 6)
        self._field(synthetic, "days", self._days_var, 2, 0)
        self._field(synthetic, "seed", self._seed_var, 2, 2)
        self._run_button = ttk.Button(synthetic, text="Fit GARCH", style="Accent.TButton", command=self._run)
        self._run_button.grid(row=2, column=4, columnspan=4, sticky="e", padx=SPACE_S, pady=2)

    def _build_chart(self) -> None:
        frame = ttk.Frame(self, style="Surface.TFrame")
        frame.grid(row=2, column=0, sticky="nsew", padx=(SPACE_L, SPACE_S), pady=(0, SPACE_L))
        self._figure = create_figure(_CHART_STYLE)
        self._canvas = FigureCanvasTkAgg(self._figure, master=frame)
        self._canvas.get_tk_widget().configure(background=PALETTE.surface, highlightthickness=0)
        self._canvas.get_tk_widget().pack(fill="both", expand=True)
        plot_message(self._figure, "choose an input and fit GARCH", _CHART_STYLE)

    def _build_stats(self) -> None:
        panel = ttk.Frame(self, style="Card.TFrame", padding=SPACE_S)
        panel.grid(row=2, column=1, sticky="nsew", padx=(0, SPACE_L), pady=(0, SPACE_L))
        ttk.Label(panel, text="FITTED PROCESS", style="CardHeading.TLabel").pack(anchor="w")
        self._values: dict[str, tk.Label] = {}
        for key, caption in (("mean", "mean / day"), ("omega", "omega"), ("alpha", "alpha"), ("beta", "beta"), ("persistence", "alpha + beta"), ("long_vol", "long-run vol (ann.)"), ("nll", "negative log-lik."), ("observations", "observations"), ("iterations", "optimizer iterations")):
            row = ttk.Frame(panel, style="Card.TFrame")
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=caption, style="Secondary.TLabel").pack(side="left")
            value = tk.Label(row, text="—", font=FONT_MONO_SM, background=PALETTE.surface, foreground=PALETTE.text)
            value.pack(side="right")
            self._values[key] = value

    def on_show(self) -> None:
        if self._fit is None:
            self._run()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {"mode": self._mode_var.get(), "source": self._source_var.get(), "symbol": self._symbol_var.get(), "lookback": self._lookback_var.get(), "mean": self._mean_var.get(), "omega": self._omega_var.get(), "alpha": self._alpha_var.get(), "beta": self._beta_var.get(), "days": self._days_var.get(), "seed": self._seed_var.get()})

    def _run(self) -> None:
        mode = self._mode_var.get()
        try:
            if mode == "Synthetic GARCH":
                parameters = GARCH11Parameters(mean=float(self._mean_var.get()) / 100.0, omega=float(self._omega_var.get()) * 1e-6, alpha=float(self._alpha_var.get()), beta=float(self._beta_var.get()))
                days = int(self._days_var.get())
                seed = int(self._seed_var.get()) if self._seed_var.get().strip() else None
            else:
                symbol = self._symbol_var.get().strip().upper()
                if not symbol:
                    raise ValueError("symbol is required")
                lookback = int(self._lookback_var.get())
                if lookback < 1:
                    raise ValueError("lookback days must be positive")
                source = self._source_var.get()
        except ValueError as exc:
            self._show_message(str(exc), error=True)
            return
        self._run_button.configure(state="disabled")
        self._show_message("fitting…")

        if mode == "Synthetic GARCH":
            def work() -> tuple[pd.Series, GARCH11Fit, str]:
                simulation = simulate_garch11(parameters, days, seed)
                returns = pd.Series(simulation.returns, index=pd.bdate_range("2024-01-02", periods=days))
                return returns, fit_garch11(simulation.returns), "synthetic GARCH"
        else:
            def work() -> tuple[pd.Series, GARCH11Fit, str]:
                end = datetime.now()
                history = self._market.get_history(symbol, end - timedelta(days=lookback), end, source=source)
                returns = history.log_returns()
                return returns, fit_garch11(returns.to_numpy()), f"{history.symbol} · {history.source} · log returns"
        self._tasks.submit(work, on_done=self._on_done, on_error=self._on_error)

    def _on_done(self, result: tuple[pd.Series, GARCH11Fit, str]) -> None:
        self._returns, self._fit, label = result
        self._run_button.configure(state="normal")
        self._show_message(label)
        plot_garch_overview(self._figure, self._returns, self._fit, _CHART_STYLE)
        self._canvas.draw_idle()
        p = self._fit.parameters
        values = {"mean": f"{p.mean:.5%}", "omega": f"{p.omega:.3e}", "alpha": f"{p.alpha:.4f}", "beta": f"{p.beta:.4f}", "persistence": f"{p.persistence:.4f}", "long_vol": f"{math.sqrt(p.unconditional_variance * 252):.2%}", "nll": f"{self._fit.negative_log_likelihood:,.1f}", "observations": str(len(self._returns)), "iterations": str(self._fit.iterations)}
        for key, text in values.items():
            self._values[key].configure(text=text)

    def _on_error(self, exc: Exception) -> None:
        self._run_button.configure(state="normal")
        self._show_message(str(exc), error=True)
        _logger.error("GARCH task failed: %s", exc)

    def _show_message(self, text: str, error: bool = False) -> None:
        self._message.configure(text=text, foreground=PALETTE.critical if error else PALETTE.muted)