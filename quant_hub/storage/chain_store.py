"""SQLite store for option-chain snapshots (D33, decision 4).

IBKR has no history for expired options, so every chain the app fetches is
kept. Over time that becomes the only option history there is: the
foundation for ever backtesting option strategies.

Schema (two tables; created idempotently on open, in the same database as
the bars, D17)
--------------------------------------------------------------------------
``option_chains``
    One row per snapshot: symbol, source, ``as_of``, spot, and the flat
    rate and dividend yield it was fetched with. ``(symbol, source,
    as_of)`` is unique, so saving the same snapshot twice is a no-op.
``option_quotes``
    The snapshot's quotes, exactly as observed and in their original order
    (``seq``): type, strike, ``dte`` (relative to ``as_of``), bid, ask, and
    the provider's reference IV (NULL when none). Deleting a snapshot
    cascades to its quotes.

Only observations are stored. Per-expiry carry is derived
(``models/carry.py``) and recomputed on load, so an improved estimator
applies to old snapshots too.

Threading contract: the same as ``BarStore``. The connection is created
with ``check_same_thread=False``, and every public method holds an
internal lock.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from quant_hub.data.options import OptionChain, OptionQuote

_logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS option_chains (
    id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    as_of TEXT NOT NULL,
    spot REAL NOT NULL CHECK (spot > 0),
    rate REAL NOT NULL,
    dividend_yield REAL NOT NULL,
    UNIQUE (symbol, source, as_of)
);
CREATE TABLE IF NOT EXISTS option_quotes (
    chain_id INTEGER NOT NULL REFERENCES option_chains(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    option_type TEXT NOT NULL CHECK (option_type IN ('call', 'put')),
    strike REAL NOT NULL CHECK (strike > 0),
    dte REAL NOT NULL CHECK (dte > 0),
    bid REAL NOT NULL CHECK (bid >= 0),
    ask REAL NOT NULL CHECK (ask > 0),
    reference_iv REAL,
    PRIMARY KEY (chain_id, seq)
);
"""

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class ChainStoreError(Exception):
    """The chain store could not be opened, read, or written."""


@dataclass(frozen=True)
class ChainSnapshotSummary:
    id: int
    symbol: str
    source: str
    as_of: datetime
    quotes: int


class ChainStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as exc:
            raise ChainStoreError(f"could not open chain store at {db_path}: {exc}") from exc
        _logger.info("chain store ready: %s", db_path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def save(self, chain: OptionChain) -> int:
        """Persist a snapshot and return its id; an identical (symbol, source,
        as_of) snapshot already stored is returned as is."""
        as_of = chain.as_of.strftime(_TIME_FORMAT)
        with self._lock:
            try:
                with self._conn:
                    row = self._conn.execute(
                        "SELECT id FROM option_chains WHERE symbol=? AND source=? AND as_of=?",
                        (chain.symbol, chain.source, as_of)).fetchone()
                    if row is not None:
                        return int(row[0])
                    cursor = self._conn.execute(
                        "INSERT INTO option_chains (symbol, source, as_of, spot, rate,"
                        " dividend_yield) VALUES (?, ?, ?, ?, ?, ?)",
                        (chain.symbol, chain.source, as_of, chain.spot, chain.rate,
                         chain.dividend_yield))
                    chain_id = int(cursor.lastrowid)
                    self._conn.executemany(
                        "INSERT INTO option_quotes (chain_id, seq, option_type, strike, dte,"
                        " bid, ask, reference_iv) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(chain_id, i, q.option_type, q.strike, q.dte, q.bid, q.ask,
                          q.reference_iv) for i, q in enumerate(chain.quotes)])
            except sqlite3.Error as exc:
                raise ChainStoreError(f"could not save the {chain.symbol} chain: {exc}") from exc
        _logger.info("saved %s chain snapshot #%d (%d quotes)", chain.symbol, chain_id,
                     len(chain.quotes))
        return chain_id

    def snapshots(self, symbol: str | None = None) -> list[ChainSnapshotSummary]:
        """Stored snapshots, newest first (optionally for one symbol)."""
        query = ("SELECT c.id, c.symbol, c.source, c.as_of, COUNT(q.chain_id)"
                 " FROM option_chains c LEFT JOIN option_quotes q ON q.chain_id = c.id"
                 + (" WHERE c.symbol = ?" if symbol else "")
                 + " GROUP BY c.id ORDER BY c.as_of DESC, c.id DESC")
        with self._lock:
            try:
                rows = self._conn.execute(query, (symbol.upper(),) if symbol else ()).fetchall()
            except sqlite3.Error as exc:
                raise ChainStoreError(f"could not list chain snapshots: {exc}") from exc
        return [ChainSnapshotSummary(int(i), s, src, datetime.strptime(t, _TIME_FORMAT), int(n))
                for i, s, src, t, n in rows]

    def load(self, chain_id: int) -> OptionChain:
        with self._lock:
            try:
                head = self._conn.execute(
                    "SELECT symbol, source, as_of, spot, rate, dividend_yield"
                    " FROM option_chains WHERE id = ?", (chain_id,)).fetchone()
                quotes = self._conn.execute(
                    "SELECT option_type, strike, dte, bid, ask, reference_iv FROM option_quotes"
                    " WHERE chain_id = ? ORDER BY seq",
                    (chain_id,)).fetchall()
            except sqlite3.Error as exc:
                raise ChainStoreError(f"could not load chain snapshot #{chain_id}: {exc}") from exc
        if head is None:
            raise ChainStoreError(f"no chain snapshot #{chain_id}")
        symbol, source, as_of, spot, rate, dividend_yield = head
        return OptionChain(
            symbol=symbol, source=source, as_of=datetime.strptime(as_of, _TIME_FORMAT),
            spot=spot, rate=rate, dividend_yield=dividend_yield,
            quotes=tuple(OptionQuote(*row) for row in quotes))

    def latest(self, symbol: str) -> OptionChain | None:
        """The newest snapshot for ``symbol``, or None."""
        summaries = self.snapshots(symbol)
        return self.load(summaries[0].id) if summaries else None
