"""Kalman Lab: filter a log price through a structural state-space model.

Two inputs, one pipeline (as in the GARCH Lab): a synthetic local level or
local linear trend path with known variances, or the log closes of a named
source's history (``ibkr`` reads the SQLite cache first). Both end in
``models.kalman``: the maximum-likelihood fit, then the RTS smoother, run
as one TaskRunner job, so every chart and stat describes the same fit.

What the page shows:

* the filtered level :math:`a_{t|t}` with its 95% band, which is what was
  knowable on day ``t``, over the smoothed level :math:`a_{t|n}`, labelled
  hindsight because it uses the whole sample (research only, never a
  day-``t`` decision);
* for the trend model, the slope as an annualized drift;
* standardized innovations against N(0, 1), the model check;
* for the local level, the steady-state gain as an EWMA weight and
  half-life (the filter *is* the optimal EWMA);
* a note when a variance fits to zero, saying what that means.

Synthetic mode knows the answer, so the table gains a TRUE column, and the
true hidden level is drawn under the estimates.

Presentation conversions only: levels are log prices, shown as prices
:math:`e^{a}` with the band :math:`e^{a \\pm 1.96\\sqrt{P}}` (the log level's
interval, mapped through exp); slopes are per day, shown ×252; σ is the
square root of a variance, in % per day (bp per day for the slope); the
EWMA half-life is :math:`\\ln 0.5 / \\ln(1 - \\bar K)` days.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QStackedWidget, QWidget

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Band, Histogram, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    CardGrid,
    Card,
    EmptyState,
    Field,
    Note,
    PageHeader,
    SegmentedControl,
    SelectField,
    StatTable,
    button,
    form_grid,
)
from quant_hub.gui.pages.base import saved_grid, PRICE_BASES, ScrollPage, basis_field, offline_note
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models.kalman import (
    LocalLevel,
    LocalLinearTrend,
    SmootherResult,
    StateSpaceSimulation,
    StructuralFit,
    StructuralModel,
    fit_local_level,
    fit_local_linear_trend,
    kalman_filter,
    rts_smoother,
    simulate_state_space,
)
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "kalman_lab"
_SYNTHETIC, _MARKET = "Synthetic", "Market history"
_LEVEL, _TREND = "Local level", "Local trend"
_SYNTHETIC_START = "2024-01-02"   # calendar for synthetic paths (as in the GARCH Lab)
_START_PRICE = 100.0
_Z95 = 1.959963984540054          # two-sided 95% normal quantile
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2   # two fields per row
# Chart window -> trailing closes shown (None = all). At ten years, the close,
# the filtered and the smoothed level are one line; a year shows them apart.
_WINDOWS: dict[str, int | None] = {"3M": 63, "1Y": 252, "ALL": None}

_FIT_GROUP = ("Fit", (("loglik", "Log-likelihood"), ("observations", "Observations"),
                      ("iterations", "Optimizer iterations")))
_LEVEL_GROUPS = (
    ("Parameters", (("level_sd", "Level σ  % / day"), ("noise_sd", "Noise σ  % / close"))),
    ("As an EWMA", (("q", "Signal-to-noise q"), ("gain", "Steady-state gain K̄"),
                    ("half_life", "Half-life  days"))),
    ("Latest", (("level", "Filtered level"), ("band", "95% band"))),
    _FIT_GROUP,
)
_TREND_GROUPS = (
    ("Parameters", (("level_sd", "Level σ  % / day"), ("slope_sd", "Slope σ  bp / day"),
                    ("noise_sd", "Noise σ  % / close"))),
    ("Latest", (("level", "Filtered level"), ("band", "95% band"),
                ("drift", "Drift  annualized"), ("drift_band", "95% band"))),
    _FIT_GROUP,
)


@dataclass(frozen=True)
class _Run:
    dates: pd.DatetimeIndex
    log_prices: np.ndarray
    fit: StructuralFit
    smoothed: SmootherResult
    truth: StateSpaceSimulation | None
    true_parameters: StructuralModel | None
    true_log_likelihood: float | None   # the likelihood at the truth: the fit must beat it
    label: str


def _half_life(gain: float) -> float:
    """Days for an EWMA with weight ``gain`` to halve an observation's weight."""
    if gain <= 0:
        return math.inf
    if gain >= 1:
        return 0.0
    return math.log(0.5) / math.log(1.0 - gain)


