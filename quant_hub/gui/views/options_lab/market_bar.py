"""The Options Lab's underlying strip: where MarketInputs come from.

Every tab prices off one ``MarketInputs``. Spot and volatility are typed
in, or loaded from a named source through ``services/underlying.py``:
``synthetic`` (a reproducible GBM world) or ``ibkr`` (SQLite-first cache,
D17/D20). Loading runs on the TaskRunner worker because the IBKR path
blocks. The provenance line says which numbers came from where, and flags
them as edited once the user overrides a loaded value.

Loading a *different* symbol than the one the tabs' strikes were built
around calls ``on_recentre(spot)``, so a $385 underlying is not priced
against strikes left over from a $100 one. Reloading the same symbol (a
refresh) never moves strikes the user has set.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.theme import SPACE_S, SPACE_XS
from quant_hub.gui.views.options_lab.common import Reporter, combo, entry, parse_float
from quant_hub.models.black_scholes import MarketInputs
from quant_hub.services.market_service import MarketService
from quant_hub.services.underlying import UnderlyingSnapshot, load_underlying

MANUAL = "manual"


class MarketBar(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        market_service: MarketService,
        tasks: TaskRunner,
        saved: dict,
        on_change: Callable[[], None],
        on_recentre: Callable[[float], None],
        report: Reporter,
    ) -> None:
        super().__init__(parent, style="Card.TFrame", padding=(SPACE_S, SPACE_XS, SPACE_S, SPACE_S))
        self._market, self._tasks = market_service, tasks
        self._on_change, self._on_recentre, self._report = on_change, on_recentre, report
        self._snapshot: UnderlyingSnapshot | None = None
        self._centred_on: str | None = saved.get("centred_on")

        sources = [MANUAL, *market_service.source_names]
        source = saved.get("source", MANUAL)
        self._source_var = tk.StringVar(value=source if source in sources else MANUAL)
        self._symbol_var = tk.StringVar(value=saved.get("symbol", "SPY"))
        self._lookback_var = tk.StringVar(value=saved.get("lookback", "365"))
        self._spot_var = tk.StringVar(value=saved.get("spot", "100"))
        self._vol_var = tk.StringVar(value=saved.get("vol", "20"))
        self._rate_var = tk.StringVar(value=saved.get("rate", "5"))
        self._div_var = tk.StringVar(value=saved.get("div", "0"))

        ttk.Label(self, text="UNDERLYING", style="CardHeading.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=SPACE_S, pady=(2, 4)
        )
        combo(self, "source", self._source_var, sources, 1, 0, width=9,
              on_commit=self._source_changed)
        self._symbol_entry = entry(self, "symbol", self._symbol_var, 1, 2, width=7,
                                   on_commit=self._load)
        self._lookback_entry = entry(self, "vol lookback days", self._lookback_var, 1, 4,
                                     width=5, on_commit=self._load)
        self._load_button = ttk.Button(self, text="Load", command=self._load)
        self._load_button.grid(row=1, column=6, sticky="w", padx=(SPACE_XS, SPACE_S))

        ttk.Separator(self, orient="vertical").grid(row=1, column=7, sticky="ns", padx=SPACE_S)
        entry(self, "spot", self._spot_var, 1, 8, width=9, on_commit=self._edited)
        entry(self, "vol %", self._vol_var, 1, 10, width=6, on_commit=self._edited)
        entry(self, "rate %", self._rate_var, 1, 12, width=6, on_commit=self._edited)
        entry(self, "div yield %", self._div_var, 1, 14, width=6, on_commit=self._edited)

        self._provenance = ttk.Label(self, text="", style="Muted.TLabel")
        self._provenance.grid(row=2, column=0, columnspan=16, sticky="w", padx=SPACE_S, pady=(2, 0))
        self._source_changed(notify=False)

    # -- public ---------------------------------------------------------

    def market(self) -> MarketInputs:
        """Current inputs; raises ValueError with a user-facing message."""
        return MarketInputs(
            spot=parse_float(self._spot_var, "spot"),
            rate=parse_float(self._rate_var, "rate", 0.01),
            sigma=parse_float(self._vol_var, "vol", 0.01),
            dividend_yield=parse_float(self._div_var, "dividend yield", 0.01),
        )

    def state(self) -> dict:
        return {
            "source": self._source_var.get(), "symbol": self._symbol_var.get(),
            "lookback": self._lookback_var.get(), "spot": self._spot_var.get(),
            "vol": self._vol_var.get(), "rate": self._rate_var.get(), "div": self._div_var.get(),
            "centred_on": self._centred_on,
        }

    # -- events ---------------------------------------------------------

    def _source_changed(self, notify: bool = True) -> None:
        manual = self._source_var.get() == MANUAL
        for widget in (self._symbol_entry, self._lookback_entry, self._load_button):
            widget.configure(state="disabled" if manual else "normal")
        if manual:
            self._snapshot = None
        self._update_provenance()
        if notify and not manual:
            self._load()

    def _edited(self) -> None:
        self._update_provenance()
        self._on_change()

    def _load(self) -> None:
        source = self._source_var.get()
        if source == MANUAL:
            return
        symbol = self._symbol_var.get()
        try:
            lookback = int(self._lookback_var.get())
        except ValueError:
            self._report("vol lookback days must be a whole number", True)
            return
        self._load_button.configure(state="disabled")
        self._report(f"loading {symbol.strip().upper()} from {source}…", False)
        self._tasks.submit(
            lambda: load_underlying(self._market, symbol, source, lookback),
            on_done=self._on_loaded,
            on_error=self._on_load_error,
        )

    def _on_loaded(self, snapshot: UnderlyingSnapshot) -> None:
        self._snapshot = snapshot
        self._load_button.configure(state="normal")
        self._spot_var.set(f"{snapshot.spot:.2f}")
        self._vol_var.set(f"{snapshot.realized_vol * 100:.2f}")
        self._update_provenance()
        message = f"loaded {snapshot.symbol} from {snapshot.source}"
        if snapshot.symbol != self._centred_on:
            self._centred_on = snapshot.symbol
            self._on_recentre(snapshot.spot)
            message += " · strikes re-centred on spot"
        self._report(message, False)
        self._on_change()

    def _on_load_error(self, exc: Exception) -> None:
        self._load_button.configure(state="normal")
        self._report(f"load failed: {exc}", True)

    def _update_provenance(self) -> None:
        snap = self._snapshot
        if snap is None:
            self._provenance.configure(
                text="manual inputs · vol is the Black-Scholes σ you assume"
                if self._source_var.get() == MANUAL
                else "press Load to fetch spot and realized vol from the source"
            )
            return
        edited = (
            self._spot_var.get() != f"{snap.spot:.2f}"
            or self._vol_var.get() != f"{snap.realized_vol * 100:.2f}"
        )
        self._provenance.configure(
            text=(
                f"{snap.symbol} · {snap.source} · spot: {snap.spot_basis} "
                f"{snap.spot_time:%Y-%m-%d %H:%M} · vol: realized, {snap.returns_used} daily "
                f"returns {snap.history_start:%Y-%m-%d}→{snap.history_end:%Y-%m-%d}"
                + (" · EDITED by hand" if edited else "")
            )
        )
