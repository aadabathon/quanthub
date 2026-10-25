"""GARCH Lab: fit GARCH(1,1) to a synthetic process or to market history.

Two inputs, one pipeline (as in the Tk page): a synthetic GARCH path with
known parameters, or log returns of a named source's history (``ibkr``
reads the SQLite cache first). Both end in ``models.garch.fit_garch11``,
run as one TaskRunner job so every chart and stat describes the same fit.

Synthetic mode knows the answer, so it also shows it: the true conditional
volatility is drawn under the fitted one and the stats table gains a TRUE
column (the same known-world-vs-estimate pattern as the Simulator).

Presentation conversion only: per-period variance → annualized volatility,
:math:`\\sigma_{ann} = \\sqrt{252\\,h_t}` (D16's 252-day convention).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Histogram, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card,
    EmptyState,
    Field,
    PageHeader,
    SegmentedControl,
    SelectField,
    StatTable,
    button,
    form_grid,
)
from quant_hub.gui.pages.base import PRICE_BASES, ScrollPage, basis_field, offline_note
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models.garch import (
    GARCH11Fit,
    GARCH11Parameters,
    GARCH11Simulation,
    fit_garch11,
    simulate_garch11,
)
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "garch_lab"          # shared with the Tk page, so inputs carry over
_SYNTHETIC, _MARKET = "Synthetic", "Market history"
_SAVED_MODE = {_SYNTHETIC: "Synthetic GARCH", _MARKET: "Market history"}   # Tk's labels
_SYNTHETIC_START = "2024-01-02"   # calendar for synthetic paths (as in the Tk page)
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2   # two fields per row

_STAT_GROUPS = (
    ("Parameters", (("mean", "μ  per day"), ("omega", "ω"), ("alpha", "α"), ("beta", "β"))),
    ("Dynamics", (("persistence", "α + β"), ("long_vol", "Long-run vol (ann.)"))),
    ("Fit", (("nll", "−log-likelihood"), ("observations", "Observations"),
             ("iterations", "Optimizer iterations"))),
)


@dataclass(frozen=True)
class _Run:
    dates: pd.DatetimeIndex
    returns: np.ndarray
    fit: GARCH11Fit
    truth: GARCH11Simulation | None
    label: str


def _annualized_vol(variance):
    return np.sqrt(np.asarray(variance) * TRADING_DAYS_PER_YEAR)


class GARCHLabPage(ScrollPage):
    def __init__(self, market_service: MarketService, tasks: TaskRunner,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._market = market_service
        self._tasks = tasks
        self._state_store = state_store
        self._run_result: _Run | None = None
        saved = state_store.load(_STATE_KEY)
        names = market_service.source_names
        self._mode = _MARKET if saved.get("mode") == _SAVED_MODE[_MARKET] else _SYNTHETIC

        self._header = PageHeader(
            "GARCH Lab",
            "GARCH(1,1) conditional volatility: fit a synthetic process with a known "
            "answer, or real market history")
        self.body.addWidget(self._header)

        main = QHBoxLayout()
        main.setSpacing(SPACE_L)
        self.body.addLayout(main, 1)

        charts = QVBoxLayout()
        charts.setSpacing(SPACE_L)
        main.addLayout(charts, 1)
        self._vol_card = Card("Conditional volatility", "")
        self._vol_stack = QStackedWidget()
        self._series_chart = StackedTimeSeries()
        self._vol_empty = EmptyState("ph.activity", "No fit yet",
                                     "Choose an input on the right and press Fit GARCH.")
        self._vol_stack.addWidget(self._series_chart)
        self._vol_stack.addWidget(self._vol_empty)
        self._vol_stack.setCurrentWidget(self._vol_empty)
        self._vol_stack.setMinimumHeight(380)
        self._vol_card.body.addWidget(self._vol_stack, 1)
        charts.addWidget(self._vol_card, 3)

        resid_card = Card("Standardized residuals",
                          "z = (r − μ̂) / σ̂ₜ should look N(0, 1) if the model fits")
        self._residuals = Histogram()
        self._residuals.setMinimumHeight(190)
        resid_card.body.addWidget(self._residuals, 1)
        charts.addWidget(resid_card, 2)

        side = QVBoxLayout()
        side.setSpacing(SPACE_L)
        side_widget = QWidget()
        side_widget.setFixedWidth(SIDE_PANEL_WIDTH)
        side_widget.setLayout(side)
        side.setContentsMargins(0, 0, 0, 0)
        main.addWidget(side_widget)
        side.addWidget(self._build_inputs(saved, names))
        stats_card = Card("Fitted process")
        self._stats = StatTable(_STAT_GROUPS)
        stats_card.body.addWidget(self._stats)
        side.addWidget(stats_card)
        side.addStretch(1)

    # ------------------------------------------------------------- inputs
    def _build_inputs(self, saved: dict, names: list[str]) -> Card:
        card = Card("Model inputs")
        self._modes = SegmentedControl([_SYNTHETIC, _MARKET], self._mode, fill=True)
        self._modes.changed.connect(self._on_mode)
        card.body.addWidget(self._modes)

        # Two input groups, one visible: hidden widgets take no space, so the
        # card is exactly as tall as the active group (a stack would size to
        # the tallest).
        synthetic = QWidget()
        self._mean = Field("Mean % / day", saved.get("mean", "0.02"), _HALF)
        self._omega = Field("ω  ×10⁻⁶", saved.get("omega", "4"), _HALF)
        self._alpha = Field("α", saved.get("alpha", "0.08"), _HALF)
        self._beta = Field("β", saved.get("beta", "0.88"), _HALF)
        self._days = Field("Days", saved.get("days", "2520"), _HALF)
        self._seed = Field("Seed", saved.get("seed", "42"), _HALF, placeholder="random")
        synthetic.setLayout(form_grid(
            [self._mean, self._omega, self._alpha, self._beta, self._days, self._seed], 2))
        market = QWidget()
        default_source = saved.get("source", "ibkr")
        self._source = SelectField("Source", names,
                                   default_source if default_source in names else names[0], _HALF)
        self._symbol = Field("Symbol", saved.get("symbol", "SPY"), _HALF, uppercase=True)
        self._lookback = Field("Lookback (calendar days)", saved.get("lookback", "1825"), _HALF)
        self._basis = basis_field(saved, _HALF)
        market.setLayout(form_grid([self._source, self._symbol, self._lookback, self._basis],
                                   2))
        self._groups = {_SYNTHETIC: synthetic, _MARKET: market}
        for group in self._groups.values():
            card.body.addWidget(group)
        for field in (self._mean, self._omega, self._alpha, self._beta, self._days,
                      self._seed, self._symbol, self._lookback):
            field.committed.connect(self._fit)

        self._fit_button = button("Fit GARCH", "primary", "ph.lightning", on_click=self._fit)
        card.body.addWidget(self._fit_button)
        self._show_mode()
        return card

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._show_mode()

    def _show_mode(self) -> None:
        for mode, group in self._groups.items():
            group.setVisible(mode == self._mode)

    # ---------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if self._run_result is None:
            self._fit()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "mode": _SAVED_MODE[self._mode], "source": self._source.current(),
            "basis": self._basis.current(),
            "symbol": self._symbol.text(), "lookback": self._lookback.text(),
            "mean": self._mean.text(), "omega": self._omega.text(),
            "alpha": self._alpha.text(), "beta": self._beta.text(),
            "days": self._days.text(), "seed": self._seed.text(),
        })

    # ---------------------------------------------------------------- fit
    def _fit(self) -> None:
        try:
            work = self._synthetic_job() if self._mode == _SYNTHETIC else self._market_job()
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._fit_button.setEnabled(False)
        self._header.set_status("Fitting…")
        self._tasks.submit(work, on_done=self._on_done, on_error=self._on_error)

    def _synthetic_job(self):
        parameters = GARCH11Parameters(
            mean=self._mean.number(scale=0.01), omega=self._omega.number(scale=1e-6),
            alpha=self._alpha.number(), beta=self._beta.number())
        days = self._days.number(integer=True, minimum=20)
        seed = self._seed.number(integer=True, allow_blank=True)

        def work() -> _Run:
            simulation = simulate_garch11(parameters, days, seed)
            dates = pd.bdate_range(_SYNTHETIC_START, periods=days)
            return _Run(dates, simulation.returns, fit_garch11(simulation.returns), simulation,
                        f"Synthetic GARCH · {days:,} days · seed {seed if seed is not None else 'random'}")
        return work

    def _market_job(self):
        symbol = self._symbol.required_text()
        lookback = self._lookback.number(integer=True, minimum=30)
        source = self._source.current()
        basis = PRICE_BASES[self._basis.current()]

        def work() -> _Run:
            end = datetime.now()
            history = self._market.get_history(symbol, end - timedelta(days=lookback), end,
                                               source=source, basis=basis)
            returns = history.log_returns()
            return _Run(returns.index, returns.to_numpy(), fit_garch11(returns.to_numpy()), None,
                        f"{history.symbol} · {history.source} · {basis.value} · "
                        f"{len(returns):,} daily log returns{offline_note(history)}")
        return work

    def _on_done(self, run: _Run) -> None:
        self._run_result = run
        self._fit_button.setEnabled(True)
        fit, truth = run.fit, run.truth
        p = fit.parameters
        long_run = math.sqrt(p.unconditional_variance * TRADING_DAYS_PER_YEAR)
        self._header.set_status(
            f"Fitted {len(run.returns):,} returns in {fit.iterations} iterations"
            if fit.converged else "Optimizer did not converge; treat this fit with caution",
            error=not fit.converged)

        # The truth is drawn first so the fit sits on top of it: where they
        # agree only blue shows, and orange marks where the fit departs.
        vol_series = []
        if truth is not None:
            vol_series.append(Series("True σ", _annualized_vol(truth.conditional_variances),
                                     PALETTE.series_2, 1.1, dashed=True, fmt="{:.1%}"))
        vol_series.append(Series("Fitted σ", _annualized_vol(fit.conditional_variances),
                                 PALETTE.series, 1.6, fmt="{:.1%}"))
        self._series_chart.set_panels(run.dates, [
            Panel("Daily log returns", [Series("Return", run.returns, PALETTE.series, 1.0,
                                               fmt="{:+.2%}")],
                  axis="percent", stretch=2, zero_line=True),
            Panel("Conditional volatility · annualized", vol_series, axis="percent", stretch=3,
                  references=[(f"long-run {long_run:.1%}", long_run)]),
        ])
        self._vol_stack.setCurrentWidget(self._series_chart)
        self._vol_card.set_subtitle(
            run.label + (" · fitted vs true σₜ" if truth else "") + " · hover for values")
        self._residuals.set_data(fit.standardized_residuals, normal=(0.0, 1.0),
                                 normal_name="N(0, 1)")
        self._render_stats(fit, truth, long_run, len(run.returns))

    def _render_stats(self, fit: GARCH11Fit, truth: GARCH11Simulation | None,
                      long_run: float, n: int) -> None:
        p = fit.parameters
        fitted = {"mean": p.mean, "omega": p.omega, "alpha": p.alpha, "beta": p.beta,
                  "persistence": p.persistence, "long_vol": long_run}
        true: dict[str, float] = {}
        if truth is not None:
            t = truth.parameters
            true = {"mean": t.mean, "omega": t.omega, "alpha": t.alpha, "beta": t.beta,
                    "persistence": t.persistence,
                    "long_vol": math.sqrt(t.unconditional_variance * TRADING_DAYS_PER_YEAR)}
        formats = {"mean": "{:+.4%}", "omega": "{:.3e}", "alpha": "{:.4f}", "beta": "{:.4f}",
                   "persistence": "{:.4f}", "long_vol": "{:.2%}"}
        for key, fmt in formats.items():
            self._stats.set_value(key, fmt.format(fitted[key]),
                                  fmt.format(true[key]) if key in true else "")
        self._stats.set_value("nll", f"{fit.negative_log_likelihood:,.1f}")
        self._stats.set_value("observations", f"{n:,}")
        self._stats.set_value("iterations", str(fit.iterations))
        self._stats.show_truth(truth is not None)

    def _on_error(self, exc: Exception) -> None:
        self._fit_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)
        _logger.error("GARCH fit failed: %s", exc)
