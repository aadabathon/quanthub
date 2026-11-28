"""The desk: today's book, every voice's claims about it, frozen (D1, D96).

``DeskService.take`` makes one snapshot (``data/desk.py``):

1. **The book:** the newest positions export rolled forward by every trade
   since (``ActivityService.current_holdings``).
2. **Prices, point in time:** each holding's adjusted closes from the named
   source. ``as_of`` is the **last day every holding has a close**, so no
   price comes from a day another holding hadn't reached. A holding with no
   history, too little of it, or an option (priced from chains, not yet)
   is named in ``unpriced`` and left out of the book, never guessed.
   **The market** (``data/desk.MARKET``, SPY) is priced too, held or not
   (D2b), and must have its close on ``as_of`` as well. If it can't be
   priced, the book is still priced and the voices make no market claims.
3. **The voices** each speak about that book from those returns. One that
   can't (too little history, say) is logged and left out of this
   snapshot; the others still speak: the baseline (``models/baseline.py``),
   GARCH, regimes and the bootstrap (``models/voices.py``), and the tail
   (``models/tail.py``), which core.py adds with French's century.
   They see two years of closes, about 500 sessions, the most Massive's
   free plan serves.
4. **Frozen:** the snapshot is stored once by its hash (``DeskStore``), and
   written as JSON to ``export_dir`` for a person or an LLM to read.
5. **One per data day:** if this exact book on this ``as_of`` is already
   stored, that snapshot is returned and nothing new is stored. A daily run
   on prices that haven't moved on (TWS closed, say) would otherwise count
   one forecast many times when the records are scored.

``decide`` records a decision on a stored snapshot (the newest unless
named): its trades, its reasoning and, required, at least one claim of
what it expects, so it's scored like a voice (D108). ``record_decision``
stores one already built. ``decisions`` lists them.

**The tail voice** (D2c, ``models/tail.py``) reads French's market history
from the store. When the desk is given the outside-data service, ``take``
first refreshes French's files if they're a week old (one network call a
week; a failure keeps the stored copy), so the daily run keeps the century
current. Without the history the tail voice is silent and says why.

``resolve`` (D4, ``models/scoring.py``) works out what happened to every
claim whose day has passed: each subject's return, volatility and deepest
fall over the horizon, from the snapshot's own source, on one adjusted
series fetched now. A missing bar leaves it pending for the next run, and
``GIVE_UP_SESSIONS`` past due it's stored as unresolvable, with why.
``record`` scores every resolved claim, pairs each voice with the baseline,
tests the edge, checks calibration, and does it again by the market's
regime at each snapshot. A decision's expectations are scored under its
author's name.

``view`` is what the Desk page shows (D3): a stored snapshot, its voices
side by side in dollars (``models/comparison.py``), each holding's band,
and each voice's papers from the canon (``PAPERS``).

``whatif`` weighs trades on a stored snapshot (A3, ``models/weighing.py``):
the book after them at that day's closes, and every voice on both books
from the same data. The held book's data is rebuilt exactly as ``take``
built it (the same window, source and columns; a name not held is joined
the way the market is), so when nothing has moved since, the voices on the
held book reproduce the stored claims, and ``WhatIf.differs`` names any
that don't. Nothing is stored.

``scorecard`` (A5) sets each decision author, you or an agent, head to
head with every other author on the claims they share (``models/scoring.py``
section 8), overall and by regime, beside the record.

``record_run`` stores what one daily run did (``data/desk.Run``, A1), and
``runs`` lists them, so a reader (``python -m quant_hub.desk status``) can
tell a quiet desk from a broken one. ``summaries`` and ``snapshot`` read
the stored snapshots.

Runs on the task-worker thread, or from the command line
(``python -m quant_hub.snapshot``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant_hub import __version__
from quant_hub.data.desk import (
    MARKET,
    Book,
    Claim,
    Decision,
    Holding,
    Outcome,
    Run,
    Snapshot,
    VoiceOutput,
    resolves_on,
)
from quant_hub.data.literature import CanonPaper
from quant_hub.models import baseline as baseline_model
from quant_hub.models import tail as tail_model
from quant_hub.models import voices as voice_models
from quant_hub.models.baseline import WINDOW, baseline
from quant_hub.models import scoring
from quant_hub.models import weighing
from quant_hub.models.comparison import Comparison, compare, holding_bands
from quant_hub.models.voices import bootstrap, garch, regimes
from quant_hub.data.trading_calendar import trading_days
from quant_hub.services.activity import ActivityService
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.market_service import MarketService
from quant_hub.storage.canon_store import load_canon
from quant_hub.storage.desk_store import DeskStore, SnapshotSummary

_logger = logging.getLogger(__name__)

Voice = Callable[[Book, pd.DataFrame], VoiceOutput]
VOICES: tuple[Voice, ...] = (baseline, garch, regimes, bootstrap)
LOOKBACK_DAYS = 730             # calendar days of closes fetched: ~500 sessions, two years
# Each voice's canon papers, by the name it speaks under.
_DAY = datetime.min.time()
PAPERS: dict[str, tuple[str, ...]] = {baseline_model.NAME: baseline_model.PAPERS,
                                      **voice_models.PAPERS,
                                      tail_model.NAME: tail_model.PAPERS}
FRENCH_MARKET = ("french", "ff3.mkt_rf")
FRENCH_BILLS = ("french", "ff3.rf")


def century(external: ExternalDataService) -> tail_model.Century:
    """French's stored market and bills as the tail voice's century. A
    ValueError says what's missing."""
    try:
        excess = external.series(*FRENCH_MARKET).values
        bills = external.series(*FRENCH_BILLS).values
    except LookupError as exc:
        raise ValueError(f"French's market history isn't stored: {exc}") from None
    return tail_model.Century.from_series(excess, bills)


