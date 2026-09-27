"""Strategy tab: build a multi-leg position and read its P&L and risk.

Legs come from a template or are added by hand. A leg whose entry premium
is left blank enters at model fair value, so the diagram shows a
hypothetical trade. A typed premium is the price actually paid, which is
what a leg loaded later from broker positions will carry. The same
``Strategy`` value feeds every number here, so the chart, the summary, and
the greeks always describe one position.

Single-expiry positions get the exact piecewise-linear expiry analysis
(``expiry_profile``). Calendars fall back to the sampled curve, and the
summary marks those values with "≈".
"""

from __future__ import annotations

import math
import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

import numpy as np

from quant_hub.gui.theme import SPACE_S, SPACE_XS
from quant_hub.gui.views.options_lab.common import (
    CHART_STYLE, Reporter, combo, days_label, embed_figure, entry, heading, parse_float, stat_rows,
)
from quant_hub.models.black_scholes import MarketInputs
from quant_hub.models.strategy import (
    STRATEGY_TEMPLATES, OptionLeg, Strategy, entry_cost, expiry_profile, grid_breakevens,
    leg_entry_premium, nearest_listed_strike, spot_grid, strategy_greeks, strategy_pnl,
)
from quant_hub.visualization.charts import plot_message, plot_strategy_pnl

_COLUMNS = (("qty", 60), ("type", 60), ("strike", 80), ("expiry", 70), ("entry / sh", 110))


