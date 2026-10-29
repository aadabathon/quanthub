"""Valuation: a discounted cash flow from SEC filings (W6, D48).

The owner's DCF modeler, inside the app:

* **Company:** a ticker or CIK. Its filings come from the SEC (W5, D47) and
  are kept in the store; Refresh refetches. Every gap is named with its
  reason, never filled.
* **Assumptions** seed from the company's own history (trailing CAGR and
  averages: the original screener's base case) and stay editable: one
  number for every year, or one per year ("12, 10, 8, 6, 5").
* **Conventions** (D46): the terminal flow (the original's last-year, or
  steady-state), value as of the fiscal year end or today, and netting
  marketable securities.
* **The result:** value per share, the EV bridge (the terminal share
  flagged above 75%), the projection, the WACC × terminal-growth grid, and
  what the price implies: every root of the reverse DCF, and when there can
  be only one.
* **Price** from a named source (its quote, else its last close) or typed,
  for the margin of safety.
* **Screen:** the holdings or a typed list, each seeded from its own
  filings, valued, ranked cheapest first; failures stay as rows; CSV.
* **Saved:** immutable records. Reopening says whether the filings have
  changed since.

Fetches (SEC, prices, the screen, saves) run on the TaskRunner. The DCF is
pure and recalculates as you type (debounced; pages may call pure models,
D26).
"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QFileDialog, QHBoxLayout

from quant_hub.data.fundamentals import FinancialHistory
from quant_hub.gui.charts import Heatmap
from quant_hub.gui.components import (
    CardGrid,
    Card,
    DataTable,
    Field,
    MetricTile,
    Note,
    PageHeader,
    SelectField,
    StatTable,
    button,
    form_grid,
    label,
)
from quant_hub.gui.pages.base import ScrollPage, debounce, saved_grid
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models.dcf import (
    LAST_YEAR,
    STEADY_STATE,
    Assumptions,
    BatchConfig,
    ValuationError,
    ValuationResult,
    assumptions_from_history,
    calibration,
    cash_flows_positive,
    growth_monotone,
    implied_growth,
    implied_wacc,
    market_comparison,
    sensitivity,
    value,
)
from quant_hub.services.portfolio import PortfolioService
from quant_hub.services.valuation import Price, ScreenRow, ValuationService, refusal, valuation_years
from quant_hub.storage.state_store import ViewStateStore
from quant_hub.tasks import TaskRunner

_logger = logging.getLogger(__name__)

_STATE_KEY = "valuation"
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_ROW = 32
_TERMINAL = {"Last year (original)": LAST_YEAR, "Steady state": STEADY_STATE}
_AS_OF = ("Fiscal year end", "Today")
_SECURITIES = ("Not netted", "Netted")
_TYPED = "typed"
_WACC_STEPS = (-0.02, -0.01, 0.0, 0.01, 0.02)       # the original CLI's grid around WACC
_GROWTH_STEPS = (-0.01, -0.005, 0.0, 0.005, 0.01)   # and around terminal growth
_TERMINAL_WARN = 0.75                               # the plan's flag: most value past the horizon
_PATHS = (("growth", "Revenue growth %", "revenue_growth"),
          ("margin", "EBIT margin %", "ebit_margin"),
          ("tax", "Tax rate %", "tax_rate"),
          ("da", "D&A % of revenue", "da_pct_revenue"),
          ("capex", "Capex % of revenue", "capex_pct_revenue"),
          ("nwc", "Working capital % of Δrev", "nwc_pct_revenue"))
_DEFAULTS = {"growth": "5", "margin": "20", "tax": "21", "da": "4", "capex": "5", "nwc": "10",
             "years": "5", "wacc": "9", "terminal": "2.5"}


def _money(value: float | None, currency: str = "USD") -> str:
    """Compact money: $391.0B, −$1.2M, EUR 12.3K."""
    if value is None or not math.isfinite(value):
        return "—"
    prefix = "$" if currency == "USD" else f"{currency} "
    sign = "−" if value < 0 else ""
    v = abs(value)
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if v >= size:
            return f"{sign}{prefix}{v / size:,.1f}{suffix}"
    return f"{sign}{prefix}{v:,.0f}"


def _per_share(value: float, currency: str = "USD") -> str:
    if not math.isfinite(value):
        return "—"
    prefix = "$" if currency == "USD" else f"{currency} "
    return f"−{prefix}{-value:,.2f}" if value < 0 else f"{prefix}{value:,.2f}"


def _pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None or not math.isfinite(value) else f"{value:.{digits}%}"


def _ratio(num: float | None, den: float | None) -> float | None:
    return num / den if num is not None and den else None


class ValuationPage(ScrollPage):
    def __init__(self, valuation: ValuationService, portfolio: PortfolioService,
                 tasks: TaskRunner, state_store: ViewStateStore) -> None:
        super().__init__()
        self._valuation = valuation
        self._portfolio = portfolio
        self._tasks = tasks
        self._state_store = state_store
        self._history: FinancialHistory | None = None
        self._result: ValuationResult | None = None
        self._assumed: Assumptions | None = None
        self._price_basis = _TYPED
        self._price_source = ""
        self._screen_rows: list[ScreenRow] = []
        self._saved_ids: list[int] = []
        self._token = 0
        saved = state_store.load(_STATE_KEY)
        self._recompute_soon = debounce(self, self._recompute)

        self._header = PageHeader("Valuation", "A discounted cash flow from the company's SEC filings")
        self.body.addWidget(self._header)
        grid = CardGrid()
        self._tiles = {"value": MetricTile("Value per share"), "price": MetricTile("Price"),
                       "margin": MetricTile("Margin of safety"),
                       "terminal": MetricTile("Terminal share of EV")}
        for column, (name, tile) in enumerate(self._tiles.items()):
            grid.add(f"{name} tile", tile, 2 * column, 0, 2)
        grid.add_column([("filings", self._build_filings(), None),
                         ("result", self._build_result(), None),
                         ("sensitivity", self._build_sensitivity(), None),
                         ("implied", self._build_implied(), None),
                         ("screen", self._build_screen(saved), None),
                         ("saved", self._build_saved(), None)], x=0, w=8, y=3)
        grid.add_column([("company", self._build_company(saved), None),
                         ("assumptions", self._build_assumptions(saved), None),
                         ("price", self._build_price(saved), None)],
                        x=8, w=4, min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, f"{_STATE_KEY}.cards")
        self.body.addWidget(grid, 1)
        self._clear_result("Load a company by ticker or CIK.")

    # ------------------------------------------------------------- layout
    def _build_company(self, saved: dict) -> Card:
        card = Card("Company")
        self._identifier = Field("Ticker or CIK", saved.get("identifier", ""), _FULL,
                                 placeholder="e.g. AAPL", uppercase=True)
        self._identifier.committed.connect(lambda: self._load(False))
        card.body.addWidget(self._identifier)
        row = QHBoxLayout()
        row.setSpacing(SPACE_M)
        row.addWidget(button("Load", "primary", "ph.magnifying-glass",
                             on_click=lambda: self._load(False)), 1)
        row.addWidget(button("Refresh", icon_name="ph.arrows-clockwise",
                             on_click=lambda: self._load(True)), 1)
        card.body.addLayout(row)
        self._identity_note = Note("ph.info", tone="info")
        self._identity_note.set_text(
            'Fetching filings needs your SEC identity: add [sec] identity = "Your Name '
            'you@example.com" to settings.toml and restart. Stored filings work without it.')
        card.body.addWidget(self._identity_note)
        self._identity_note.setVisible(not self._valuation.has_identity)
        # Provenance and gaps sit here, at the panel's fixed width: wrapped
        # text in the main column grows past what the page's minimum counts
        # (ScrollPage opts out of height-for-width) and squeezes the cards
        # below it.
        self._missing = Note("ph.warning", tone="critical")
        self._sources = Note("ph.info", tone="info")
        for note in (self._missing, self._sources):
            card.body.addWidget(note)
            note.setVisible(False)
        return card

    def _build_assumptions(self, saved: dict) -> Card:
        card = Card("Assumptions", "One number for every year, or one per year")
        values = saved.get("assumptions", {})
        self._fields: dict[str, Field] = {}
        for key, caption, _name in _PATHS:
            self._fields[key] = Field(caption, values.get(key, _DEFAULTS[key]), _HALF)
        self._fields["years"] = Field("Years", values.get("years", _DEFAULTS["years"]), _HALF)
        self._fields["wacc"] = Field("WACC %", values.get("wacc", _DEFAULTS["wacc"]), _HALF)
        self._fields["terminal"] = Field("Terminal growth %",
                                         values.get("terminal", _DEFAULTS["terminal"]), _HALF)
        for key, caption in (("net_debt", "Net debt (override)"), ("shares", "Shares (override)"),
                             ("base_revenue", "Base revenue (override)")):
            self._fields[key] = Field(caption, values.get(key, ""), _HALF, placeholder="from filing")
        terminal = saved.get("terminal_method", next(iter(_TERMINAL)))
        self._terminal_method = SelectField("Terminal flow", list(_TERMINAL),
                                            terminal if terminal in _TERMINAL else
                                            next(iter(_TERMINAL)), _HALF)
        as_of = saved.get("as_of", _AS_OF[0])
        self._as_of = SelectField("Value as of", list(_AS_OF),
                                  as_of if as_of in _AS_OF else _AS_OF[0], _HALF)
        securities = saved.get("securities", _SECURITIES[0])
        self._securities = SelectField("Marketable securities", list(_SECURITIES),
                                       securities if securities in _SECURITIES else _SECURITIES[0],
                                       _HALF)
        order = [self._fields[k] for k in ("growth", "margin", "tax", "da", "capex", "nwc",
                                           "years", "wacc", "terminal")]
        card.body.addLayout(form_grid([*order, self._terminal_method, self._as_of,
                                       self._securities, self._fields["net_debt"],
                                       self._fields["shares"], self._fields["base_revenue"]], 2))
        for f in self._fields.values():
            f.edit.textEdited.connect(lambda _t: self._recompute_soon())
            f.committed.connect(self._recompute)
        for select in (self._terminal_method, self._as_of, self._securities):
            select.select.current_changed.connect(lambda _v: self._recompute())
        card.body.addWidget(button("Seed from history", icon_name="ph.arrows-clockwise",
                                   on_click=self._seed))
        return card

    def _build_price(self, saved: dict) -> Card:
        card = Card("Price", "For the margin of safety and the reverse DCF")
        sources = self._valuation.price_sources
        source = saved.get("price_source", "ibkr")
        self._source = SelectField("Price from", sources, source if source in sources
                                   else sources[0], _HALF)
        self._price = Field("Price", "", _HALF, placeholder="fetch or type")
        self._price.edit.textEdited.connect(self._on_price_typed)
        self._price.committed.connect(self._recompute)
        card.body.addLayout(form_grid([self._source, self._price], 2))
        self._price_caption = label("", "muted", wrap=True)
        card.body.addWidget(self._price_caption)
        row = QHBoxLayout()
        row.setSpacing(SPACE_M)
        row.addWidget(button("Get price", icon_name="ph.currency-circle-dollar",
                             on_click=self._get_price), 1)
        self._save_button = button("Save valuation", icon_name="ph.floppy-disk",
                                   on_click=self._save)
        row.addWidget(self._save_button, 1)
        card.body.addLayout(row)
        return card

    def _build_filings(self) -> Card:
        self._filings_card = Card("Filings", "")
        self._filings = DataTable([("Fiscal year", "left"), ("Revenue", "right"),
                                   ("EBIT margin", "right"), ("D&A %", "right"),
                                   ("Capex %", "right"), ("Tax rate", "right")], row_height=_ROW)
        self._filings.balance_columns()
        self._filings_card.body.addWidget(self._filings)
        return self._filings_card

    def _build_result(self) -> Card:
        self._result_card = Card("Valuation", "")
        self._bridge = StatTable([
            ("Enterprise value", (("pv_explicit", "PV of the projection"),
                                  ("pv_terminal", "PV of the terminal value"),
                                  ("ev", "Enterprise value"))),
            ("Equity", (("net_debt", "Less net debt"), ("equity", "Equity value"),
                        ("shares", "Diluted shares"), ("vps", "Value per share"))),
        ], value_header="")
        self._result_card.body.addWidget(self._bridge)
        self._projection = DataTable([("Year", "left"), ("Revenue", "right"), ("EBIT", "right"),
                                      ("D&A", "right"), ("Capex", "right"), ("ΔNWC", "right"),
                                      ("UFCF", "right"), ("PV", "right")], row_height=_ROW)
        self._projection.balance_columns()
        self._result_card.body.addWidget(self._projection)
        self._refused = Note("ph.warning", tone="critical")
        self._result_card.body.addWidget(self._refused)
        self._refused.setVisible(False)
        return self._result_card

    def _build_sensitivity(self) -> Card:
        self._sensitivity_card = Card("Sensitivity: WACC × terminal growth", "")
        # Growth in percent units, so the numeric axis reads 2.5 rather than 0.025.
        self._heatmap = Heatmap(x_name="terminal growth", x_fmt="{:.1f}%", value_fmt="${:,.2f}",
                                value_axis="money", value_name="value per share")
        self._heatmap.setMinimumHeight(220)
        self._sensitivity_card.body.addWidget(self._heatmap)
        return self._sensitivity_card

    def _build_implied(self) -> Card:
        self._implied_card = Card("What the price implies", "")
        self._implied = DataTable([("Holding the rest fixed", "left"), ("The price implies", "right"),
                                   ("You assumed", "right"), ("Unique?", "left")],
                                  row_height=_ROW)
        self._implied.balance_columns()
        self._implied.setFixedHeight(36 + 2 * _ROW)
        self._implied_card.body.addWidget(self._implied)
        return self._implied_card

    def _build_screen(self, saved: dict) -> Card:
        self._screen_card = Card("Screen", "Each company seeded from its own filings, with this "
                                 "page's WACC, terminal growth, working capital and conventions; "
                                 "ranked cheapest first")
        # Field and buttons on two rows: on one, they set the page's minimum
        # width past a 1280 px window.
        self._screen_list = Field("Tickers", saved.get("screen", ""), _FULL,
                                  placeholder="e.g. AAPL MSFT NVDA", uppercase=True)
        self._screen_card.body.addWidget(self._screen_list)
        row = QHBoxLayout()
        row.setSpacing(SPACE_M)
        row.addWidget(button("Holdings", icon_name="ph.briefcase", on_click=self._fill_holdings))
        self._screen_button = button("Run screen", "primary", "ph.magnifying-glass",
                                     on_click=self._run_screen)
        row.addWidget(self._screen_button)
        row.addWidget(button("Export CSV…", icon_name="ph.download-simple",
                             on_click=self._export_screen))
        row.addStretch(1)
        self._screen_card.body.addLayout(row)
        self._screen = DataTable([("Ticker", "left"), ("Price", "right"), ("Value", "right"),
                                  ("Margin", "right"), ("Implied growth", "right"),
                                  ("Terminal", "right"), ("Note", "left")], row_height=_ROW)
        self._screen.balance_columns()
        self._screen_card.body.addWidget(self._screen)
        self._screen.setFixedHeight(36)
        return self._screen_card

    def _build_saved(self) -> Card:
        self._saved_card = Card("Saved valuations", "Immutable records; click one to reopen it "
                                "against today's filings")
        self._saved = DataTable([("Saved", "left"), ("Ticker", "left"), ("Value", "right"),
                                 ("Price", "right"), ("Margin", "right")], row_height=_ROW)
        self._saved.balance_columns()
        self._saved.row_activated.connect(self._reopen)
        self._saved_card.body.addWidget(self._saved)
        self._saved_note = Note("ph.info", tone="info")
        self._saved_card.body.addWidget(self._saved_note)
        self._saved_note.setVisible(False)
        return self._saved_card

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        self._refresh_saved()
        if self._history is None and self._identifier.text():
            self._load(False)

    def on_hide(self) -> None:
        self._state_store.save(_STATE_KEY, {
            "identifier": self._identifier.text(),
            "assumptions": {k: f.text() for k, f in self._fields.items()},
            "terminal_method": self._terminal_method.current(), "as_of": self._as_of.current(),
            "securities": self._securities.current(), "price_source": self._source.current(),
            "screen": self._screen_list.text(),
        })

    # ------------------------------------------------------------- company
    def _load(self, refresh: bool) -> None:
        try:
            identifier = self._identifier.required_text()
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._token += 1
        token = self._token
        self._header.set_status(f"{'Refreshing' if refresh else 'Loading'} {identifier}…")
        self._tasks.submit(lambda: self._valuation.company(identifier, refresh),
                           on_done=lambda hist: self._on_company(token, hist, seed=True),
                           on_error=self._on_error)

    def _on_company(self, token: int, hist: FinancialHistory, seed: bool) -> None:
        if token != self._token:
            return                                   # superseded by a later load
        self._history = hist
        self._header.set_status(f"{hist.company or hist.cik} · {len(hist.years)} fiscal years · "
                                f"{hist.sources.get('data', '')}")
        self._render_filings(hist)
        if seed:
            self._seed()
        else:
            self._recompute()

    def _seed(self) -> None:
        """Growth, margin, tax, D&A and capex from the company's own averages;
        WACC, terminal growth, years and working capital stay yours."""
        if self._history is None:
            return
        try:
            years = int(self._fields["years"].number(integer=True, minimum=1))
        except ValueError:
            years = int(_DEFAULTS["years"])
        seeded = assumptions_from_history(self._history, BatchConfig(projection_years=years))
        for key, value_ in (("growth", seeded.revenue_growth), ("margin", seeded.ebit_margin),
                            ("tax", seeded.tax_rate), ("da", seeded.da_pct_revenue),
                            ("capex", seeded.capex_pct_revenue)):
            self._fields[key].set_text(f"{100 * float(value_):.1f}")
        self._recompute()

    # --------------------------------------------------------- assumptions
    def _path(self, key: str, years: int) -> float | list[float]:
        field = self._fields[key]
        parts = field.text().replace(",", " ").split()
        caption = dict((k, c) for k, c, _n in _PATHS)[key]
        if not parts:
            raise field.fail(f"{caption} is required")
        try:
            values = [float(p) / 100 for p in parts]
        except ValueError:
            raise field.fail(f"{caption}: one number, or one per year") from None
        if len(values) == 1:
            return values[0]
        if len(values) != years:
            raise field.fail(f"{caption}: {len(values)} values for {years} years")
        return values

    def _assumptions(self, hist: FinancialHistory) -> tuple[Assumptions, str]:
        years = int(self._fields["years"].number(integer=True, minimum=1))
        overrides = {name: self._fields[key].number(allow_blank=True)
                     for key, name in (("net_debt", "net_debt_override"),
                                       ("shares", "shares_override"),
                                       ("base_revenue", "base_revenue_override"))}
        s, note = (valuation_years(hist, date.today()) if self._as_of.current() == "Today"
                   else (0.0, ""))
        return Assumptions(
            projection_years=years,
            **{name: self._path(key, years) for key, _caption, name in _PATHS},
            wacc=self._fields["wacc"].number(scale=0.01),
            terminal_growth=self._fields["terminal"].number(scale=0.01),
            terminal_method=_TERMINAL[self._terminal_method.current()],
            valuation_years=s,
            net_marketable_securities=self._securities.current() == "Netted",
            **overrides), note

    # ------------------------------------------------------------- results
    def _recompute(self) -> None:
        hist = self._history
        if hist is None:
            return
        try:
            a, note = self._assumptions(hist)
            result = value(hist, a)
        except ValuationError as exc:
            self._clear_result("The DCF refuses: " + refusal(exc) + ". Override the missing "
                               "input, or pick another company.")
            return
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        self._result, self._assumed = result, a
        self._refused.setVisible(False)
        currency = hist.currency
        price = self._price_value()
        self._render_tiles(result, price, currency)
        self._render_bridge(result, currency)
        self._render_projection(result, currency)
        self._render_sensitivity(hist, a)
        self._render_implied(hist, a, result, price)
        latest = hist.latest()
        year_end = latest.period_end if latest else "latest"
        dated = (f"as of {a.valuation_years:.2f} years after the {year_end} year end"
                 if a.valuation_years else f"as of the {year_end} year end")
        self._result_card.set_subtitle(
            f"{_TERMINAL_LABEL[a.terminal_method]} terminal flow · valued {dated}"
            + (f" · {note}" if note else "")
            + (" · marketable securities netted" if a.net_marketable_securities else ""))

    def _clear_result(self, message: str) -> None:
        self._result = self._assumed = None
        for tile in self._tiles.values():
            tile.set_value("—", hint="")
        self._bridge.clear()
        self._projection.set_rows([])
        self._projection.setFixedHeight(36)
        self._refused.set_text(message)
        self._refused.setVisible(self._history is not None)
        self._implied.set_rows([])
        for card in (self._result_card, self._sensitivity_card, self._implied_card):
            card.set_subtitle(message if self._history is None else "")

    def _price_value(self) -> float | None:
        text = self._price.text()
        if not text:
            return None
        try:
            price = float(text.replace("$", "").replace(",", ""))
        except ValueError:
            return None
        return price if price > 0 else None

    def _render_tiles(self, r: ValuationResult, price: float | None, currency: str) -> None:
        self._tiles["value"].set_value(_per_share(r.value_per_share, currency),
                                       hint=f"equity {_money(r.equity_value, currency)}")
        if price is None:
            self._tiles["price"].set_value("—", hint="fetch or type a price")
            self._tiles["margin"].set_value("—", hint="needs a price")
        else:
            mc = market_comparison(r, price)
            self._tiles["price"].set_value(_per_share(price, currency), hint=self._price_basis)
            self._tiles["margin"].set_value(
                _pct(mc.margin_of_safety), "good" if mc.margin_of_safety > 0 else "critical",
                hint=f"upside {mc.upside:+.1%}")
        share = r.terminal_share
        self._tiles["terminal"].set_value(
            _pct(share), "critical" if share > _TERMINAL_WARN else "",
            hint="most of the value is past the horizon" if share > _TERMINAL_WARN
            else "PV of the terminal value / EV")

    def _render_bridge(self, r: ValuationResult, currency: str) -> None:
        b = self._bridge
        b.set_value("pv_explicit", _money(r.pv_explicit, currency))
        b.set_value("pv_terminal", f"{_money(r.pv_terminal, currency)} · {_pct(r.terminal_share)}")
        b.set_value("ev", _money(r.enterprise_value, currency))
        b.set_value("net_debt", _money(-r.net_debt, currency))
        b.set_value("equity", _money(r.equity_value, currency))
        b.set_value("shares", f"{r.shares / 1e6:,.1f}M" if r.shares >= 1e6 else f"{r.shares:,.0f}")
        b.set_value("vps", _per_share(r.value_per_share, currency),
                    tone="good" if r.value_per_share > 0 else "critical")

    def _render_projection(self, r: ValuationResult, currency: str) -> None:
        rows = [[str(y.year), _money(y.revenue, currency), _money(y.ebit, currency),
                 _money(y.da, currency), _money(y.capex, currency), _money(y.delta_nwc, currency),
                 _money(y.ufcf, currency), _money(y.pv, currency)] for y in r.projection]
        rows.append([("Terminal", PALETTE.text_secondary), ("—", PALETTE.muted),
                     ("—", PALETTE.muted), ("—", PALETTE.muted), ("—", PALETTE.muted),
                     ("—", PALETTE.muted), _money(r.terminal_flow, currency),
                     _money(r.pv_terminal, currency)])
        self._projection.set_rows(rows)
        self._projection.setFixedHeight(36 + len(rows) * _ROW)

    def _render_sensitivity(self, hist: FinancialHistory, a: Assumptions) -> None:
        waccs = [a.wacc + d for d in _WACC_STEPS]
        growths = [a.terminal_growth + d for d in _GROWTH_STEPS]
        table = sensitivity(hist, a, waccs, growths)
        if np.isnan(table.grid).all():
            self._sensitivity_card.set_subtitle("Every cell has terminal growth at or above WACC.")
            return
        self._heatmap.set_grid(100 * np.array(growths), [f"WACC {w:.1%}" for w in waccs],
                               table.grid)
        self._sensitivity_card.set_subtitle(
            "Value per share across ±2 points of WACC (rows) and ±1 point of terminal growth "
            "(across, %); blank where growth reaches WACC · hover a cell")

    def _render_implied(self, hist: FinancialHistory, a: Assumptions, r: ValuationResult,
                        price: float | None) -> None:
        if price is None:
            self._implied.set_rows([])
            self._implied_card.set_subtitle("Fetch or type a price to see what it implies.")
            return
        growths = implied_growth(hist, a, price)
        waccs = implied_wacc(hist, a, price)
        assumed = (f"{a.revenue_growth:.1%}" if isinstance(a.revenue_growth, float)
                   else "a path")
        muted = PALETTE.muted
        self._implied.set_rows([
            ["Flat revenue growth",
             " or ".join(f"{g:+.1%}" for g in growths) if growths
             else ("none in −20% to 60%", muted), assumed,
             ("yes: margin ≥ working capital", muted) if growth_monotone(a)
             else ("maybe not: working capital > margin", PALETTE.warning)],
            ["WACC",
             " or ".join(f"{w:.2%}" for w in waccs) if waccs else ("none in 4% to 30%", muted),
             f"{a.wacc:.2%}",
             ("yes: every flow positive", muted) if cash_flows_positive(r)
             else ("maybe not: a negative flow", PALETTE.warning)],
        ])
        self._implied_card.set_subtitle(
            "Two growth rates give this price: working capital outruns the cash margin, so value "
            "rises then falls with growth" if len(growths) > 1 else
            "The reverse DCF: every root in range, and whether there can be only one")

    def _render_filings(self, hist: FinancialHistory) -> None:
        currency = hist.currency
        rows = []
        for y in hist.ordered():
            rows.append([f"{y.fiscal_year} · {y.period_end}", _money(y.revenue, currency),
                         _pct(_ratio(y.operating_income, y.revenue)),
                         _pct(_ratio(y.depreciation_amortization, y.revenue)),
                         _pct(_ratio(y.capex, y.revenue)), _pct(y.effective_tax_rate())])
        c = calibration(hist)
        rows.append([("Trailing", PALETTE.text_secondary),
                     (f"CAGR {_pct(c.revenue_cagr)}", PALETTE.text_secondary),
                     (_pct(c.ebit_margin), PALETTE.text_secondary),
                     (_pct(c.da_pct_revenue), PALETTE.text_secondary),
                     (_pct(c.capex_pct_revenue), PALETTE.text_secondary),
                     (_pct(c.tax_rate), PALETTE.text_secondary)])
        self._filings.set_rows(rows)
        self._filings.setFixedHeight(36 + len(rows) * _ROW)
        self._filings_card.set_subtitle(
            f"{hist.company or ''} · CIK {hist.cik} · {currency} · annual 10-K facts, latest "
            "filing per year (restatements win) · Trailing seeds the assumptions")
        missing = [f"{k}: {v}" for k, v in sorted(hist.missing.items())]
        self._missing.set_text("Missing from the filings: " + "; ".join(missing))
        self._missing.setVisible(bool(missing))
        wanted = [(k, hist.sources[k]) for k in ("revenue", "total_debt", "shares_diluted")
                  if k in hist.sources]
        self._sources.set_text("Sources: " + "; ".join(f"{k} from {v}" for k, v in wanted))
        self._sources.setVisible(bool(wanted))

    # --------------------------------------------------------------- price
    def _on_price_typed(self, _text: str) -> None:
        self._price_basis = _TYPED
        self._price_caption.setText("Typed by hand.")
        self._recompute_soon()

    def _get_price(self) -> None:
        symbol = (self._history.ticker if self._history else "") or self._identifier.text()
        source = self._source.current()
        self._header.set_status(f"Pricing {symbol} from {source}…")
        self._tasks.submit(lambda: self._valuation.price(symbol, source),
                           on_done=self._on_price, on_error=self._on_error)

    def _on_price(self, price: Price) -> None:
        self._price.set_text(f"{price.value:.2f}")
        self._price_basis = price.basis
        self._price_caption.setText(f"{price.source}: {price.basis}, {price.as_of:%Y-%m-%d %H:%M}")
        self._header.set_status(f"Priced from {price.source} ({price.basis}).")
        self._recompute()

    # --------------------------------------------------------------- screen
    def _fill_holdings(self) -> None:
        portfolio = self._portfolio

        def work() -> list[str]:
            summaries = portfolio.snapshots()
            if not summaries:
                return []
            snapshot = portfolio.load(summaries[0].id)
            return sorted({p.symbol for p in snapshot.positions if p.kind.value == "equity"})

        def done(symbols: list[str]) -> None:
            if not symbols:
                self._header.set_status("No imported positions: import them on the Portfolio "
                                        "page, or type tickers.", error=True)
                return
            self._screen_list.set_text(" ".join(symbols))
            self._header.set_status(f"{len(symbols)} holdings from the latest import.")
        self._tasks.submit(work, on_done=done, on_error=self._on_error)

    def _run_screen(self) -> None:
        tickers = self._screen_list.text().replace(",", " ").split()
        if not tickers:
            self._header.set_status("Type tickers to screen, or use Holdings.", error=True)
            return
        try:
            # Working capital can't be read from filings: the page's, as WACC
            # and terminal growth (the original screener assumed none).
            nwc = self._fields["nwc"].text().replace(",", " ").split()
            cfg = BatchConfig(
                wacc=self._fields["wacc"].number(scale=0.01),
                terminal_growth=self._fields["terminal"].number(scale=0.01),
                projection_years=int(self._fields["years"].number(integer=True, minimum=1)),
                nwc_pct_revenue=float(nwc[0]) / 100 if len(nwc) == 1 else 0.0)
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        template = Assumptions(terminal_method=_TERMINAL[self._terminal_method.current()],
                               net_marketable_securities=self._securities.current() == "Netted")
        source = self._source.current()
        today = self._as_of.current() == "Today"
        self._screen_button.setEnabled(False)
        self._header.set_status(f"Screening {len(tickers)} companies…")
        self._tasks.submit(lambda: self._valuation.screen(tickers, source, cfg, template, today),
                           on_done=self._on_screen, on_error=self._on_screen_error)

    def _on_screen(self, rows: list[ScreenRow]) -> None:
        self._screen_button.setEnabled(True)
        self._screen_rows = rows
        table = []
        for r in rows:
            margin = r.margin_of_safety
            note = r.error or (f"missing {r.missing_fields}" if r.missing_fields else "")
            table.append([
                r.ticker,
                _per_share(r.price) if math.isfinite(r.price) else ("—", PALETTE.muted),
                _per_share(r.value_per_share) if math.isfinite(r.value_per_share)
                else ("—", PALETTE.muted),
                (_pct(margin), PALETTE.good if margin > 0 else PALETTE.critical)
                if math.isfinite(margin) else ("—", PALETTE.muted),
                " / ".join(f"{g:.1%}" for g in r.implied_growth) or ("—", PALETTE.muted),
                _pct(r.terminal_share) if math.isfinite(r.terminal_share) else ("—", PALETTE.muted),
                (note, PALETTE.warning if r.error else PALETTE.muted),
            ])
        self._screen.set_rows(table)
        self._screen.setFixedHeight(36 + len(table) * _ROW)
        valued = sum(math.isfinite(r.value_per_share) for r in rows)
        self._header.set_status(f"Screened {len(rows)}: {valued} valued, cheapest first.")

    def _on_screen_error(self, exc: Exception) -> None:
        self._screen_button.setEnabled(True)
        self._on_error(exc)

    def _export_screen(self) -> None:
        if not self._screen_rows:
            self._header.set_status("Run a screen first.", error=True)
            return
        path, _filter = QFileDialog.getSaveFileName(self, "Export the screen",
                                                    str(Path.home() / "screen.csv"),
                                                    "CSV files (*.csv)")
        if path:
            self.export_screen(Path(path))

    def export_screen(self, path: Path) -> None:
        rows = list(self._screen_rows)
        self._tasks.submit(lambda: self._valuation.write_csv(rows, path),
                           on_done=lambda p: self._header.set_status(f"Exported {p.name}."),
                           on_error=self._on_error)

    # ---------------------------------------------------------------- saved
    def _save(self) -> None:
        if self._history is None or self._result is None or self._assumed is None:
            self._header.set_status("Value a company first.", error=True)
            return
        hist, assumed, result = self._history, self._assumed, self._result
        price_value = self._price_value()
        price = (None if price_value is None else
                 Price(price_value, self._price_basis, self._source.current()
                       if self._price_basis != _TYPED else _TYPED, datetime.now()))
        self._tasks.submit(lambda: self._valuation.save(hist, assumed, result, price),
                           on_done=self._on_saved, on_error=self._on_error)

    def _on_saved(self, valuation_id: int) -> None:
        self._header.set_status(f"Saved valuation #{valuation_id}.")
        self._refresh_saved()

    def _refresh_saved(self) -> None:
        self._tasks.submit(self._valuation.saved, on_done=self._render_saved,
                           on_error=self._on_error)

    def _render_saved(self, records) -> None:
        self._saved_ids = [r.id for r in records]
        rows = []
        for r in records:
            price = r.payload.get("price") or {}
            margin = price.get("margin_of_safety")
            rows.append([f"{r.saved_at:%Y-%m-%d %H:%M}", r.ticker,
                         _per_share(r.payload["result"]["value_per_share"],
                                    r.payload.get("currency", "USD")),
                         _per_share(price["value"]) if price else ("—", PALETTE.muted),
                         (_pct(margin), PALETTE.good if margin > 0 else PALETTE.critical)
                         if margin is not None else ("—", PALETTE.muted)])
        self._saved.set_rows(rows)
        self._saved.setFixedHeight(36 + max(len(rows), 1) * _ROW)

    def _reopen(self, row: int) -> None:
        if not 0 <= row < len(self._saved_ids):
            return
        valuation_id = self._saved_ids[row]
        self._token += 1
        token = self._token
        self._header.set_status(f"Reopening valuation #{valuation_id}…")
        self._tasks.submit(lambda: self._valuation.reopen(valuation_id),
                           on_done=lambda result: self._on_reopened(token, result),
                           on_error=self._on_error)

    def _on_reopened(self, token: int, result) -> None:
        record, assumptions, hist, note = result
        self._identifier.set_text(record.ticker)
        for key, _caption, name in _PATHS:
            path = getattr(assumptions, name)
            self._fields[key].set_text(" ".join(f"{100 * v:g}" for v in path)
                                       if isinstance(path, list) else f"{100 * path:g}")
        self._fields["years"].set_text(str(assumptions.projection_years))
        self._fields["wacc"].set_text(f"{100 * assumptions.wacc:g}")
        self._fields["terminal"].set_text(f"{100 * assumptions.terminal_growth:g}")
        for key, name in (("net_debt", "net_debt_override"), ("shares", "shares_override"),
                          ("base_revenue", "base_revenue_override")):
            override = getattr(assumptions, name)
            self._fields[key].set_text("" if override is None else f"{override:g}")
        self._terminal_method.select.set_current(_TERMINAL_LABEL_REVERSE[assumptions.terminal_method])
        self._securities.select.set_current(_SECURITIES[int(assumptions.net_marketable_securities)])
        price = record.payload.get("price")
        if price:
            self._price.set_text(f"{price['value']:.2f}")
            self._price_basis = f"saved · {price['basis']}"
            self._price_caption.setText(f"The saved price: {price['source']}, {price['basis']}, "
                                        f"{price['as_of'][:16]}")
        saved_value = _per_share(record.payload["result"]["value_per_share"],
                                 record.payload.get("currency", "USD"))
        self._saved_note.set_text(f"#{record.id}, saved {record.saved_at:%Y-%m-%d %H:%M}, said "
                                  f"{saved_value} a share. Today: {note}.")
        self._saved_note.setVisible(True)
        if hist is not None:
            self._on_company(token, hist, seed=False)
        else:
            self._header.set_status(note, error=True)

    def _on_error(self, exc: Exception) -> None:
        self._header.set_status(str(exc), error=True)
        _logger.error("valuation: %s", exc)


_TERMINAL_LABEL = {LAST_YEAR: "Last-year", STEADY_STATE: "Steady-state"}
_TERMINAL_LABEL_REVERSE = {v: k for k, v in _TERMINAL.items()}
