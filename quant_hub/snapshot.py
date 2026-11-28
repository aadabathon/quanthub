"""Take today's desk snapshot from the command line (D1, D96).

    python -m quant_hub.snapshot [--source ibkr|file]

Builds the core from your settings (no window), first resolves every claim
whose day has passed (D4, ``DeskService.resolve``), then takes one snapshot
of the book with every voice's claims (``services/desk.py``), stores it,
writes its JSON beside the store (``<store folder>/desk/``), and prints
where. A failure to resolve doesn't stop the snapshot, and neither stops
the backup.
The source defaults to Massive when ``[massive] key`` is set (daily bars
without TWS, D97), else IBKR when it's on in settings (stored bars serve
when TWS isn't running), else imported files.

Then it copies the whole store to ``[backup] folder`` (default:
``backups/daily`` beside the store), keeping the newest ``[backup] keep``
copies (``storage/backup.py``, D108), unless ``--no-backup``. The record
can't be rebuilt, so the copy runs even when the snapshot fails.

Last, it stores what it did (``data/desk.Run``, A1): when, the snapshot,
what it resolved, the backup, what failed and what was left out. That's
how ``python -m quant_hub.desk status`` knows whether last night's run
worked. It exits 1 when anything failed, so the scheduler shows it too.

Meant for a daily scheduled run (before the open: Massive's free plan
serves a day's bar the next morning), so every voice's record
starts accruing whether or not the app is open. Nothing here trades.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from quant_hub.console import safe_console
from quant_hub.core import build_core
from quant_hub.data.desk import Run
from quant_hub.services.desk import default_source
from quant_hub.storage.backup import backup_store

_logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(prog="python -m quant_hub.snapshot",
                                     description="Take today's desk snapshot.")
    parser.add_argument("--source", help="price source: massive, ibkr or file (default: "
                                         "massive if keyed, else ibkr if on)")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the daily copy of the store")
    args = parser.parse_args(argv)
    started = datetime.now().replace(microsecond=0)
    core = build_core()
    problems: list[str] = []
    notes: list[str] = []
    stored = pending = 0
    snapshot_id = None
    source = args.source or default_source(bool(core.config.massive_key),
                                           core.config.ibkr_enabled)
    try:
        try:
            resolved = core.desk.resolve()
            stored, pending = resolved.stored, len(resolved.pending)
            if resolved.stored or resolved.pending:
                print(f"resolved {resolved.stored} outcomes ({resolved.unresolvable} "
                      f"unresolvable); {len(resolved.pending)} pending")
                for line in resolved.pending[:10]:
                    print(f"  pending: {line}")
            notes += [f"pending: {line}" for line in resolved.pending]
        except Exception as exc:     # the snapshot is still taken; the run says why
            _logger.exception("desk: nothing resolved")
            problems.append(f"nothing resolved: {exc}")
            print(f"nothing resolved: {exc}", file=sys.stderr)
        try:
            taken = core.desk.take(source)
            snapshot = taken.snapshot
            snapshot_id = taken.id
            claims = sum(len(v.claims) for v in snapshot.voices)
            print(f"snapshot #{taken.id} as of {snapshot.as_of} from {source}: "
                  f"{len(snapshot.book.holdings)} holdings, {len(snapshot.voices)} voices, "
                  f"{claims} claims, sha256 {snapshot.sha256[:12]}")
            for line in snapshot.book.unpriced:
                print(f"  not priced: {line}")
            for line in taken.silent:
                print(f"  silent: {line}")
            if taken.path is not None:
                print(f"  written to {taken.path}")
            notes += [f"not priced: {line}" for line in snapshot.book.unpriced]
            notes += [f"silent: {line}" for line in taken.silent]
        except Exception as exc:     # the backup still runs; the run says why
            if not isinstance(exc, ValueError):
                _logger.exception("desk: no snapshot")
            problems.append(f"no snapshot: {exc}")
            print(f"no snapshot: {exc}", file=sys.stderr)
        backup = ""
        if not args.no_backup:
            config = core.config
            folder = config.backup_folder or Path(config.db_path).parent / "backups" / "daily"
            try:
                backup = str(backup_store(Path(config.db_path), Path(folder), datetime.now(),
                                          config.backup_keep))
                print(f"backed up to {backup}")
            except (OSError, sqlite3.Error, ValueError) as exc:
                problems.append(f"no backup: {exc}")
                print(f"no backup: {exc}", file=sys.stderr)
        run = Run(started, datetime.now().replace(microsecond=0), source, snapshot_id, stored,
                  pending, backup, tuple(problems), tuple(notes))
        try:
            core.desk.record_run(run)
        except Exception as exc:     # nothing else to fall back on: say so, fail the run
            print(f"run not recorded: {exc}", file=sys.stderr)
            return 1
    finally:
        core.shutdown()
    return 0 if run.ok else 1

if __name__ == "__main__":
    sys.exit(main())
