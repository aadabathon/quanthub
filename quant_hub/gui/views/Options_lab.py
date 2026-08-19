"""Options Lab: a bare-bones Black-Scholes calculator.

No options-data source is wired in (no free API covers it) -- every input is
typed in by hand and priced instantly on the GUI thread. Black-Scholes is
closed-form (no loop, no I/O), so this doesn't need TaskRunner the way
Simulator's Monte Carlo does.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from quant_hub.gui.theme import FONT_MONO, FONT_MONO_LG, PALETTE, SPACE_L, SPACE_S, SPACE_XS
from quant_hub.gui.views.base import View
from quant_hub.services.Options.options_service import BlackScholesInputs, OptionService
from quant_hub.storage.state_store import ViewStateStore

_STATE_KEY = "options_lab"


class OptionsLabView(View):
    def __init__(
        self,
        parent: tk.Misc,
        option_service: OptionService,
        state_store: ViewStateStore,
    ) -> None:
        super().__init__(parent)
        self._service = option_service
        self._state_store = state_store

        saved = state_store.load(_STATE_KEY)
        self._initial = {
            "spot": saved.get("spot", "100"),
            "strike": saved.get("strike", "100"),
            "dte": saved.get("dte", "30"),
            "rate": saved.get("rate", "5"),
            "sigma": saved.get("sigma", "20"),
            "option_type": saved.get("option_type", "call"),
        }

        greeks = {}
        self._greek_vars = {
            "Delta": tk.StringVar(value="—"),
            "Gamma": tk.StringVar(value="—"),
            "Theta": tk.StringVar(value="—"),
            "Vega": tk.StringVar(value="—"),
            "Rho": tk.StringVar(value="—"),
        }

        card = ttk.Frame(self, style="Card.TFrame", padding=SPACE_L)
        card.place(relx=0.03, rely=0.05, anchor="nw")

        ttk.Label(card, text="Black-Scholes Calculator", style="ViewTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, SPACE_S)
        )

        i = self._initial
        self._spot_var = self._field(card, "Spot (S)", i["spot"], 1)
        self._strike_var = self._field(card, "Strike (K)", i["strike"], 2)
        self._dte_var = self._field(card, "Days to expiration", i["dte"], 3)
        self._rate_var = self._field(card, "Risk-free rate %", i["rate"], 4)
        self._sigma_var = self._field(card, "Volatility %", i["sigma"], 5)

        ttk.Label(card, text="Type", style="Field.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, SPACE_S), pady=SPACE_XS
        )
        self._type_var = tk.StringVar(value=i["option_type"])
        ttk.Combobox(
            card, textvariable=self._type_var, values=["call", "put"],
            state="readonly", width=10,
        ).grid(row=6, column=1, sticky="w", pady=SPACE_XS)

        ttk.Button(
            card, text="Calculate", style="Accent.TButton", command=self._calculate,
        ).grid(row=7, column=0, columnspan=2, sticky="ew", pady=(SPACE_S, 0))

        self._result_label = tk.Label(
            card, text="—", font=FONT_MONO_LG,
            background=PALETTE.surface, foreground=PALETTE.text,
        )
        self._result_label.grid(row=8, column=0, columnspan=2, pady=(SPACE_S, 0))
        self._error_label = ttk.Label(card, text="", style="Muted.TLabel")
        self._error_label.grid(row=10, column=0, columnspan=2)
        
        for i, (name, var) in enumerate(self._greek_vars.items()):
            ttk.Label(
                card,
                text=name,
                style="Field.TLabel",
            ).grid(
                row=11 + i,
                column=0,
                sticky="w",
                padx=(0, SPACE_S),
                pady=SPACE_XS,
            )

            ttk.Label(
                card,
                textvariable=var,
                style="Field.TLabel",
            ).grid(
                row=11 + i,
                column=1,
                sticky="w",
                pady=SPACE_XS,
            )

    def _field(self, parent: ttk.Frame, caption: str, initial: str, row: int) -> tk.StringVar:
        ttk.Label(parent, text=caption, style="Field.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, SPACE_S), pady=SPACE_XS
        )
        var = tk.StringVar(value=initial)
        ttk.Entry(parent, textvariable=var, width=12, font=FONT_MONO).grid(
            row=row, column=1, sticky="w", pady=SPACE_XS
        )
        return var

    def _calculate(self) -> None:
        try:
            spot = float(self._spot_var.get())
            strike = float(self._strike_var.get())
            dte = float(self._dte_var.get())
            rate = float(self._rate_var.get()) / 100.0
            sigma = float(self._sigma_var.get()) / 100.0
        except ValueError:
            self._show_error("inputs must be numeric")
            return

        try:
            inputs = BlackScholesInputs(
                spot=spot, strike=strike, dte=dte, rate=rate, sigma=sigma,
                option_type=self._type_var.get(),
            )
        except ValueError as exc:
            self._show_error(str(exc))
            return
        
        greeks = self._service.greeks(inputs)
        price = self._service.price(inputs)
        self._result_label.configure(text=f"{price:,.4f}")
        self._error_label.configure(text="")

        self._greek_vars["Delta"].set(f"{greeks['delta']:.4f}")
        self._greek_vars["Gamma"].set(f"{greeks['gamma']:.6f}")
        self._greek_vars["Theta"].set(f"{greeks['theta']:.4f}")
        self._greek_vars["Vega"].set(f"{greeks['vega']:.4f}")
        self._greek_vars["Rho"].set(f"{greeks['rho']:.4f}")

    def _show_error(self, message: str) -> None:
        self._result_label.configure(text="—")
        self._error_label.configure(text=message, foreground=PALETTE.critical)

    def on_hide(self) -> None:
        self._state_store.save(
            _STATE_KEY,
            {
                "spot": self._spot_var.get(),
                "strike": self._strike_var.get(),
                "dte": self._dte_var.get(),
                "rate": self._rate_var.get(),
                "sigma": self._sigma_var.get(),
                "option_type": self._type_var.get(),
            },
        )