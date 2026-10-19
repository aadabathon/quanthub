"""Immutable experiment payloads and a mutable local paper catalog (R3).

The store treats JSON as opaque domain data; no model/GUI imports. Every
public operation holds the connection lock, matching the other stores.
Runs are append-only except their separate research notes. Paper metadata
can be edited without changing an experiment's recorded evidence.
"""
import json
import sqlite3
import threading
from pathlib import Path

from quant_hub.storage.bar_store import StorageError

# Frozen v3 definitions, also used by the 2 -> 3 schema migration.
V3_TABLES = (
    "CREATE TABLE IF NOT EXISTS research_runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, "
    "title TEXT NOT NULL, payload TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '')",
    "CREATE TABLE IF NOT EXISTS research_papers (id TEXT PRIMARY KEY, metadata TEXT NOT NULL)",
)


class ResearchStore:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self.library_path = (Path(db_path).resolve().parent / (Path(db_path).stem + ".papers")
                             if str(db_path) != ":memory:" else None)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                for statement in V3_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open research store: {exc}") from exc

    def close(self):
        with self._lock:
            self._conn.close()

    def save_run(self, run_id: str, created_at: str, title: str, payload: dict):
        self._write("INSERT INTO research_runs (id,created_at,title,payload) VALUES (?,?,?,?)",
                    (run_id, created_at, title, json.dumps(payload, allow_nan=False)))

    def list_runs(self) -> list[dict]:
        rows = self._read("SELECT id,created_at,title,notes FROM research_runs ORDER BY created_at DESC, id")
        return [dict(zip(("id", "created_at", "title", "notes"), row)) for row in rows]

    def load_run(self, run_id: str) -> tuple[dict, str]:
        rows = self._read("SELECT payload,notes FROM research_runs WHERE id=?", (run_id,))
        if not rows:
            raise ValueError("saved run was not found")
        return json.loads(rows[0][0]), rows[0][1]

    def save_notes(self, run_id: str, notes: str):
        self._write("UPDATE research_runs SET notes=? WHERE id=?", (notes, run_id))

    def papers(self) -> list[dict]:
        return [json.loads(row[0]) for row in self._read("SELECT metadata FROM research_papers ORDER BY id")]

    def save_paper(self, paper: dict):
        self._write("INSERT INTO research_papers (id,metadata) VALUES (?,?) ON CONFLICT(id) "
                    "DO UPDATE SET metadata=excluded.metadata",
                    (paper["id"], json.dumps(paper, allow_nan=False)))

    def _write(self, sql, arguments):
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(sql, arguments)
            except sqlite3.Error as exc:
                raise StorageError(f"research write failed: {exc}") from exc

    def _read(self, sql, arguments=()):
        with self._lock:
            try:
                return self._conn.execute(sql, arguments).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"research read failed: {exc}") from exc
