"""Activity: a broker's transaction history, imported, kept and checked (TR1–TR3, D90–D94).

``ActivityService.import_activity`` reads one or more history files
(``importers/activity_csv.py``) and keeps their transactions
(``storage/activity_store.py``):

* **All or nothing.** Every file is read before any is stored. If any row of
  any file fails its checks, nothing is stored, and the error lists every
  bad row with its file and line, so one pass fixes them all.
* **Overlap is safe.** Exports covering the same days, or the same file
  twice, add only transactions the store doesn't hold yet (the occurrence
  rule in ``data/activity.py``). Each file's result says how many of its
  rows were new, and whether that exact file was imported before.
* **Unmapped wording is named.** A broker's action its layout doesn't map
  yet is stored as ``other`` (``importers/activity_csv.py``), and each
  file's result lists that wording, so it can be mapped from the export.

``ActivityService.reconcile`` replays the history against the positions
snapshots imported from files (``models/ledger.py``): the newest import of
each day, since two snapshots of one day can't be told apart by the
history. A snapshot of a broker's live accounts (``From IBKR``) is left
out: its accounts aren't the history's. Every other snapshot came from a
file, including those imported before files were hashed (schema v10),
which have no hash.

``current_holdings`` is the book as of the history's last day: the newest
file snapshot, rolled forward by every transaction after its day (the
export taken after its day's trades, the replay's tie-break), summed by
symbol across accounts. Its cash is the snapshot's cash plus every amount
since; the replay doesn't reconcile cash, so this cash is only as good as
that.

``round_trips`` and ``basis_checks`` match every sale to the purchase it
closed (``models/lots.py``), FIFO, and check the open lots' cost against
the same file snapshots' cost-basis column. A symbol those snapshots hold
as cash is cash.

``timing`` measures how well each trade was timed (``models/timing.py``,
TR4): it reads the traded symbols' daily bars and the benchmark's from a
named source, and names every symbol it couldn't get. It's the one method
here that reads prices; nothing touches a broker.

Runs on the task-worker thread (file and database I/O).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from quant_hub.data.activity import ActivityFile, Kind, Transaction
from quant_hub.importers.activity_csv import ActivityFileError, read_activity_csv
from quant_hub.data.positions import Holding, PositionSnapshot
from quant_hub.models.ledger import Reconciliation, reconcile
from quant_hub.models.lots import BasisCheck, RoundTrip, basis_checks, round_trips
from quant_hub.models.timing import TimingReport, timing
from quant_hub.data.types import PriceBasis
from quant_hub.services.accounts import SOURCE as LIVE_ACCOUNTS
from quant_hub.storage.activity_store import ActivityImportSummary, ActivityStore
from quant_hub.storage.position_store import PositionStore


class ActivityImportError(ValueError):
    """Nothing was stored; ``problems`` says why, one line each."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = tuple(problems)
        count = len(self.problems)
        super().__init__(f"nothing was imported: {count} problem{'s' * (count != 1)}\n"
                         + "\n".join(self.problems))


@dataclass(frozen=True)
class FileImported:
    """One file of an import: what it held and what was new."""

    summary: ActivityImportSummary
    seen_before: bool              # the same file (by hash) was imported earlier
    unrecognized: tuple[str, ...] = ()   # wording read as "other", once each

    @property
    def already_stored(self) -> int:
        return self.summary.rows - self.summary.added


@dataclass(frozen=True)
class Holdings:
    """The book rolled forward from an export (module docstring)."""

    base: str                      # the export it starts from
    base_day: date
    through: date                  # the last day of history applied
    units: dict[str, float]        # by symbol across accounts; options by contract
    options: frozenset[str]
    cash: float


