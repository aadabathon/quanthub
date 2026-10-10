"""Monte Carlo tab: an independent check of the Pricer's closed-form price.

Simulates the exact risk-neutral terminal law (``models/option_monte_carlo``)
and shows the running estimate converging to Black–Scholes with its 95%
band narrowing like 1/√n; the z-score says whether the remaining gap is
sampling noise. Runs on the TaskRunner (up to a million paths) and never
re-runs itself: when inputs change the result is only marked stale, so
typing in the underlying card doesn't queue simulations.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Curve, CurveChart, Histogram, Series
from quant_hub.gui.components import (
    Badge,
    Card,
    EmptyState,
    Field,
    SegmentedControl,
    StatTable,
    button,
    label,
)
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_S
from quant_hub.models.black_scholes import BlackScholesInputs
from quant_hub.models.option_monte_carlo import MonteCarloPricing, monte_carlo_price

Reporter = Callable[[str, bool], None]
_PATHS = {"10K": 10_000, "100K": 100_000, "1M": 1_000_000}
_HIST_SAMPLE = 200_000   # terminal prices drawn in the histogram (legibility, speed)


class MonteCarloTab(QWidget):
    def __init__(self, get_inputs: Callable[[], BlackScholesInputs], tasks: TaskRunner,
                 saved: dict, report: Reporter) -> None:
        super().__init__()
        self._get_inputs, self._tasks, self._report = get_inputs, tasks, report
        self._result: MonteCarloPricing | None = None
        saved_paths = int(str(saved.get("paths", "100,000")).replace(",", "") or 100_000)
        self._paths = next((k for k, v in _PATHS.items() if v == saved_paths), "100K")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_L)
        left = QVBoxLayout()
        left.setSpacing(SPACE_L)
        layout.addLayout(left, 1)
        self._conv_card = Card("Convergence", "Estimate vs paths · the band narrows like 1/√n")
        self._stack = QStackedWidget()
        self._chart = CurveChart(x_axis="compact", y_axis="price", log_x=True, x_name="n",
                                 x_fmt="{:,.0f}")
        self._empty = EmptyState("ph.dice-five", "No simulation yet",
                                 "Run Monte Carlo to price the Pricer tab's contract.")
        self._stack.addWidget(self._chart)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)
        self._stack.setMinimumHeight(300)
        self._conv_card.body.addWidget(self._stack, 1)
        left.addWidget(self._conv_card, 3)
        self._dist_card = Card("Risk-neutral terminal price", "")
        self._hist = Histogram(axis="price", bin_fmt="{:,.2f}")
        self._hist.setMinimumHeight(190)
        self._dist_card.body.addWidget(self._hist, 1)
        left.addWidget(self._dist_card, 2)

        side = QWidget()
        side.setFixedWidth(SIDE_PANEL_WIDTH)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_L)
        column.addWidget(self._build_controls(saved))
        result = Card("Result")
        verdict_row = QHBoxLayout()
        self._badge = Badge("NOT RUN", "neutral")
        verdict_row.addWidget(self._badge)
        verdict_row.addStretch(1)
        result.body.addLayout(verdict_row)
        self._verdict = label("", "muted", wrap=True)
        result.body.addWidget(self._verdict)
        self._stats = StatTable([(None, (
            ("estimate", "MC estimate"), ("se", "Standard error"), ("ci", "95% interval"),
            ("closed", "Closed form"), ("z", "z-score"), ("paths", "Paths")))], value_header="")
        result.body.addWidget(self._stats)
        column.addWidget(result)
        column.addStretch(1)
        layout.addWidget(side)

    def _build_controls(self, saved: dict) -> Card:
        card = Card("Simulation")
        card.body.addWidget(label("Paths", "fieldCaption"))
        paths = SegmentedControl(list(_PATHS), self._paths, fill=True)
        paths.changed.connect(lambda p: setattr(self, "_paths", p))
        card.body.addWidget(paths)
        self._seed = Field("Seed", str(saved.get("seed", "7")), SIDE_PANEL_WIDTH // 2 - SPACE_L,
                           placeholder="random")
        self._seed.committed.connect(self.run)
        card.body.addWidget(self._seed)
        self._antithetic = QCheckBox("Antithetic variates (pairs Z with −Z)")
        self._antithetic.setChecked(bool(saved.get("antithetic", True)))
        card.body.addWidget(self._antithetic)
        card.body.addSpacing(SPACE_S)
        self._run_button = button("Run Monte Carlo", "primary", "ph.play", on_click=self.run)
        card.body.addWidget(self._run_button)
        self._contract = label("", "muted", wrap=True)
        card.body.addWidget(self._contract)
        return card

    # -- public ---------------------------------------------------------
    def state(self) -> dict:
        return {"paths": f"{_PATHS[self._paths]:,}", "seed": self._seed.text(),
                "antithetic": self._antithetic.isChecked()}

    def mark_stale(self) -> None:
        if self._result is not None:
            self._badge.set_badge("STALE", "neutral")
            self._verdict.setText("Inputs changed since this run; run again to update.")

    def run(self) -> None:
        try:
            inputs = self._get_inputs()
            seed = self._seed.number(integer=True, allow_blank=True)
        except ValueError as exc:
            self._report(str(exc), True)
            return
        n_paths, antithetic = _PATHS[self._paths], self._antithetic.isChecked()
        self._contract.setText(
            f"Pricing {inputs.option_type} K={inputs.strike:g}, {inputs.dte:g}d · "
            f"S={inputs.spot:g} · σ={inputs.sigma:.1%} · r={inputs.rate:.2%} · "
            f"q={inputs.dividend_yield:.2%}")
        self._run_button.setEnabled(False)
        self._report(f"Simulating {n_paths:,} paths…", False)
        self._tasks.submit(lambda: monte_carlo_price(inputs, n_paths, seed, antithetic),
                           on_done=self._on_done, on_error=self._on_error)

    # -- results --------------------------------------------------------
    def _on_done(self, result: MonteCarloPricing) -> None:
        self._result = result
        self._run_button.setEnabled(True)
        self._report(f"Simulated {result.n_paths:,} paths", False)
        half = 1.96 * result.standard_error
        for key, text in (
            ("estimate", f"{result.estimate:,.4f}"), ("se", f"{result.standard_error:,.4f}"),
            ("ci", f"{result.estimate - half:,.3f} – {result.estimate + half:,.3f}"),
            ("closed", f"{result.closed_form:,.4f}"), ("z", f"{result.z_score:+.2f}"),
            ("paths", f"{result.n_paths:,}" + (" antithetic" if result.antithetic else "")),
        ):
            self._stats.set_value(key, text)
        z = abs(result.z_score)
        if result.standard_error == 0:
            badge, tone, text = ("NO ERROR ESTIMATE", "warning",
                                 "Every path expired worthless; add paths or move the strike.")
        elif z <= 1.96:
            badge, tone, text = ("CONSISTENT", "good",
                                 "The closed form is inside the 95% interval.")
        elif z <= 3.5:
            badge, tone, text = ("OUTSIDE 95%", "warning",
                                 "Outside the 95% interval; expected about 1 run in 20.")
        else:
            badge, tone, text = ("INVESTIGATE", "critical", "Large disagreement; investigate.")
        self._badge.set_badge(badge, tone)
        self._verdict.setText(text)

        n = result.checkpoints.astype(float)
        band = 1.96 * result.running_standard_error
        self._chart.set_curves([
            Curve("MC estimate", n, result.running_estimate, PALETTE.series, 1.8, fmt="{:,.4f}"),
            Curve("Closed form", n, np.full_like(n, result.closed_form), PALETTE.text_secondary,
                  1.3, dashed=True, fmt="{:,.4f}"),
        ], band=(n, result.running_estimate - band, result.running_estimate + band, "95%"))
        self._stack.setCurrentWidget(self._chart)
        strike_marker = Series("Strike", np.empty(0), PALETTE.text_secondary, 1.3, dashed=True)
        self._hist.set_data(result.terminal_prices[:_HIST_SAMPLE], bins=80,
                            markers=[(strike_marker, result.inputs.strike)])
        shown = min(len(result.terminal_prices), _HIST_SAMPLE)
        self._dist_card.set_subtitle(
            f"S(T) under the risk-neutral drift r − q · {shown:,} of {result.n_paths:,} paths shown")

    def _on_error(self, exc: Exception) -> None:
        self._run_button.setEnabled(True)
        self._report(f"Monte Carlo failed: {exc}", True)
