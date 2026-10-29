"""Markov Lab: hidden volatility regimes in daily returns.

Two inputs, one pipeline (as in the GARCH and Kalman Labs): a synthetic
world with known regimes, or the log returns of a named source's history
(``ibkr`` reads the SQLite cache first). Both end in ``models.markov``,
run as one TaskRunner job: the fit (one regime count, or the BIC sweep over
1–3), then the filter, the smoother, the Viterbi path and the regime
forecast. So every chart, table and stat describes the same fit.

What the page shows:

* the price, tinted behind by the probability of each regime above the
  calmest (more strongly the more turbulent the regime), over a stacked
  panel of every regime's probability. A toggle picks which
  probabilities:

  - **filtered**: each day uses returns up to its own close, the only ones
    that could be traded on (though the parameters use the whole window,
    which the subtitle says);
  - **smoothed**: each day uses the whole sample, hindsight;
  - **Viterbi**: the single most likely regime path, hindsight;

* in synthetic mode, a strip of the true regimes under them;
* per-regime vol, drift, expected stay, long-run share and share of the
  sample's days (smoothed; in synthetic mode against the true regimes'
  realized share, which a short sample can hold far from the long run);
  the fit's log likelihood and BIC, and the BIC of every regime count
  tried;
* the transition matrix, and the regime odds from the last day forward
  (tomorrow, 5 and 20 days, the long run);
* notes when EM didn't converge (too many regimes, usually), when a regime
  sits on the variance floor, or when BIC chose a different count than the
  synthetic truth (then there's no TRUE column to line up).

Regimes are sorted by vol (``GaussianHMM.ordered``), so the calmest is
always green and the most turbulent always orange, in every panel and
table.

Presentation conversions only: means ×252 and vols ×√252 (D16), stays
1/(1 − p_ii) trading days, probabilities as percentages.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Panel, Series, Shade, StackedTimeSeries
from quant_hub.gui.components import (
    CardGrid,
    Card,
    DataTable,
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
from quant_hub.models.markov import (
    MIN_OBSERVATIONS,
    VARIANCE_FLOOR_RATIO,
    GaussianHMM,
    HMMFilterResult,
    HMMFit,
    HMMSimulation,
    RegimeForecast,
    fit_hmm,
    hmm_filter,
    hmm_smoother,
    regime_forecast,
    select_regimes,
    simulate_hmm,
    viterbi,
)
from quant_hub.services.market_service import MarketService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "markov_lab"
_SYNTHETIC, _MARKET = "Synthetic", "Market history"
_AUTO = "Auto (BIC)"
_COUNTS: dict[str, int | None] = {"2 regimes": 2, "3 regimes": 3, _AUTO: None}
_FILTERED, _SMOOTHED, _VITERBI = "Filtered", "Smoothed", "Viterbi"
_SYNTHETIC_START = "2024-01-02"   # calendar for synthetic paths (as in the other labs)
_START_PRICE = 100.0
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2   # two fields per row
_WINDOWS: dict[str, int | None] = {"1Y": 252, "3Y": 756, "ALL": None}
_FORECAST_DAYS = (1, 5, 20)
_VIEW_TITLES = {
    _FILTERED: "Filtered probabilities · each day from returns up to its close "
               "(the parameters use the whole window)",
    _SMOOTHED: "Smoothed probabilities · hindsight: each day from the whole sample",
    _VITERBI: "Viterbi path · hindsight: the single most likely regime sequence",
}
_ROW = 32                         # table row height

# Regimes are sorted by vol, so names and colours follow the order.
_NAMES = {1: ("All days",), 2: ("Calm", "Turbulent"), 3: ("Calm", "Normal", "Turbulent")}
_COLORS = {1: (PALETTE.series,), 2: (PALETTE.series_3, PALETTE.series_2),
           3: (PALETTE.series_3, PALETTE.series, PALETTE.series_2)}


def _world(vols: tuple[float, ...], means: tuple[float, ...], stays: tuple[float, ...],
           leave: tuple[tuple[float, ...], ...]) -> GaussianHMM:
    """A synthetic world in readable units: annualized vols and drifts, the
    expected stay in each regime (days), and where each regime goes when it
    leaves (row shares)."""
    k = len(vols)
    transition = np.zeros((k, k))
    for i in range(k):
        exit_rate = 1.0 / stays[i]
        transition[i] = exit_rate * np.array(leave[i])
        transition[i, i] = 1.0 - exit_rate
    return GaussianHMM(np.array(means) / TRADING_DAYS_PER_YEAR,
                       np.array(vols) / math.sqrt(TRADING_DAYS_PER_YEAR), transition)


_WORLDS = {
    "Two regimes": _world((0.10, 0.28), (0.15, -0.20), (60, 20), ((0, 1), (1, 0))),
    "Three regimes": _world((0.08, 0.16, 0.35), (0.20, 0.05, -0.40), (40, 25, 15),
                            ((0, 0.9, 0.1), (0.6, 0, 0.4), (0.2, 0.8, 0))),
    "No regimes": _world((0.16,), (0.08,), (math.inf,), ((0,),)),
}


@dataclass(frozen=True)
class _Run:
    dates: pd.DatetimeIndex      # each return's date (the close it ends on)
    prices: np.ndarray           # the closes on those dates
    returns: np.ndarray
    fit: HMMFit                  # the fit shown (BIC's choice in auto mode)
    fits: tuple[HMMFit, ...]     # every fit made
    filtered: HMMFilterResult
    smoothed: np.ndarray
    path: np.ndarray             # Viterbi
    forecast: RegimeForecast     # from the last day's filtered probabilities
    truth: HMMSimulation | None
    true_log_likelihood: float | None   # at the true parameters: the fit must beat it
    label: str


def _analyse(dates: pd.DatetimeIndex, prices: np.ndarray, returns: np.ndarray,
             count: int | None, truth: HMMSimulation | None, label: str) -> _Run:
    """The whole job, on the worker thread."""
    if count is None:
        selection = select_regimes(returns)
        fit, fits = selection.chosen, selection.fits
    else:
        fit = fit_hmm(returns, count)
        fits = (fit,)
    filtered = hmm_filter(fit.model, returns)
    return _Run(dates, prices, returns, fit, fits, filtered, hmm_smoother(filtered).smoothed,
                viterbi(fit.model, returns),
                regime_forecast(fit.model, filtered.filtered[-1], max(_FORECAST_DAYS)),
                truth,
                hmm_filter(truth.model, returns).log_likelihood if truth is not None else None,
                label)


def _stat_groups(k: int):
    regimes = tuple(
        (name, ((f"vol{i}", "Vol  annualized"), (f"mean{i}", "Drift  annualized"),
                (f"stay{i}", "Expected stay  days"), (f"share{i}", "Long-run share"),
                (f"days{i}", "Share of these days")))
        for i, name in enumerate(_NAMES[k]))
    return (*regimes,
            ("Fit", (("loglik", "Log-likelihood"), ("bic", "BIC"), ("observations", "Returns"),
                     ("iterations", "EM iterations"))),
            ("BIC", (("bic1", "1 regime"), ("bic2", "2 regimes"), ("bic3", "3 regimes"))))


def _stay(days: float) -> str:
    return "∞" if not math.isfinite(days) else f"{days:,.1f}"


class MarkovLabPage(ScrollPage):
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
        self._count = saved.get("count") if saved.get("count") in _COUNTS else _AUTO
        self._view = saved.get("view") if saved.get("view") in (
            _FILTERED, _SMOOTHED, _VITERBI) else _FILTERED
        self._window = saved.get("window") if saved.get("window") in _WINDOWS else "ALL"

        self._header = PageHeader(
            "Markov Lab",
            "Hidden volatility regimes in daily returns, on a known synthetic world or real "
            "history")
        self.body.addWidget(self._header)

        grid = CardGrid()
        grid.add_column([("regimes", self._build_chart_card(), 14),
                         ("tables", self._build_tables_card(), None)], x=0, w=9)

        inputs = self._build_inputs(saved, names)
        self._warning = Note("ph.warning", tone="critical")
        self._finding = Note("ph.info", tone="info")
        for note in (self._warning, self._finding):
            note.setVisible(False)
        stats_card = Card("Estimates")
        self._stats = {k: StatTable(_stat_groups(k)) for k in _NAMES}
        for table in self._stats.values():
            stats_card.body.addWidget(table)
        self._show_stats(2)
        grid.add_column([("inputs", inputs, None), ("warning", self._warning, None),
                         ("finding", self._finding, None), ("stats", stats_card, None)],
                        x=9, w=3, min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------- layout
    def _build_chart_card(self) -> Card:
        self._chart_card = Card("Regimes", "")
        self._views = SegmentedControl([_FILTERED, _SMOOTHED, _VITERBI], self._view)
        self._views.setToolTip(
            "Filtered: each day from returns up to its close (what could be traded on). "
            "Smoothed: each day from the whole sample (hindsight). "
            "Viterbi: the single most likely regime path (hindsight).")
        self._views.changed.connect(self._on_view)
        self._chart_card.add_action(self._views)
        self._windows = SegmentedControl(list(_WINDOWS), self._window)
        self._windows.changed.connect(self._on_window)
        self._chart_card.add_action(self._windows)
        self._chart_stack = QStackedWidget()
        self._chart = StackedTimeSeries(readout_per_panel=True)
        self._chart_empty = EmptyState("ph.share-network", "No fit yet",
                                       "Choose an input on the right and press Fit regimes.")
        self._chart_stack.addWidget(self._chart)
        self._chart_stack.addWidget(self._chart_empty)
        self._chart_stack.setCurrentWidget(self._chart_empty)
        self._chart_stack.setMinimumHeight(460)
        self._chart_card.body.addWidget(self._chart_stack, 1)
        return self._chart_card

    def _build_tables_card(self) -> Card:
        self._tables_card = Card(
            "Transitions and odds",
            "Row: today's regime; column: tomorrow's. Odds run forward from the last day's "
            "filtered probabilities.")
        # Stacked, each full width: side by side, the columns don't fit at 1280.
        rows = QVBoxLayout()
        rows.setSpacing(SPACE_L)
        self._tables_card.body.addLayout(rows)
        self._tables_row = rows
        self._tables_k = 0
        self._transitions: DataTable | None = None
        self._odds: DataTable | None = None
        return self._tables_card

    def _build_inputs(self, saved: dict, names: list[str]) -> Card:
        card = Card("Model inputs")
        self._modes = SegmentedControl([_SYNTHETIC, _MARKET], self._mode, fill=True)
        self._modes.changed.connect(self._on_mode)
        card.body.addWidget(self._modes)
        self._counts = SegmentedControl(list(_COUNTS), self._count, fill=True)
        self._counts.setToolTip("Auto fits 1, 2 and 3 regimes and keeps the lowest BIC.")
        self._counts.changed.connect(self._on_count)
        card.body.addWidget(self._counts)

        synthetic = QWidget()
        # Three regimes by default: every candidate count is identified there, so
        # the BIC sweep converges in ~4 s. On the two-regime world the 3-regime
        # fit is unidentified and runs out its 500 EM iterations (~9 s).
        world = saved.get("world", "Three regimes")
        self._world = SelectField("True world", list(_WORLDS),
                                  world if world in _WORLDS else "Three regimes",
                                  2 * _HALF + SPACE_M)          # the width of two fields
        self._days = Field("Days", saved.get("days", "2520"), _HALF)
        self._seed = Field("Seed", saved.get("seed", "42"), _HALF, placeholder="random")
        rows = QVBoxLayout(synthetic)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(SPACE_M)
        rows.addWidget(self._world)
        rows.addLayout(form_grid([self._days, self._seed], 2))
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
        for field in (self._days, self._seed, self._symbol, self._lookback):
            field.committed.connect(self._fit)

        self._fit_button = button("Fit regimes", "primary", "ph.lightning", on_click=self._fit)
        card.body.addWidget(self._fit_button)
        self._show_inputs()
        return card

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._show_inputs()

    def _on_count(self, count: str) -> None:
        self._count = count

    def _show_inputs(self) -> None:
        for mode, group in self._groups.items():
            group.setVisible(mode == self._mode)

    def _on_view(self, view: str) -> None:
        self._view = view
        if self._run_result is not None:
            self._render_chart(self._run_result)

    def _on_window(self, window: str) -> None:
        self._window = window
        if self._run_result is not None:
            self._render_chart(self._run_result)

    def _show_stats(self, k: int) -> None:
        for count, table in self._stats.items():
            table.setVisible(count == k)

    # ---------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if self._run_result is None:
            self._fit()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "mode": self._mode, "count": self._count, "view": self._view,
            "window": self._window, "world": self._world.current(),
            "days": self._days.text(), "seed": self._seed.text(),
            "source": self._source.current(), "basis": self._basis.current(),
            "symbol": self._symbol.text(),
            "lookback": self._lookback.text(),
        })

    # ---------------------------------------------------------------- fit
    def _fit(self) -> None:
        try:
            work = self._synthetic_job() if self._mode == _SYNTHETIC else self._market_job()
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._fit_button.setEnabled(False)
        self._header.set_status("Fitting regimes…" if _COUNTS[self._count] else
                                "Fitting 1, 2 and 3 regimes…")
        self._tasks.submit(work, on_done=self._on_done, on_error=self._on_error)

    def _synthetic_job(self):
        world = self._world.current()
        model = _WORLDS[world]
        days = self._days.number(integer=True, minimum=MIN_OBSERVATIONS + 1)
        seed = self._seed.number(integer=True, allow_blank=True)
        count = _COUNTS[self._count]

        def work() -> _Run:
            simulation = simulate_hmm(model, days, seed)
            prices = _START_PRICE * np.exp(np.cumsum(simulation.returns))
            return _analyse(pd.bdate_range(_SYNTHETIC_START, periods=days), prices,
                            simulation.returns, count, simulation,
                            f"Synthetic · {world.lower()} · {days:,} days · "
                            f"seed {seed if seed is not None else 'random'}")
        return work

    def _market_job(self):
        symbol = self._symbol.required_text()
        lookback = self._lookback.number(integer=True, minimum=150)
        source = self._source.current()
        basis = PRICE_BASES[self._basis.current()]
        count = _COUNTS[self._count]

        def work() -> _Run:
            end = datetime.now()
            history = self._market.get_history(symbol, end - timedelta(days=lookback), end,
                                               source=source, basis=basis)
            returns = history.log_returns()
            if len(returns) < MIN_OBSERVATIONS:
                raise ValueError(f"{len(returns)} daily returns; a regime fit needs at least "
                                 f"{MIN_OBSERVATIONS}: lengthen the lookback")
            prices = history.closes.loc[returns.index].to_numpy(dtype=float)
            return _analyse(returns.index, prices, returns.to_numpy(dtype=float), count, None,
                            f"{history.symbol} · {history.source} · {basis.value} · "
                            f"{len(returns):,} daily returns{offline_note(history)}")
        return work

    def _on_done(self, run: _Run) -> None:
        self._run_result = run
        self._fit_button.setEnabled(True)
        fit = run.fit
        k = fit.model.n_regimes
        how = " (lowest BIC)" if len(run.fits) > 1 else ""
        self._header.set_status(
            f"Fitted {k} regime{'s' if k != 1 else ''}{how} to {len(run.returns):,} returns"
            + (f" · {fit.iterations} EM iterations" if k > 1 else ""))
        self._render_chart(run)
        self._chart_stack.setCurrentWidget(self._chart)
        self._render_tables(run)
        self._render_notes(run)
        self._render_stats(run)

    def _on_error(self, exc: Exception) -> None:
        self._fit_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)
        _logger.error("Markov fit failed: %s", exc)

    # -------------------------------------------------------------- chart
    def _probabilities(self, run: _Run) -> np.ndarray:
        if self._view == _SMOOTHED:
            return run.smoothed
        if self._view == _VITERBI:
            return np.eye(run.fit.model.n_regimes)[run.path]
        return run.filtered.filtered

    def _render_chart(self, run: _Run) -> None:
        """Draw the trailing window; the fit always uses every return."""
        count = _WINDOWS[self._window]
        start = 0 if count is None else max(0, len(run.returns) - count)
        k = run.fit.model.n_regimes
        names, colors = _NAMES[k], _COLORS[k]
        probabilities = self._probabilities(run)[start:]
        # Each regime above calm tints the background by its probability,
        # more strongly the more turbulent it is: calm none, turbulent full.
        shades = [Shade(probabilities[:, i] * i / (k - 1), colors[i]) for i in range(1, k)]
        price = Panel("Price · tinted by regime, more strongly the more turbulent",
                      [Series("Close", run.prices[start:], PALETTE.text, 1.2)],
                      stretch=3, shades=shades)
        regimes = Panel(_VIEW_TITLES[self._view],
                        [Series(names[i], probabilities[:, i], colors[i], fmt="{:.0%}")
                         for i in range(k)],
                        axis="percent", stretch=2, y_range=(0.0, 1.0), stacked=True)
        panels = [price, regimes]
        if run.truth is not None:
            true_k = run.truth.model.n_regimes
            one_hot = np.eye(true_k)[run.truth.states[start:]]
            panels.append(Panel("True regime",
                                [Series(f"True {_NAMES[true_k][i].lower()}", one_hot[:, i],
                                        _COLORS[true_k][i], fmt="{:.0%}")
                                 for i in range(true_k)],
                                axis="percent", stretch=1, y_range=(0.0, 1.0), stacked=True))
        self._chart.set_panels(run.dates[start:], panels)
        self._chart_card.set_subtitle(f"{run.label} · hover for values")

    # ------------------------------------------------------------- tables
    def _render_tables(self, run: _Run) -> None:
        model = run.fit.model
        k = model.n_regimes
        if k != self._tables_k:
            self._build_tables(k)
        names, colors = _NAMES[k], _COLORS[k]
        truth = self._aligned_truth(run)
        rows = []
        for i in range(k):
            cells = [(names[i], colors[i])]
            for j in range(k):
                text = f"{model.transition[i, j]:.1%}"
                if truth is not None:
                    text += f"  ({truth.transition[i, j]:.1%})"
                cells.append(text)
            stay = _stay(model.expected_durations[i])
            if truth is not None:
                stay += f"  ({_stay(truth.expected_durations[i])})"
            cells.append(stay)
            rows.append(cells)
        self._transitions.set_rows(rows)

        today = run.filtered.filtered[-1]
        ahead = run.forecast.probabilities
        long_run = model.stationary_distribution
        self._odds.set_rows([
            [(names[i], colors[i]), f"{today[i]:.0%}",
             *(f"{ahead[h - 1, i]:.0%}" for h in _FORECAST_DAYS), f"{long_run[i]:.0%}"]
            for i in range(k)])
        self._tables_card.set_subtitle(
            "Row: today's regime; column: tomorrow's"
            + (" (the truth in parentheses)" if truth is not None else "")
            + ". Odds run forward from the last day's filtered probabilities.")

    def _build_tables(self, k: int) -> None:
        for table in (self._transitions, self._odds):
            if table is not None:
                self._tables_row.removeWidget(table)
                table.deleteLater()
        names = _NAMES[k]
        self._transitions = DataTable(
            [("From \\ to", "left"), *((name, "right") for name in names),
             ("Stay  days", "right")], row_height=_ROW)
        self._odds = DataTable(
            [("Odds", "left"), ("Today", "right"),
             *((f"{h} day{'s' if h != 1 else ''}", "right") for h in _FORECAST_DAYS),
             ("Long run", "right")], row_height=_ROW)
        for table in (self._transitions, self._odds):
            table.balance_columns()
            table.setFixedHeight(36 + k * _ROW)
            self._tables_row.addWidget(table, 1)
        self._tables_k = k

    # -------------------------------------------------------------- stats
    def _aligned_truth(self, run: _Run) -> GaussianHMM | None:
        """The true model when its regimes line up with the fit's (same count)."""
        if run.truth is None or run.truth.model.n_regimes != run.fit.model.n_regimes:
            return None
        return run.truth.model

    def _render_notes(self, run: _Run) -> None:
        fit = run.fit
        k = fit.model.n_regimes
        self._warning.setVisible(not fit.converged)
        if not fit.converged:
            self._warning.set_text(
                f"EM stopped at {fit.iterations} iterations without converging. That usually "
                "means more regimes than the data supports: compare the BIC values.")
        findings = []
        if fit.at_floor:
            which = " and ".join(_NAMES[k][i].lower() for i in fit.at_floor)
            findings.append(
                f"The {which} regime's σ sits on the variance floor "
                f"({math.sqrt(VARIANCE_FLOOR_RATIO):.0%} of the sample's σ). It is fitting a "
                "cluster of near-identical returns, such as stale prices, not a market regime.")
        if run.truth is not None and run.truth.model.n_regimes != k:
            true_k = run.truth.model.n_regimes
            findings.append(
                f"BIC chose {k} regime{'s' if k != 1 else ''}; this world has {true_k}, so "
                "there's no TRUE column to line up. Short samples often can't tell regimes "
                "apart.")
        self._finding.set_text(" ".join(findings))
        self._finding.setVisible(bool(findings))

    def _render_stats(self, run: _Run) -> None:
        fit, model = run.fit, run.fit.model
        k = model.n_regimes
        table = self._stats[k]
        self._show_stats(k)
        truth = self._aligned_truth(run)
        table.show_truth(truth is not None)
        root_year = math.sqrt(TRADING_DAYS_PER_YEAR)

        def regime_stats(m: GaussianHMM) -> list[tuple[str, str, str, str]]:
            shares, stays = m.stationary_distribution, m.expected_durations
            return [(f"{m.sigmas[i] * root_year:.1%}",
                     f"{m.means[i] * TRADING_DAYS_PER_YEAR:+.1%}",
                     _stay(stays[i]), f"{shares[i]:.1%}") for i in range(m.n_regimes)]

        fitted = regime_stats(model)
        true = regime_stats(truth) if truth is not None else [("",) * 4] * k
        for i in range(k):
            for key, value, true_value in zip(("vol", "mean", "stay", "share"),
                                              fitted[i], true[i]):
                table.set_value(f"{key}{i}", value, true_value)
        # A short sample's mix of regimes can sit far from the long run (a few
        # dozen spells of each); this row compares the fit with what happened.
        occupied = run.smoothed.mean(axis=0)
        realized = (np.bincount(run.truth.states, minlength=k) / len(run.returns)
                    if truth is not None else None)
        for i in range(k):
            table.set_value(f"days{i}", f"{occupied[i]:.1%}",
                            f"{realized[i]:.1%}" if realized is not None else "")

        table.set_value("loglik", f"{fit.log_likelihood:,.1f}",
                        f"{run.true_log_likelihood:,.1f}"
                        if truth is not None and run.true_log_likelihood is not None else "")
        table.set_value("bic", f"{fit.bic:,.1f}")
        table.set_value("observations", f"{len(run.returns):,}")
        table.set_value("iterations", "closed form" if k == 1 else
                        f"{fit.iterations}" + ("" if fit.converged else " (stopped)"))
        by_count = {f.model.n_regimes: f for f in run.fits}
        for count in (1, 2, 3):
            other = by_count.get(count)
            if other is None:
                table.set_value(f"bic{count}", "not fitted")
            else:
                chosen = other is fit and len(run.fits) > 1
                marks = ("  ✓ lowest" if chosen else "") + ("" if other.converged
                                                             else "  (EM stopped)")
                table.set_value(f"bic{count}", f"{other.bic:,.1f}{marks}",
                                tone="good" if chosen else "")
