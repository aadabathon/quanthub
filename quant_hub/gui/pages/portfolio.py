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
  its standard error, which is the point;
* regressions (W2, ``models/regression.py``): each holding's and the
  portfolio's α and β on the benchmark, and up to three extra factor ETFs,
  with Newey–West errors (optionally the Dimson sum beta with one lag), a
  rolling β with its band for the
  selected row, and the residual links between holdings, strongest first:
  are the streams orthogonal?

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
from quant_hub.gui.charts import Band, Histogram, Panel, Series, StackedTimeSeries
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
    label,
)
from quant_hub.gui.pages.base import ScrollPage
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models.regression import Z95
from quant_hub.services.market_service import MarketService
from quant_hub.services.portfolio import (
    PORTFOLIO_ROW,
    PortfolioService,
    PortfolioView,
    Regressions,
    example_snapshot,
)
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.tasks import TaskRunner

_logger = logging.getLogger(__name__)

_STATE_KEY = "portfolio"
_IMPORTED, _EXAMPLE = "Imported", "Example"
_ALL_ACCOUNTS = "All accounts"
_LAGS = {"Off": 0, "1 day": 1}          # the Dimson lag of the benchmark
_LINK_ROWS = 8                          # residual links shown before the table scrolls
_MAX_FACTORS = 3                        # extra factor columns the table fits at 1280 px
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_ROW = 32


def _money(value: float) -> str:
    return f"−${-value:,.0f}" if value < 0 else f"${value:,.0f}"


