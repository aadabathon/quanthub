"""Activity: a broker's transaction history, imported, kept and checked (TR1, TR2, D90, D92).

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
history. Snapshots that didn't come from a file (a broker's live accounts)
are left out: their accounts aren't the history's.

Nothing here reads prices or touches a broker. Lots and round trips come
next (TR3) and read the history through ``transactions``.

Runs on the task-worker thread (file and database I/O).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quant_hub.data.activity import ActivityFile, Transaction
from quant_hub.importers.activity_csv import ActivityFileError, read_activity_csv
from quant_hub.models.ledger import Reconciliation, reconcile
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
        if self._positions is None:
            raise ValueError("no positions store to reconcile against")
        newest: dict[date, int] = {}
        for summary in self._positions.snapshots():          # newest import first
            if summary.sha256:
                newest.setdefault(summary.as_of.date(), summary.id)
        snapshots = [self._positions.load(i) for i in newest.values()]
        return reconcile(self._store.transactions(), snapshots)
