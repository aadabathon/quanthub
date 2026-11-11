"""Simulator: controlled GBM experiments (Monte Carlo included).

Synthetic only, by design: a known process is simulated, then estimated and
compared with the truth. Real instruments live on Markets.

One piece of state drives every chart and number (the D15 invariant): the
``(simulation, analysis, Monte Carlo summary, fan bands)`` produced by one
TaskRunner job, so everything on screen describes the same draw. Changing
the vol window re-runs only the analysis of the same path (the draw stays
fixed; only the estimator window moves).

No math here: analytics come from ``data.stats`` / ``data.simulation``. The
only arithmetic is presentation — aligning the rolling-vol series to the
path's dates (its first ``window`` values are undefined, shown as gaps).
"""

from __future__ import annotations

import logging
import math

import numpy as np
from PySide6.QtWidgets import QStackedWidget

from quant_hub.config import AppConfig
from quant_hub.data.simulation import (
    GBMSimulationResult,
    MonteCarloSummary,
    monte_carlo_summary,
    path_percentiles,
    run_gbm_simulation,
)
from quant_hub.data.stats import SeriesAnalysis, analyze_history
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import FanChart, Histogram, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    CardGrid,
    Card,
    EmptyState,
    Field,
    PageHeader,
    SegmentedControl,
    StatTable,
    button,
    form_grid,
)
from quant_hub.gui.pages.base import saved_grid, ScrollPage
from quant_hub.gui.theme import FIELD_S, PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "simulator"          # shared with the Tk page
_PATH, _MONTE_CARLO = "Sample path", "Monte Carlo"
_LOG, _SIMPLE = "Log", "Simple"
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2

_STAT_GROUPS = (
    ("Process", (("mu", "μ  drift"), ("sigma", "σ  volatility"), ("log_drift", "μ − σ²/2"))),
    ("Sample path", (
        ("end_price", "End price"), ("total_return", "Total return"), ("cagr", "CAGR"),
        ("annualized_vol", "Volatility (ann.)"), ("sharpe", "Sharpe (rf = 0)"),
        ("max_drawdown", "Max drawdown"), ("skewness", "Skewness (log r)"),
        ("excess_kurtosis", "Excess kurtosis"), ("n_observations", "Observations"))),
)
_FORMATS = {"end_price": "price", "total_return": "signed_pct", "cagr": "signed_pct",
            "annualized_vol": "pct", "sharpe": "signed_num", "max_drawdown": "signed_pct",
            "skewness": "signed_num", "excess_kurtosis": "signed_num", "n_observations": "int"}
_SIGNED = {"total_return", "cagr", "sharpe", "max_drawdown"}


def _fmt(value: float, kind: str) -> str:
    if kind == "int":
        return f"{int(value):,}"
    if isinstance(value, float) and math.isnan(value):
        return "n/a"
    return {"price": f"{value:,.2f}", "pct": f"{value:.2%}", "signed_pct": f"{value:+.2%}",
            "signed_num": f"{value:+.2f}"}[kind]


def _tone(value: float) -> str:
    if not math.isfinite(value) or value == 0:
        return ""
    return "good" if value > 0 else "critical"