def tail_voice(external: ExternalDataService) -> Voice:
    """The tail voice, reading the century from the store each time it speaks."""
    def tail(book: Book, log_returns: pd.DataFrame) -> VoiceOutput:
        return tail_model.tail(book, log_returns, century(external))
    return tail


def default_source(massive_keyed: bool, ibkr_enabled: bool) -> str:
    """Where a snapshot's prices come from unless told: Massive when keyed
    (daily bars without TWS), else IBKR when it's on, else the store's file
    imports."""
    return "massive" if massive_keyed else "ibkr" if ibkr_enabled else "file"


@dataclass(frozen=True)
class Taken:
    """A snapshot as stored, where it was written, and what was left out."""

    id: int
    snapshot: Snapshot
    path: Path | None
    silent: tuple[str, ...]        # voices that couldn't speak, and why


@dataclass(frozen=True)
class Resolved:
    """What one ``resolve`` did."""

    stored: int                    # outcomes stored now, resolved or not
    unresolvable: int              # of them, the ones that can't be known
    pending: tuple[str, ...]       # due, not yet resolvable, and why


@dataclass(frozen=True)
class DeskRecord:
    """Every voice's record (``models/scoring.py``): the scored claims, each
    voice's series against the baseline overall and by regime, and its
    calibration."""

    scored: tuple[scoring.Scored, ...]
    series: tuple[scoring.Series, ...]
    by_regime: Mapping[str, tuple[scoring.Series, ...]]
    calibration: tuple[scoring.Calibration, ...]
    cutoffs: tuple[float, float] | None    # the regimes' volatility terciles
    resolved: int = 0                      # outcomes stored as resolved
    unresolvable: int = 0                  # outcomes that can't be known
    next_due: date | None = None           # the first claim still waiting on its day


@dataclass(frozen=True)
class DeskScorecard:
    """The record, and each decision author head to head with every other
    author (module docstring)."""

    record: DeskRecord
    judges: tuple[str, ...]                          # the decisions' authors
    duels: tuple[scoring.Duel, ...]
    by_regime: Mapping[str, tuple[scoring.Duel, ...]]


