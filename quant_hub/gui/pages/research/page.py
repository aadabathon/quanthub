"""Research experiments: forward decisions, retained evidence, explicit selection."""
from datetime import date

import numpy as np
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from quant_hub.gui.charts import Heatmap, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card, DataTable, EmptyState, Field, MetricTile, PageHeader, PageStack,
    SegmentedControl, SelectField, TextArea, button, form_grid, label,
)
from quant_hub.gui.pages.base import PRICE_BASES, ScrollPage, basis_field
from quant_hub.gui.pages.research.history import SavedRuns
from quant_hub.gui.pages.research.library import PaperLibrary
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models.backtest import BacktestConfig
from quant_hub.models.backtest_stats import rolling_sharpe
from quant_hub.models.research_worlds import WORLDS
from quant_hub.models.strategies import STRATEGIES, StrategySpec
from quant_hub.services.research import Experiment, WORLD_SOURCE

_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_TABS = ("Experiments", "Saved runs", "Papers")
_RULES = {
    "Momentum": ("0", "Buy when the trailing log return exceeds the threshold. Threshold 0.02 means about 2%."),
    "Mean reversion": ("1", "Buy below the rolling log-price mean by more than the threshold, in sample standard deviations."),
    "Kalman trend": ("0.5", "Buy when the forward-filtered slope / its state standard error exceeds the threshold."),
    "Kalman level": ("1", "Buy below the filtered local level by more than the threshold, in observation-noise standard deviations."),
    "GARCH volatility": ("0", "Hold the basket; size with the one-step GARCH variance and training correlation. Threshold is unused."),
    "Markov calm": ("0.65", "Buy when the filtered probability of the lowest-volatility regime exceeds the threshold (0–1)."),
}


def _number(value, pattern="{:.2f}"):
    return "—" if value is None or not np.isfinite(value) else pattern.format(value)