class SimulatorPage(ScrollPage):
    def __init__(self, tasks: TaskRunner, config: AppConfig,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._tasks = tasks
        self._state_store = state_store
        self._sim: GBMSimulationResult | None = None
        self._analysis: SeriesAnalysis | None = None
        self._mc: MonteCarloSummary | None = None
        self._bands: dict[int, np.ndarray] | None = None
        saved = state_store.load(_STATE_KEY)
        c = config
        self._returns_kind = _SIMPLE if saved.get("returns") == "simple" else _LOG
        self._view = _MONTE_CARLO if saved.get("view") == "mc" else _PATH

        self._header = PageHeader(
            "Simulator",
            "Controlled GBM experiments: a known process, simulated, estimated and "
            "compared with the truth")
        self.body.addWidget(self._header)
        # The two distribution cards sit side by side; on a narrow page the
        # grid drops the second below the first.
        grid = CardGrid()
        grid.add("path", self._build_path_card(saved.get("window", str(c.default_vol_window))),
                 0, 0, 9, 14)
        grid.add("returns", self._build_returns_card(), 0, 14, 5, 9)
        grid.add("terminal", self._build_terminal_card(), 5, 14, 4, 9)
        inputs = self._build_inputs(saved, c)
        stats_card = Card("Estimates")
        self._stats = StatTable(_STAT_GROUPS, value_header="ESTIMATE")
        stats_card.body.addWidget(self._stats)
        grid.add_column([("inputs", inputs, None), ("stats", stats_card, None)],
                        x=9, w=3, min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------ building
    def _build_path_card(self, window: str) -> Card:
        self._path_card = Card("Paths", "")
        self._window = Field("Vol window", window, FIELD_S, inline=True)
        self._window.setToolTip("Trailing window (days) for the rolling-volatility panel")
        self._window.committed.connect(self._reanalyze)
        self._path_card.add_action(self._window)
        self._views = SegmentedControl([_PATH, _MONTE_CARLO], self._view)
        self._views.changed.connect(self._on_view)
        self._path_card.add_action(self._views)
        self._path_stack = QStackedWidget()
        self._series_chart = StackedTimeSeries()
        self._fan = FanChart()
        self._path_empty = EmptyState("ph.trend-up", "No simulation yet",
                                      "Set the process on the right and press Run simulation.")
        for widget in (self._series_chart, self._fan, self._path_empty):
            self._path_stack.addWidget(widget)
        self._path_stack.setCurrentWidget(self._path_empty)
        self._path_stack.setMinimumHeight(360)
        self._path_card.body.addWidget(self._path_stack, 1)
        return self._path_card

    def _build_returns_card(self) -> Card:
        self._returns_card = Card("Return distribution", "")
        kinds = SegmentedControl([_LOG, _SIMPLE], self._returns_kind)
        kinds.changed.connect(self._on_returns_kind)
        self._returns_card.add_action(kinds)
        self._returns_hist = Histogram(axis="percent", bin_fmt="{:+.2%}")
        self._returns_hist.setMinimumHeight(200)
        self._returns_card.body.addWidget(self._returns_hist, 1)
        return self._returns_card

    def _build_terminal_card(self) -> Card:
        self._terminal_card = Card("Terminal price S(T)", "")
        self._terminal_hist = Histogram(axis="price", bin_fmt="{:,.2f}")
        self._terminal_hist.setMinimumHeight(200)
        self._terminal_card.body.addWidget(self._terminal_hist, 1)
        return self._terminal_card

    def _build_inputs(self, saved: dict, c: AppConfig) -> Card:
        card = Card("GBM process")
        self._s0 = Field("S₀", saved.get("s0", f"{c.default_initial_price:g}"), _HALF)
        self._mu = Field("μ  % / yr", saved.get("mu", f"{c.default_mu * 100:g}"), _HALF)
        self._sigma = Field("σ  % / yr", saved.get("sigma", f"{c.default_sigma * 100:g}"), _HALF)
        self._days = Field("Days", saved.get("days", str(c.default_sim_days)), _HALF)
        self._seed = Field("Seed", saved.get("seed", str(c.default_seed)), _HALF,
                           placeholder="random")
        self._paths = Field("Paths", saved.get("paths", str(c.default_n_paths)), _HALF)
        fields = [self._s0, self._mu, self._sigma, self._days, self._seed, self._paths]
        card.body.addLayout(form_grid(fields, 2))
        for field in fields:
            field.committed.connect(self._run)
        self._run_button = button("Run simulation", "primary", "ph.play", on_click=self._run)
        card.body.addWidget(self._run_button)
        return card

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if self._analysis is None:
            self._run()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "s0": self._s0.text(), "mu": self._mu.text(), "sigma": self._sigma.text(),
            "days": self._days.text(), "seed": self._seed.text(), "paths": self._paths.text(),
            "window": self._window.text(), "returns": self._returns_kind.lower(),
            "view": "mc" if self._view == _MONTE_CARLO else "path",
        })

    # ------------------------------------------------------------- actions
    def _run(self) -> None:
        try:
            s0 = self._s0.number()
            mu = self._mu.number(scale=0.01)
            sigma = self._sigma.number(scale=0.01)
            days = self._days.number(integer=True, minimum=2)
            seed = self._seed.number(integer=True, allow_blank=True)
            n_paths = self._paths.number(integer=True, minimum=2)
            window = self._window.number(integer=True, minimum=2)
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._run_button.setEnabled(False)
        self._header.set_status("Simulating…")

        def work():
            sim = run_gbm_simulation(initial_price=s0, mu=mu, sigma=sigma, n_days=days,
                                     seed=seed, n_paths=n_paths)
            return (sim, analyze_history(sim.history, vol_window=window),
                    monte_carlo_summary(sim), path_percentiles(sim))

        self._tasks.submit(work, on_done=self._on_simulated, on_error=self._on_error)

    def _reanalyze(self) -> None:
        if self._analysis is None:
            return
        try:
            window = self._window.number(integer=True, minimum=2)
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        history = self._analysis.history
        self._tasks.submit(lambda: analyze_history(history, vol_window=window),
                           on_done=self._on_reanalyzed, on_error=self._on_error)

    def _on_view(self, view: str) -> None:
        self._view = view
        self._show_view()

    def _on_returns_kind(self, kind: str) -> None:
        self._returns_kind = kind
        if self._analysis is not None:
            self._render_returns()

    # -------------------------------------------------------------- results
    def _on_simulated(self, result) -> None:
        self._sim, self._analysis, self._mc, self._bands = result
        self._run_button.setEnabled(True)
        sim = self._sim
        self._header.set_status(f"Simulated {sim.n_paths:,} paths × {sim.n_days} days")
        self._render_all()

    def _on_reanalyzed(self, analysis: SeriesAnalysis) -> None:
        self._analysis = analysis
        self._header.set_status(f"Rolling volatility re-estimated with a "
                                f"{analysis.vol_window}-day window")
        self._render_path()
        self._render_stats()

    def _on_error(self, exc: Exception) -> None:
        self._run_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)
        _logger.error("simulation failed: %s", exc)

    # --------------------------------------------------------------- render
    def _render_all(self) -> None:
        self._render_path()
        self._render_fan()
        self._render_returns()
        self._render_terminal()
        self._render_stats()
        self._show_view()

    def _show_view(self) -> None:
        if self._analysis is None:
            return
        self._path_stack.setCurrentWidget(
            self._series_chart if self._view == _PATH else self._fan)
        self._window.setVisible(self._view == _PATH)

    def _render_path(self) -> None:
        a, p = self._analysis, self._sim.params
        dates = a.history.frame.index
        rolling = a.rolling_vol.reindex(dates).to_numpy(dtype=float)
        self._series_chart.set_panels(dates, [
            Panel(f"{a.history.symbol} close", [Series(
                "Close", a.history.closes.to_numpy(dtype=float), PALETTE.series, 1.8)],
                stretch=5),
            Panel(f"Drawdown · max {a.stats.max_drawdown:.1%}", [Series(
                "Drawdown", a.drawdowns.to_numpy(dtype=float), PALETTE.series, 1.0,
                fmt="{:.1%}", fill=True)], axis="percent", stretch=2, zero_line=True),
            Panel(f"Rolling volatility · {a.vol_window}-day window · annualized", [Series(
                "Rolling σ", rolling, PALETTE.series, 1.4, fmt="{:.1%}")],
                axis="percent", stretch=3, references=[(f"true σ {p.sigma:.0%}", p.sigma)]),
        ])
        seed = "random" if p.seed is None else p.seed
        self._path_card.set_subtitle(
            f"GBM · μ {p.mu:.2%} · σ {p.sigma:.2%} · T {self._sim.horizon_years:.2f}y · "
            f"{self._sim.n_paths:,} paths · seed {seed} · the sample path is path 1")

    def _render_fan(self) -> None:
        sim = self._sim
        self._fan.set_fan(sim.dates, sim.paths, self._bands, sim.n_paths, overlays=[
            Series("E[Sₜ] = S₀·exp(μt)", sim.expected_price_curve(), PALETTE.text_secondary,
                   1.4, dashed=True),
            Series("Sample path", sim.paths[0], PALETTE.series_2, 1.4),
        ])

    def _render_returns(self) -> None:
        a = self._analysis
        if self._returns_kind == _LOG:
            values, mean, sd = a.log_returns, a.stats.mean_log_return, a.stats.std_log_return
        else:
            values, mean, sd = (a.simple_returns, a.stats.mean_simple_return,
                                a.stats.std_simple_return)
        normal = (mean, sd) if math.isfinite(sd) and sd > 0 else None
        self._returns_hist.set_data(values.to_numpy(dtype=float), normal=normal,
                                    normal_name="normal, sample moments")
        self._returns_card.set_subtitle(
            f"Daily {self._returns_kind.lower()} returns of the sample path")

    def _render_terminal(self) -> None:
        sim, mc = self._sim, self._mc
        marker = lambda name, color, width=1.4, dashed=False: Series(  # noqa: E731
            name, np.empty(0), color, width, dashed=dashed)
        # S(T) is lognormal: a minority of large outcomes pulls the mean above
        # the typical (median) outcome; E[S(T)] is the mean's closed form.
        self._terminal_hist.set_data(sim.terminal_prices, markers=[
            (marker("Median", PALETTE.series_light), mc.median),
            (marker("Mean", PALETTE.text), mc.mean),
            (marker("E[S(T)]", PALETTE.text_secondary, dashed=True), mc.expected),
            (marker("p5", PALETTE.muted, 1.0, dashed=True), mc.percentiles[5]),
            (marker("p95", PALETTE.muted, 1.0, dashed=True), mc.percentiles[95]),
        ])
        self._terminal_card.set_subtitle(
            f"All {sim.n_paths:,} paths · E[S(T)] = S₀·exp(μT) is the closed form · "
            "lognormal, so mean > median")

    def _render_stats(self) -> None:
        a, p = self._analysis, self._sim.params
        est = a.gbm
        for key, true, value, kind in (
            ("mu", p.mu, est.mu_hat, "signed_pct"),
            ("sigma", p.sigma, est.sigma_hat, "pct"),
            ("log_drift", p.mu - 0.5 * p.sigma**2, est.log_drift_hat, "signed_pct"),
        ):
            self._stats.set_value(key, _fmt(value, kind), _fmt(true, kind))
        self._stats.show_truth(True)
        for key, kind in _FORMATS.items():
            value = getattr(a.stats, key)
            self._stats.set_value(key, _fmt(value, kind),
                                  tone=_tone(value) if key in _SIGNED else "")