@dataclass(frozen=True)
class DeskView:
    """What the Desk page shows of one stored snapshot (module docstring).
    ``comparison`` is None when the book can't be put in dollars, and
    ``problem`` says why."""

    snapshots: tuple[SnapshotSummary, ...]          # every one stored, newest first
    id: int
    snapshot: Snapshot
    comparison: Comparison | None
    problem: str
    holdings: Mapping[str, tuple[float, ...]]       # the baseline's 21-session $ band
    papers: Mapping[str, tuple[CanonPaper, ...]]    # each voice's, from the canon
    record: DeskRecord | None = None                # every voice's record (D4b)
    regime: str | None = None                       # this snapshot's label
    decisions: tuple = ()                           # (Decision, its snapshot), newest first


@dataclass(frozen=True)
class WhatIf:
    """Trades weighed on a stored snapshot (module docstring): the voices on
    the book as held and on the book after, each as a snapshot that isn't
    stored. ``differs`` names the voices whose claims on the held book
    aren't the stored ones (data or a version moved since); ``silent`` the
    voices that couldn't speak on the book after."""

    summary: SnapshotSummary
    stored: Snapshot
    traded: weighing.Traded
    held: Snapshot
    after: Snapshot
    differs: tuple[str, ...]
    silent: tuple[str, ...]