class ResearchPage(ScrollPage):
    def __init__(self, service, market, tasks, state_store):
        super().__init__()
        self._service, self._tasks, self._state_store = service, tasks, state_store
        self._sources = [WORLD_SOURCE, *market.source_names]
        self._run_result = None
        self._busy = False
        self._header = PageHeader("Research", "Turn a hypothesis into a reproducible experiment")
        self.body.addWidget(self._header)
        self._tabs = SegmentedControl(_TABS, _TABS[0])
        self.body.addWidget(self._tabs)
        self._stack = PageStack()
        self.body.addWidget(self._stack, 1)
        self._stack.addWidget(self._build_experiments(market.source_names))
        self._history = SavedRuns(service, tasks, self._header.set_status, self.open_run)
        self._library = PaperLibrary(service, tasks, self._header.set_status,
                                     lambda: self._run_result.id if self._run_result else None, self.open_run)
        self._stack.addWidget(self._history)
        self._stack.addWidget(self._library)
        self._tabs.changed.connect(self._tab)
        self._restore(state_store.load("research"))

    def _build_experiments(self, sources):
        page = QWidget()
        main = QHBoxLayout(page)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(SPACE_L)
        content = QVBoxLayout()
        content.setSpacing(SPACE_L)
        main.addLayout(content, 1)
        grid = QGridLayout()
        grid.setSpacing(SPACE_M)
        self._tiles = {"return": MetricTile("Net return", "test window"),
                       "cagr": MetricTile("CAGR", "252 sessions / year"),
                       "vol": MetricTile("Volatility", "annualized"),
                       "sharpe": MetricTile("Sharpe", "excess cash · IID SE"),
                       "drawdown": MetricTile("Max drawdown", "initial capital included"),
                       "turnover": MetricTile("Turnover", "annual · absolute notional / NAV")}
        for i, tile in enumerate(self._tiles.values()):
            grid.addWidget(tile, i // 3, i % 3)
        content.addLayout(grid)
        self._chart_card = Card("Test performance", "Run a controlled experiment or use observed daily prices")
        self._chart_stack = PageStack()
        self._chart_stack.addWidget(EmptyState("ph.flask", "Start with a control",
             "Run the default random walk: a useful research workflow should expose noise as well as find a planted edge."))
        self._chart = StackedTimeSeries(readout_per_panel=True)
        self._chart.setMinimumHeight(510)
        self._chart_stack.addWidget(self._chart)
        self._chart_stack.setMinimumHeight(510)
        self._chart_card.body.addWidget(self._chart_stack, 1)
        self._provenance = label("Close t decisions earn t → t+1. Long/cash only. Every completed run is saved.", "muted", wrap=True)
        self._chart_card.body.addWidget(self._provenance)
        content.addWidget(self._chart_card, 1)
        evidence = Card("Evidence", "Inspect trades, training windows and the full sweep")
        tabs = SegmentedControl(["Trades", "Fits", "Robustness"], "Trades")
        self._evidence_tabs = tabs
        evidence.body.addWidget(tabs)
        self._detail = PageStack()
        self._trades = DataTable([("Decision close", "left"), ("Asset", "left"),
                                  ("Target", "right"), ("Trade $", "right"), ("Fees $", "right")], stretch=1)
        self._fits = DataTable([("Asset", "left"), ("Training", "left"), ("Test decisions", "left"),
                                ("Fit status", "left")], stretch=1)
        self._fits.row_activated.connect(self._show_fit)
        robust = QWidget()
        layout = QVBoxLayout(robust)
        layout.setContentsMargins(0, 0, 0, 0)
        self._heatmap = Heatmap("lookback", "{:.0f}", "{:.2f}", "price", "Sharpe")
        self._heatmap.setMinimumHeight(250)
        self._selection = label("Run a grid to compare all recorded trials on the same test dates.", "muted", wrap=True)
        layout.addWidget(self._heatmap)
        layout.addWidget(self._selection)
        self._heatmap.hide()
        for widget in (self._trades, self._fits, robust):
            self._detail.addWidget(widget)
        self._detail.setMinimumHeight(300)
        tabs.changed.connect(lambda name: self._detail.setCurrentIndex(["Trades", "Fits", "Robustness"].index(name)))
        evidence.body.addWidget(self._detail)
        self._fit_detail = label("", "muted", wrap=True)
        evidence.body.addWidget(self._fit_detail)
        content.addWidget(evidence)
        notes = Card("Research notes", "Record the conclusion, next test, and reasons to reject the hypothesis")
        self._notes = TextArea(placeholder="What would invalidate this result? What should be tested on untouched data?")
        self._save_notes = button("Save notes", on_click=self._save_conclusion)
        self._save_notes.setEnabled(False)
        notes.add_action(self._save_notes)
        notes.body.addWidget(self._notes)
        content.addWidget(notes)

        side = QVBoxLayout()
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(SPACE_L)
        holder = QWidget()
        holder.setFixedWidth(SIDE_PANEL_WIDTH)
        holder.setLayout(side)
        main.addWidget(holder)
        self._controls = Card("Experiment", "Daily prices · explicit data and assumptions")
        mode = SegmentedControl(["Data", "Rule", "Costs"], "Data", fill=True)
        self._controls.body.addWidget(mode)
        form = PageStack()
        self._controls.body.addWidget(form)
        fields = {}
        def field(key, caption, value, width=_HALF):
            widget = Field(caption, value, width)
            fields[key] = widget
            return widget
        self._fields = fields
        data = QWidget()
        data_layout = QVBoxLayout(data)
        data_layout.setContentsMargins(0, 0, 0, 0)
        data_layout.setSpacing(SPACE_M)
        self._source = SelectField("Source", [WORLD_SOURCE, *sources], WORLD_SOURCE, _FULL)
        self._world = SelectField("Control world", WORLDS, WORLDS[0], _FULL)
        self._basis = basis_field({}, _FULL)
        for widget in (self._source, self._world, self._basis):
            data_layout.addWidget(widget)
        data_layout.addWidget(field("symbols", "Universe · comma separated", "WORLD", _FULL))
        data_layout.addLayout(form_grid([field("start", "From", "2020-01-01"),
                                        field("end", "Through", date.today().isoformat())], 2))
        data_layout.addLayout(form_grid([field("observations", "World prices", "1000"),
                                        field("seed", "Seed", "42")], 2))
        data_layout.addWidget(label("Hypothesis", "fieldCaption"))
        self._hypothesis = TextArea(placeholder="State the mechanism before looking at results…")
        self._hypothesis.setMaximumHeight(120)
        data_layout.addWidget(self._hypothesis)
        form.addWidget(data)
        rule = QWidget()
        rule_layout = QVBoxLayout(rule)
        rule_layout.setContentsMargins(0, 0, 0, 0)
        rule_layout.setSpacing(SPACE_M)
        self._strategy = SelectField("Strategy", STRATEGIES, STRATEGIES[0], _FULL)
        rule_layout.addWidget(self._strategy)
        rule_layout.addLayout(form_grid([field("lookback", "Signal / risk days", "63"),
                                        field("threshold", "Threshold", "0"),
                                        field("train_size", "Training prices", "252"),
                                        field("refit_every", "Refit every · days", "126"),
                                        field("target_vol", "Target vol % / year", "15"),
                                        field("gross_cap", "Gross cap %", "100")], 2))
        self._rule_help = label(_RULES[STRATEGIES[0]][1], "muted", wrap=True)
        rule_layout.addWidget(self._rule_help)
        rule_layout.addWidget(label("Equal asset budgets. Risk sizing uses known history; the cap leaves the remainder in cash. Training is excluded from reported returns.", "muted", wrap=True))
        form.addWidget(rule)
        costs = QWidget()
        cost_layout = QVBoxLayout(costs)
        cost_layout.setContentsMargins(0, 0, 0, 0)
        cost_layout.setSpacing(SPACE_M)
        cost_layout.addLayout(form_grid([field("half_spread_bps", "Half spread · bps", "1"),
                                        field("commission_bps", "Commission · bps", "1"),
                                        field("cash_rate", "Cash rate % / year", "0"),
                                        field("initial_equity", "Initial equity $", "100000")], 2))
        cost_layout.addWidget(label("Costs apply to absolute traded notional, including entry and rebalancing. No forced final liquidation. No impact, taxes or borrow model. Cash earns the effective annual rate.", "muted", wrap=True))
        form.addWidget(costs)
        mode.changed.connect(lambda name: form.setCurrentIndex(["Data", "Rule", "Costs"].index(name)))
        self._run_button = button("Run", "primary", "ph.play", self._run)
        self._controls.add_action(self._run_button)
        side.addWidget(self._controls)
        self._grid_card = Card("Parameter sweep", "All trials saved · common test window")
        self._grid_lookbacks = Field("Risk / signal days · evenly spaced", "21, 42, 63", _FULL)
        self._grid_thresholds = Field("Thresholds · comma separated", "0, 0.01, 0.02", _FULL)
        self._independent = Field("Effective independent trials · assumption", "9", _FULL)
        for widget in (self._grid_lookbacks, self._grid_thresholds, self._independent):
            self._grid_card.body.addWidget(widget)
        self._grid_card.body.addWidget(label("This count is your assumption about independent trials; correlated configurations are not independent. Include earlier searches when interpreting evidence.", "muted", wrap=True))
        self._sweep_button = button("Run grid", icon_name="ph.squares-four", on_click=self._sweep)
        self._grid_card.body.addWidget(self._sweep_button)
        side.addWidget(self._grid_card)
        side.addStretch(1)
        self._source.select.current_changed.connect(lambda _: self._data_mode())
        self._strategy.select.current_changed.connect(self._rule_changed)
        self._data_mode()
        return page

    def _tab(self, name):
        self._stack.setCurrentIndex(_TABS.index(name))
        if name == "Saved runs":
            self._history.refresh()
        elif name == "Papers":
            self._library.refresh()

    def _data_mode(self):
        world = self._source.current() == WORLD_SOURCE
        self._world.setVisible(world)
        self._basis.setVisible(not world)
        for key in ("start", "end"):
            self._fields[key].setEnabled(not world)
            self._fields[key].setVisible(not world)
        self._fields["observations"].setEnabled(world)

    def _rule_changed(self, name):
        threshold, text = _RULES[name]
        self._fields["threshold"].set_text(threshold)
        self._rule_help.setText(text)
        self._fields["threshold"].setEnabled(name != "GARCH volatility")
        self._sweep_button.setEnabled(name != "GARCH volatility")
        self._grid_thresholds.set_text("0.5, 0.65, 0.8" if name == "Markov calm" else
                                        "0.5, 1, 1.5" if name != "Momentum" else "0, 0.01, 0.02")

    def _experiment(self):
        f = self._fields
        spec = StrategySpec(self._strategy.current(), f["lookback"].number(integer=True),
                            f["threshold"].number(), f["train_size"].number(integer=True),
                            f["refit_every"].number(integer=True), f["target_vol"].number(scale=.01),
                            f["gross_cap"].number(scale=.01), f["seed"].number(integer=True))
        costs = BacktestConfig(initial_equity=f["initial_equity"].number(),
                               half_spread_bps=f["half_spread_bps"].number(),
                               commission_bps=f["commission_bps"].number(),
                               cash_rate=f["cash_rate"].number(scale=.01))
        observations = f["observations"].number(integer=True, minimum=3)
        if observations > 100_000:
            raise ValueError("World prices must be at most 100,000")
        return Experiment(spec, costs, self._source.current(),
                          tuple(s.strip().upper() for s in f["symbols"].required_text().split(",")),
                          PRICE_BASES[self._basis.current()].value,
                          f["start"].text(), f["end"].text(), self._world.current(), observations,
                          self._hypothesis.toPlainText())

    def _set_busy(self, busy):
        self._busy = busy
        self._controls.setEnabled(not busy)
        self._grid_card.setEnabled(not busy)

    def _run(self):
        if self._busy:
            return
        try:
            experiment = self._experiment()
        except ValueError as exc:
            self._error(exc)
            return
        self._set_busy(True)
        self._header.set_status("Running training / test blocks and saving evidence…")
        self._tasks.submit(lambda: self._service.run(experiment), on_done=self._render, on_error=self._error)

    def _sweep(self):
        if self._busy:
            return
        try:
            experiment = self._experiment()
            lookbacks = sorted(int(s.strip()) for s in self._grid_lookbacks.required_text().split(","))
            thresholds = sorted(float(s.strip()) for s in self._grid_thresholds.required_text().split(","))
            if len(lookbacks) < 2 or not np.allclose(np.diff(lookbacks), lookbacks[1] - lookbacks[0]):
                raise ValueError("Heatmap lookbacks must have equal spacing and at least two values")
            independent = self._independent.number(integer=True)
        except ValueError as exc:
            self._error(exc)
            return
        self._set_busy(True)
        self._header.set_status("Running grid; every completed trial is retained…")
        self._tasks.submit(lambda: self._service.sweep(experiment, lookbacks, thresholds, independent),
                           on_done=lambda result: self._render(result["best"]), on_error=self._error)

    def open_run(self, run_id):
        if self._busy:
            return
        self._set_busy(True)
        self._header.set_status("Verifying saved evidence and replaying accounting…")
        def done(run):
            self._restore_experiment(run.payload["experiment"])
            self._render(run)
            self._tabs.set_current("Experiments")
            self._tab("Experiments")
        self._tasks.submit(lambda: self._service.reopen(run_id), on_done=done, on_error=self._error)

    def _render(self, run):
        self._set_busy(False)
        self._run_result = run
        result, benchmark, payload = run.result, run.benchmark, run.payload
        stats = payload["summary"]
        self._tiles["return"].set_value(_number(stats["total_return"], "{:+.1%}"))
        self._tiles["cagr"].set_value(_number(stats["cagr"], "{:+.1%}"))
        self._tiles["vol"].set_value(_number(stats["volatility"], "{:.1%}"))
        self._tiles["sharpe"].set_value(_number(stats["sharpe"]["sharpe"]),
                hint="IID SE " + _number(stats["sharpe"]["standard_error_iid"]))
        self._tiles["drawdown"].set_value(_number(stats["max_drawdown"], "{:.1%}"))
        self._tiles["turnover"].set_value(_number(stats["turnover_annual"], "{:.1f}×"))
        dates = result.equity.index
        rolling = rolling_sharpe(result.net_returns, cash_rate=result.config.cash_rate)
        exposure = result.targets.sum(axis=1).reindex(dates)
        self._chart.set_panels(dates, [
            Panel("Equity · net of costs", [Series("Strategy", result.equity.to_numpy(), fmt="${:,.0f}"),
                     Series("Buy & hold", benchmark.equity.to_numpy(), PALETTE.series_2, dashed=True, fmt="${:,.0f}")], axis="money", stretch=2),
            Panel("Drawdown", [Series("Strategy", result.drawdown.to_numpy(), fill=True, fmt="{:.1%}")], axis="percent"),
            Panel("Rolling Sharpe · 63 sessions", [Series("Sharpe", rolling.reindex(dates).to_numpy(), fmt="{:.2f}")], zero_line=True),
            Panel("Target gross exposure · decision close", [Series("Exposure", exposure.to_numpy(), fmt="{:.0%}")], axis="percent", y_range=(0, 1)),
        ])
        self._chart_stack.setCurrentWidget(self._chart)
        prov = payload["provenance"]
        self._chart_card.set_subtitle(f"{payload['title']} · {prov.get('source', '')} · {prov.get('basis', '')}")
        full_dates = payload["prices"]["dates"]
        first = payload["first_decision"]
        warning_count = sum(not fit.get("converged", True) for fit in payload["fits"])
        self._provenance.setText(
            f"Training first block: {full_dates[0][:10]} → {full_dates[first - 1][:10]}. "
            f"Test P&L: {str(dates[1].date())} → {str(dates[-1].date())} · {stats['observations']:,} sessions. "
            f"Before-cost return {result.gross_equity.iloc[-1] / result.config.initial_equity - 1:+.1%} · "
            f"Fees ${result.fees.sum():,.2f} · Sortino {_number(stats['sortino'])}. "
            f"Benchmark: equal-weight buy & hold, same dates and costs. "
            f"Sharpe inference assumes independent returns. "
            + (f"{warning_count} fit(s) did not converge. " if warning_count else "")
            + ("Implementation changed since this run; saved decisions replayed. " if run.code_changed else "")
            + f"Evidence {payload['evidence_sha256'][:12]}.")
        trade_rows = []
        for i, day in enumerate(result.trades.index):
            for j, symbol in enumerate(result.trades.columns):
                value = result.trades.iloc[i, j]
                if abs(value) > 1e-7:
                    trade_rows.append([str(day.date()), symbol, f"{result.targets.iloc[i, j]:.1%}",
                                       f"{value:+,.2f}", f"{abs(value) * result.config.cost_rate:,.2f}"])
        self._trades.set_rows(trade_rows)
        self._fits.set_rows([[f["symbol"], f"{f['train_start']} → {f['train_end']}",
                              f"{f['test_start']} → {f['test_end']}",
                              "Converged" if f.get("parameters") and f["converged"] else
                              "NOT CONVERGED" if not f["converged"] else "Rolling rule"] for f in payload["fits"]])
        self._fit_detail.clear()
        self._notes.setPlainText(run.notes)
        self._save_notes.setEnabled(True)
        sweep = payload.get("sweep")
        self._heatmap.setVisible(bool(sweep and any(v is not None for v in sweep["sharpes"])))
        if sweep:
            grid = np.array([np.nan if x is None else x for x in sweep["sharpes"]]).reshape(len(sweep["thresholds"]), -1)
            if np.isfinite(grid).any():
                self._heatmap.set_grid(np.array(sweep["lookbacks"]), [f"threshold {v:g}" for v in sweep["thresholds"]], grid)
            self._selection.setText(f"Best grid trial · DSR (IID) {_number(sweep['deflated_probability_iid'], '{:.1%}')} · "
                     f"{len(sweep['run_ids'])} recorded trials · effective independent trials {sweep['independent_trials']}. "
                     + sweep["selection_warning"] + (" DSR unavailable: " + sweep["reason"] if sweep["reason"] else ""))
            self._detail.setCurrentIndex(2)
            self._evidence_tabs.set_current("Robustness")
        else:
            self._selection.setText("No sweep is attached to this run. Reported metrics describe one configuration; earlier searches still matter.")
        self._header.set_status("Saved · " + payload["title"], error=bool(warning_count))

    def _show_fit(self, row):
        if not self._run_result or not 0 <= row < len(self._run_result.payload["fits"]):
            return
        fit = self._run_result.payload["fits"][row]
        self._fit_detail.setText(str(fit))

    def _save_conclusion(self):
        if not self._run_result:
            return
        run_id, notes = self._run_result.id, self._notes.toPlainText()
        self._tasks.submit(lambda: self._service.save_notes(run_id, notes),
                           on_done=lambda _: self._header.set_status("Research notes saved"), on_error=self._error)

    def _error(self, exc):
        self._set_busy(False)
        self._header.set_status(str(exc), error=True)

    def _restore(self, state):
        for key, value in state.get("fields", {}).items():
            if key in self._fields:
                self._fields[key].set_text(str(value))
        for field, key, choices in [(self._source, "source", None), (self._world, "world", WORLDS),
                                    (self._strategy, "strategy", STRATEGIES), (self._basis, "basis", PRICE_BASES)]:
            if key in state:
                items = list(choices) if choices else self._sources
                field.select.set_items(items, state[key] if state[key] in items else items[0])
        self._hypothesis.setPlainText(state.get("hypothesis", ""))
        self._rule_help.setText(_RULES[self._strategy.current()][1])
        self._fields["threshold"].setEnabled(self._strategy.current() != "GARCH volatility")
        self._sweep_button.setEnabled(self._strategy.current() != "GARCH volatility")
        self._data_mode()

    def _restore_experiment(self, experiment):
        spec, costs = experiment["strategy"], experiment["costs"]
        values = {**spec, **costs, "symbols": ", ".join(experiment["symbols"]),
                  "start": experiment["start"], "end": experiment["end"],
                  "observations": experiment["observations"], "target_vol": spec["target_vol"] * 100,
                  "gross_cap": spec["gross_cap"] * 100, "cash_rate": costs["cash_rate"] * 100}
        self._restore({"fields": values, "source": experiment["source"], "world": experiment["world"],
                       "basis": "Adjusted" if experiment["basis"] == "adjusted" else "Traded",
                       "strategy": spec["name"], "hypothesis": experiment["hypothesis"]})

    def on_hide(self):
        self._state_store.save("research", {"fields": {k: f.text() for k, f in self._fields.items()},
                  "source": self._source.current(), "world": self._world.current(), "basis": self._basis.current(),
                  "strategy": self._strategy.current(), "hypothesis": self._hypothesis.toPlainText()})
