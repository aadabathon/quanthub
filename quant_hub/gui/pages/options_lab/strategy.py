"""Strategy tab: build a multi-leg position and read its P&L and risk.

Legs come from a template or are added by hand. A leg whose entry premium is
left blank enters at model fair value (a hypothetical trade); a typed
premium is the price actually paid. One ``Strategy`` value feeds every
number, so the chart, the tiles and the greeks always describe one position.

Single-expiry positions get the exact piecewise-linear expiry analysis
(``expiry_profile``); calendars fall back to the sampled curve and their
numbers are marked "≈".

With a market surface on the underlying card (C4), every leg takes its own
implied vol and its expiry's carry from the surface (``apply_surface``), and
the legs table shows each leg's σ.

The "days forward" slider moves the scenario date: the second curve is the
position marked to model at T+h with each leg's σ, r, q held fixed (sticky
strike: no vol dynamics).
It recomputes on every slider step — the whole grid is a few milliseconds.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QSlider, QVBoxLayout, QWidget

from quant_hub.gui.charts import Curve, CurveChart
from quant_hub.gui.components import (
    Card,
    EmptyState,
    PageStack,
    DataTable,
    Field,
    MetricTile,
    Select,
    SelectField,
    StatTable,
    button,
    form_grid,
    label,
    metric_row,
)
from quant_hub.gui.pages.base import debounce
from quant_hub.gui.theme import (
    FIELD_M,
    FIELD_S,
    PALETTE,
    SIDE_PANEL_WIDTH,
    SPACE_L,
    SPACE_M,
    SPACE_S,
)
from quant_hub.models.black_scholes import MarketInputs
from quant_hub.models.market_surface import MarketSurface, apply_surface
from quant_hub.models.strategy import (
    STRATEGY_TEMPLATES,
    OptionLeg,
    Strategy,
    entry_cost,
    expiry_profile,
    grid_breakevens,
    leg_entry_premium,
    nearest_listed_strike,
    spot_grid,
    strategy_greeks,
    strategy_pnl,
)

Reporter = Callable[[str, bool], None]
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2


def days_label(days: float) -> str:
    return f"{days:g}d"


def _money(value: float) -> str:
    if math.isinf(value):
        return "unlimited" if value > 0 else "−unlimited"
    return f"{value:+,.2f}"


def _legs_from_state(raw) -> list[OptionLeg]:
    legs = []
    for item in raw or []:
        try:
            legs.append(OptionLeg(item["type"], float(item["strike"]), float(item["dte"]),
                                  float(item["qty"]), item.get("premium")))
        except (KeyError, TypeError, ValueError):
            continue   # a stale or corrupt saved leg is dropped, not fatal
    return legs


class StrategyTab(QWidget):
    def __init__(self, get_market: Callable[[], MarketInputs], saved: dict,
                 report: Reporter,
                 get_surface: Callable[[], MarketSurface | None] = lambda: None) -> None:
        super().__init__()
        self._get_market, self._report = get_market, report
        self._get_surface = get_surface
        self._legs: list[OptionLeg] = _legs_from_state(saved.get("legs"))
        try:
            self._horizon = max(0, int(float(saved.get("horizon", "0"))))
        except ValueError:
            self._horizon = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_L)
        left = QVBoxLayout()
        left.setSpacing(SPACE_L)
        layout.addLayout(left, 1)
        left.addWidget(self._build_chart_card(), 3)
        self._tiles = {"entry": MetricTile("Net entry"), "max_profit": MetricTile("Max profit"),
                       "max_loss": MetricTile("Max loss"),
                       "breakevens": MetricTile("Breakevens")}
        left.addLayout(metric_row(list(self._tiles.values())))
        left.addWidget(self._build_legs_card(saved), 2)

        side = QWidget()
        side.setFixedWidth(SIDE_PANEL_WIDTH)
        column = QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_L)
        column.addWidget(self._build_template_card(saved))
        greeks_card = Card("Position greeks")
        self._greeks = StatTable([("At spot", (
            ("delta", "Delta (shares)"), ("gamma", "Gamma (shares / $1)"),
            ("theta", "Theta ($ / day)"), ("vega", "Vega ($ / vol pt)")))], value_header="")
        greeks_card.body.addWidget(self._greeks)
        self._greeks_card = greeks_card
        column.addWidget(greeks_card)
        column.addStretch(1)
        layout.addWidget(side)

        if not self._legs and self._shares.text() in ("", "0"):
            self._apply_template(refresh=False)

    # ------------------------------------------------------------ building
    def _build_chart_card(self) -> Card:
        self._chart_card = Card("P&L vs underlying", "")
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setFixedWidth(180)
        self._slider.setRange(0, 30)
        self._slider.setValue(self._horizon)
        self._slider.valueChanged.connect(self._on_horizon)
        self._horizon_label = label("", "fieldCaption")
        self._horizon_label.setMinimumWidth(96)
        self._chart_card.add_action(label("Days forward", "fieldCaption"))
        self._chart_card.add_action(self._slider)
        self._chart_card.add_action(self._horizon_label)
        self._chart = CurveChart(x_axis="price", y_axis="money", x_name="Spot")
        self._error = EmptyState("ph.warning", "Can't value this position")
        self._chart_stack = PageStack()
        self._chart_stack.addWidget(self._chart)
        self._chart_stack.addWidget(self._error)
        self._chart_stack.setMinimumHeight(260)
        self._chart_card.body.addWidget(self._chart_stack, 1)
        return self._chart_card

    def _build_legs_card(self, saved: dict) -> Card:
        card = Card("Legs", "Blank premium = enters at model fair value · Delete removes the "
                            "selected leg")
        self._table = DataTable([("Qty", "left"), ("Type", "left"), ("Strike", "right"),
                                 ("Expiry", "right"), ("σ", "right"), ("Entry / share", "right")],
                                row_height=34, focusable=True)
        self._table.balance_columns()
        self._table.setMinimumHeight(130)
        delete = QShortcut(QKeySequence.Delete, self._table, context=Qt.WidgetShortcut)
        delete.activated.connect(self._remove_leg)
        card.body.addWidget(self._table, 1)

        editor = QHBoxLayout()
        editor.setSpacing(SPACE_M)
        self._new_qty = Field("Qty ±", "1", FIELD_S)
        self._new_type = Select(min_width=FIELD_S)
        self._new_type.setFixedWidth(FIELD_S)
        self._new_type.set_items(["call", "put"], "call")
        type_box = QVBoxLayout()
        type_box.setSpacing(4)
        type_box.addWidget(label("Type", "fieldCaption"))
        type_box.addWidget(self._new_type)
        self._new_strike = Field("Strike", "100", FIELD_S)
        self._new_dte = Field("Days", "30", FIELD_S)
        self._new_premium = Field("Paid / share", "", FIELD_S, placeholder="fair")
        self._shares = Field("Shares ±", saved.get("shares", "0"), FIELD_S)
        editor.addWidget(self._new_qty, 0, Qt.AlignBottom)
        editor.addLayout(type_box)
        for widget in (self._new_strike, self._new_dte, self._new_premium):
            editor.addWidget(widget, 0, Qt.AlignBottom)
        editor.addWidget(button("Add leg", icon_name="ph.plus", on_click=self._add_leg),
                         0, Qt.AlignBottom)
        editor.addWidget(button("Remove", "ghost", "ph.trash", on_click=self._remove_leg),
                         0, Qt.AlignBottom)
        editor.addStretch(1)
        editor.addWidget(self._shares, 0, Qt.AlignBottom)
        card.body.addLayout(editor)
        self._new_premium.committed.connect(self._add_leg)
        live = debounce(self, self.refresh)
        self._shares.edit.textEdited.connect(lambda _t: live())
        self._shares.committed.connect(self.refresh)
        return card

    def _build_template_card(self, saved: dict) -> Card:
        card = Card("Template", "Rebuilds the legs around spot")
        names = list(STRATEGY_TEMPLATES)
        template = saved.get("template", "Iron condor")
        self._template = SelectField("Strategy", names,
                                     template if template in names else "Iron condor",
                                     SIDE_PANEL_WIDTH - 2 * SPACE_L)
        card.body.addWidget(self._template)
        self._width = Field("Strike width %", saved.get("width", "5"), _HALF)
        self._dte = Field("Days to expiry", saved.get("dte", "30"), _HALF)
        card.body.addLayout(form_grid([self._width, self._dte], 2))
        for field in (self._width, self._dte):
            field.committed.connect(self._apply_template)
        self._template.select.current_changed.connect(lambda _n: self._apply_template())
        card.body.addSpacing(SPACE_S)
        card.body.addWidget(button("Use template", "primary", "ph.stack",
                                   on_click=self._apply_template))
        return card

    # -------------------------------------------------------------- public
    def recentre(self, spot: float) -> None:
        """Rebuild the selected template (and the add-leg default) around a new spot."""
        self._new_strike.set_text(f"{nearest_listed_strike(spot, spot):g}")
        self._apply_template(refresh=False)

    def state(self) -> dict:
        return {"legs": [{"type": leg.option_type, "strike": leg.strike, "dte": leg.dte,
                          "qty": leg.quantity, "premium": leg.premium} for leg in self._legs],
                "shares": self._shares.text(), "template": self._template.current(),
                "width": self._width.text(), "dte": self._dte.text(),
                "horizon": str(self._horizon)}

    def refresh(self) -> None:
        try:
            market = self._get_market()
            strategy = Strategy(tuple(self._legs), stock_quantity=self._shares.number())
            surface = self._get_surface()
            if surface is not None:
                strategy = apply_surface(strategy, surface)
        except ValueError as exc:
            self._report(str(exc), True)
            self._show_empty(str(exc))
            return
        self._report("", False)
        self._chart_stack.setCurrentWidget(self._chart)
        self._render_legs(market, strategy.legs, surface)
        self._show(strategy, market)

    # ------------------------------------------------------------- editing
    def _apply_template(self, refresh: bool = True) -> None:
        try:
            spot = self._get_market().spot
            width = self._width.number(scale=0.01, minimum=0.01)
            dte = self._dte.number(minimum=1)
            template = STRATEGY_TEMPLATES[self._template.current()](spot, dte, width)
        except ValueError as exc:
            self._report(str(exc), True)
            return
        self._legs = list(template.legs)
        self._shares.set_text(f"{template.stock_quantity:g}")
        if refresh:
            self.refresh()

    def _add_leg(self) -> None:
        try:
            premium = self._new_premium.number(minimum=0.0, allow_blank=True)
            leg = OptionLeg(option_type=self._new_type.current(),
                            strike=self._new_strike.number(minimum=0.01),
                            dte=self._new_dte.number(minimum=0.01),
                            quantity=self._new_qty.number(), premium=premium)
        except ValueError as exc:
            self._report(str(exc), True)
            return
        self._legs.append(leg)
        self.refresh()

    def _remove_leg(self) -> None:
        row = self._table.selected_row()
        if 0 <= row < len(self._legs):
            del self._legs[row]
            self.refresh()

    def _on_horizon(self, value: int) -> None:
        self._horizon = value
        self.refresh()

    # ----------------------------------------------------------- rendering
    def _render_legs(self, market: MarketInputs, priced: tuple[OptionLeg, ...],
                     surface: MarketSurface | None) -> None:
        rows, extrapolated = [], []
        for leg in priced:
            paid = (f"{leg.premium:,.2f}" if leg.premium is not None
                    else (f"fair {leg_entry_premium(leg, market):,.2f}", PALETTE.text_secondary))
            vol = leg.vol if leg.vol is not None else market.sigma
            outside = surface is not None and surface.point(leg.strike, leg.dte).extrapolated
            extrapolated.append(outside)
            rows.append([(f"{leg.quantity:+g}",
                          PALETTE.good if leg.quantity > 0 else PALETTE.critical),
                         leg.option_type, f"{leg.strike:,.2f}", days_label(leg.dte),
                         (f"{vol:.1%}" + (" *" if outside else ""),
                          PALETTE.warning if outside else PALETTE.text_secondary), paid])
        self._table.set_rows(rows)
        for row, outside in enumerate(extrapolated):
            if outside:
                self._table.item(row, 4).setToolTip(
                    "Extrapolated: outside the chain's quoted strikes or expiries, so "
                    "this vol comes from the fitted wing or the flat-vol rule, not quotes")

    def _show(self, strategy: Strategy, market: MarketInputs) -> None:
        spots = spot_grid(strategy, market)
        first = strategy.expiries[0] if strategy.legs else 0.0
        last = strategy.expiries[-1] if strategy.legs else 0.0
        self._slider.blockSignals(True)
        self._slider.setRange(0, max(int(math.ceil(last)), 1))
        self._slider.blockSignals(False)
        horizon = min(float(self._horizon), last)
        self._horizon_label.setText(f"T + {days_label(horizon)}")

        expiry_pnl = strategy_pnl(strategy, market, spots, first)
        cost = entry_cost(strategy, market)
        if len(strategy.expiries) <= 1:
            profile = expiry_profile(strategy, market)
            breakevens, approx = profile.breakevens, ""
            max_profit, max_loss = profile.max_profit, profile.max_loss
        else:
            breakevens, approx = grid_breakevens(spots, expiry_pnl), "≈ "
            max_profit, max_loss = float(np.max(expiry_pnl)), float(np.min(expiry_pnl))

        curves = [Curve(f"At expiry ({days_label(first)})" if strategy.legs else "Stock",
                        spots, expiry_pnl, PALETTE.series, 2.0, fmt="${:,.0f}", fill="signed")]
        if horizon < first:
            curves.append(Curve(f"T + {days_label(horizon)}", spots,
                                strategy_pnl(strategy, market, spots, horizon),
                                PALETTE.ramp[2], 1.6, fmt="${:,.0f}"))
        shown = [b for b in breakevens if spots[0] <= b <= spots[-1]]
        self._chart.set_curves(curves, vlines=[("spot", market.spot)], zero_line=True,
                               points=[(b, f"{b:,.2f}") for b in shown])
        n_legs = len(strategy.legs)
        stock = f" + {strategy.stock_quantity:+g} shares" if strategy.stock_quantity else ""
        self._chart_card.set_subtitle(
            f"{n_legs} leg{'s' if n_legs != 1 else ''}{stock} · ×{strategy.multiplier:g} "
            "per contract · green: profit, red: loss · dots: breakevens")

        self._tiles["entry"].set_value(f"{abs(cost):,.2f}", hint="debit" if cost >= 0 else "credit")
        self._tiles["max_profit"].set_value(approx + _money(max_profit), "good",
                                            hint="at expiry")
        self._tiles["max_loss"].set_value(approx + _money(max_loss), "critical",
                                          hint="at expiry")
        bes = ", ".join(f"{b:,.2f}" for b in breakevens[:2]) or "none"
        self._tiles["breakevens"].set_value(
            approx + bes + (" …" if len(breakevens) > 2 else ""),
            hint=f"{len(breakevens)} at expiry")

        g = strategy_greeks(strategy, market, np.array([market.spot]), horizon)
        self._greeks_card.set_subtitle(f"Spot {market.spot:,.2f}, T + {days_label(horizon)}")
        for key, fmt in (("delta", "{:+,.1f}"), ("gamma", "{:+,.3f}"), ("theta", "{:+,.2f}"),
                         ("vega", "{:+,.2f}")):
            self._greeks.set_value(key, fmt.format(getattr(g, key)[0]))

    def _show_empty(self, reason: str) -> None:
        self._greeks.clear()
        for tile in self._tiles.values():
            tile.set_value("—")
        self._error.set_text("Can't value this position", reason[:1].upper() + reason[1:])
        self._chart_stack.setCurrentWidget(self._error)
