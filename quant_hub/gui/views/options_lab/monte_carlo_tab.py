"""Monte Carlo tab: an independent check of the closed-form price.

Prices the Pricer tab's contract by simulating the exact risk-neutral
terminal law (``models/option_monte_carlo.py``) and shows the estimate
converging to Black-Scholes. The z-score says whether the gap is sampling
noise. Runs on the TaskRunner (up to a million paths). It never re-runs
itself: when inputs change, the result is only marked stale, so a user
typing in the strip doesn't queue simulations.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import SPACE_S, SPACE_XS
from quant_hub.gui.views.options_lab.common import (
    CHART_STYLE, Reporter, combo, embed_figure, entry, heading, stat_rows,
)
from quant_hub.models.black_scholes import BlackScholesInputs
from quant_hub.models.option_monte_carlo import MonteCarloPricing, monte_carlo_price
from quant_hub.visualization.charts import plot_message, plot_monte_carlo_pricing

_PATH_CHOICES = ["10,000", "100,000", "1,000,000"]


class MonteCarloTab(ttk.Frame):
    def __init__(
        self, parent: tk.Misc, get_inputs: Callable[[], BlackScholesInputs], tasks: TaskRunner,
        saved: dict, report: Reporter,
    ) -> None:
        super().__init__(parent, style="Surface.TFrame")
        self._get_inputs, self._tasks, self._report = get_inputs, tasks, report
        self._result: MonteCarloPricing | None = None
        self._paths_var = tk.StringVar(value=saved.get("paths", "100,000"))
        self._seed_var = tk.StringVar(value=saved.get("seed", "7"))
        self._antithetic_var = tk.BooleanVar(value=bool(saved.get("antithetic", True)))

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=270)
        self.rowconfigure(0, weight=1)
        chart = ttk.Frame(self, style="Surface.TFrame")
        chart.grid(row=0, column=0, sticky="nsew", padx=(0, SPACE_S), pady=SPACE_S)
        self._figure, self._canvas = embed_figure(chart)
        plot_message(self._figure, "run a simulation to price the Pricer tab's contract",
                     CHART_STYLE)

        panel = ttk.Frame(self, style="Card.TFrame", padding=SPACE_S)
        panel.grid(row=0, column=1, sticky="nsew", pady=SPACE_S)
        heading(panel, "SIMULATION")
        form = ttk.Frame(panel, style="Card.TFrame")
        form.pack(fill="x", pady=(2, 0))
        combo(form, "paths", self._paths_var, _PATH_CHOICES, 0, 0, width=10)
        entry(form, "seed (blank = random)", self._seed_var, 1, 0, width=6)
        ttk.Checkbutton(form, text="antithetic variates", variable=self._antithetic_var).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=SPACE_S, pady=2
        )
        self._run_button = ttk.Button(panel, text="Run Monte Carlo", style="Accent.TButton",
                                      command=self.run)
        self._run_button.pack(fill="x", pady=(SPACE_XS, SPACE_S))
        self._contract = ttk.Label(panel, text="", style="Muted.TLabel", wraplength=250)
        self._contract.pack(anchor="w", pady=(0, SPACE_XS))

        heading(panel, "RESULT")
        self._values = stat_rows(panel, [
            ("estimate", "MC estimate"), ("se", "standard error"), ("ci", "95% interval"),
            ("closed", "closed form"), ("z", "z-score"), ("paths", "paths"),
        ])
        self._verdict = ttk.Label(panel, text="", style="Muted.TLabel", wraplength=250)
        self._verdict.pack(anchor="w", pady=(SPACE_XS, 0))

    def state(self) -> dict:
        return {"paths": self._paths_var.get(), "seed": self._seed_var.get(),
                "antithetic": self._antithetic_var.get()}

    def mark_stale(self) -> None:
        if self._result is not None:
            self._verdict.configure(text="inputs changed since this run; run again to update")

    def run(self) -> None:
        try:
            inputs = self._get_inputs()
            n_paths = int(self._paths_var.get().replace(",", ""))
            seed_text = self._seed_var.get().strip()
            seed = int(seed_text) if seed_text else None
        except ValueError as exc:
            self._report(str(exc), True)
            return
        antithetic = self._antithetic_var.get()
        self._contract.configure(
            text=f"{inputs.option_type} K={inputs.strike:g}, {inputs.dte:g}d, "
                 f"S={inputs.spot:g}, σ={inputs.sigma:.1%}, r={inputs.rate:.2%}, "
                 f"q={inputs.dividend_yield:.2%}"
        )
        self._run_button.configure(state="disabled")
        self._report(f"simulating {n_paths:,} paths…", False)
        self._tasks.submit(
            lambda: monte_carlo_price(inputs, n_paths, seed, antithetic),
            on_done=self._on_done, on_error=self._on_error,
        )

    def _on_done(self, result: MonteCarloPricing) -> None:
        self._result = result
        self._run_button.configure(state="normal")
        self._report(f"simulated {result.n_paths:,} paths", False)
        half = 1.96 * result.standard_error
        texts = {
            "estimate": f"{result.estimate:,.4f}", "se": f"{result.standard_error:,.4f}",
            "ci": f"{result.estimate - half:,.3f} – {result.estimate + half:,.3f}",
            "closed": f"{result.closed_form:,.4f}", "z": f"{result.z_score:+.2f}",
            "paths": f"{result.n_paths:,}" + (" (antithetic)" if result.antithetic else ""),
        }
        for key, text in texts.items():
            self._values[key].configure(text=text)
        self._verdict.configure(text=(
            "every path expired worthless: no error estimate; add paths or move the strike"
            if result.standard_error == 0
            else "closed form inside the 95% interval: consistent" if abs(result.z_score) <= 1.96
            else "closed form outside the 95% interval (expected ~1 run in 20)"
            if abs(result.z_score) <= 3.5 else "large disagreement: investigate"
        ))
        plot_monte_carlo_pricing(self._figure, result, CHART_STYLE)
        self._canvas.draw_idle()

    def _on_error(self, exc: Exception) -> None:
        self._run_button.configure(state="normal")
        self._report(f"Monte Carlo failed: {exc}", True)
