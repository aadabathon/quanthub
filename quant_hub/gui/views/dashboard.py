"""Dashboard: the end-to-end demonstration of the architecture.

Flow on Load: submit ``market_service.get_history`` to the TaskRunner (worker
thread), then on the GUI thread hand the result to ``visualization.charts``
for the figure and ``data.stats`` for the table. No math happens in this
file -- only formatting.
"""

from __future__ import annotations

import logging
import math
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from quant_hub.config import AppConfig
from quant_hub.data.stats import ReturnStats, summary_stats
from quant_hub.data.types import PriceHistory
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import FONT_MONO, FONT_MONO_LG, PALETTE
from quant_hub.gui.views.base import View
from quant_hub.services.market_service import MarketService
from quant_hub.visualization.charts import ChartStyle, create_figure, plot_price_history

_logger = logging.getLogger(__name__)

RANGE_DAYS = {"3M": 91, "6M": 182, "1Y": 365, "2Y": 730, "5Y": 1826}

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

_STAT_ROWS: list[tuple[str, str]] = [
    ("total_return", "Total return"),
    ("cagr", "CAGR"),
    ("annualized_vol", "Volatility (ann.)"),
    ("sharpe", "Sharpe (rf=0)"),
    ("max_drawdown", "Max drawdown"),
    ("n_observations", "Observations"),
]