class ActivityService:
    def __init__(self, store: ActivityStore, positions: PositionStore | None = None) -> None:
        self._store = store
        self._positions = positions

    def import_activity(self, paths: str | Path | Sequence[str | Path]) -> list[FileImported]:
        """Read every file, then store them all, or none (module docstring).
        Raises ``ActivityImportError``."""
        if isinstance(paths, (str, Path)):
            paths = [paths]
        paths = [Path(p) for p in paths]
        if not paths:
            raise ActivityImportError(["no files to import"])
        files: list[ActivityFile] = []
        problems: list[str] = []
        for path in paths:
            try:
                file = read_activity_csv(path)
            except ActivityFileError as exc:
                problems.append(f"{path.name}: {exc}")
                continue
            problems.extend(f"{file.name} line {p.line}: {p.message}" for p in file.problems)
            files.append(file)
        if problems:
            raise ActivityImportError(problems)
        results = []
        for path, file in zip(paths, files):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            seen = bool(self._store.with_hash(digest))
            results.append(FileImported(self._store.save(file, digest), seen, file.unrecognized))
        return results

    def transactions(self, account: str | None = None, start: date | None = None,
                     end: date | None = None) -> list[Transaction]:
        """Stored transactions by trade date (``ActivityStore.transactions``)."""
        return self._store.transactions(account, start, end)

    def imports(self) -> list[ActivityImportSummary]:
        """Every import, newest first."""
        return self._store.imports()

    def reconcile(self) -> Reconciliation:
        """The history against the file snapshots, one per day, the newest
        import of each (module docstring)."""
        return reconcile(self._store.transactions(), self._file_snapshots())

    def round_trips(self) -> tuple[RoundTrip, ...]:
        """Every round trip, FIFO, oldest first (``models/lots.py``)."""
        snapshots = self._file_snapshots() if self._positions is not None else []
        cash = {p.symbol for s in snapshots for p in s.positions if p.kind is Holding.CASH}
        return round_trips(self._store.transactions(), cash)

    def basis_checks(self) -> tuple[BasisCheck, ...]:
        """FIFO and average cost against each file snapshot's cost basis."""
        return basis_checks(self._store.transactions(), self._file_snapshots())

    def current_holdings(self) -> Holdings:
        """The newest export rolled forward by the history (module docstring)."""
        snapshots = self._file_snapshots()
        if not snapshots:
            raise ValueError("no positions export imported on Portfolio to start from")
        base = max(snapshots, key=lambda s: s.as_of)
        base_day = base.as_of.date()
        funds = {p.symbol for p in base.positions if p.kind is Holding.CASH}
        units: dict[str, float] = {}
        options = {p.symbol for p in base.positions if p.kind is Holding.OPTION}
        cash = 0.0
        for p in base.positions:
            if p.kind is Holding.CASH:
                cash += p.market_value or 0.0
            else:
                units[p.symbol] = units.get(p.symbol, 0.0) + p.quantity
        through = base_day
        for t in self._store.transactions(start=base_day + timedelta(days=1)):
            if t.kind is Kind.OTHER:
                continue
            through = max(through, t.trade_date)
            if t.quantity and t.symbol in funds:
                continue                         # cash into cash
            cash += t.amount
            if t.quantity:
                units[t.symbol] = units.get(t.symbol, 0.0) + t.quantity
                if t.option is not None:
                    options.add(t.symbol)
        held = {s: q for s, q in units.items() if abs(q) > 1e-9}
        return Holdings(base.name, base_day, through, held, frozenset(options & set(held)), cash)

    def timing(self, market, source: str, benchmark: str = "SPY",
               now: datetime | None = None) -> tuple[TimingReport, dict[str, str]]:
        """How well each trade was timed (module docstring), and each symbol
        whose bars couldn't be had, with why. ``market`` is a MarketService."""
        history = self._store.transactions()
        if not history:
            raise ValueError("no history imported yet")
        snapshots = self._file_snapshots() if self._positions is not None else []
        cash = {p.symbol for s in snapshots for p in s.positions if p.kind is Holding.CASH}
        pieces = [p for trip in round_trips(history, cash) for p in trip.pieces]
        symbols = sorted({p.symbol for p in pieces if not p.option}
                         | {t.symbol for t in history if t.option is None and t.price is not None
                            and t.kind in (Kind.BUY, Kind.SELL, Kind.SHORT, Kind.COVER)})
        # Through yesterday: the store already covers it, so nothing is fetched
        # again, and timing has no use for today's unfinished session.
        end = datetime.combine((now or datetime.now()).date() - timedelta(days=1),
                               datetime.min.time())
        start = datetime.combine(min(t.trade_date for t in history), datetime.min.time())             - timedelta(days=400)
        bars, missing = {}, {}
        for symbol in [*symbols, benchmark]:
            try:
                history_bars = market.get_history(symbol, start, end, source,
                                                  basis=PriceBasis.TRADED)
                bars[symbol] = history_bars.frame
            except Exception as exc:     # any source failure leaves the symbol out, named
                missing[symbol] = str(exc)
        bench = bars.pop(benchmark, None)
        report = timing(pieces, history, bars, None if bench is None else bench["close"])
        return report, missing

    def _file_snapshots(self) -> list[PositionSnapshot]:
        """The snapshots imported from files (not a live broker's), the newest
        import of each day."""
        if self._positions is None:
            raise ValueError("no positions store to reconcile against")
        newest: dict[date, int] = {}
        for summary in self._positions.snapshots():          # newest import first
            if summary.source != LIVE_ACCOUNTS:
                newest.setdefault(summary.as_of.date(), summary.id)
        return [self._positions.load(i) for i in newest.values()]