class DeskService:
    def __init__(self, store: DeskStore, market: MarketService, activity: ActivityService,
                 export_dir: Path | None = None, voices: Sequence[Voice] = VOICES,
                 external: ExternalDataService | None = None) -> None:
        self._store, self._market, self._activity = store, market, activity
        self._external = external
        self._export_dir = export_dir
        self._voices = tuple(voices)
        self._canon = None              # read on the first view, on the worker thread

    def take(self, source: str, now: datetime | None = None) -> Taken:
        """Today's snapshot from ``source``'s prices (module docstring)."""
        now = (now or datetime.now()).replace(microsecond=0)
        if self._external is not None:
            try:
                self._external.refresh_french()      # only what's a week old
            except Exception as exc:     # the stored copy stays; the tail voice says if none
                _logger.warning("desk: French's files weren't refreshed: %s", exc)
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
        market = closes.get(MARKET)
        if market is None:
            try:
                market = self._market.get_return_history(MARKET, start, now,
                                                          source).frame["close"].astype(float)
            except Exception as exc:     # the book is priced without it; no market claims
                _logger.warning("desk: the market (%s) wasn't priced: %s", MARKET, exc)
        ends = [series.index.max() for series in closes.values()]
        if market is not None:
            ends.append(market.index.max())
        as_of = min(ends).date()
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
        columns, market_price = list(kept), None
        if market is not None:
            market = market.loc[:pd.Timestamp(as_of)]
            if market.index.max().date() == as_of:
                market_price = float(market.iloc[-1])
                if MARKET not in returns.columns:
                    returns = returns.join(np.log(market).diff().rename(MARKET), how="left")
                if MARKET not in columns:
                    columns.append(MARKET)
        book = Book(as_of, tuple(holdings), held.cash, tuple(unpriced),
                    held.through if held.through > as_of else None,
                    MARKET if market_price is not None else "", market_price)
        return book, returns[columns].dropna(subset=kept)

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

    def view(self, snapshot_id: int | None = None) -> DeskView | None:
        """A stored snapshot (the newest unless named) as the Desk page shows
        it; None when nothing is stored yet."""
        summaries = tuple(self._store.snapshots())
        if not summaries:
            return None
        chosen = snapshot_id if snapshot_id is not None else summaries[0].id
        snapshot = self._store.load(chosen)
        try:
            comparison, problem = compare(snapshot), ""
        except ValueError as exc:
            comparison, problem = None, str(exc)
        if self._canon is None:
            self._canon = load_canon()
        papers = {v.voice: tuple(self._canon.paper(p) for p in PAPERS.get(v.voice, ())
                                 if p in self._canon) for v in snapshot.voices}
        record = self.record()
        return DeskView(summaries, chosen, snapshot, comparison, problem,
                        holding_bands(snapshot), papers, record,
                        self._regime(snapshot, record.cutoffs), tuple(self.decisions()))

    def summaries(self) -> list[SnapshotSummary]:
        """Every stored snapshot, newest first."""
        return self._store.snapshots()

    def snapshot(self, snapshot_id: int | None = None) -> Snapshot:
        """One stored snapshot, the newest unless named (``DeskStoreError``
        when there's none)."""
        return self._store.load(snapshot_id)

    def whatif(self, moves: Sequence[weighing.Move],
               snapshot_id: int | None = None) -> WhatIf:
        """Weigh ``moves`` on a stored snapshot, the newest unless named
        (module docstring). A ValueError says what can't be weighed."""
        summaries = self._store.snapshots()
        if not summaries:
            raise ValueError("no snapshot to weigh trades on yet: take one first")
        summary = (summaries[0] if snapshot_id is None else
                   next((s for s in summaries if s.id == snapshot_id), None))
        if summary is None:
            raise ValueError(f"no snapshot #{snapshot_id}")
        stored = self._store.load(summary.id)
        book = stored.book
        held = {h.symbol for h in book.holdings}
        new = sorted({m.symbol for m in moves} - held)
        returns, prices = self._returns_as_taken(stored, new)
        traded = weighing.apply(book, moves, prices)
        after_names = [h.symbol for h in traded.book.holdings]
        held_names = [h.symbol for h in book.holdings]
        market = [MARKET] if book.market and MARKET in returns.columns else []
        held_returns = returns[[*held_names, *(c for c in market if c not in held_names)]
                               ].dropna(subset=held_names)
        after_returns = returns[[*after_names, *(c for c in market if c not in after_names)]
                                ].dropna(subset=after_names)
        if len(after_returns) < WINDOW:
            raise ValueError(f"the book after has fewer than {WINDOW} sessions in common")
        on_held, _ = self._speak(book, held_returns)
        on_after, silent = self._speak(traded.book, after_returns)
        was = {v.voice: v for v in stored.voices}
        differs = tuple(v.voice for v in on_held
                        if v.voice not in was or was[v.voice].claims != v.claims)
        now = stored.taken_at
        return WhatIf(summary, stored, traded,
                      Snapshot(now, stored.as_of, stored.source, __version__, book, on_held),
                      Snapshot(now, stored.as_of, stored.source, __version__, traded.book,
                               on_after), differs, tuple(silent))

    def _speak(self, book: Book, returns: pd.DataFrame) -> tuple[tuple[VoiceOutput, ...],
                                                                  list[str]]:
        spoken, silent = [], []
        for voice in self._voices:
            name = getattr(voice, "__name__", str(voice))
            try:
                spoken.append(voice(book, returns))
            except ValueError as exc:
                silent.append(f"{name}: {exc}")
        return tuple(spoken), silent

    def _returns_as_taken(self, snap: Snapshot,
                          new: Sequence[str]) -> tuple[pd.DataFrame, dict[str, float]]:
        """The daily log returns ``take`` gave the voices for ``snap`` (the
        same window and source, ``book``'s construction), with each name in
        ``new`` joined as the market is; and each new name's close on
        ``as_of``."""
        start = snap.taken_at - timedelta(days=LOOKBACK_DAYS)
        end, as_of = snap.taken_at, pd.Timestamp(snap.as_of)
        # Every name take priced, and those it read but left out for too
        # little history: they shaped the frame's dates too.
        short = [u.split(" ")[0] for u in snap.book.unpriced
                 if "sessions of closes" in u]
        closes = {}
        for symbol in sorted({h.symbol for h in snap.book.holdings} | set(short)):
            closes[symbol] = self._market.get_return_history(
                symbol, start, end, snap.source).frame["close"].astype(float)
        frame = pd.DataFrame(closes).loc[:as_of]
        returns = np.log(frame).diff().iloc[1:]
        joined = [s for s in new if s not in returns.columns]
        if snap.book.market and MARKET not in returns.columns and MARKET not in joined:
            joined.append(MARKET)
        prices = {}
        for symbol in joined:
            try:
                series = self._market.get_return_history(
                    symbol, start, end, snap.source).frame["close"].astype(float).loc[:as_of]
            except Exception as exc:     # any source failure: that name can't be weighed
                raise ValueError(f"{symbol} couldn't be priced from {snap.source}: {exc}") \
                    from None
            if series.empty or series.index.max() != as_of:
                raise ValueError(f"{symbol} has no close on {snap.as_of} to trade it at")
            prices[symbol] = float(series.iloc[-1])
            returns = returns.join(np.log(series).diff().rename(symbol), how="left")
            if symbol in new and returns[symbol].iloc[-WINDOW:].notna().sum() < WINDOW:
                raise ValueError(f"{symbol} has fewer than {WINDOW} sessions of closes")
        if MARKET in new and MARKET not in prices and snap.book.market_price:
            prices[MARKET] = snap.book.market_price
        return returns, prices

    def regime(self, snapshot: Snapshot) -> str | None:
        """The market's regime at ``snapshot``: calm, middling or stormy;
        None without French's century."""
        return self._regime(snapshot, self._cutoffs())

    def record_run(self, run: Run) -> int:
        """Store what one daily run did (module docstring)."""
        return self._store.save_run(run)

    def runs(self, limit: int | None = None) -> list[Run]:
        """The daily runs, newest first."""
        return self._store.runs(limit)

    def record_decision(self, decision: Decision) -> int:
        """Store a decision on a stored snapshot (``Decision.snapshot_sha``)."""
        return self._store.save_decision(decision)

    def decide(self, author: str, expects: Sequence[Claim], rationale: str = "",
               trades: Sequence[tuple[str, float]] = (), snapshot_id: int | None = None,
               now: datetime | None = None) -> tuple[int, Decision, SnapshotSummary]:
        """Record a decision on the newest stored snapshot, or the one named
        (module docstring). A ValueError says what's missing."""
        if not author.strip():
            raise ValueError("a decision needs its author: you, an LLM, a name")
        if not expects:
            raise ValueError("a decision needs at least one claim of what you expect, "
                             "so it can be scored")
        summaries = self._store.snapshots()
        if not summaries:
            raise ValueError("no snapshot to decide on yet: take one first")
        summary = (summaries[0] if snapshot_id is None else
                   next((s for s in summaries if s.id == snapshot_id), None))
        if summary is None:
            raise ValueError(f"no snapshot #{snapshot_id}")
        made = (now or datetime.now()).replace(microsecond=0)
        decision = Decision(made, summary.sha256, author.strip(), tuple(trades),
                            rationale.strip(), tuple(expects))
        return self._store.save_decision(decision), decision, summary

    def decisions(self) -> list[tuple[Decision, SnapshotSummary | None]]:
        """Every decision, newest first, with the snapshot it cites."""
        by_sha = {s.sha256: s for s in self._store.snapshots()}
        return [(d, by_sha.get(d.snapshot_sha)) for d in reversed(self._store.decisions())]

    # -------------------------------------------------------------- D4
    def _due(self, today: date) -> dict[tuple[int, str, int], date]:
        """(snapshot, subject, horizon) -> resolve day, for everything due
        today or before that has no outcome yet: voices' claims and
        decisions' expectations."""
        done = {(o.snapshot_id, o.subject, o.horizon) for o in self._store.outcomes()}
        due: dict[tuple[int, str, int], date] = {}
        for c in self._store.claims_due(today):
            key = (c.snapshot_id, c.claim.target.rpartition(".")[0], c.claim.horizon)
            if key not in done:
                due[key] = c.resolves_on
        by_sha = {s.sha256: s for s in self._store.snapshots()}
        for decision in self._store.decisions():
            summary = by_sha.get(decision.snapshot_sha)
            for claim in decision.expects if summary is not None else ():
                day = resolves_on(summary.as_of, claim.horizon)
                key = (summary.id, claim.target.rpartition(".")[0], claim.horizon)
                if day <= today and key not in done:
                    due[key] = day
        return due

    def resolve(self, now: datetime | None = None) -> Resolved:
        """Work out what happened to everything due (module docstring)."""
        now = (now or datetime.now()).replace(microsecond=0)
        today = now.date()
        due = self._due(today)
        if not due:
            return Resolved(0, 0, ())
        snapshots = {i: self._store.load(i) for i in {key[0] for key in due}}
        windows: dict[tuple[str, str], list[date]] = {}
        for (snapshot_id, subject, _h), day in due.items():
            snap = snapshots[snapshot_id]
            for name in self._names(snap, subject):
                lo, hi = windows.setdefault((name, snap.source), [snap.as_of, day])
                windows[(name, snap.source)] = [min(lo, snap.as_of), max(hi, day)]
        closes: dict[tuple[str, str], pd.Series | Exception] = {}
        for (name, source), (lo, hi) in windows.items():
            try:
                frame = self._market.get_return_history(
                    name, datetime.combine(lo - timedelta(days=7), _DAY),
                    datetime.combine(hi + timedelta(days=1), _DAY), source).frame
                closes[(name, source)] = frame["close"].astype(float)
            except Exception as exc:     # any source failure: pending, then named
                closes[(name, source)] = exc
        stored = unresolvable = 0
        pending = []
        for (snapshot_id, subject, h), day in sorted(due.items()):
            snap = snapshots[snapshot_id]
            try:
                outcome = self._outcome(snap, snapshot_id, subject, h, day, closes, now)
            except ValueError as exc:
                late = len(trading_days(day + timedelta(days=1), today))
                if late < scoring.GIVE_UP_SESSIONS:
                    pending.append(f"#{snapshot_id} {subject}@{h}: {exc}")
                    continue
                outcome = Outcome(snapshot_id, subject, h, "unresolvable", None, None, None,
                                  str(exc), snap.source, now)
                unresolvable += 1
            stored += self._store.save_outcome(outcome)
        _logger.info("desk: %d outcomes stored (%d unresolvable), %d pending", stored,
                     unresolvable, len(pending))
        return Resolved(stored, unresolvable, tuple(pending))

    @staticmethod
    def _names(snap: Snapshot, subject: str) -> list[str]:
        return [h.symbol for h in snap.book.holdings] if subject == "book" else [subject]

    def _outcome(self, snap: Snapshot, snapshot_id: int, subject: str, h: int, day: date,
                 closes: Mapping[tuple[str, str], pd.Series | Exception],
                 now: datetime) -> Outcome:
        """One subject's outcome (``models/scoring.py``, section 1); a
        ValueError says why it can't be had yet."""
        sessions = pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days(snap.as_of, day)])
        if len(sessions) != h + 1:
            raise ValueError(f"{len(sessions) - 1} sessions to {day}, {h} claimed")
        columns = {}
        for name in self._names(snap, subject):
            series = closes.get((name, snap.source))
            if isinstance(series, Exception) or series is None:
                raise ValueError(f"{name}: {series}")
            on_days = series.reindex(sessions)
            missing = on_days[on_days.isna()].index
            if len(missing):
                raise ValueError(f"{name} has no close on {missing[0].date()}")
            columns[name] = on_days
        frame = pd.DataFrame(columns)
        if subject == "book":
            book = snap.book
            values = scoring.path_values(frame, book.weights(), book.cash / book.value)
        else:
            values = scoring.path_values(frame, {subject: 1.0})
        ret, vol, fall = scoring.resolve_path(values)
        return Outcome(snapshot_id, subject, h, "resolved", ret, vol, fall, "", snap.source, now)

    def record(self) -> DeskRecord:
        """Every resolved claim scored, and every voice's record (module
        docstring)."""
        outcomes = {(o.snapshot_id, o.subject, o.horizon): o for o in self._store.outcomes()}
        cutoffs = self._cutoffs()
        claims = self._store.claims_due(date.max)
        waiting = [c.resolves_on for c in claims
                   if (c.snapshot_id, c.claim.target.rpartition(".")[0], c.claim.horizon)
                   not in outcomes]
        next_due = min(waiting) if waiting else None
        resolved = sum(o.status == "resolved" for o in outcomes.values())
        unresolvable = len(outcomes) - resolved
        if not outcomes:
            return DeskRecord((), (), {}, (), cutoffs, 0, 0, next_due)
        snaps: dict[int, Snapshot] = {}

        def snapshot(snapshot_id: int) -> Snapshot:
            if snapshot_id not in snaps:
                snaps[snapshot_id] = self._store.load(snapshot_id)
            return snaps[snapshot_id]

        scored = []

        def add(snapshot_id, as_of, voice, version, claim, regime):
            outcome = outcomes.get((snapshot_id, claim.target.rpartition(".")[0], claim.horizon))
            result = scoring.score(claim, outcome) if outcome is not None else None
            if result is not None:
                scored.append(scoring.Scored(snapshot_id, as_of, voice, version, claim, *result,
                                             regime))

        for c in claims:
            key = (c.snapshot_id, c.claim.target.rpartition(".")[0], c.claim.horizon)
            if key not in outcomes:
                continue
            snap = snapshot(c.snapshot_id)
            versions = {v.voice: v.version for v in snap.voices}
            add(c.snapshot_id, c.as_of, c.voice, versions.get(c.voice, ""), c.claim,
                self._regime(snap, cutoffs))
        by_sha = {s.sha256: s for s in self._store.snapshots()}
        for decision in self._store.decisions():
            summary = by_sha.get(decision.snapshot_sha)
            if summary is None:
                continue
            snap = snapshot(summary.id)
            for claim in decision.expects:
                add(summary.id, summary.as_of, decision.author, "decision", claim,
                    self._regime(snap, cutoffs))
        regimes = {s.regime for s in scored if s.regime is not None}
        return DeskRecord(tuple(scored), tuple(scoring.record(scored)),
                          {r: tuple(scoring.record(scored, r)) for r in scoring.REGIMES
                           if r in regimes},
                          tuple(scoring.calibration(scored)), cutoffs, resolved,
                          unresolvable, next_due)

    def scorecard(self) -> DeskScorecard:
        """Each decision author head to head with every other author, on
        the record's scored claims (module docstring)."""
        record = self.record()
        scored = record.scored
        judges = tuple(sorted({s.voice for s in scored if s.version == "decision"}))
        authors = sorted({s.voice for s in scored})

        def duels(regime: str | None) -> tuple[scoring.Duel, ...]:
            return tuple(d for judge in judges for other in authors if other != judge
                         for d in scoring.head_to_head(scored, judge, other, regime))
        regimes = {s.regime for s in scored if s.regime is not None}
        return DeskScorecard(record, judges, duels(None),
                             {r: duels(r) for r in scoring.REGIMES if r in regimes})

    def _cutoffs(self) -> tuple[float, float] | None:
        """The regimes' terciles from French's century, when it's stored."""
        if self._external is None:
            return None
        try:
            excess = self._external.series(*FRENCH_MARKET).values
            bills = self._external.series(*FRENCH_BILLS).values
        except LookupError:
            return None
        frame = pd.concat({"x": excess, "r": bills}, axis=1, join="inner").dropna()
        return scoring.regime_cutoffs((frame["x"] + frame["r"]).to_numpy())

    @staticmethod
    def _regime(snap: Snapshot, cutoffs: tuple[float, float] | None) -> str | None:
        """The snapshot's label: the baseline's market σ̂ at its as_of."""
        base = next((v for v in snap.voices if v.voice == scoring.BASELINE), None)
        sigma = base.parameters.get("market_sigma_daily") if base is not None else None
        annual = float(sigma) * np.sqrt(252) if sigma is not None else None
        return scoring.regime_label(annual, cutoffs)

    def _export(self, snapshot: Snapshot) -> Path | None:
        if self._export_dir is None:
            return None
        self._export_dir.mkdir(parents=True, exist_ok=True)
        path = self._export_dir / f"desk-{snapshot.as_of}-{snapshot.sha256[:8]}.json"
        body = {"sha256": snapshot.sha256, "snapshot": json.loads(snapshot.to_json())}
        path.write_text(json.dumps(body, indent=2, sort_keys=True), encoding="utf-8")
        return path