class DashboardView(View):
    def __init__(
        self,
        parent: tk.Misc,
        market_service: MarketService,
        tasks: TaskRunner,
        config: AppConfig,
    ) -> None:
        super().__init__(parent)
        self._market = market_service
        self._tasks = tasks

        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=1, minsize=230)
        self.rowconfigure(2, weight=1)

        # -- controls row --------------------------------------------------
        controls = ttk.Frame(self, style="Surface.TFrame")
        controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=16, pady=(14, 6))

        ttk.Label(controls, text="Symbol", style="Muted.TLabel").pack(side="left")
        self._symbol_var = tk.StringVar(value=config.default_symbol)
        symbol_entry = ttk.Entry(controls, textvariable=self._symbol_var, width=10, font=FONT_MONO)
        symbol_entry.pack(side="left", padx=(6, 16))
        symbol_entry.bind("<Return>", lambda _e: self._load())

        ttk.Label(controls, text="Range", style="Muted.TLabel").pack(side="left")
        self._range_var = tk.StringVar(value=config.default_range)
        ttk.Combobox(
            controls,
            textvariable=self._range_var,
            values=list(RANGE_DAYS),
            state="readonly",
            width=5,
        ).pack(side="left", padx=(6, 16))

        ttk.Label(controls, text="Source", style="Muted.TLabel").pack(side="left")
        self._source_var = tk.StringVar(value=self._market.active_source)
        source_box = ttk.Combobox(
            controls,
            textvariable=self._source_var,
            values=self._market.source_names,
            state="readonly",
            width=10,
        )
        source_box.pack(side="left", padx=(6, 16))
        source_box.bind("<<ComboboxSelected>>", self._on_source_changed)

        self._load_button = ttk.Button(
            controls, text="Load", style="Accent.TButton", command=self._load
        )
        self._load_button.pack(side="left")

        self._message = ttk.Label(controls, text="", style="Muted.TLabel")
        self._message.pack(side="left", padx=16)

        # -- quote strip ---------------------------------------------------
        quote = ttk.Frame(self, style="Surface.TFrame")
        quote.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 6))
        self._quote_symbol = ttk.Label(quote, text="—", style="ViewTitle.TLabel")
        self._quote_symbol.pack(side="left")
        self._quote_price = tk.Label(
            quote,
            text="",
            font=FONT_MONO_LG,
            background=PALETTE.surface,
            foreground=PALETTE.text,
        )
        self._quote_price.pack(side="left", padx=(18, 8))
        self._quote_detail = ttk.Label(quote, text="", style="Muted.TLabel")
        self._quote_detail.pack(side="left", pady=(6, 0))

        # -- chart ---------------------------------------------------------
        self._figure = create_figure(_CHART_STYLE)
        self._canvas = FigureCanvasTkAgg(self._figure, master=self)
        canvas_widget = self._canvas.get_tk_widget()
        canvas_widget.configure(background=PALETTE.surface, highlightthickness=0)
        canvas_widget.grid(row=2, column=0, sticky="nsew", padx=(16, 8), pady=(0, 16))

        # -- stats panel ----------------------------------------------------
        stats = ttk.Frame(self, style="Surface.TFrame")
        stats.grid(row=2, column=1, sticky="nsew", padx=(8, 16), pady=(0, 16))
        ttk.Label(stats, text="RETURN STATISTICS", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(2, 10)
        )
        self._stat_values: dict[str, tk.Label] = {}
        for row, (key, label) in enumerate(_STAT_ROWS, start=1):
            ttk.Label(stats, text=label, style="Secondary.TLabel").grid(
                row=row, column=0, sticky="w", pady=3
            )
            value = tk.Label(
                stats,
                text="—",
                font=FONT_MONO,
                background=PALETTE.surface,
                foreground=PALETTE.text,
                anchor="e",
            )
            value.grid(row=row, column=1, sticky="e", padx=(24, 0), pady=3)
            self._stat_values[key] = value
        stats.columnconfigure(1, weight=1)

        self._loaded_once = False

    # ------------------------------------------------------------------ events
    def on_show(self) -> None:
        if not self._loaded_once:
            self._load()

    def _on_source_changed(self, _event: object) -> None:
        self._market.active_source = self._source_var.get()

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        symbol = self._symbol_var.get().strip().upper()
        if not symbol:
            self._show_message("enter a symbol", error=True)
            return
        days = RANGE_DAYS[self._range_var.get()]
        end = datetime.now()
        start = end - timedelta(days=days)

        self._load_button.configure(state="disabled")
        self._show_message(f"loading {symbol}…")
        self._tasks.submit(
            lambda: self._market.get_history(symbol, start, end),
            on_done=self._on_history,
            on_error=self._on_error,
        )

    def _on_history(self, history: PriceHistory) -> None:
        self._loaded_once = True
        self._load_button.configure(state="normal")
        self._show_message("")

        plot_price_history(self._figure, history, _CHART_STYLE)
        self._canvas.draw_idle()

        last = float(history.closes.iloc[-1])
        self._quote_symbol.configure(text=history.symbol)
        self._quote_price.configure(text=f"{last:,.2f}")
        self._quote_detail.configure(
            text=f"last close · {history.end:%Y-%m-%d} · source: {history.source}"
        )

        try:
            self._show_stats(summary_stats(history))
        except ValueError as exc:
            _logger.warning("stats unavailable: %s", exc)

    def _on_error(self, exc: Exception) -> None:
        self._load_button.configure(state="normal")
        self._show_message(str(exc), error=True)
        _logger.error("history load failed: %s", exc)

    # ------------------------------------------------------------------ display
    def _show_message(self, text: str, error: bool = False) -> None:
        self._message.configure(
            text=text,
            foreground=PALETTE.critical if error else PALETTE.muted,
        )

    def _show_stats(self, stats: ReturnStats) -> None:
        def fmt_pct(x: float) -> str:
            return "n/a" if math.isnan(x) else f"{x:+.2%}"

        def fmt_num(x: float) -> str:
            return "n/a" if math.isnan(x) else f"{x:+.2f}"

        def signed_color(x: float) -> str:
            if math.isnan(x) or x == 0:
                return PALETTE.text
            return PALETTE.good if x > 0 else PALETTE.critical

        display: dict[str, tuple[str, str]] = {
            "total_return": (fmt_pct(stats.total_return), signed_color(stats.total_return)),
            "cagr": (fmt_pct(stats.cagr), signed_color(stats.cagr)),
            "annualized_vol": (fmt_pct(stats.annualized_vol).lstrip("+"), PALETTE.text),
            "sharpe": (fmt_num(stats.sharpe), signed_color(stats.sharpe)),
            "max_drawdown": (fmt_pct(stats.max_drawdown), signed_color(stats.max_drawdown)),
            "n_observations": (str(stats.n_observations), PALETTE.text),
        }
        for key, (text, color) in display.items():
            self._stat_values[key].configure(text=text, foreground=color)