class KalmanLabPage(ScrollPage):
    def __init__(self, market_service: MarketService, tasks: TaskRunner,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._market = market_service
        self._tasks = tasks
        self._state_store = state_store
        self._run_result: _Run | None = None
        saved = state_store.load(_STATE_KEY)
        names = market_service.source_names
        self._mode = _MARKET if saved.get("mode") == _MARKET else _SYNTHETIC
        self._model = _TREND if saved.get("model") == _TREND else _LEVEL
        self._window = saved.get("window") if saved.get("window") in _WINDOWS else "1Y"

        self._header = PageHeader(
            "Kalman Lab",
            "Kalman filtering of log prices into a hidden level and trend, on a known "
            "synthetic world or real history")
        self.body.addWidget(self._header)

        grid = CardGrid()
        self._level_card = Card("Filtered level", "")
        self._windows = SegmentedControl(list(_WINDOWS), self._window)
        self._windows.changed.connect(self._on_window)
        self._level_card.add_action(self._windows)
        self._level_stack = QStackedWidget()
        self._series_chart = StackedTimeSeries(readout_per_panel=True)
        self._level_empty = EmptyState("ph.sliders-horizontal", "No fit yet",
                                       "Choose an input on the right and press Fit model.")
        self._level_stack.addWidget(self._series_chart)
        self._level_stack.addWidget(self._level_empty)
        self._level_stack.setCurrentWidget(self._level_empty)
        self._level_stack.setMinimumHeight(420)
        self._level_card.body.addWidget(self._level_stack, 1)

        innovation_card = Card(
            "Standardized innovations",
            "eₜ / √Sₜ, the one-day-ahead surprises, should look N(0, 1) if the model fits; "
            "fat tails are jumps a Gaussian model can't see")
        self._innovations = Histogram()
        self._innovations.setMinimumHeight(190)
        innovation_card.body.addWidget(self._innovations, 1)
        grid.add_column([("level", self._level_card, 15), ("innovations", innovation_card, 9)],
                        x=0, w=9)

        inputs = self._build_inputs(saved, names)
        self._zero_note = Note("ph.info", tone="info")
        self._zero_note.setVisible(False)
        stats_card = Card("Estimates")
        self._stats = {_LEVEL: StatTable(_LEVEL_GROUPS), _TREND: StatTable(_TREND_GROUPS)}
        for table in self._stats.values():
            stats_card.body.addWidget(table)
        self._show_stats(self._model)
        grid.add_column([("inputs", inputs, None), ("zero note", self._zero_note, None),
                         ("stats", stats_card, None)],
                        x=9, w=3, min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------- inputs
    def _build_inputs(self, saved: dict, names: list[str]) -> Card:
        card = Card("Model inputs")
        self._modes = SegmentedControl([_SYNTHETIC, _MARKET], self._mode, fill=True)
        self._modes.changed.connect(self._on_mode)
        card.body.addWidget(self._modes)
        self._models = SegmentedControl([_LEVEL, _TREND], self._model, fill=True)
        self._models.changed.connect(self._on_model)
        card.body.addWidget(self._models)

        # Hidden widgets take no space, so the card is exactly as tall as the
        # active inputs (the slope field sits last so hiding it leaves no gap).
        synthetic = QWidget()
        self._level_sd = Field("Level σ  % / day", saved.get("level_sd", "1.0"), _HALF)
        self._noise_sd = Field("Noise σ  % / close", saved.get("noise_sd", "0.5"), _HALF)
        self._days = Field("Days", saved.get("days", "2520"), _HALF)
        self._seed = Field("Seed", saved.get("seed", "42"), _HALF, placeholder="random")
        self._slope_sd = Field("Slope σ  bp / day", saved.get("slope_sd", "0.2"), _HALF)
        synthetic.setLayout(form_grid(
            [self._level_sd, self._noise_sd, self._days, self._seed, self._slope_sd], 2))
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
        for field in (self._level_sd, self._noise_sd, self._slope_sd, self._days, self._seed,
                      self._symbol, self._lookback):
            field.committed.connect(self._fit)

        self._fit_button = button("Fit model", "primary", "ph.lightning", on_click=self._fit)
        card.body.addWidget(self._fit_button)
        self._show_inputs()
        return card

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._show_inputs()

    def _on_model(self, model: str) -> None:
        self._model = model
        self._show_inputs()

    def _show_inputs(self) -> None:
        for mode, group in self._groups.items():
            group.setVisible(mode == self._mode)
        self._slope_sd.setVisible(self._model == _TREND)

    def _on_window(self, window: str) -> None:
        self._window = window
        if self._run_result is not None:
            self._render_charts(self._run_result)

    def _show_stats(self, model: str) -> None:
        for name, table in self._stats.items():
            table.setVisible(name == model)

    # ---------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if self._run_result is None:
            self._fit()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "mode": self._mode, "model": self._model, "window": self._window,
            "source": self._source.current(), "basis": self._basis.current(),
            "symbol": self._symbol.text(), "lookback": self._lookback.text(),
            "level_sd": self._level_sd.text(), "noise_sd": self._noise_sd.text(),
            "slope_sd": self._slope_sd.text(), "days": self._days.text(),
            "seed": self._seed.text(),
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

    def _fitter(self):
        return fit_local_linear_trend if self._model == _TREND else fit_local_level

    def _synthetic_job(self):
        level_sd = self._level_sd.number(scale=0.01, minimum=0)
        noise_sd = self._noise_sd.number(scale=0.01)
        if noise_sd <= 0:   # R > 0: the model needs some observation noise
            raise self._noise_sd.fail("Noise σ must be positive")
        model_name = self._model
        trend = model_name == _TREND
        if trend:
            slope_sd = self._slope_sd.number(scale=1e-4, minimum=0)
            parameters: StructuralModel = LocalLinearTrend(level_sd**2, slope_sd**2, noise_sd**2)
            start = [math.log(_START_PRICE), 0.0]
        else:
            parameters = LocalLevel(level_sd**2, noise_sd**2)
            start = [math.log(_START_PRICE)]
        days = self._days.number(integer=True, minimum=20)
        seed = self._seed.number(integer=True, allow_blank=True)
        fitter = self._fitter()

        def work() -> _Run:
            model = parameters.state_space()
            simulation = simulate_state_space(model, days, start, seed)
            y = simulation.observations
            fit = fitter(y)
            return _Run(
                pd.bdate_range(_SYNTHETIC_START, periods=days), y, fit,
                rts_smoother(fit.filtered), simulation, parameters,
                kalman_filter(model, y).log_likelihood,
                f"Synthetic {model_name.lower()} · {days:,} days · "
                f"seed {seed if seed is not None else 'random'}")
        return work

    def _market_job(self):
        symbol = self._symbol.required_text()
        lookback = self._lookback.number(integer=True, minimum=30)
        source = self._source.current()
        basis = PRICE_BASES[self._basis.current()]
        fitter = self._fitter()

        def work() -> _Run:
            end = datetime.now()
            history = self._market.get_history(symbol, end - timedelta(days=lookback), end,
                                               source=source, basis=basis)
            closes = history.closes
            y = np.log(closes.to_numpy(dtype=float))
            fit = fitter(y)
            return _Run(closes.index, y, fit, rts_smoother(fit.filtered), None, None, None,
                        f"{history.symbol} · {history.source} · {basis.value} · "
                        f"{len(y):,} daily closes{offline_note(history)}")
        return work

    def _on_done(self, run: _Run) -> None:
        self._run_result = run
        self._fit_button.setEnabled(True)
        fit = run.fit
        trend = isinstance(fit.parameters, LocalLinearTrend)
        self._header.set_status(
            f"Fitted {len(run.log_prices):,} closes in {fit.iterations} iterations"
            if fit.converged else "Optimizer did not converge; treat this fit with caution",
            error=not fit.converged)

        self._render_charts(run)
        self._level_stack.setCurrentWidget(self._series_chart)
        self._level_card.set_subtitle(
            run.label + " · the smoothed level uses the whole sample (hindsight) · "
            "hover for values")
        filtered = fit.filtered
        self._innovations.set_data(filtered.standardized_innovations[filtered.likelihood_mask],
                                   normal=(0.0, 1.0), normal_name="N(0, 1)")
        self._render_note(fit, trend)
        self._render_stats(run, trend)

    # ------------------------------------------------------------ charts
    def _render_charts(self, run: _Run) -> None:
        """Draw the trailing window of the run; the fit always uses it all."""
        count = _WINDOWS[self._window]
        start = 0 if count is None else max(0, len(run.log_prices) - count)
        panels = [self._level_panel(run, start)]
        if isinstance(run.fit.parameters, LocalLinearTrend):
            panels.append(self._drift_panel(run, start))
        self._series_chart.set_panels(run.dates[start:], panels)

    def _level_panel(self, run: _Run, start: int) -> Panel:
        filtered = run.fit.filtered
        level = filtered.filtered_means[start:, 0]
        sd = np.sqrt(filtered.filtered_covariances[start:, 0, 0])
        series = [Series("Close", np.exp(run.log_prices[start:]), PALETTE.muted, 1.0)]
        # The truth is drawn under the estimates: where they agree, only the
        # estimates show.
        if run.truth is not None:
            series.append(Series("True level", np.exp(run.truth.states[start:, 0]),
                                 PALETTE.series_2, 1.2, dashed=True))
        series.append(Series("Smoothed (hindsight)", np.exp(run.smoothed.means[start:, 0]),
                             PALETTE.series_3, 1.3, dashed=True))
        series.append(Series("Filtered level", np.exp(level), PALETTE.series, 1.8))
        band = Band("95% band", np.exp(level - _Z95 * sd), np.exp(level + _Z95 * sd))
        return Panel("Price · filtered level with its 95% band", series, axis="price",
                     stretch=3, bands=[band])

    def _drift_panel(self, run: _Run, start: int) -> Panel:
        filtered = run.fit.filtered
        drift = TRADING_DAYS_PER_YEAR * filtered.filtered_means[start:, 1]
        sd = TRADING_DAYS_PER_YEAR * np.sqrt(filtered.filtered_covariances[start:, 1, 1])
        smoothed = TRADING_DAYS_PER_YEAR * run.smoothed.means[start:, 1]
        lower, upper = drift - _Z95 * sd, drift + _Z95 * sd
        # The filtered slope starts from a diffuse prior, so its first weeks
        # swing wildly; scale the axis to what follows, band included (a
        # drift is rarely known to better than tens of percent, and the axis
        # shouldn't hide that), and let the early part run off the edges.
        settled = slice(max(0, max(2, len(run.log_prices) // 20) - start), None)
        shown = [smoothed, lower[settled], upper[settled]]
        series = []
        if run.truth is not None:
            true_drift = TRADING_DAYS_PER_YEAR * run.truth.states[start:, 1]
            shown.append(true_drift)
            series.append(Series("True drift", true_drift, PALETTE.series_2, 1.2, dashed=True,
                                 fmt="{:+.1%}"))
        series.append(Series("Smoothed drift", smoothed, PALETTE.series_3, 1.3, dashed=True,
                             fmt="{:+.1%}"))
        series.append(Series("Filtered drift", drift, PALETTE.series, 1.6, fmt="{:+.1%}"))
        band = Band("95% band", lower, upper, fmt="{:+.1%}")
        low = min(float(np.min(v)) for v in shown)
        high = max(float(np.max(v)) for v in shown)
        middle, half = (low + high) / 2, max((high - low) / 2 * 1.15, 0.01)
        return Panel("Slope · drift, annualized", series, axis="percent", stretch=2,
                     zero_line=True, bands=[band], y_range=(middle - half, middle + half))

    # ------------------------------------------------------------- stats
    def _render_note(self, fit: StructuralFit, trend: bool) -> None:
        notes = {
            "noise_variance": "Noise σ fit to zero. Noise would make daily changes negatively "
                              "autocorrelated; this sample's aren't, so the model sees none "
                              "and the filtered level is the close itself.",
            "level_variance": ("Level σ fit to zero: the level moves only through the slope, "
                               "a smooth trend, and every day-to-day move is noise."
                               if trend else
                               "Level σ fit to zero: the level is constant and every move "
                               "is noise."),
            "slope_variance": "Slope σ fit to zero: one constant drift fits the whole sample.",
        }
        text = " ".join(notes[name] for name in fit.zero_variances)
        self._zero_note.set_text(text)
        self._zero_note.setVisible(bool(text))

    def _render_stats(self, run: _Run, trend: bool) -> None:
        fit, truth = run.fit, run.true_parameters
        table = self._stats[_TREND if trend else _LEVEL]
        self._show_stats(_TREND if trend else _LEVEL)
        table.show_truth(truth is not None)

        def sd(parameters: StructuralModel, name: str, scale: float, fmt: str,
               zero: tuple[str, ...] = ()) -> str:
            if name in zero:
                return "≈ 0"
            return fmt.format(math.sqrt(getattr(parameters, name)) * scale)

        fitted = fit.parameters
        for key, name, scale, fmt in (("level_sd", "level_variance", 100, "{:.3f}%"),
                                      ("noise_sd", "noise_variance", 100, "{:.3f}%"),
                                      ("slope_sd", "slope_variance", 1e4, "{:.2f} bp")):
            if key == "slope_sd" and not trend:
                continue
            table.set_value(key, sd(fitted, name, scale, fmt, fit.zero_variances),
                            sd(truth, name, scale, fmt) if truth is not None else "")

        if not trend:
            def ewma(parameters: LocalLevel, zero: tuple[str, ...] = ()) -> tuple[str, str, str]:
                gain = parameters.steady_state_gain
                q = "∞" if "noise_variance" in zero else f"{parameters.signal_to_noise:.3g}"
                return q, f"{gain:.4f}", f"{_half_life(gain):.2f}"
            fitted_ewma = ewma(fitted, fit.zero_variances)
            true_ewma = ewma(truth) if truth is not None else ("", "", "")
            for key, value, true_value in zip(("q", "gain", "half_life"), fitted_ewma, true_ewma):
                table.set_value(key, value, true_value)

        filtered = fit.filtered
        level = filtered.filtered_means[-1, 0]
        level_sd = math.sqrt(filtered.filtered_covariances[-1, 0, 0])
        table.set_value("level", f"{math.exp(level):,.2f}",
                        f"{math.exp(run.truth.states[-1, 0]):,.2f}" if run.truth else "")
        table.set_value("band", f"{math.exp(level - _Z95 * level_sd):,.2f} – "
                                f"{math.exp(level + _Z95 * level_sd):,.2f}")
        if trend:
            drift = TRADING_DAYS_PER_YEAR * filtered.filtered_means[-1, 1]
            drift_sd = TRADING_DAYS_PER_YEAR * math.sqrt(filtered.filtered_covariances[-1, 1, 1])
            true_drift = (f"{TRADING_DAYS_PER_YEAR * run.truth.states[-1, 1]:+.1%}"
                          if run.truth else "")
            table.set_value("drift", f"{drift:+.1%}", true_drift)
            table.set_value("drift_band", f"{drift - _Z95 * drift_sd:+.1%} – "
                                          f"{drift + _Z95 * drift_sd:+.1%}")

        table.set_value("loglik", f"{fit.log_likelihood:,.1f}",
                        f"{run.true_log_likelihood:,.1f}"
                        if run.true_log_likelihood is not None else "")
        table.set_value("observations", f"{len(run.log_prices):,}")
        table.set_value("iterations", str(fit.iterations))

    def _on_error(self, exc: Exception) -> None:
        self._fit_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)
        _logger.error("Kalman fit failed: %s", exc)
