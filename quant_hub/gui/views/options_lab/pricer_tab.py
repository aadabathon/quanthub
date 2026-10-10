"""Pricer tab: one European contract, priced forward or inverted to IV.

"price from vol" prices at the underlying strip's σ. "vol from price"
inverts a quoted option price to implied volatility (the bracketed-Newton
solver in ``models/black_scholes.py``), then shows greeks and profiles
*at that IV*, i.e. the option as the market is pricing it. All closed form,
so it runs on the GUI thread (well under a millisecond per solve).
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from tkinter import ttk

from quant_hub.gui.theme import FONT_MONO_LG, PALETTE, SPACE_S, SPACE_XS
from quant_hub.gui.views.lab_widgets import (
    CHART_STYLE, Reporter, combo, days_label, embed_figure, entry, heading, parse_float, stat_rows,
)
from quant_hub.models.black_scholes import (
    BlackScholesInputs, MarketInputs, greeks, greeks_array, implied_volatility, intrinsic_value,
    price, price_array,
)
from quant_hub.models.strategy import OptionLeg, Strategy, nearest_listed_strike, spot_grid
from quant_hub.visualization.charts import plot_message, plot_option_profile

PRICE_FROM_VOL = "price from vol"
VOL_FROM_PRICE = "vol from price"
_METRICS = ["price", "delta", "gamma", "theta / day", "vega / 1%"]


@dataclass(frozen=True)
class Contract:
    option_type: str
    strike: float
    dte: float


class PricerTab(ttk.Frame):
    def __init__(
        self, parent: tk.Misc, get_market: Callable[[], MarketInputs], saved: dict,
        report: Reporter,
    ) -> None:
        super().__init__(parent, style="Surface.TFrame")
        self._get_market, self._report = get_market, report
        self._type_var = tk.StringVar(value=saved.get("option_type", "call"))
        self._strike_var = tk.StringVar(value=saved.get("strike", "100"))
        self._dte_var = tk.StringVar(value=saved.get("dte", "30"))
        self._mode_var = tk.StringVar(value=saved.get("mode", PRICE_FROM_VOL))
        self._quote_var = tk.StringVar(value=saved.get("quote", "3.00"))
        self._metric_var = tk.StringVar(value=saved.get("metric", "price"))

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=250)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        chart = ttk.Frame(self, style="Surface.TFrame")
        chart.grid(row=1, column=0, sticky="nsew", padx=(0, SPACE_S), pady=(0, SPACE_S))
        self._figure, self._canvas = embed_figure(chart)
        panel = ttk.Frame(self, style="Card.TFrame", padding=SPACE_S)
        panel.grid(row=1, column=1, sticky="nsew", pady=(0, SPACE_S))
        self._build_panel(panel)
        self._mode_changed()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="Card.TFrame", padding=(0, SPACE_XS))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=SPACE_S)
        combo(bar, "type", self._type_var, ["call", "put"], 0, 0, width=5, on_commit=self.refresh)
        entry(bar, "strike", self._strike_var, 0, 2, width=8, on_commit=self.refresh)
        entry(bar, "days to expiry", self._dte_var, 0, 4, width=5, on_commit=self.refresh)
        combo(bar, "solve", self._mode_var, [PRICE_FROM_VOL, VOL_FROM_PRICE], 0, 6, width=13,
              on_commit=self._mode_changed)
        self._quote_entry = entry(bar, "option price", self._quote_var, 0, 8, width=7,
                                  on_commit=self.refresh)
        ttk.Button(bar, text="Calculate", style="Accent.TButton", command=self.refresh).grid(
            row=0, column=10, padx=SPACE_S
        )
        bar.columnconfigure(11, weight=1)
        combo(bar, "chart", self._metric_var, _METRICS, 0, 12, width=11, on_commit=self.refresh)

    def _build_panel(self, panel: ttk.Frame) -> None:
        self._hero_caption = ttk.Label(panel, text="MODEL PRICE", style="CardHeading.TLabel")
        self._hero_caption.pack(anchor="w")
        self._hero = tk.Label(panel, text="—", font=FONT_MONO_LG, background=PALETTE.surface,
                              foreground=PALETTE.text)
        self._hero.pack(anchor="w", pady=(0, SPACE_XS))
        self._values = stat_rows(panel, [
            ("vol", "σ used"), ("intrinsic", "intrinsic"), ("time_value", "time value"),
        ])
        ttk.Separator(panel).pack(fill="x", pady=SPACE_XS)
        heading(panel, "GREEKS (PER SHARE)")
        self._values.update(stat_rows(panel, [
            ("delta", "delta"), ("gamma", "gamma"), ("theta", "theta / day"),
            ("vega", "vega / 1 vol pt"), ("rho", "rho / 1 rate pt"),
        ]))

    # -- public ---------------------------------------------------------

    def contract(self) -> Contract:
        option_type = self._type_var.get()
        strike = parse_float(self._strike_var, "strike")
        dte = parse_float(self._dte_var, "days to expiry")
        if strike <= 0 or dte <= 0:
            raise ValueError("strike and days to expiry must be positive")
        return Contract(option_type, strike, dte)

    def recentre(self, spot: float) -> None:
        """Move the strike to the at-the-money listed strike (new underlying)."""
        self._strike_var.set(f"{nearest_listed_strike(spot, spot):g}")

    def state(self) -> dict:
        return {
            "option_type": self._type_var.get(), "strike": self._strike_var.get(),
            "dte": self._dte_var.get(), "mode": self._mode_var.get(),
            "quote": self._quote_var.get(), "metric": self._metric_var.get(),
        }

    def inputs(self) -> BlackScholesInputs:
        """The contract as priced here: σ is the strip's, or the solved IV.

        Raises ValueError (bad field, or a quote outside no-arbitrage bounds).
        """
        market = self._get_market()
        contract = self.contract()
        sigma = market.sigma
        if self._mode_var.get() == VOL_FROM_PRICE:
            quote = parse_float(self._quote_var, "option price")
            sigma = implied_volatility(
                quote, market.spot, contract.strike, contract.dte, market.rate,
                contract.option_type, market.dividend_yield,
            )
        return BlackScholesInputs(
            market.spot, contract.strike, contract.dte, market.rate, sigma,
            contract.option_type, market.dividend_yield,
        )

    def refresh(self) -> None:
        try:
            inputs = self.inputs()
            market = self._get_market()
        except ValueError as exc:
            self._report(str(exc), True)
            self._show_empty(str(exc))
            return
        self._report("", False)
        self._show(inputs, market)

    # -- rendering ------------------------------------------------------

    def _mode_changed(self) -> None:
        inverting = self._mode_var.get() == VOL_FROM_PRICE
        self._quote_entry.configure(state="normal" if inverting else "disabled")
        self._hero_caption.configure(text="IMPLIED VOLATILITY" if inverting else "MODEL PRICE")
        self.refresh()

    def _show(self, inputs: BlackScholesInputs, market: MarketInputs) -> None:
        value = price(inputs)
        g = greeks(inputs)
        intrinsic = float(intrinsic_value(inputs.spot, inputs.strike, inputs.option_type))
        inverting = self._mode_var.get() == VOL_FROM_PRICE
        self._hero.configure(text=f"{inputs.sigma:.2%}" if inverting else f"{value:,.4f}")
        vol_note = f"{inputs.sigma:.2%}"
        if inverting:
            vol_note += f"  (strip {market.sigma:.1%})"
        texts = {
            "vol": vol_note, "intrinsic": f"{intrinsic:,.4f}",
            "time_value": f"{value - intrinsic:,.4f}", "delta": f"{g.delta:+.4f}",
            "gamma": f"{g.gamma:.5f}", "theta": f"{g.theta:+.4f}", "vega": f"{g.vega:.4f}",
            "rho": f"{g.rho:+.4f}",
        }
        for key, text in texts.items():
            self._values[key].configure(text=text)
        self._draw_profile(inputs)

    def _draw_profile(self, inputs: BlackScholesInputs) -> None:
        dte = inputs.dte
        horizons = sorted({dte, dte / 2, dte / 4, min(1.0, dte)}, reverse=True)
        spots = spot_grid(
            Strategy((OptionLeg(inputs.option_type, inputs.strike, dte, 1),)),
            MarketInputs(inputs.spot, inputs.rate, inputs.sigma, inputs.dividend_yield),
        )
        metric = self._metric_var.get()
        curves = []
        for days in horizons:
            args = (spots, inputs.strike, days, inputs.rate, inputs.sigma, inputs.option_type,
                    inputs.dividend_yield)
            if metric == "price":
                values = price_array(*args)
            else:
                attribute = {"delta": "delta", "gamma": "gamma", "theta / day": "theta",
                             "vega / 1%": "vega"}[metric]
                values = getattr(greeks_array(*args), attribute)
            curves.append((days_label(days), values))
        plot_option_profile(self._figure, spots, curves, metric, inputs.spot, inputs.strike,
                            CHART_STYLE)
        self._canvas.draw_idle()

    def _show_empty(self, message: str) -> None:
        self._hero.configure(text="—")
        for label in self._values.values():
            label.configure(text="—")
        plot_message(self._figure, message, CHART_STYLE)
        self._canvas.draw_idle()

