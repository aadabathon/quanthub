"""The desk: today's book, every voice's claims about it, frozen (D1, D96).

``DeskService.take`` makes one snapshot (``data/desk.py``):

1. **The book:** the newest positions export rolled forward by every trade
   since (``ActivityService.current_holdings``).
2. **Prices, point in time:** each holding's adjusted closes from the named
   source. ``as_of`` is the **last day every holding has a close**, so no
   price comes from a day another holding hadn't reached. A holding with no
   history, too little of it, or an option (priced from chains, not yet)
   is named in ``unpriced`` and left out of the book, never guessed.
3. **The voices** each speak about that book from those returns. One that
   can't (too little history, say) is logged and left out of this
   snapshot; the others still speak. The baseline (``models/baseline.py``)
   and, since D2, GARCH, regimes and the bootstrap (``models/voices.py``).
   They see two years of closes, about 500 sessions, the most Massive's
   free plan serves.
4. **Frozen:** the snapshot is stored once by its hash (``DeskStore``), and
   written as JSON to ``export_dir`` for a person or an LLM to read.
5. **One per data day:** if this exact book on this ``as_of`` is already
   stored, that snapshot is returned and nothing new is stored. A daily run
   on prices that haven't moved on (TWS closed, say) would otherwise count
   one forecast many times when the records are scored.

``record`` stores a decision made on a stored snapshot.

Runs on the task-worker thread, or from the command line
(``python -m quant_hub.snapshot``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant_hub import __version__
from quant_hub.data.desk import Book, Decision, Holding, Snapshot, VoiceOutput
from quant_hub.models.baseline import WINDOW, baseline
from quant_hub.models.voices import bootstrap, garch, regimes
from quant_hub.services.activity import ActivityService
from quant_hub.services.market_service import MarketService
from quant_hub.storage.desk_store import DeskStore

_logger = logging.getLogger(__name__)

Voice = Callable[[Book, pd.DataFrame], VoiceOutput]
VOICES: tuple[Voice, ...] = (baseline, garch, regimes, bootstrap)
LOOKBACK_DAYS = 730             # calendar days of closes fetched: ~500 sessions, two years


@dataclass(frozen=True)
class Taken:
    """A snapshot as stored, where it was written, and what was left out."""

    id: int
    snapshot: Snapshot
    path: Path | None
    silent: tuple[str, ...]        # voices that couldn't speak, and why


class DeskService:
    def __init__(self, store: DeskStore, market: MarketService, activity: ActivityService,
                 export_dir: Path | None = None, voices: Sequence[Voice] = VOICES) -> None:
        self._store, self._market, self._activity = store, market, activity
        self._export_dir = export_dir
        self._voices = tuple(voices)

    def take(self, source: str, now: datetime | None = None) -> Taken:
        """Today's snapshot from ``source``'s prices (module docstring)."""
        now = (now or datetime.now()).replace(microsecond=0)
        book, returns = self.book(source, now)
        same = self._same_day(book)
        if same is not None:
            return Taken(same[0], same[1], None, ("nothing new: this book on this day is "
                                                  f"already snapshot #{same[0]}",))
        spoken, silent = [], []
        for voice in self._voices:
            name = getattr(voice, "__name__", str(voice))
            try:
                spoken.append(voice(book, returns))
            except ValueError as exc:
                _logger.warning("desk: %s said nothing: %s", name, exc)
                silent.append(f"{name}: {exc}")
        snapshot = Snapshot(now, book.as_of, source, __version__, book, tuple(spoken))
        snapshot_id = self._store.save_snapshot(snapshot)
        return Taken(snapshot_id, snapshot, self._export(snapshot), tuple(silent))

    def book(self, source: str, now: datetime) -> tuple[Book, pd.DataFrame]:
        """The rolled-forward book priced at its ``as_of``, and its holdings'
        daily log returns up to then."""
        held = self._activity.current_holdings()
        start = now - timedelta(days=LOOKBACK_DAYS)
        closes: dict[str, pd.Series] = {}
        unpriced = []
        for symbol, units in sorted(held.units.items()):
            if symbol in held.options:
                unpriced.append(f"{symbol} (an option)")
                continue
            try:
                history = self._market.get_return_history(symbol, start, now, source)
            except Exception as exc:     # any source failure leaves the holding out, named
                unpriced.append(f"{symbol} ({exc})")
                continue
            closes[symbol] = history.frame["close"].astype(float)
        if not closes:
            raise ValueError("no holding could be priced from " + source)
        as_of = min(series.index.max() for series in closes.values()).date()
        frame = pd.DataFrame(closes).loc[:pd.Timestamp(as_of)]
        returns = np.log(frame).diff().iloc[1:]
        holdings = []
        for symbol, series in returns.items():
            if series.iloc[-WINDOW:].notna().sum() < WINDOW:
                unpriced.append(f"{symbol} (fewer than {WINDOW} sessions of closes)")
                continue
            holdings.append(Holding(symbol, held.units[symbol], float(frame[symbol].iloc[-1])))
        kept = [h.symbol for h in holdings]
        if not kept:
            raise ValueError("no holding has enough history to price the book")
        book = Book(as_of, tuple(holdings), held.cash, tuple(unpriced),
                    held.through if held.through > as_of else None)
        return book, returns[kept].dropna()

    def _same_day(self, book: Book) -> tuple[int, Snapshot] | None:
        """The stored snapshot of this exact book on this day, if any: one
        snapshot per data day, so a daily run on stale prices doesn't count
        the same forecast twice."""
        for summary in self._store.snapshots():
            if summary.as_of < book.as_of:
                break
            if summary.as_of == book.as_of:
                stored = self._store.load(summary.id)
                if stored.book == book:
                    return summary.id, stored
        return None

    def record(self, decision: Decision) -> int:
        """Store a decision on a stored snapshot (``Decision.snapshot_sha``)."""
        return self._store.save_decision(decision)

    def _export(self, snapshot: Snapshot) -> Path | None:
        if self._export_dir is None:
            return None
        self._export_dir.mkdir(parents=True, exist_ok=True)
        path = self._export_dir / f"desk-{snapshot.as_of}-{snapshot.sha256[:8]}.json"
        body = {"sha256": snapshot.sha256, "snapshot": json.loads(snapshot.to_json())}
        path.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
        return path
