"""SQLite store for the desk: snapshots, their claims, decisions, outcomes
and the daily runs (D1, D96, D104, A1).

**Append only.** There is no update and no delete. A snapshot is stored
once, by its SHA-256; storing it again returns the same row. That is what
lets a record be trusted later: nothing the desk said can be revised after
the outcome is known.

Schema (five tables, created idempotently on open, in the same database as
the bars, D17; schema v12, v13 for the outcomes, v14 for the runs)
------------------------------------------------------------------
``desk_snapshots``
    One row per snapshot: when it was taken, its ``as_of``, its hash, the
    code version, and the snapshot itself as canonical JSON.
``desk_claims``
    Every claim of every voice, one row each, with the day it resolves, so
    that scoring (D4) can find what's due without reading every snapshot.
``desk_decisions``
    Decisions, each citing the hash of the snapshot it was made on, stored
    as canonical JSON with their own hash.
``desk_outcomes`` (D4, D104)
    What happened to one target of one snapshot over one horizon: its
    return, volatility and deepest fall (``models/scoring.py``), or why it
    can't be known. One row each, stored once: a record must not move
    because a provider revised a bar later.
``desk_runs`` (A1)
    One row per daily run (``python -m quant_hub.snapshot``): when it
    started and finished, the snapshot it took, what it resolved, the
    backup it made, and what failed or was left out, so ``python -m
    quant_hub.desk status`` can tell a quiet desk from a broken one.

Threading contract: the same as ``BarStore``. The connection is created
with ``check_same_thread=False``, and every public method holds an
internal lock.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from quant_hub.data.desk import Claim, Decision, Outcome, Run, Snapshot, resolves_on

_logger = logging.getLogger(__name__)

# Frozen: schema step 11 -> 12 (storage/schema.py) creates exactly these.
V12_TABLES = (
    "CREATE TABLE IF NOT EXISTS desk_snapshots (id INTEGER PRIMARY KEY, taken_at TEXT NOT NULL, "
    "as_of TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, code_version TEXT NOT NULL, "
    "payload TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS desk_claims (snapshot_id INTEGER NOT NULL "
    "REFERENCES desk_snapshots(id), voice TEXT NOT NULL, claim TEXT NOT NULL, "
    "target TEXT NOT NULL, horizon INTEGER NOT NULL, kind TEXT NOT NULL, "
    "resolves_on TEXT NOT NULL, payload TEXT NOT NULL, "
    "PRIMARY KEY (snapshot_id, voice, claim)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS desk_claims_due ON desk_claims (resolves_on)",
    "CREATE TABLE IF NOT EXISTS desk_decisions (id INTEGER PRIMARY KEY, made_at TEXT NOT NULL, "
    "snapshot_sha TEXT NOT NULL REFERENCES desk_snapshots(sha256), author TEXT NOT NULL, "
    "sha256 TEXT NOT NULL UNIQUE, payload TEXT NOT NULL)",
)

# Frozen: schema step 12 -> 13 creates exactly these.
V13_TABLES = (
    "CREATE TABLE IF NOT EXISTS desk_outcomes (snapshot_id INTEGER NOT NULL "
    "REFERENCES desk_snapshots(id), subject TEXT NOT NULL, horizon INTEGER NOT NULL, "
    "status TEXT NOT NULL, ret REAL, vol REAL, fall REAL, reason TEXT NOT NULL, "
    "source TEXT NOT NULL, resolved_at TEXT NOT NULL, "
    "PRIMARY KEY (snapshot_id, subject, horizon)) WITHOUT ROWID",
)

# Frozen: schema step 13 -> 14 creates exactly these.
V14_TABLES = (
    "CREATE TABLE IF NOT EXISTS desk_runs (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, "
    "finished_at TEXT NOT NULL, source TEXT NOT NULL, snapshot_id INTEGER "
    "REFERENCES desk_snapshots(id), resolved INTEGER NOT NULL, pending INTEGER NOT NULL, "
    "backup TEXT NOT NULL, problems TEXT NOT NULL, notes TEXT NOT NULL)",
)


class DeskStoreError(Exception):
    """The desk store could not be opened, read, or written."""


@dataclass(frozen=True)
class SnapshotSummary:
    id: int
    taken_at: datetime
    as_of: date
    sha256: str
    claims: int


@dataclass(frozen=True)
class DueClaim:
    """A stored claim, with where it came from and when it resolves."""

    snapshot_id: int
    as_of: date
    voice: str
    claim: Claim
    resolves_on: date


class DeskStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            with self._conn:
                for statement in (*V12_TABLES, *V13_TABLES, *V14_TABLES):
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise DeskStoreError(f"could not open the desk store at {db_path}: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save_snapshot(self, snapshot: Snapshot) -> int:
        """Store ``snapshot`` and its claims, once; returns its id (the
        existing one if this exact snapshot is already stored)."""
        payload, sha = snapshot.to_json(), snapshot.sha256
        claims = [(v.voice, c.key, c.target, c.horizon, c.kind,
                   resolves_on(snapshot.as_of, c.horizon).isoformat(),
                   json.dumps({"quantiles": list(c.quantiles), "event": c.event,
                               "probability": c.probability}, sort_keys=True))
                  for v in snapshot.voices for c in v.claims]
        with self._lock:
            try:
                row = self._conn.execute("SELECT id FROM desk_snapshots WHERE sha256 = ?",
                                         (sha,)).fetchone()
                if row is not None:
                    return int(row[0])
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO desk_snapshots (taken_at, as_of, sha256, code_version,"
                        " payload) VALUES (?, ?, ?, ?, ?)",
                        (snapshot.taken_at.isoformat(), snapshot.as_of.isoformat(), sha,
                         snapshot.code_version, payload))
                    snapshot_id = int(cursor.lastrowid)
                    self._conn.executemany(
                        "INSERT INTO desk_claims (snapshot_id, voice, claim, target, horizon,"
                        f" kind, resolves_on, payload) VALUES ({snapshot_id}, ?, ?, ?, ?, ?, ?, ?)",
                        claims)
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not save the snapshot: {exc}") from exc
        _logger.info("desk snapshot #%d as of %s: %d claims (%s)", snapshot_id, snapshot.as_of,
                     len(claims), sha[:12])
        return snapshot_id

    def load(self, snapshot_id: int | None = None, sha256: str | None = None) -> Snapshot:
        """One snapshot by id or hash; with neither, the newest."""
        query, params = "SELECT payload FROM desk_snapshots ORDER BY id DESC LIMIT 1", ()
        if snapshot_id is not None:
            query, params = "SELECT payload FROM desk_snapshots WHERE id = ?", (snapshot_id,)
        elif sha256 is not None:
            query, params = "SELECT payload FROM desk_snapshots WHERE sha256 = ?", (sha256,)
        with self._lock:
            try:
                row = self._conn.execute(query, params).fetchone()
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not read the snapshot: {exc}") from exc
        if row is None:
            raise DeskStoreError("no such snapshot" if (snapshot_id or sha256)
                                 else "no snapshot yet")
        return Snapshot.from_json(row[0])

    def snapshots(self) -> list[SnapshotSummary]:
        """Every snapshot, newest first."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT s.id, s.taken_at, s.as_of, s.sha256, COUNT(c.claim)"
                    " FROM desk_snapshots s LEFT JOIN desk_claims c ON c.snapshot_id = s.id"
                    " GROUP BY s.id ORDER BY s.id DESC").fetchall()
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not list the snapshots: {exc}") from exc
        return [SnapshotSummary(int(i), datetime.fromisoformat(at), date.fromisoformat(as_of),
                                sha, int(n)) for i, at, as_of, sha, n in rows]

    def claims_due(self, on: date) -> list[DueClaim]:
        """Every claim that resolves on or before ``on``, oldest first."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT c.snapshot_id, s.as_of, c.voice, c.target, c.horizon, c.payload,"
                    " c.resolves_on FROM desk_claims c JOIN desk_snapshots s"
                    " ON s.id = c.snapshot_id WHERE c.resolves_on <= ?"
                    " ORDER BY c.resolves_on, c.snapshot_id, c.voice, c.claim",
                    (on.isoformat(),)).fetchall()
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not read the claims: {exc}") from exc
        due = []
        for snapshot_id, as_of, voice, target, horizon, payload, resolves in rows:
            p = json.loads(payload)
            due.append(DueClaim(int(snapshot_id), date.fromisoformat(as_of), voice,
                                Claim(target, int(horizon), tuple(p["quantiles"]), p["event"],
                                      p["probability"]), date.fromisoformat(resolves)))
        return due

    def save_decision(self, decision: Decision) -> int:
        """Store a decision once; its snapshot must be stored already."""
        payload = decision.to_json()
        sha = hashlib.sha256(payload.encode()).hexdigest()
        with self._lock:
            try:
                row = self._conn.execute("SELECT id FROM desk_decisions WHERE sha256 = ?",
                                         (sha,)).fetchone()
                if row is not None:
                    return int(row[0])
                if self._conn.execute("SELECT 1 FROM desk_snapshots WHERE sha256 = ?",
                                      (decision.snapshot_sha,)).fetchone() is None:
                    raise DeskStoreError("a decision's snapshot must be stored first")
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO desk_decisions (made_at, snapshot_sha, author, sha256,"
                        " payload) VALUES (?, ?, ?, ?, ?)",
                        (decision.made_at.isoformat(), decision.snapshot_sha, decision.author,
                         sha, payload))
                    return int(cursor.lastrowid)
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not save the decision: {exc}") from exc

    def save_outcome(self, outcome: Outcome) -> bool:
        """Store ``outcome`` once; False if that snapshot's subject and
        horizon already has one (it's never replaced)."""
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT OR IGNORE INTO desk_outcomes (snapshot_id, subject, horizon,"
                        " status, ret, vol, fall, reason, source, resolved_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (outcome.snapshot_id, outcome.subject, outcome.horizon, outcome.status,
                         outcome.ret, outcome.vol, outcome.fall, outcome.reason, outcome.source,
                         outcome.resolved_at.isoformat()))
                    return cursor.rowcount == 1
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not save the outcome: {exc}") from exc

    def outcomes(self) -> list[Outcome]:
        """Every stored outcome, by snapshot, subject and horizon."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT snapshot_id, subject, horizon, status, ret, vol, fall, reason,"
                    " source, resolved_at FROM desk_outcomes"
                    " ORDER BY snapshot_id, subject, horizon").fetchall()
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not read the outcomes: {exc}") from exc
        return [Outcome(int(i), subject, int(h), status, ret, vol, fall, reason, source,
                        datetime.fromisoformat(at))
                for i, subject, h, status, ret, vol, fall, reason, source, at in rows]

    def save_run(self, run: Run) -> int:
        """Append one daily run; returns its id."""
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        "INSERT INTO desk_runs (started_at, finished_at, source, snapshot_id,"
                        " resolved, pending, backup, problems, notes)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run.started_at.isoformat(), run.finished_at.isoformat(), run.source,
                         run.snapshot_id, run.resolved, run.pending, run.backup,
                         json.dumps(list(run.problems)), json.dumps(list(run.notes))))
                    return int(cursor.lastrowid)
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not save the run: {exc}") from exc

    def runs(self, limit: int | None = None) -> list[Run]:
        """The daily runs, newest first; the newest ``limit`` when given."""
        query = ("SELECT started_at, finished_at, source, snapshot_id, resolved, pending,"
                 " backup, problems, notes FROM desk_runs ORDER BY id DESC")
        with self._lock:
            try:
                rows = self._conn.execute(query + (" LIMIT ?" if limit else ""),
                                          (limit,) if limit else ()).fetchall()
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not read the runs: {exc}") from exc
        return [Run(datetime.fromisoformat(started), datetime.fromisoformat(finished), source,
                    None if snapshot_id is None else int(snapshot_id), int(resolved),
                    int(pending), backup, tuple(json.loads(problems)), tuple(json.loads(notes)))
                for started, finished, source, snapshot_id, resolved, pending, backup,
                problems, notes in rows]

    def decisions(self) -> list[Decision]:
        """Every decision, oldest first."""
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT payload FROM desk_decisions ORDER BY id").fetchall()
            except sqlite3.Error as exc:
                raise DeskStoreError(f"could not read the decisions: {exc}") from exc
        return [Decision.from_json(r[0]) for r in rows]
