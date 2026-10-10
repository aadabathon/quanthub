"""The Options Lab's underlying card: where the one ``MarketInputs`` comes from.

Spot and volatility are typed in, or loaded from a named source through
``services/underlying.py`` (``synthetic``, or ``ibkr`` via the SQLite-first
cache). Loading runs on the TaskRunner because the IBKR path blocks. The
subtitle says which numbers came from where; a badge says MANUAL / LOADED /
EDITED (a loaded value overridden by hand).

Loading a *different* symbol than the one the tabs' strikes were built
around emits ``recentre(spot)``, so a $385 underlying isn't priced against
strikes left over from a $100 one. Reloading the same symbol never moves
strikes the user has set.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout

from quant_hub.tasks import TaskRunner
from quant_hub.gui.components import Badge, Card, Field, SelectField, button, divider
from quant_hub.gui.pages.base import debounce
from quant_hub.gui.theme import FIELD_M, FIELD_S, SPACE_M
from quant_hub.models.black_scholes import MarketInputs
from quant_hub.services.market_service import MarketService
from quant_hub.services.underlying import UnderlyingSnapshot, load_underlying

MANUAL = "manual"
Reporter = Callable[[str, bool], None]


class UnderlyingCard(Card):
    changed = Signal()            # the MarketInputs changed (typed or loaded)
    recentre = Signal(float)      # a new symbol loaded at this spot

    def __init__(self, market_service: MarketService, tasks: TaskRunner, saved: dict,
                 report: Reporter) -> None:
        super().__init__("Underlying", "")
        self._market, self._tasks, self._report = market_service, tasks, report
        self._snapshot: UnderlyingSnapshot | None = None
        self._centred_on: str | None = saved.get("centred_on")
        self._badge = Badge()
        self.add_action(self._badge)

        sources = [MANUAL, *market_service.source_names]
        source = saved.get("source", MANUAL)
        self._source = SelectField("Source", sources, source if source in sources else MANUAL,
                                   FIELD_M)
        self._symbol = Field("Symbol", saved.get("symbol", "SPY"), FIELD_S, uppercase=True)
        self._lookback = Field("Vol lookback (days)", saved.get("lookback", "365"), FIELD_M)
        self._load_button = button("Load", icon_name="ph.download-simple", on_click=self._load)
        self._spot = Field("Spot", saved.get("spot", "100"), FIELD_S)
        self._vol = Field("Vol %", saved.get("vol", "20"), FIELD_S)
        self._rate = Field("Rate %", saved.get("rate", "5"), FIELD_S)
        self._div = Field("Div yield %", saved.get("div", "0"), FIELD_S)

        row = QHBoxLayout()
        row.setSpacing(SPACE_M)
        for widget in (self._source, self._symbol, self._lookback):
            row.addWidget(widget, 0, Qt.AlignBottom)
        row.addWidget(self._load_button, 0, Qt.AlignBottom)
        row.addSpacing(SPACE_M)
        row.addWidget(divider(vertical=True))
        row.addSpacing(SPACE_M)
        for widget in (self._spot, self._vol, self._rate, self._div):
            row.addWidget(widget, 0, Qt.AlignBottom)
        row.addStretch(1)
        self.body.addLayout(row)

        live = debounce(self, self._edited)
        for field in (self._spot, self._vol, self._rate, self._div):
            field.edit.textEdited.connect(lambda _t: live())
            field.committed.connect(self._edited)
        for field in (self._symbol, self._lookback):
            field.committed.connect(self._load)
        self._source.select.current_changed.connect(lambda _s: self._source_changed())
        self._source_changed(notify=False)

    # -- public ---------------------------------------------------------
    def market(self) -> MarketInputs:
        """Current inputs; raises ValueError (and flags the field)."""
        return MarketInputs(spot=self._spot.number(), rate=self._rate.number(scale=0.01),
                            sigma=self._vol.number(scale=0.01),
                            dividend_yield=self._div.number(scale=0.01))

    def state(self) -> dict:
        return {"source": self._source.current(), "symbol": self._symbol.text(),
                "lookback": self._lookback.text(), "spot": self._spot.text(),
                "vol": self._vol.text(), "rate": self._rate.text(), "div": self._div.text(),
                "centred_on": self._centred_on}

    # -- events ---------------------------------------------------------
    def _source_changed(self, notify: bool = True) -> None:
        manual = self._source.current() == MANUAL
        for widget in (self._symbol, self._lookback, self._load_button):
            widget.setEnabled(not manual)
        if manual:
            self._snapshot = None
        self._update_provenance()
        if notify and not manual:
            self._load()

    def _edited(self) -> None:
        self._update_provenance()
        self.changed.emit()

    def _load(self) -> None:
        source = self._source.current()
        if source == MANUAL:
            return
        try:
            symbol = self._symbol.required_text()
            lookback = self._lookback.number(integer=True, minimum=5)
        except ValueError as exc:
            self._report(str(exc), True)
            return
        self._load_button.setEnabled(False)
        self._report(f"Loading {symbol} from {source}…", False)
        self._tasks.submit(lambda: load_underlying(self._market, symbol, source, lookback),
                           on_done=self._on_loaded, on_error=self._on_load_error)

    def _on_loaded(self, snapshot: UnderlyingSnapshot) -> None:
        self._snapshot = snapshot
        self._load_button.setEnabled(True)
        self._spot.set_text(f"{snapshot.spot:.2f}")
        self._vol.set_text(f"{snapshot.realized_vol * 100:.2f}")
        self._update_provenance()
        message = f"Loaded {snapshot.symbol} from {snapshot.source}"
        if snapshot.symbol != self._centred_on:
            self._centred_on = snapshot.symbol
            self.recentre.emit(snapshot.spot)
            message += " · strikes re-centred on spot"
        self._report(message, False)
        self.changed.emit()

    def _on_load_error(self, exc: Exception) -> None:
        self._load_button.setEnabled(True)
        self._report(f"Load failed: {exc}", True)

    def _update_provenance(self) -> None:
        snap = self._snapshot
        if snap is None:
            manual = self._source.current() == MANUAL
            self._badge.set_badge("MANUAL" if manual else "NOT LOADED", "neutral")
            self.set_subtitle("Manual inputs · vol is the Black–Scholes σ you assume"
                              if manual else "Press Load to fetch spot and realized vol")
            return
        edited = (self._spot.text() != f"{snap.spot:.2f}"
                  or self._vol.text() != f"{snap.realized_vol * 100:.2f}")
        self._badge.set_badge("EDITED" if edited else "LOADED", "warning" if edited else "info")
        self.set_subtitle(
            f"{snap.symbol} · {snap.source} · spot: {snap.spot_basis} "
            f"{snap.spot_time:%Y-%m-%d %H:%M} · vol: realized over {snap.returns_used} daily "
            f"returns {snap.history_start:%Y-%m-%d} → {snap.history_end:%Y-%m-%d}"
            + (" · edited by hand" if edited else ""))
