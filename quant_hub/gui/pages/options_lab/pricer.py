"""Pricer tab: one European contract, priced forward or inverted to IV.

"Price from σ" prices at the underlying card's σ. "σ from price" inverts a
quoted option price to implied volatility (the bracketed-Newton solver in
``models/black_scholes.py``), then shows greeks and profiles *at that IV*,
i.e. the option as the market prices it. All closed form, so it runs on the
GUI thread (well under a millisecond per solve) and recomputes as you type.

The profile chart shows one metric against spot at four times to expiry
(the contract's own, half, a quarter, and one day), colored along the
ordinal ramp because time is ordered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from quant_hub.gui.charts import Curve, CurveChart
from quant_hub.gui.components import (
    Card,
    EmptyState,
    PageStack,
    Field,
    MetricTile,
    SegmentedControl,
    StatTable,
    divider,
    form_grid,
    label,
    metric_row,
)
from quant_hub.gui.pages.base import debounce
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M, SPACE_S
from quant_hub.models.black_scholes import (
    BlackScholesInputs,
    MarketInputs,
    greeks,
    greeks_array,
    implied_volatility,
    intrinsic_value,
    price,
    price_array,
)
from quant_hub.models.strategy import OptionLeg, Strategy, nearest_listed_strike, spot_grid

Reporter = Callable[[str, bool], None]
_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2

PRICE_FROM_VOL, VOL_FROM_PRICE = "Price from σ", "σ from price"
_SAVED_MODE = {PRICE_FROM_VOL: "price from vol", VOL_FROM_PRICE: "vol from price"}  # Tk's
# Chart metric label -> (Tk saved key, Greeks attribute or None for price, axis caption)
_METRICS = {
    "Price": ("price", None, "Option value"),
    "Delta": ("delta", "delta", "Delta"),
    "Gamma": ("gamma", "gamma", "Gamma"),
    "Theta": ("theta / day", "theta", "Theta per day"),
    "Vega": ("vega / 1%", "vega", "Vega per vol point"),
}


@dataclass(frozen=True)
class Contract:
    option_type: str
    strike: float
    dte: float


def days_label(days: float) -> str:
    return f"{days:g}d"


class PricerTab(QWidget):
    def __init__(self, get_market: Callable[[], MarketInputs], saved: dict,
                 report: Reporter) -> None:
        super().__init__()
        self._get_market, self._report = get_market, report
        saved_metric = saved.get("metric", "price")
        self._metric = next((k for k, v in _METRICS.items() if v[0] == saved_metric), "Price")
        self._type = "put" if saved.get("option_type") == "put" else "call"
        self._mode = VOL_FROM_PRICE if saved.get("mode") == "vol from price" else PRICE_FROM_VOL

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE_L)
        left = QVBoxLayout()
        left.setSpacing(SPACE_L)
        layout.addLayout(left, 1)
        self._chart_card = Card("Value vs spot", "")
        metrics = SegmentedControl(list(_METRICS), self._metric)
        metrics.changed.connect(self._on_metric)
        self._chart_card.add_action(metrics)
        self._chart = CurveChart(x_axis="price", y_axis="price", x_name="Spot")
        self._error = EmptyState("ph.warning", "Can't price this contract")
        self._chart_stack = PageStack()
        self._chart_stack.addWidget(self._chart)
        self._chart_stack.addWidget(self._error)
        self._chart_stack.setMinimumHeight(300)
        self._chart_card.body.addWidget(self._chart_stack, 1)
        left.addWidget(self._chart_card, 1)
        self._tiles = {
            "delta": MetricTile("Delta", "per share"),
            "gamma": MetricTile("Gamma", "per share, per $1"),
            "theta": MetricTile("Theta / day", "per share"),
            "vega": MetricTile("Vega / vol pt", "per share"),
            "rho": MetricTile("Rho / rate pt", "per share"),
        }
        left.addLayout(metric_row(list(self._tiles.values())))

        layout.addWidget(self._build_contract(saved))
        live = debounce(self, self.refresh)
        for field in (self._strike, self._dte, self._quote):
            field.edit.textEdited.connect(lambda _t: live())
            field.committed.connect(self.refresh)
        self._show_mode()

    def _build_contract(self, saved: dict) -> Card:
        card = Card("Contract")
        card.setFixedWidth(SIDE_PANEL_WIDTH)
        types = SegmentedControl(["Call", "Put"], self._type.capitalize(), fill=True)
        types.changed.connect(self._on_type)
        card.body.addWidget(types)
        self._strike = Field("Strike", saved.get("strike", "100"), _HALF)
        self._dte = Field("Days to expiry", saved.get("dte", "30"), _HALF)
        card.body.addLayout(form_grid([self._strike, self._dte], 2))
        modes = SegmentedControl([PRICE_FROM_VOL, VOL_FROM_PRICE], self._mode, fill=True)
        modes.changed.connect(self._on_mode)
        card.body.addSpacing(SPACE_S)
        card.body.addWidget(modes)
        self._quote = Field("Option price (market quote)", saved.get("quote", "3.00"), _HALF)
        card.body.addWidget(self._quote)
        card.body.addWidget(divider())
        self._hero_caption = label("", "groupTitle")
        card.body.addWidget(self._hero_caption)
        self._hero = label("—", "heroValue")
        card.body.addWidget(self._hero)
        self._stats = StatTable([(None, (("vol", "σ used"), ("intrinsic", "Intrinsic value"),
                                         ("time_value", "Time value")))], value_header="")
        card.body.addWidget(self._stats)
        card.body.addStretch(1)
        return card

    # -- public ---------------------------------------------------------
    def contract(self) -> Contract:
        strike = self._strike.number(minimum=0.01)
        dte = self._dte.number(minimum=0.01)
        return Contract(self._type, strike, dte)

    def recentre(self, spot: float) -> None:
        """Move the strike to the at-the-money listed strike (new underlying)."""
        self._strike.set_text(f"{nearest_listed_strike(spot, spot):g}")

    def state(self) -> dict:
        return {"option_type": self._type, "strike": self._strike.text(),
                "dte": self._dte.text(), "mode": _SAVED_MODE[self._mode],
                "quote": self._quote.text(), "metric": _METRICS[self._metric][0]}

    def inputs(self) -> BlackScholesInputs:
        """The contract as priced here: σ is the card's, or the solved IV.
        Raises ValueError (bad field, or a quote outside no-arbitrage bounds)."""
        market = self._get_market()
        contract = self.contract()
        sigma = market.sigma
        if self._mode == VOL_FROM_PRICE:
            sigma = implied_volatility(self._quote.number(minimum=0.0), market.spot,
                                       contract.strike, contract.dte, market.rate,
                                       contract.option_type, market.dividend_yield)
        return BlackScholesInputs(market.spot, contract.strike, contract.dte, market.rate, sigma,
                                  contract.option_type, market.dividend_yield)

    def refresh(self) -> None:
        try:
            inputs = self.inputs()
            market = self._get_market()
        except ValueError as exc:
            self._report(str(exc), True)
            self._show_empty(str(exc))
            return
        self._report("", False)
        self._chart_stack.setCurrentWidget(self._chart)
        self._show(inputs, market)

    # -- events ---------------------------------------------------------
    def _on_type(self, text: str) -> None:
        self._type = text.lower()
        self.refresh()

    def _on_mode(self, mode: str) -> None:
        self._mode = mode
        self._show_mode()
        self.refresh()

    def _on_metric(self, metric: str) -> None:
        self._metric = metric
        self.refresh()

    def _show_mode(self) -> None:
        inverting = self._mode == VOL_FROM_PRICE
        self._quote.setVisible(inverting)
        self._hero_caption.setText("IMPLIED VOLATILITY" if inverting else "MODEL PRICE")

    # -- rendering ------------------------------------------------------
    def _show(self, inputs: BlackScholesInputs, market: MarketInputs) -> None:
        value = price(inputs)
        g = greeks(inputs)
        intrinsic = float(intrinsic_value(inputs.spot, inputs.strike, inputs.option_type))
        inverting = self._mode == VOL_FROM_PRICE
        self._hero.setText(f"{inputs.sigma:.2%}" if inverting else f"{value:,.4f}")
        vol_note = f"{inputs.sigma:.2%}" + (f"  (card {market.sigma:.1%})" if inverting else "")
        self._stats.set_value("vol", vol_note)
        self._stats.set_value("intrinsic", f"{intrinsic:,.4f}")
        self._stats.set_value("time_value", f"{value - intrinsic:,.4f}")
        for key, fmt in (("delta", "{:+.4f}"), ("gamma", "{:.5f}"), ("theta", "{:+.4f}"),
                         ("vega", "{:.4f}"), ("rho", "{:+.4f}")):
            self._tiles[key].set_value(fmt.format(getattr(g, key)))
        self._draw_profile(inputs)

    def _draw_profile(self, inputs: BlackScholesInputs) -> None:
        dte = inputs.dte
        horizons = sorted({dte, dte / 2, dte / 4, min(1.0, dte)}, reverse=True)
        spots = spot_grid(
            Strategy((OptionLeg(inputs.option_type, inputs.strike, dte, 1),)),
            MarketInputs(inputs.spot, inputs.rate, inputs.sigma, inputs.dividend_yield))
        _saved, attribute, caption = _METRICS[self._metric]
        curves = []
        for i, days in enumerate(horizons):
            args = (spots, inputs.strike, days, inputs.rate, inputs.sigma, inputs.option_type,
                    inputs.dividend_yield)
            values = (price_array(*args) if attribute is None
                      else getattr(greeks_array(*args), attribute))
            curves.append(Curve(f"{days_label(days)} to expiry" if i == 0 else days_label(days),
                                spots, values, PALETTE.ramp[min(i, len(PALETTE.ramp) - 1)],
                                2.0 if i == 0 else 1.4,
                                fmt="{:,.4f}" if attribute else "{:,.2f}"))
        self._chart.set_curves(curves, vlines=[("spot", inputs.spot), ("strike", inputs.strike)],
                               zero_line=attribute is not None)
        self._chart_card.set_subtitle(
            f"{caption} of the {inputs.option_type} K={inputs.strike:g} against spot, at four "
            "times to expiry · σ " + f"{inputs.sigma:.1%}")

    def _show_empty(self, reason: str) -> None:
        self._hero.setText("—")
        self._stats.clear()
        for tile in self._tiles.values():
            tile.set_value("—")
        self._error.set_text("Can't price this contract", reason[:1].upper() + reason[1:])
        self._chart_stack.setCurrentWidget(self._error)
        self._chart_card.set_subtitle("")
