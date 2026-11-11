"""The Options Lab's underlying card: where the one ``MarketInputs`` comes from.

Spot and volatility are typed in, or loaded from a named source through
``services/underlying.py`` (``synthetic``, or ``ibkr`` via the SQLite-first
cache). Loading runs on the TaskRunner because the IBKR path blocks. The
subtitle says which numbers came from where; a badge says MANUAL / LOADED /
EDITED (a loaded value overridden by hand).

Volatility (C4): "Flat σ" uses the card's one σ, rate and dividend yield
for every contract. "Surface" loads the newest saved chain for the symbol
(fetched in the Volatility Lab), fits it, and prices every contract from
the surface at its own strike and expiry: implied vol, and that expiry's
parity carry (``models/market_surface``). Spot becomes the chain's, the one
its forwards were measured against, and the σ/rate/dividend fields grey
out because nothing uses them.

Loading a different symbol than the one the tabs' strikes were built
around, or the same symbol more than ``RECENTRE_MOVE`` (20%) from the spot
they were built at, emits ``recentre(spot)``. So a $771 underlying isn't
priced against strikes left over from a $385 one, even when both are
called "SPY" (synthetic and real). An ordinary reload of the same symbol
never moves strikes the user has set.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout

from quant_hub.tasks import TaskRunner
from quant_hub.gui.components import (
    Badge,
    Card,
    Field,
    SegmentedControl,
    SelectField,
    button,
    divider,
)
from quant_hub.gui.pages.base import debounce
from quant_hub.gui.theme import FIELD_M, FIELD_S, SPACE_M
from quant_hub.models.black_scholes import MarketInputs
from quant_hub.models.market_surface import MarketSurface
from quant_hub.services.market_service import MarketService
from quant_hub.services.option_chains import ChainService, load_market_surface
from quant_hub.services.underlying import UnderlyingSnapshot, load_underlying

MANUAL = "manual"
Reporter = Callable[[str, bool], None]

# A spot move this large leaves template strikes (a few percent apart) deep
# in the wings, so the tabs rebuild them even for the same symbol.
RECENTRE_MOVE = 0.20


FLAT, SURFACE = "Flat σ", "Surface"


class UnderlyingCard(Card):
    changed = Signal()            # the MarketInputs changed (typed or loaded)
    recentre = Signal(float)      # a new symbol loaded at this spot

    def __init__(self, market_service: MarketService, chains: ChainService, tasks: TaskRunner,
                 saved: dict, report: Reporter) -> None:
        super().__init__("Underlying", "")
        self._market, self._tasks, self._report = market_service, tasks, report
        self._chains = chains
        self._snapshot: UnderlyingSnapshot | None = None
        self._surface: MarketSurface | None = None
        self._surface_snapshot: int | None = None
        self._vol_modes = SegmentedControl([FLAT, SURFACE], FLAT)
        self._vol_modes.setToolTip("Flat σ: one vol, rate and dividend yield for every "
                                   "contract. Surface: each contract's implied vol and "
                                   "carry from the newest saved chain.")
        self._vol_modes.changed.connect(self._on_vol_mode)
        self.add_action(self._vol_modes)
        # Where the strikes were last built: the symbol, and the spot level.
        self._centred_on: str | None = saved.get("centred_on")
        self._centred_at: float | None = saved.get("centred_at")
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
        if saved.get("vol_mode") == SURFACE:
            self._vol_modes.set_current(SURFACE)
            self._on_vol_mode(SURFACE)

    # -- public ---------------------------------------------------------
    def market(self) -> MarketInputs:
        """Current inputs; raises ValueError (and flags the field)."""
        return MarketInputs(spot=self._spot.number(), rate=self._rate.number(scale=0.01),
                            sigma=self._vol.number(scale=0.01),
                            dividend_yield=self._div.number(scale=0.01))

    def surface(self) -> MarketSurface | None:
        """The market surface every contract is priced from, or None (flat σ)."""
        return self._surface

    def state(self) -> dict:
        return {"vol_mode": SURFACE if self._surface is not None else FLAT,
                "source": self._source.current(), "symbol": self._symbol.text(),
                "lookback": self._lookback.text(), "spot": self._spot.text(),
                "vol": self._vol.text(), "rate": self._rate.text(), "div": self._div.text(),
                "centred_on": self._centred_on, "centred_at": self._centred_at}

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
        if self._maybe_recentre(snapshot.symbol, snapshot.spot):
            message += " · strikes re-centred on spot"
        self._report(message, False)
        self.changed.emit()

    def _on_vol_mode(self, mode: str) -> None:
        if mode == FLAT:
            self._surface, self._surface_snapshot = None, None
            self._show_surface_fields()
            self._update_provenance()
            self.changed.emit()
            return
        try:
            symbol = self._symbol.required_text()
        except ValueError as exc:
            self._vol_modes.set_current(FLAT)
            self._report(str(exc), True)
            return
        self._vol_modes.setEnabled(False)
        self._report(f"Fitting the newest saved {symbol} chain…", False)
        self._tasks.submit(lambda: load_market_surface(self._chains, symbol),
                           on_done=self._on_surface, on_error=self._on_surface_error)

    def _on_surface(self, result: tuple[MarketSurface, int]) -> None:
        self._surface, self._surface_snapshot = result
        self._vol_modes.setEnabled(True)
        self._spot.set_text(f"{self._surface.spot:.2f}")
        self._show_surface_fields()
        self._update_provenance()
        message = (f"Pricing from the {self._surface.symbol} surface as of "
                   f"{self._surface.as_of_text}")
        if self._maybe_recentre(self._surface.symbol, self._surface.spot):
            message += " · strikes re-centred on spot"
        self._report(message, False)
        self.changed.emit()

    def _maybe_recentre(self, symbol: str, spot: float) -> bool:
        """Rebuild the strikes around ``spot`` if they were built for another
        symbol, or for a spot level far from this one.

        The symbol alone isn't enough: synthetic "SPY" and real SPY share a
        name but not a price, and strikes left from one would sit deep in the
        other's wings (and, on a surface, in its extrapolated region).
        """
        moved = (self._centred_at is None
                 or abs(math.log(spot / self._centred_at)) > math.log(1 + RECENTRE_MOVE))
        if symbol == self._centred_on and not moved:
            return False
        self._centred_on, self._centred_at = symbol, spot
        self.recentre.emit(spot)
        return True

    def _on_surface_error(self, exc: Exception) -> None:
        self._vol_modes.setEnabled(True)
        self._vol_modes.set_current(FLAT)
        hint = (" Fetch one in the Volatility Lab (Market chain mode) first."
                if "no saved" in str(exc) else "")
        self._report(f"No surface: {exc}.{hint}", True)

    def _show_surface_fields(self) -> None:
        on_surface = self._surface is not None
        for field in (self._vol, self._rate, self._div):
            field.setEnabled(not on_surface)
            field.setToolTip("From the surface: each contract's own implied vol and carry"
                             if on_surface else "")

    def _on_load_error(self, exc: Exception) -> None:
        self._load_button.setEnabled(True)
        self._report(f"Load failed: {exc}", True)

    def _update_provenance(self) -> None:
        surface = self._surface
        if surface is not None:
            edited = self._spot.text() != f"{surface.spot:.2f}"
            self._badge.set_badge("SURFACE", "warning" if edited else "info")
            self.set_subtitle(
                f"{surface.symbol} · vol and carry from the {surface.source} chain as of "
                f"{surface.as_of_text} (snapshot #{self._surface_snapshot}) · spot "
                + ("edited by hand" if edited else "is that chain's"))
            return
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
