"""Return histogram (HRH): one stored security's return distribution (H2).

Godel Terminal's HRH, rebuilt on the local store. The math is
``data/return_distribution.py`` (H1, D84). This page only chooses its
inputs and formats its result; every number on it comes from one
``ReturnDistribution``, so the chart, the latest move and the tables always
describe the same sample.

Data: stored daily bars only, read through ``services.workstation`` as the
Dashboard reads them. So the page renders the same with IBKR connected or
not, and it says "stored daily closes · not live". Returns use the Prices
basis chosen (D38), adjusted by default since this is return math. A series
stored only on the other basis falls back to it, and the data line says so.
Split-sized gaps (D57) are named on that line too, and kept in the sample,
as D84 records.

Threading: the series list and each analysis are TaskRunner jobs. Settings
can change faster than a job finishes, so each analysis carries a token,
and a result whose token was superseded is dropped.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass

from PySide6.QtWidgets import QStackedWidget

from quant_hub.data.return_distribution import (
    Frequency,
    ReturnDistribution,
    ReturnKind,
    Window,
    analyze,
)
from quant_hub.data.types import PriceBasis, PriceHistory
from quant_hub.tasks import TaskRunner
from quant_hub.gui.charts import Curve, CurveChart, Histogram, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card,
    CardGrid,
    ElidedLabel,
    EmptyState,
    Field,
    MenuButton,
    MetricTile,
    Note,
    PageHeader,
    SegmentedControl,
    SelectField,
    StatTable,
    button,
    form_grid,
    metric_row,
)
from quant_hub.gui.pages.base import PRICE_BASES, ScrollPage, basis_field, history_note, saved_grid
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
# The series summary is the shape WorkstationService takes and returns; the
# GUI may not import the bar store itself (tests/test_layering.py).
from quant_hub.services.workstation import StoredSeriesSummary, WorkstationService
from quant_hub.storage.state_store import ViewStateStore

_logger = logging.getLogger(__name__)

_STATE_KEY = "returns"
_DAILY = "1 day"
# The panel's inner width, less its 1 px border each side and a little slack,
# so its cards stay within SIDE_PANEL_WIDTH and the grid keeps them beside the
# chart (a card wider than its column is moved below).
_INNER = SIDE_PANEL_WIDTH - 2 * SPACE_L - 6
_FULL = _INNER                                  # a field across the panel
_HALF = (_INNER - SPACE_M) // 2                 # two fields per row

FREQUENCIES = {"Daily": Frequency.DAY, "Weekly": Frequency.WEEK, "Monthly": Frequency.MONTH}
WINDOWS = [w.value for w in Window]
KINDS = {"Simple": ReturnKind.SIMPLE, "Log": ReturnKind.LOG}
BINS = ["Auto", "20", "30", "50", "75", "100"]
ALPHAS = {"10%": 0.10, "5%": 0.05, "2%": 0.02, "1%": 0.01}
VIEWS = ("Histogram", "Q-Q")
_PERIOD = {Frequency.DAY: "day", Frequency.WEEK: "week", Frequency.MONTH: "month"}
_ADJECTIVE = {Frequency.DAY: "daily", Frequency.WEEK: "weekly", Frequency.MONTH: "monthly"}

_DISTRIBUTION = (
    ("Moments", (("mean", "Mean"), ("median", "Median"), ("sd", "Sigma"),
                      ("vol", "Annualized vol"), ("skew", "Skewness"),
                      ("kurt", "Excess kurtosis"))),
    ("Extremes", (("best", "Best"), ("worst", "Worst"), ("updown", "Up / down"))),
    ("Normality", (("ad", "Anderson–Darling A*²"), ("jb", "Jarque–Bera"),
                   ("verdict", "Normal at the test level"), ("tested", "Returns tested"))),
)
_TAILS = (
    ("Beyond", (("t1", "±1σ"), ("t2", "±2σ"), ("t3", "±3σ"), ("t4", "±4σ"))),
    ("Quantiles", (("q0.01", "1st percentile"), ("q0.05", "5th percentile"),
                   ("q0.95", "95th percentile"), ("q0.99", "99th percentile"))),
)

# Marker styles in the histogram's legend (``Histogram`` markers).
_MEAN = Series("Mean", (), PALETTE.text_secondary, 1.2, dashed=True, fmt="{:+.2%}")
_LATEST = Series("Latest", (), PALETTE.series_light, 1.8, fmt="{:+.2%}")


def _empty_text() -> tuple[str, str]:
    return ("No stored history yet",
            "Import a CSV of daily bars on the Dashboard, or connect IBKR and use "
            "“Sync 1Y history” on the Markets page.")


def p_text(p: float, bound: bool) -> str:
    """A p-value for a table: a bound as "< …", small ones in e-notation."""
    if bound:
        return f"< {p:.2g}"
    return f"{p:.3f}" if p >= 0.001 else f"{p:.1e}"


@dataclass(frozen=True)
class _Run:
    """One analysis and what it was computed from."""

    history: PriceHistory
    summary: StoredSeriesSummary
    distribution: ReturnDistribution
    basis_note: str        # "" or why the basis differs from the one chosen


class ReturnHistogramPage(ScrollPage):
    def __init__(self, workstation: WorkstationService, tasks: TaskRunner,
                 state_store: ViewStateStore) -> None:
        super().__init__()
        self._workstation = workstation
        self._tasks = tasks
        self._state_store = state_store
        saved = state_store.load(_STATE_KEY)
        self._saved = saved
        # Series by picker key: the symbol, or "SPY · file" when the store
        # holds it from more than one source. Each maps basis -> summary.
        self._series: dict[str, dict[PriceBasis, StoredSeriesSummary]] = {}
        self._listed = False
        self._run: _Run | None = None
        self._token = 0
        self._view = saved.get("view") if saved.get("view") in VIEWS else VIEWS[0]
        self._log_y = bool(saved.get("log_y", True))

        self._header = PageHeader(
            "Return histogram",
            "HRH: a stored security's returns against a normal, and where the latest "
            "move sits")
        self.body.addWidget(self._header)

        grid = CardGrid()
        grid.add_column([("chart", self._build_chart(), 14),
                         ("latest", self._build_latest(), None),
                         ("series", self._build_series(), 8)], x=0, w=9)
        grid.add_column([("inputs", self._build_inputs(saved), None),
                         ("distribution", self._build_distribution(), None),
                         ("tails", self._build_tails(), None)],
                        x=9, w=3, min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------- building
    def _build_chart(self) -> Card:
        self._chart_card = Card("Return distribution", "")
        self._views = SegmentedControl(list(VIEWS), self._view)
        self._views.changed.connect(self._on_view)
        self._chart_card.add_action(self._views)
        self._log_button = button("Log y", "segment")
        self._log_button.setCheckable(True)
        self._log_button.setChecked(self._log_y)
        self._log_button.toggled.connect(self._on_log_y)
        self._chart_card.add_action(self._log_button)

        self._chart_stack = QStackedWidget()
        self._histogram = Histogram(axis="percent", bin_fmt="{:+.2%}")
        self._qq = CurveChart(x_axis="percent", y_axis="percent", x_name="Normal quantile",
                              x_fmt="{:+.2%}", band_fmt="{:+.2%}")
        self._chart_empty = EmptyState("mdi6.chart-histogram", "No distribution yet",
                                       "Pick a stored series on the right.")
        for widget in (self._histogram, self._qq, self._chart_empty):
            self._chart_stack.addWidget(widget)
        self._chart_stack.setCurrentWidget(self._chart_empty)
        self._chart_stack.setMinimumHeight(320)
        self._chart_card.body.addWidget(self._chart_stack, 1)
        return self._chart_card

    def _build_latest(self) -> Card:
        card = Card("Latest move", "The return ending at the last stored bar, ranked "
                                   "against the window it isn't part of")
        self._latest_value = MetricTile("Latest move")
        self._latest_z = MetricTile("Z-score", "against the window's mean and sigma")
        self._latest_count = MetricTile("At least this far")
        self._latest_normal = MetricTile("Normal expects")
        card.body.addLayout(metric_row([self._latest_value, self._latest_z,
                                        self._latest_count, self._latest_normal]))
        return card

    def _build_series(self) -> Card:
        self._series_card = Card("Returns over the window", "Volatility clusters: calm and "
                                                            "wild stretches pool into fat tails")
        self._series_chart = StackedTimeSeries()
        self._series_chart.setMinimumHeight(150)
        self._series_card.body.addWidget(self._series_chart, 1)
        return self._series_card

    def _build_inputs(self, saved: dict) -> Card:
        card = Card("Inputs", "Stored daily closes · not live")
        card.add_action(MenuButton([("Refresh series", self.refresh)]))
        self._series_field = SelectField("Series", [], None, _FULL)
        self._basis = basis_field(saved, _HALF)
        self._window = SelectField("Window", WINDOWS, saved.get("window", "1Y"), _HALF)
        self._frequency = SelectField("Returns of", list(FREQUENCIES),
                                      saved.get("frequency", "Daily"), _HALF)
        self._horizon = Field("Periods per return", saved.get("horizon", "1"), _HALF)
        self._kind = SelectField("Kind", list(KINDS), saved.get("kind", "Simple"), _HALF)
        self._bins = SelectField("Bins", BINS, saved.get("bins", "Auto"), _HALF)
        self._alpha = SelectField("Test level", list(ALPHAS), saved.get("alpha", "5%"), _HALF)
        card.body.addWidget(self._series_field)
        card.body.addLayout(form_grid([self._basis, self._window, self._frequency,
                                       self._horizon, self._kind, self._bins, self._alpha], 2))
        for field in (self._series_field, self._basis, self._window, self._frequency,
                      self._kind, self._bins, self._alpha):
            field.select.current_changed.connect(lambda _v: self._analyze())
        self._horizon.committed.connect(self._analyze)
        return card

    def _build_distribution(self) -> Card:
        card = Card("Distribution")
        self._stats = StatTable(_DISTRIBUTION, value_header="")
        card.body.addWidget(self._stats)
        self._tests_note = Note("ph.info", "info", width=SIDE_PANEL_WIDTH - 2 * SPACE_L)
        self._tests_note.setVisible(False)
        card.body.addWidget(self._tests_note)
        return card

    def _build_tails(self) -> Card:
        card = Card("Tails and quantiles", "Observed against the fitted normal")
        self._tails = StatTable(_TAILS, value_header="OBSERVED", truth_header="NORMAL")
        self._tails.show_truth(True)
        card.body.addWidget(self._tails)
        return card

    # ---------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if not self._listed:
            self.refresh()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "series": self._series_field.current(), "basis": self._basis.current(),
            "window": self._window.current(), "frequency": self._frequency.current(),
            "horizon": self._horizon.text(), "kind": self._kind.current(),
            "bins": self._bins.current(), "alpha": self._alpha.current(),
            "view": self._view, "log_y": self._log_y,
        })

    def refresh(self) -> None:
        """Re-read the store's list of daily series, then analyze."""
        self._tasks.submit(lambda: self._workstation.stored_series(_DAILY),
                           on_done=self._on_series, on_error=self._on_error)

    def select_series(self, key: str) -> None:
        """Public: analyze ``key``, a picker key or a plain symbol."""
        wanted = key if key in self._series else next(
            (k for k, by_basis in self._series.items()
             if next(iter(by_basis.values())).symbol == key), None)
        if wanted is not None:
            self._series_field.select.set_current(wanted)

    # -------------------------------------------------------------- series
    def _on_series(self, summaries: list[StoredSeriesSummary]) -> None:
        self._listed = True
        counts = Counter(symbol for symbol, _ in {(s.symbol, s.source) for s in summaries})
        series: dict[str, dict[PriceBasis, StoredSeriesSummary]] = {}
        for s in sorted(summaries, key=lambda s: (s.symbol, s.source)):
            key = s.symbol if counts[s.symbol] == 1 else f"{s.symbol} · {s.source}"
            series.setdefault(key, {})[s.basis] = s
        self._series = series
        keys = list(series)
        current = self._series_field.current() or self._saved.get("series")
        self._series_field.select.set_items(keys, current if current in series else None)
        if not keys:
            self._show_empty(*_empty_text())
            return
        self._analyze()

    def _summary(self) -> tuple[StoredSeriesSummary, str]:
        """The chosen series on the chosen basis, or on the one stored."""
        by_basis = self._series[self._series_field.current()]
        wanted = PRICE_BASES[self._basis.current()]
        if wanted in by_basis:
            return by_basis[wanted], ""
        other = next(iter(by_basis.values()))
        return other, f" · {wanted.value} prices not stored, so {other.basis.value}"

    # ------------------------------------------------------------- analyze
    def _analyze(self) -> None:
        if not self._series or self._series_field.current() not in self._series:
            return
        try:
            horizon = self._horizon.number(integer=True, minimum=1)
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        summary, basis_note = self._summary()
        frequency = FREQUENCIES[self._frequency.current()]
        settings = dict(frequency=frequency, horizon=horizon,
                        kind=KINDS[self._kind.current()], window=Window(self._window.current()),
                        alpha=ALPHAS[self._alpha.current()],
                        bins=None if self._bins.current() == "Auto" else int(self._bins.current()))
        self._token += 1
        token = self._token
        self._header.set_status(f"Analyzing {summary.symbol}…")

        def work() -> _Run:
            history = self._workstation.history(summary, "ALL")
            distribution = analyze(history.closes, gap_days=[g.day for g in history.split_gaps],
                                   **settings)
            return _Run(history, summary, distribution, basis_note)

        self._tasks.submit(work, on_done=lambda run: self._on_done(token, run),
                           on_error=lambda exc: self._on_failed(token, exc))

    def _on_done(self, token: int, run: _Run) -> None:
        if token != self._token:
            return   # superseded by a newer choice
        self._run = run
        d = run.distribution
        period = _PERIOD[d.frequency]
        span = f"{d.horizon}-{period}" if d.horizon > 1 else _ADJECTIVE[d.frequency]
        self._header.set_status(
            f"{d.n:,} {span} {d.kind.value} returns · {d.start:%b %d, %Y} – {d.end:%b %d, %Y}")
        self._chart_card.set_subtitle(
            f"{run.summary.symbol} · {run.summary.source.upper()} · "
            f"{run.summary.basis.value} · stored daily closes, not live"
            f"{run.basis_note}{history_note(run.history)}")
        self._render_chart()
        self._render_latest(d)
        self._render_tables(d)
        self._render_series(d)

    def _on_failed(self, token: int, exc: Exception) -> None:
        if token != self._token:
            return
        self._show_empty("Couldn't compute the distribution", str(exc))
        self._header.set_status(str(exc), error=True)
        _logger.error("return histogram failed: %s", exc)

    def _on_error(self, exc: Exception) -> None:
        self._show_empty("Couldn't read the local store", str(exc))
        self._header.set_status(str(exc), error=True)

    def _show_empty(self, title: str, body: str) -> None:
        self._run = None
        self._chart_empty.set_text(title, body)
        self._chart_stack.setCurrentWidget(self._chart_empty)
        self._stats.clear()
        self._tails.clear()
        self._tests_note.setVisible(False)
        self._series_chart.clear()
        for tile in (self._latest_value, self._latest_z, self._latest_count,
                     self._latest_normal):
            tile.set_value("—", hint="")

    # -------------------------------------------------------------- render
    def _on_view(self, view: str) -> None:
        self._view = view
        self._render_chart()

    def _on_log_y(self, checked: bool) -> None:
        self._log_y = checked
        self._render_chart()

    def _render_chart(self) -> None:
        self._log_button.setEnabled(self._view == "Histogram")
        if self._run is None:
            return
        d = self._run.distribution
        if self._view == "Q-Q":
            qq = d.qq
            if qq.n == 0:
                self._chart_empty.set_text("No Q-Q view", "Too few returns to plot.")
                self._chart_stack.setCurrentWidget(self._chart_empty)
                return
            sample = (f"{qq.n:,} non-overlapping returns" if d.horizon > 1 else
                      f"{qq.n:,} returns")
            self._qq.set_curves([
                Curve("Normal", qq.theoretical, qq.theoretical, PALETTE.text_secondary, 1.2,
                      dashed=True, fmt="{:+.2%}"),
                Curve(f"Observed ({sample})", qq.theoretical, qq.observed, PALETTE.series,
                      fmt="{:+.2%}", marker="o", line=False),
            ], band=(qq.theoretical, qq.lower, qq.upper, "95% band"))
            self._chart_stack.setCurrentWidget(self._qq)
            return
        latest = d.latest.value
        low = min(float(d.bin_edges[0]), latest)
        high = max(float(d.bin_edges[-1]), latest)
        self._histogram.set_counts(
            d.bin_edges, d.bin_counts, expected=d.bin_expected, expected_name="Normal fit",
            markers=[(_MEAN, d.mean), (_LATEST, latest)], log_y=self._log_y, signed=True,
            x_range=(low, high))
        self._chart_stack.setCurrentWidget(self._histogram)

    def _render_latest(self, d: ReturnDistribution) -> None:
        m = d.latest
        to_date = f" · {_PERIOD[d.frequency]} to date" if m.partial else ""
        gap = " · spans a split-sized gap" if m.spans_gap else ""
        self._latest_value.set_value(
            f"{m.value:+.2%}", "critical" if m.value < 0 else "good",
            hint=f"{m.start:%b %d} → {m.end:%b %d, %Y}{to_date}{gap}")
        self._latest_z.set_value(f"{m.z:+.2f}σ")
        side = "down" if m.down else "up"
        self._latest_count.set_value(
            f"{m.count:,} of {d.n:,}",
            hint=("beyond every return in the window" if m.count == 0 else
                  f"{m.rank:.1%} of the window this far {side}"))
        self._latest_normal.set_value(f"{m.normal_expected:,.1f}",
                                      hint=f"of {d.n:,}, if returns were normal")

    def _render_tables(self, d: ReturnDistribution) -> None:
        s = self._stats
        s.set_value("mean", f"{d.mean:+.3%}")
        s.set_value("median", f"{d.median:+.3%}")
        s.set_value("sd", f"{d.sd:.3%}")
        s.set_value("vol", f"{d.annualized_vol:.2%}")
        s.set_value("skew", f"{d.skewness:+.3f}")
        s.set_value("kurt", f"{d.excess_kurtosis:+.3f}")
        s.set_value("best", f"{d.best[1]:+.2%} · {d.best[0]:%b %d, %Y}")
        s.set_value("worst", f"{d.worst[1]:+.2%} · {d.worst[0]:%b %d, %Y}")
        s.set_value("updown", f"{d.up:,} / {d.down:,}" + (f" · {d.unchanged:,} flat"
                                                           if d.unchanged else ""))
        tested = f"{d.test_n:,}" + (" non-overlapping" if d.horizon > 1 else "")
        s.set_value("tested", tested)
        if d.tests:
            ad, jb = d.tests
            s.set_value("ad", f"{ad.statistic:.3f} · p {p_text(ad.p_value, ad.p_is_bound)}")
            s.set_value("jb", f"{jb.statistic:,.1f} · p {p_text(jb.p_value, jb.p_is_bound)}")
            s.set_value("verdict", f"{'No' if ad.rejects else 'Yes'} (A-D) · "
                                   f"{'No' if jb.rejects else 'Yes'} (JB)")
            self._tests_note.setVisible(False)
        else:
            for key in ("ad", "jb", "verdict"):
                s.set_value(key, "—")
            self._tests_note.set_text(f"No normality test: {d.tests_skipped}.")
            self._tests_note.setVisible(True)
        t = self._tails
        for row in d.tails:
            t.set_value(f"t{row.sds}", f"{row.observed:,} · {row.observed_share:.1%}",
                        truth=f"{row.normal_expected:,.1f} · {row.normal_share:.2%}")
        for row in d.quantiles:
            t.set_value(f"q{row.level}", f"{row.observed:+.2%}", truth=f"{row.normal:+.2%}")

    def _render_series(self, d: ReturnDistribution) -> None:
        r = d.returns
        span = (f"{d.horizon}-{_PERIOD[d.frequency]}" if d.horizon > 1 else
                _ADJECTIVE[d.frequency].capitalize())
        self._series_chart.set_panels(r.index, [Panel(
            f"{span} {d.kind.value} returns, by end date",
            [Series("Return", r.to_numpy(dtype=float), PALETTE.series, 1.0, fmt="{:+.2%}")],
            axis="percent", zero_line=True,
            references=[("±2σ", d.mean + 2 * d.sd), ("", d.mean - 2 * d.sd)])])