def _estimate(value: float, error: float, fmt: str = "{:+.2f}") -> str:
    """``value ± error``, with values that round to zero shown unsigned
    rather than as −0.00."""
    shown = fmt.format(value)
    if float(fmt.format(abs(value)).strip("+%")) == 0:
        shown = fmt.replace("+", "").format(0.0)
    return f"{shown} ± {fmt.replace('+', '').format(error)}"


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
        self._regressions: Regressions | None = None
        self._regression_keys: list[str] = []
        self._regression_columns: list[tuple[str, str]] = []
        self._exposure_columns: list[tuple[str, str]] = []
        self._rolling_key = PORTFOLIO_ROW

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
        column.addWidget(self._build_regressions())
        column.addWidget(self._build_rolling())
        column.addWidget(self._build_links())

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

    def _build_regressions(self) -> Card:
        self._regress_card = Card("Regressions on the benchmark", "")
        self._regress_table = DataTable([
            ("Holding", "left"), ("α a year", "right"), ("β", "right"), ("R²", "right"),
            ("Residual vol", "right")], row_height=_ROW)
        self._regress_table.balance_columns()
        self._regress_table.row_activated.connect(self._on_regression_row)
        self._regress_card.body.addWidget(self._regress_table)
        # Extra factors' betas and the lag's: a second table, so neither one
        # outgrows the 1280 px window.
        self._exposure_caption = label("Other exposures, ± Newey–West errors", "fieldCaption")
        self._exposure_table = DataTable([("Holding", "left")], row_height=_ROW)
        self._exposure_table.balance_columns()
        self._exposure_table.row_activated.connect(self._on_regression_row)
        self._regress_card.body.addWidget(self._exposure_caption)
        self._regress_card.body.addWidget(self._exposure_table)
        self._exposure_caption.setVisible(False)
        self._exposure_table.setVisible(False)
        return self._regress_card

    def _build_rolling(self) -> Card:
        self._rolling_card = Card("Rolling β", "")
        self._rolling_stack = QStackedWidget()
        self._rolling = StackedTimeSeries()
        self._rolling_empty = EmptyState("ph.chart-line", "No rolling β yet")
        self._rolling_stack.addWidget(self._rolling)
        self._rolling_stack.addWidget(self._rolling_empty)
        self._rolling_stack.setCurrentWidget(self._rolling_empty)
        self._rolling_stack.setMinimumHeight(240)
        self._rolling_card.body.addWidget(self._rolling_stack, 1)
        return self._rolling_card

    def _build_links(self) -> Card:
        self._links_card = Card("Residual links, strongest first", "")
        self._links = DataTable([("Pair", "left"), ("Residual ρ", "right"),
                                 ("95% range", "right"), ("Raw ρ", "right")], row_height=_ROW)
        self._links.balance_columns()
        self._links_card.body.addWidget(self._links)
        return self._links_card

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
        lags = saved.get("lags", "Off")
        self._lags = SelectField("Lagged β", list(_LAGS), lags if lags in _LAGS else "Off", _HALF)
        self._lags.select.current_changed.connect(lambda _l: self._analyse())
        self._factors = Field("Extra factors", saved.get("factors", ""), _HALF,
                              placeholder="e.g. QQQ TLT", uppercase=True)
        card.body.addLayout(form_grid([self._source, self._benchmark, self._lookback,
                                       self._rate, self._lags, self._factors], 2))
        for field in (self._benchmark, self._lookback, self._rate, self._factors):
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
            "lags": self._lags.current(), "factors": self._factors.text(),
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
            factors = tuple(dict.fromkeys(                   # pasted text isn't uppercased
                f for f in self._factors.text().upper().replace(",", " ").split()
                if f != benchmark))
            if len(factors) > _MAX_FACTORS:
                raise self._factors.fail(f"Extra factors: at most {_MAX_FACTORS}")
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        source = self._source.current()
        mode = self._mode
        account = None if self._accounts.current() == _ALL_ACCOUNTS else self._accounts.current()
        lags = _LAGS[self._lags.current()]
        portfolio, market = self._portfolio, self._market

        def work() -> tuple[PortfolioView, bool]:
            fallback = False
            summaries = portfolio.snapshots() if mode == _IMPORTED else []
            if summaries:
                snapshot = portfolio.load(summaries[0].id)
            else:
                snapshot, fallback = example_snapshot(market, source), mode == _IMPORTED
            chosen = account if account in snapshot.accounts else None
            return portfolio.analyse(snapshot, chosen, source, benchmark, lookback, rate,
                                     dimson_lags=lags, factors=factors), fallback

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
        self._render_regressions(view)

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

    # ---------------------------------------------------------- regressions
    def _render_regressions(self, view: PortfolioView) -> None:
        reg = self._regressions = view.regressions
        if reg is None:
            self._regression_keys = []
            self._regress_table.set_rows([])
            self._regress_table.setFixedHeight(36)
            self._exposure_caption.setVisible(False)
            self._exposure_table.setVisible(False)
            self._links.set_rows([])
            self._links.setFixedHeight(36)
            for card in (self._regress_card, self._rolling_card, self._links_card):
                card.set_subtitle(view.regression_note)
            self._rolling_empty.set_text("No rolling β", view.regression_note)
            self._rolling_stack.setCurrentWidget(self._rolling_empty)
            return
        self._regression_keys = list(reg.fits)
        columns = [("Holding", "left"), ("α a year", "right"), (f"β {reg.benchmark}", "right"),
                   ("R²", "right"), ("Residual vol", "right")]
        if columns != self._regression_columns:
            self._regress_table.set_columns(columns)
            self._regression_columns = columns
        exposures = [("Holding", "left"), *((f"β {f}", "right") for f in reg.factors),
                     *((("Lag β", "right"),) if reg.dimson_lags else ())]
        if exposures != self._exposure_columns:
            self._exposure_table.set_columns(exposures)
            self._exposure_columns = exposures
        rows, others = [], []
        for key in self._regression_keys:
            fit = reg.fits[key]
            alpha, alpha_se = fit.alpha_annual, fit.alpha_error_annual()
            beta, beta_se = reg.beta(key)
            lag = reg.lag_beta(key)
            noise = abs(alpha) <= Z95 * alpha_se
            name = (key, PALETTE.text if key == PORTFOLIO_ROW else PALETTE.text_secondary)
            rows.append([
                name,
                (_estimate(alpha, alpha_se, "{:+.1%}"), PALETTE.muted if noise else PALETTE.text),
                _estimate(beta, beta_se),
                ("—", PALETTE.muted) if not math.isfinite(fit.r2) else f"{fit.r2:.0%}",
                f"{fit.residual_vol_annual:.1%}",
            ])
            others.append([
                name,
                *(_estimate(*reg.factor_beta(key, f)) for f in reg.factors),
                *(() if lag is None else ((
                    _estimate(*lag),
                    PALETTE.warning if abs(lag[0]) > Z95 * lag[1] else PALETTE.muted),)),
            ])
        self._regress_table.set_rows(rows)
        self._regress_table.setFixedHeight(36 + len(rows) * _ROW)
        has_others = len(exposures) > 1
        self._exposure_caption.setVisible(has_others)
        self._exposure_table.setVisible(has_others)
        if has_others:
            self._exposure_table.set_rows(others)
            self._exposure_table.setFixedHeight(36 + len(others) * _ROW)
        any_fit = reg.fits[PORTFOLIO_ROW]
        on = " + ".join((reg.benchmark, *reg.factors))
        self._regress_card.set_subtitle(
            f"Daily excess returns on {on} · {any_fit.observations:,} days · "
            f"Newey–West errors ({any_fit.lags} lags) · grey α: within 1.96 errors of zero"
            + (" · each β holds the other factors fixed" if reg.factors else "")
            + (" · β is the Dimson sum, today's and yesterday's; amber lag β below: the "
               "price trails the market" if reg.dimson_lags else "")
            + " · click a row to chart its rolling β"
            + (f" · {view.regression_note}" if view.regression_note else ""))
        if self._rolling_key not in reg.fits:
            self._rolling_key = PORTFOLIO_ROW
        self._render_rolling()
        self._render_links(view)

    def _on_regression_row(self, row: int) -> None:
        if self._regressions is not None and 0 <= row < len(self._regression_keys):
            self._rolling_key = self._regression_keys[row]
            self._render_rolling()

    def _render_rolling(self) -> None:
        reg = self._regressions
        frame = reg.rolling.get(self._rolling_key) if reg is not None else None
        if frame is None or frame.empty:
            self._rolling_empty.set_text(
                "No rolling β", f"A rolling β needs {reg.window} days of returns; this "
                "lookback has fewer." if reg is not None else "")
            self._rolling_stack.setCurrentWidget(self._rolling_empty)
            return
        beta, se = frame["beta"].to_numpy(), frame["se"].to_numpy()
        panel = Panel(f"β to {reg.benchmark}",
                      [Series("β", beta, PALETTE.series, 1.8, fmt="{:.2f}")], axis="price",
                      zero_line=True,
                      bands=[Band("95%", beta - Z95 * se, beta + Z95 * se, PALETTE.series,
                                  fmt="{:.2f}")])
        self._rolling.set_panels(frame.index, [panel])
        self._rolling_stack.setCurrentWidget(self._rolling)
        self._rolling_card.set_subtitle(
            f"{self._rolling_key} · each point uses the {reg.window} days ending that day, "
            "so only what was known then · band ±1.96 Newey–West errors · hover for values")

    def _render_links(self, view: PortfolioView) -> None:
        reg, report = view.regressions, view.report
        symbols = list(reg.correlation.columns)
        sd = np.sqrt(np.diag(report.covariance))
        raw = report.covariance / np.outer(sd, sd)
        pairs = []
        for i in range(len(symbols)):
            for j in range(i + 1, len(symbols)):
                rho = float(reg.correlation.iat[i, j])
                pairs.append((symbols[i], symbols[j], rho, float(reg.lower.iat[i, j]),
                              float(reg.upper.iat[i, j]), float(raw[i, j])))
        pairs.sort(key=lambda p: -abs(p[2]) if math.isfinite(p[2]) else 0.0)
        rows = []
        for a, b, rho, low, high, plain in pairs:
            if not math.isfinite(rho):
                rows.append([f"{a} · {b}", ("—", PALETTE.muted), ("—", PALETTE.muted),
                             f"{plain:+.2f}"])
                continue
            linked = low > 0 or high < 0
            rows.append([f"{a} · {b}", (f"{rho:+.2f}", PALETTE.warning if linked else PALETTE.text),
                         f"{low:+.2f} to {high:+.2f}", f"{plain:+.2f}"])
        self._links.set_rows(rows)
        self._links.setFixedHeight(36 + max(min(len(rows), _LINK_ROWS), 1) * _ROW)
        self._links_card.set_subtitle(
            "Correlation of what's left after the factors: amber where its 95% range excludes "
            "zero, so the two aren't orthogonal (Fisher z, approximate when volatility "
            "clusters) · raw ρ before the benchmark is taken out"
            if rows else "One holding in the model: no pairs to compare.")