class StrategyTab(ttk.Frame):
    def __init__(
        self, parent: tk.Misc, get_market: Callable[[], MarketInputs], saved: dict,
        report: Reporter,
    ) -> None:
        super().__init__(parent, style="Surface.TFrame")
        self._get_market, self._report = get_market, report
        self._legs: list[OptionLeg] = _legs_from_state(saved.get("legs"))
        self._template_var = tk.StringVar(value=saved.get("template", "Iron condor"))
        self._width_var = tk.StringVar(value=saved.get("width", "5"))
        self._dte_var = tk.StringVar(value=saved.get("dte", "30"))
        self._horizon_var = tk.StringVar(value=saved.get("horizon", "0"))
        self._shares_var = tk.StringVar(value=saved.get("shares", "0"))
        self._new = {
            "qty": tk.StringVar(value="1"), "type": tk.StringVar(value="call"),
            "strike": tk.StringVar(value="100"), "dte": tk.StringVar(value="30"),
            "premium": tk.StringVar(value=""),
        }

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=250)
        self.rowconfigure(1, weight=1)
        self._build_toolbar()
        chart = ttk.Frame(self, style="Surface.TFrame")
        chart.grid(row=1, column=0, sticky="nsew", padx=(0, SPACE_S))
        self._figure, self._canvas = embed_figure(chart)
        self._build_legs(ttk.Frame(self, style="Card.TFrame", padding=SPACE_S))
        panel = ttk.Frame(self, style="Card.TFrame", padding=SPACE_S)
        panel.grid(row=1, column=1, rowspan=2, sticky="nsew", pady=(0, SPACE_S))
        self._build_panel(panel)
        if not self._legs and self._shares_var.get() in ("", "0"):
            self._apply_template(refresh=False)
        self._render_legs()

    # -- layout ---------------------------------------------------------

    def _build_legs(self, frame: ttk.Frame) -> None:
        frame.grid(row=2, column=0, sticky="ew", padx=(0, SPACE_S), pady=SPACE_S)
        frame.columnconfigure(0, weight=1)
        self._tree = ttk.Treeview(frame, columns=[c for c, _ in _COLUMNS], show="headings",
                                  height=4, selectmode="browse")
        for name, width in _COLUMNS:
            self._tree.heading(name, text=name)
            self._tree.column(name, width=width, anchor="e", stretch=True)
        self._tree.grid(row=0, column=0, sticky="ew")

        editor = ttk.Frame(frame, style="Card.TFrame")
        editor.grid(row=1, column=0, sticky="w", pady=(SPACE_XS, 0))
        entry(editor, "qty ±", self._new["qty"], 0, 0, width=4)
        combo(editor, "type", self._new["type"], ["call", "put"], 0, 2, width=5)
        entry(editor, "strike", self._new["strike"], 0, 4, width=7)
        entry(editor, "days", self._new["dte"], 0, 6, width=5)
        entry(editor, "paid (blank = fair)", self._new["premium"], 0, 8, width=6,
              on_commit=self._add_leg)
        ttk.Button(editor, text="Add leg", command=self._add_leg).grid(row=0, column=10,
                                                                      padx=(SPACE_XS, 0))
        ttk.Button(editor, text="Remove", command=self._remove_leg).grid(row=0, column=11,
                                                                         padx=(SPACE_XS, 0))
        entry(editor, "shares ±", self._shares_var, 0, 12, width=6, on_commit=self.refresh)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self, style="Card.TFrame", padding=(0, SPACE_XS))
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=SPACE_S)
        combo(bar, "template", self._template_var, list(STRATEGY_TEMPLATES), 0, 0, width=14)
        entry(bar, "strike width %", self._width_var, 0, 2, width=5, on_commit=self._apply_template)
        entry(bar, "days to expiry", self._dte_var, 0, 4, width=5, on_commit=self._apply_template)
        ttk.Button(bar, text="Use template", command=self._apply_template).grid(
            row=0, column=6, padx=SPACE_S
        )
        bar.columnconfigure(7, weight=1)
        ttk.Label(bar, text="scenario: days forward", style="Field.TLabel").grid(
            row=0, column=8, sticky="w", padx=(SPACE_S, 2)
        )
        self._horizon_spin = ttk.Spinbox(bar, textvariable=self._horizon_var, from_=0,
                                         to=365, increment=1, width=5, command=self.refresh)
        self._horizon_spin.grid(row=0, column=9, sticky="w", padx=(0, SPACE_S))
        self._horizon_spin.bind("<Return>", lambda _e: self.refresh())

    def _build_panel(self, panel: ttk.Frame) -> None:
        heading(panel, "AT EXPIRY")
        self._values = stat_rows(panel, [
            ("entry", "net entry"), ("max_profit", "max profit"), ("max_loss", "max loss"),
            ("breakevens", "breakevens"),
        ])
        ttk.Separator(panel).pack(fill="x", pady=SPACE_XS)
        self._greeks_heading = ttk.Label(panel, text="POSITION GREEKS", style="CardHeading.TLabel")
        self._greeks_heading.pack(anchor="w")
        self._values.update(stat_rows(panel, [
            ("delta", "delta (shares)"), ("gamma", "gamma"), ("theta", "theta $/day"),
            ("vega", "vega $/vol pt"),
        ]))

    # -- public ---------------------------------------------------------

    def recentre(self, spot: float) -> None:
        """Rebuild the selected template (and the add-leg default) around a new spot."""
        self._new["strike"].set(f"{nearest_listed_strike(spot, spot):g}")
        self._apply_template(refresh=False)

    def state(self) -> dict:
        return {
            "legs": [
                {"type": leg.option_type, "strike": leg.strike, "dte": leg.dte,
                 "qty": leg.quantity, "premium": leg.premium}
                for leg in self._legs
            ],
            "shares": self._shares_var.get(), "template": self._template_var.get(),
            "width": self._width_var.get(), "dte": self._dte_var.get(),
            "horizon": self._horizon_var.get(),
        }

    def refresh(self) -> None:
        try:
            market = self._get_market()
            strategy = Strategy(tuple(self._legs), stock_quantity=parse_float(self._shares_var, "shares"))
            horizon = parse_float(self._horizon_var, "days forward")
            if horizon < 0:
                raise ValueError("days forward must be non-negative")
        except ValueError as exc:
            self._report(str(exc), True)
            self._show_empty(str(exc))
            return
        self._report("", False)
        self._render_legs(market)
        self._show(strategy, market, horizon)

    # -- editing --------------------------------------------------------

    def _apply_template(self, refresh: bool = True) -> None:
        try:
            spot = self._get_market().spot
            width = parse_float(self._width_var, "strike width", 0.01)
            dte = parse_float(self._dte_var, "days to expiry")
            template = STRATEGY_TEMPLATES[self._template_var.get()](spot, dte, width)
        except ValueError as exc:
            self._report(str(exc), True)
            return
        self._legs = list(template.legs)
        self._shares_var.set(f"{template.stock_quantity:g}")
        if refresh:
            self.refresh()

    def _add_leg(self) -> None:
        try:
            premium_text = self._new["premium"].get().strip()
            leg = OptionLeg(
                option_type=self._new["type"].get(),
                strike=parse_float(self._new["strike"], "strike"),
                dte=parse_float(self._new["dte"], "days"),
                quantity=parse_float(self._new["qty"], "qty"),
                premium=float(premium_text) if premium_text else None,
            )
        except ValueError as exc:
            self._report(str(exc), True)
            return
        self._legs.append(leg)
        self.refresh()

    def _remove_leg(self) -> None:
        selected = self._tree.selection()
        if selected:
            del self._legs[self._tree.index(selected[0])]
            self.refresh()

    # -- rendering ------------------------------------------------------

    def _render_legs(self, market: MarketInputs | None = None) -> None:
        self._tree.delete(*self._tree.get_children())
        for leg in self._legs:
            if leg.premium is not None:
                paid = f"{leg.premium:,.2f}"
            elif market is not None:
                paid = f"fair {leg_entry_premium(leg, market):,.2f}"
            else:
                paid = "fair"
            self._tree.insert("", "end", values=(
                f"{leg.quantity:+g}", leg.option_type, f"{leg.strike:,.2f}",
                days_label(leg.dte), paid,
            ))

    def _show(self, strategy: Strategy, market: MarketInputs, horizon: float) -> None:
        spots = spot_grid(strategy, market)
        first_expiry = strategy.expiries[0] if strategy.legs else 0.0
        last_expiry = strategy.expiries[-1] if strategy.legs else 0.0
        horizon = min(horizon, last_expiry)
        self._horizon_spin.configure(to=max(last_expiry, 1.0))

        expiry_pnl = strategy_pnl(strategy, market, spots, first_expiry)
        horizon_pnl = None
        if horizon < first_expiry:
            horizon_pnl = strategy_pnl(strategy, market, spots, horizon)
        expiry_label = f"at expiry ({days_label(first_expiry)})" if strategy.legs else "stock"

        cost = entry_cost(strategy, market)
        if len(strategy.expiries) <= 1:
            profile = expiry_profile(strategy, market)
            breakevens, approx = profile.breakevens, ""
            max_profit, max_loss = profile.max_profit, profile.max_loss
        else:
            breakevens, approx = grid_breakevens(spots, expiry_pnl), "≈ "
            max_profit, max_loss = float(np.max(expiry_pnl)), float(np.min(expiry_pnl))

        plot_strategy_pnl(
            self._figure, spots, expiry_pnl, expiry_label, market.spot, breakevens,
            horizon_pnl, f"T+{days_label(horizon)}", CHART_STYLE,
        )
        self._canvas.draw_idle()

        g = strategy_greeks(strategy, market, np.array([market.spot]), horizon)
        self._greeks_heading.configure(text=f"POSITION GREEKS · SPOT, T+{days_label(horizon)}")
        texts = {
            "entry": f"{abs(cost):,.2f} {'debit' if cost >= 0 else 'credit'}",
            "max_profit": approx + _money(max_profit),
            "max_loss": approx + _money(max_loss),
            "breakevens": approx + (", ".join(f"{b:,.2f}" for b in breakevens[:3]) or "none")
            + (" …" if len(breakevens) > 3 else ""),
            "delta": f"{g.delta[0]:+,.1f}", "gamma": f"{g.gamma[0]:+,.3f}",
            "theta": f"{g.theta[0]:+,.2f}", "vega": f"{g.vega[0]:+,.2f}",
        }
        for key, text in texts.items():
            self._values[key].configure(text=text)

    def _show_empty(self, message: str) -> None:
        for label in self._values.values():
            label.configure(text="—")
        plot_message(self._figure, message, CHART_STYLE)
        self._canvas.draw_idle()


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
            continue  # a stale/corrupt saved leg is dropped, not fatal
    return legs
