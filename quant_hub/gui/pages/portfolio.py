"""Portfolio: what today's holdings could lose, and where the risk sits (P3).

Risk first, for a stock and ETF portfolio that uses options sparingly:

* six headline numbers: equity, volatility, one-day VaR (95%) and expected
  shortfall (99%), beta, and the worst drawdown today's mix would have had;
* the holdings, each with its share of the *value* and of the *risk*
  (``models/portfolio.py``, risk contributions), sorted by risk: the
  holdings whose risk share outruns their weight are where the risk is;
* today's mix replayed over the lookback against the benchmark, with its
  drawdown;
* the distribution of one-day P&L from that replay, with the VaR marks
  and the Gaussian curve of the same vol, so fat tails show;
* sizing: the mix's mean return and vol drag, and the Kelly leverage with
  its standard error, which is the point.

Holdings come from the latest imported positions file (a broker's CSV:
Fidelity, or the generic layout; ``services/portfolio.py``), or a built-in
example portfolio before anything is imported. Prices come from a named
source; returns are adjusted (D38). One TaskRunner job computes everything,
so every number describes the same picture.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from quant_hub.data.types import PriceBasis
from quant_hub.gui.charts import Histogram, Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card,
    DataTable,
    EmptyState,
    Field,
    MetricTile,
    Note,
    PageHeader,
    SegmentedControl,
    SelectField,
    StatTable,
    button,
    form_grid,
)
from quant_hub.gui.pages.base import ScrollPage
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import PortfolioService, PortfolioView, example_snapshot
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.tasks import TaskRunner

_logger = logging.getLogger(__name__)

_STATE_KEY = "portfolio"
_IMPORTED, _EXAMPLE = "Imported", "Example"
_ALL_ACCOUNTS = "All accounts"
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_ROW = 32


def _money(value: float) -> str:
    return f"−${-value:,.0f}" if value < 0 else f"${value:,.0f}"


class PortfolioPage(ScrollPage):
    def __init__(self, portfolio: PortfolioService, market_service: MarketService,
                 tasks: TaskRunner, state_store: ViewStateStore) -> None:
        super().__init__()
        self._portfolio = portfolio
        self._market = market_service
        self._tasks = tasks
        self._state_store = state_store
        self._view: PortfolioView | None = None
        saved = state_store.load(_STATE_KEY)
        self._mode = _EXAMPLE if saved.get("mode") == _EXAMPLE else _IMPORTED
        self._account = saved.get("account") or _ALL_ACCOUNTS
        self._import_dir = saved.get("import_dir") or str(Path.home())
        self._keep_status = False       # an import's message outlives its analysis

        self._header = PageHeader(
            "Portfolio", "What today's holdings could lose, and where the risk sits")
        self.body.addWidget(self._header)

        main = QHBoxLayout()
        main.setSpacing(SPACE_L)
        self.body.addLayout(main, 1)
        column = QVBoxLayout()
        column.setSpacing(SPACE_L)
        main.addLayout(column, 1)
        column.addLayout(self._build_tiles())
        column.addWidget(self._build_holdings())
        column.addWidget(self._build_replay(), 1)
        column.addWidget(self._build_losses())

        side = QVBoxLayout()
        side.setSpacing(SPACE_L)
        side.setContentsMargins(0, 0, 0, 0)
        side_widget = QWidget()
        side_widget.setFixedWidth(SIDE_PANEL_WIDTH)
        side_widget.setLayout(side)
        main.addWidget(side_widget)
        side.addWidget(self._build_inputs(saved))
        self._warning = Note("ph.warning", tone="critical")
        self._finding = Note("ph.info", tone="info")
        for note in (self._warning, self._finding):
            note.setVisible(False)
            side.addWidget(note)
        side.addWidget(self._build_sizing())
        side.addStretch(1)

    # ------------------------------------------------------------- layout
    def _build_tiles(self) -> QGridLayout:
        """Two rows of three: six in a row would force the page wider than
        1280 and squeeze every number."""
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_M)
        grid.setVerticalSpacing(SPACE_M)
        self._tiles = {
            "equity": MetricTile("Equity"), "vol": MetricTile("Volatility · annual"),
            "var": MetricTile("1-day VaR · 95%"), "es": MetricTile("Expected shortfall · 99%"),
            "beta": MetricTile("Beta"), "drawdown": MetricTile("Worst drawdown"),
        }
        for i, tile in enumerate(self._tiles.values()):
            grid.addWidget(tile, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        return grid

    def _build_holdings(self) -> Card:
        self._holdings_card = Card("Holdings by risk", "")
        self._table = DataTable([("Holding", "left"), ("Value", "right"), ("Options Δ", "right"),
                                 ("Weight", "right"), ("Risk share", "right"),
                                 ("Vol", "right"), ("Beta", "right")], row_height=_ROW)
        self._table.balance_columns()
        self._holdings_card.body.addWidget(self._table)
        return self._holdings_card

    def _build_replay(self) -> Card:
        self._replay_card = Card("Today's mix, replayed", "")
        self._replay_stack = QStackedWidget()
        self._replay = StackedTimeSeries(readout_per_panel=True)
        self._replay_empty = EmptyState("ph.chart-pie-slice", "No portfolio yet",
                                        "Import a positions CSV, or look at the example.")
        self._replay_stack.addWidget(self._replay)
        self._replay_stack.addWidget(self._replay_empty)
        self._replay_stack.setCurrentWidget(self._replay_empty)
        self._replay_stack.setMinimumHeight(340)
        self._replay_card.body.addWidget(self._replay_stack, 1)
        return self._replay_card

    def _build_losses(self) -> Card:
        self._losses_card = Card("One-day P&L", "")
        self._losses = Histogram(axis="money", bin_fmt="{:+,.0f}")
        self._losses.setMinimumHeight(200)
        self._losses_card.body.addWidget(self._losses)
        return self._losses_card

    def _build_inputs(self, saved: dict) -> Card:
        card = Card("Holdings")
        self._modes = SegmentedControl([_IMPORTED, _EXAMPLE], self._mode, fill=True)
        self._modes.changed.connect(self._on_mode)
        card.body.addWidget(self._modes)
        self._accounts = SelectField("Account", [_ALL_ACCOUNTS], _ALL_ACCOUNTS, _FULL)
        self._accounts.select.current_changed.connect(lambda _a: self._analyse())
        card.body.addWidget(self._accounts)
        names = self._market.source_names
        default = saved.get("source", "ibkr")
        self._source = SelectField("Prices from", names,
                                   default if default in names else names[0], _HALF)
        self._benchmark = Field("Benchmark", saved.get("benchmark", "SPY"), _HALF, uppercase=True)
        self._lookback = Field("Lookback (calendar days)", saved.get("lookback", "730"), _HALF)
        self._rate = Field("Rate %", saved.get("rate", "4.0"), _HALF)
        card.body.addLayout(form_grid([self._source, self._benchmark, self._lookback,
                                       self._rate], 2))
        for field in (self._benchmark, self._lookback, self._rate):
            field.committed.connect(self._analyse)
        buttons = QHBoxLayout()
        buttons.setSpacing(SPACE_M)
        self._import_button = button("Import positions…", icon_name="ph.upload-simple",
                                     on_click=self._choose_file)
        self._analyse_button = button("Analyse", "primary", "ph.lightning",
                                      on_click=self._analyse)
        buttons.addWidget(self._import_button, 1)
        buttons.addWidget(self._analyse_button, 1)
        card.body.addLayout(buttons)
        return card

    def _build_sizing(self) -> Card:
        card = Card("Sizing", "Kelly from the replayed mean: read the error first")
        self._sizing = StatTable([
            ("The mix", (("mean", "Mean return  annual"), ("mix_vol", "Volatility  annual"),
                         ("drag", "Volatility drag  σ²/2"),
                         ("growth_now", "Growth at today's size"))),
            ("Kelly", (("exposure", "Today's net exposure"), ("kelly", "Kelly leverage f*"),
                       ("kelly_error", "± its standard error"), ("half", "Half Kelly"))),
            ("Spread", (("n_eff", "Effective holdings"), ("top", "Largest holding"))),
        ], value_header="")
        card.body.addWidget(self._sizing)
        return card

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        if self._view is None:
            self._analyse()

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "mode": self._mode, "account": self._account, "source": self._source.current(),
            "benchmark": self._benchmark.text(), "lookback": self._lookback.text(),
            "rate": self._rate.text(), "import_dir": self._import_dir,
        })

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._analyse()

    # -------------------------------------------------------------- import
    def _choose_file(self) -> None:
        paths, _filter = QFileDialog.getOpenFileNames(
            self, "Import positions: one export, or one per account from the same day",
            self._import_dir, "CSV files (*.csv);;All files (*)")
        if paths:
            self._import_dir = str(Path(paths[0]).parent)
            self.import_files([Path(p) for p in paths])

    def import_file(self, path: Path) -> None:
        """Import a positions CSV, then analyse it."""
        self.import_files([path])

    def import_files(self, paths: list[Path]) -> None:
        """Import positions CSVs as one snapshot (one account each, same day),
        then analyse it."""
        self._header.set_status(f"Importing {' + '.join(p.name for p in paths)}…")
        self._tasks.submit(lambda: self._portfolio.import_positions(paths),
                           on_done=self._on_imported, on_error=self._on_error)

    def _on_imported(self, result) -> None:
        snapshot_id, snapshot = result
        accounts = len(snapshot.accounts)
        self._header.set_status(
            f"Imported {snapshot.name}: {len(snapshot.positions)} positions"
            + (f" in {accounts} accounts" if accounts > 1 else "")
            + f", as of {snapshot.as_of:%b %d, %Y} ({snapshot.source} layout, #{snapshot_id})")
        self._mode = _IMPORTED
        self._modes.set_current(_IMPORTED)
        self._account = _ALL_ACCOUNTS
        self._keep_status = True
        self._analyse()

    # ------------------------------------------------------------- analyse
    def _analyse(self) -> None:
        try:
            lookback = self._lookback.number(integer=True, minimum=120)
            rate = self._rate.number(scale=0.01)
            benchmark = self._benchmark.required_text()
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        source = self._source.current()
        mode = self._mode
        account = None if self._accounts.current() == _ALL_ACCOUNTS else self._accounts.current()
        portfolio, market = self._portfolio, self._market

        def work() -> tuple[PortfolioView, bool]:
            fallback = False
            summaries = portfolio.snapshots() if mode == _IMPORTED else []
            if summaries:
                snapshot = portfolio.load(summaries[0].id)
            else:
                snapshot, fallback = example_snapshot(market, source), mode == _IMPORTED
            chosen = account if account in snapshot.accounts else None
            return portfolio.analyse(snapshot, chosen, source, benchmark, lookback, rate), fallback

        self._analyse_button.setEnabled(False)
        if not self._keep_status:
            self._header.set_status("Analysing…")
        self._tasks.submit(work, on_done=self._on_done, on_error=self._on_error)

    def _on_done(self, result: tuple[PortfolioView, bool]) -> None:
        view, fallback = result
        self._view = view
        self._analyse_button.setEnabled(True)
        snapshot = view.snapshot
        accounts = [_ALL_ACCOUNTS, *snapshot.accounts]
        self._accounts.select.blockSignals(True)
        self._accounts.select.set_items(accounts, view.account or _ALL_ACCOUNTS)
        self._accounts.select.blockSignals(False)
        self._account = view.account or _ALL_ACCOUNTS
        if self._keep_status:
            self._keep_status = False
        else:
            self._header.set_status(
                f"{snapshot.name} · as of {snapshot.as_of:%b %d, %Y} · "
                f"{len(view.rows)} holdings in the model")
        self._render_tiles(view)
        self._render_table(view)
        self._render_replay(view)
        self._render_losses(view)
        self._render_sizing(view)
        self._render_notes(view, fallback)

    def _on_error(self, exc: Exception) -> None:
        self._analyse_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)
        _logger.error("portfolio: %s", exc)

    # -------------------------------------------------------------- render
    def _render_tiles(self, view: PortfolioView) -> None:
        r = view.report
        e = view.equity
        self._tiles["equity"].set_value(
            _money(e), hint=f"{len(view.rows)} holdings · cash {view.cash / e:.0%}")
        self._tiles["vol"].set_value(f"{r.vol_annual:.1%}",
                                     hint=f"{_money(r.vol * e)} on a 1σ day")
        self._tiles["var"].set_value(
            _money(r.var_historical[0.95]), "critical",
            hint=f"historical · Gaussian {_money(r.var_gaussian[0.95])}")
        self._tiles["es"].set_value(
            _money(r.es_historical[0.99]), "critical",
            hint=f"historical · Gaussian {_money(r.es_gaussian[0.99])}")
        if r.beta is None:
            self._tiles["beta"].set_value("—", hint=f"no {view.benchmark} history")
        else:
            self._tiles["beta"].set_value(
                f"{r.beta:.2f}", hint=f"{view.benchmark} −1% ≈ {_money(-0.01 * r.beta * e)}")
        self._tiles["drawdown"].set_value(f"{r.drawdown.min():.1%}", "critical",
                                          hint=f"today's mix over {r.years:.1f} years")

    def _render_table(self, view: PortfolioView) -> None:
        rows = []
        for row in sorted(view.rows, key=lambda h: -h.risk_share):
            heavy = row.risk_share > 1.5 * abs(row.weight) and row.risk_share > 0.05
            rows.append([
                row.symbol, _money(row.value),
                _money(row.option_delta) if row.option_delta else ("—", PALETTE.muted),
                f"{row.weight:.1%}",
                (f"{row.risk_share:.1%}", PALETTE.warning if heavy else PALETTE.text),
                f"{row.vol:.1%}",
                "—" if row.beta is None else f"{row.beta:.2f}",
            ])
        if view.cash:
            rows.append([("Cash", PALETTE.text_secondary), _money(view.cash),
                         ("—", PALETTE.muted),
                         f"{view.cash / view.equity:.1%}", ("0.0%", PALETTE.muted),
                         ("—", PALETTE.muted), ("—", PALETTE.muted)])
        self._table.set_rows(rows)
        self._table.setFixedHeight(36 + len(rows) * _ROW)
        self._holdings_card.set_subtitle(
            "Each holding's share of the value and of the risk (risk contributions). "
            "Amber: risk well above weight. Options count by their delta.")

    def _render_replay(self, view: PortfolioView) -> None:
        r = view.report
        series = [Series("Today's mix", r.path.to_numpy(), PALETTE.series, 1.8, fmt="{:.3f}")]
        if r.benchmark_path is not None:
            series.insert(0, Series(view.benchmark, r.benchmark_path.to_numpy(),
                                    PALETTE.text_secondary, 1.2, fmt="{:.3f}"))
        panels = [
            Panel("Value of $1 held in today's mix", series, axis="price", stretch=3,
                  references=[("start", 1.0)]),
            Panel("Drawdown", [Series("Drawdown", r.drawdown.to_numpy(), PALETTE.critical, 1.2,
                                      fmt="{:.1%}", fill=True)], axis="percent", stretch=1),
        ]
        self._replay.set_panels(r.path.index, panels)
        self._replay_stack.setCurrentWidget(self._replay)
        basis = "adjusted (total return)" if view.basis is PriceBasis.ADJUSTED else "traded"
        self._replay_card.set_subtitle(
            f"Today's weights held over {r.years:.1f} years of {view.source} {basis} prices, "
            "not the account's own history · hover for values")

    def _render_losses(self, view: PortfolioView) -> None:
        r = view.report
        sd = r.vol * view.equity
        markers = [(Series("VaR 95%", np.array([]), PALETTE.warning, fmt="{:+,.0f}"),
                    -r.var_historical[0.95]),
                   (Series("VaR 99%", np.array([]), PALETTE.critical, fmt="{:+,.0f}"),
                    -r.var_historical[0.99])]
        self._losses.set_data(r.pnl.to_numpy(), normal=(0.0, sd),
                              normal_name="Gaussian, same vol", markers=markers)
        self._losses_card.set_subtitle(
            f"{len(r.pnl):,} days replayed on today's holdings (options repriced in full) · "
            "tails beyond the curve are fat tails")

    def _render_sizing(self, view: PortfolioView) -> None:
        r = view.report
        s = self._sizing
        if not math.isfinite(r.kelly):
            for key in ("mean", "mix_vol", "drag", "growth_now", "exposure", "kelly",
                        "kelly_error", "half"):
                s.set_value(key, "—")
        else:
            sigma = r.mix_vol
            s.set_value("mean", f"{r.mean:+.1%}")
            s.set_value("mix_vol", f"{sigma:.1%}")
            s.set_value("drag", f"{0.5 * sigma**2:.1%}")
            s.set_value("growth_now", f"{r.growth(r.net_exposure):+.1%}")
            s.set_value("exposure", f"{r.net_exposure:.2f}×")
            s.set_value("kelly", f"{r.kelly:+.1f}×")
            s.set_value("kelly_error", f"±{r.kelly_error:.1f}×")
            s.set_value("half", f"{r.kelly / 2:+.1f}×")
        s.set_value("n_eff", f"{r.n_effective:.1f}")
        top = max(view.rows, key=lambda h: abs(h.weight)) if view.rows else None
        s.set_value("top", f"{top.symbol} {top.weight:.0%}" if top else "—")

    def _render_notes(self, view: PortfolioView, fallback: bool) -> None:
        warnings = list(view.left_out)
        if view.unmodelled > 0:
            warnings.append(f"{view.unmodelled:.1%} of equity is outside the risk model.")
        self._warning.set_text(" ".join(warnings))
        self._warning.setVisible(bool(warnings))
        findings = []
        if fallback:
            findings.append("No positions imported yet: this is the example portfolio. Import "
                            "a broker's positions CSV (Fidelity, or symbol,quantity,price).")
        elif view.snapshot.source == "Example":
            findings.append("The example portfolio: $100,000 in a plain stock and ETF mix.")
        if view.basis is PriceBasis.TRADED:
            findings.append("Some prices are traded, not adjusted, so dividends show as drops.")
        if view.options:
            findings.append(f"{view.options} option position{'s' if view.options != 1 else ''}: "
                            "counted by delta in the vol, beta and drawdown, repriced in full "
                            "in the one-day P&L.")
        self._finding.set_text(" ".join(findings))
        self._finding.setVisible(bool(findings))
