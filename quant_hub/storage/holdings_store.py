"""Funds' holdings as filed on N-PORT, in the store's file (D62, D17).

* ``fund_tickers``: the SEC's fund ticker map (ticker → trust CIK, series,
  class), so a ticker resolves offline.
* ``fund_filings``: one row per fund and report date. It holds the trust,
  series, name, accession, filing date and net assets, when it was fetched,
  and the SHA-256 of the filing's XML.
* ``fund_holdings``: that report's lines, in filing order.

Every report date is kept, so a fund's holdings history accumulates (D59:
accumulate free data). Fetching a report date again (an amended filing)
replaces that date's lines whole.

Schema v8 (``storage/schema.py``) adds the three tables to older files,
after the backup. Every public method holds the connection lock, as the
other stores do.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from quant_hub.data.holdings import FundHoldings, Holding
from quant_hub.storage.bar_store import StorageError

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Frozen v8 definitions, also used by the 7 -> 8 schema migration.
V8_TABLES = (
    "CREATE TABLE IF NOT EXISTS fund_tickers (ticker TEXT PRIMARY KEY, cik INTEGER NOT NULL, "
    "series_id TEXT NOT NULL, class_id TEXT NOT NULL, fetched_at TEXT NOT NULL) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS fund_filings (fund TEXT NOT NULL, report_date TEXT NOT NULL, "
    "cik INTEGER NOT NULL, series_id TEXT NOT NULL, fund_name TEXT NOT NULL, "
    "accession TEXT NOT NULL, filed TEXT NOT NULL, net_assets REAL, fetched_at TEXT NOT NULL, "
    "sha256 TEXT NOT NULL, PRIMARY KEY (fund, report_date)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS fund_holdings (fund TEXT NOT NULL, report_date TEXT NOT NULL, "
    "line INTEGER NOT NULL, name TEXT NOT NULL, lei TEXT NOT NULL, cusip TEXT NOT NULL, "
    "isin TEXT NOT NULL, ticker TEXT NOT NULL, balance REAL, units TEXT NOT NULL, value_usd REAL, "
    "pct REAL, asset_cat TEXT NOT NULL, issuer_cat TEXT NOT NULL, country TEXT NOT NULL, "
    "payoff TEXT NOT NULL, PRIMARY KEY (fund, report_date, line)) WITHOUT ROWID",
)


class HoldingsStore:
    """See module docstring. ``db_path`` may be ``":memory:"`` (tests)."""

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
            with self._conn:
                for statement in V8_TABLES:
                    self._conn.execute(statement)
        except sqlite3.Error as exc:
            raise StorageError(f"could not open the holdings store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --------------------------------------------------------- ticker map
    def save_fund_tickers(self, tickers: dict[str, tuple[int, str, str]],
                          fetched_at: datetime) -> None:
        stamp = fetched_at.strftime(_TIME_FORMAT)
        rows = [(t.upper(), cik, series, cls, stamp) for t, (cik, series, cls) in tickers.items()]
        self._write(lambda: (self._conn.execute("DELETE FROM fund_tickers"),
                             self._conn.executemany("INSERT INTO fund_tickers VALUES (?,?,?,?,?)",
                                                    rows)),
                    "the fund ticker map")

    def fund_ticker(self, ticker: str) -> tuple[int, str, str] | None:
        rows = self._read("SELECT cik, series_id, class_id FROM fund_tickers WHERE ticker=?",
                          (ticker.upper(),))
        return (int(rows[0][0]), rows[0][1], rows[0][2]) if rows else None

    def fund_tickers_fetched_at(self) -> datetime | None:
        rows = self._read("SELECT MIN(fetched_at) FROM fund_tickers")
        return datetime.strptime(rows[0][0], _TIME_FORMAT) if rows and rows[0][0] else None

    # ----------------------------------------------------------- holdings
    def save(self, report: FundHoldings, fetched_at: datetime, sha256: str) -> int:
        """Store one report (replacing the same fund and date); lines kept."""
        key = (report.fund.upper(), report.report_date)
        lines = [(*key, i, h.name, h.lei, h.cusip, h.isin, h.ticker, h.balance, h.units,
                  h.value_usd, h.pct, h.asset_cat, h.issuer_cat, h.country, h.payoff)
                 for i, h in enumerate(report.holdings)]

        def work():
            self._conn.execute("DELETE FROM fund_holdings WHERE fund=? AND report_date=?", key)
            self._conn.executemany(
                "INSERT INTO fund_holdings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lines)
            self._conn.execute(
                "INSERT OR REPLACE INTO fund_filings VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*key, report.cik, report.series_id, report.fund_name, report.accession,
                 report.filed, report.net_assets, fetched_at.strftime(_TIME_FORMAT), sha256))
        self._write(work, f"holdings for {report.fund}")
        return len(lines)

    def latest(self, fund: str) -> tuple[FundHoldings, datetime] | None:
        """The fund's newest stored report and when it was fetched, if any."""
        dates = self.report_dates(fund)
        return self.report(fund, dates[-1]) if dates else None

    def report(self, fund: str, report_date: str) -> tuple[FundHoldings, datetime] | None:
        head = self._read("SELECT cik, series_id, fund_name, accession, filed, net_assets, "
                          "fetched_at FROM fund_filings WHERE fund=? AND report_date=?",
                          (fund.upper(), report_date))
        if not head:
            return None
        cik, series, name, accession, filed, net, fetched = head[0]
        rows = self._read("SELECT name, lei, cusip, isin, ticker, balance, units, value_usd, pct, "
                          "asset_cat, issuer_cat, country, payoff FROM fund_holdings "
                          "WHERE fund=? AND report_date=? ORDER BY line",
                          (fund.upper(), report_date))
        holdings = tuple(Holding(*row) for row in rows)
        return (FundHoldings(fund.upper(), int(cik), series, name, report_date, accession, filed,
                             net, holdings),
                datetime.strptime(fetched, _TIME_FORMAT))

    def report_dates(self, fund: str) -> list[str]:
        rows = self._read("SELECT report_date FROM fund_filings WHERE fund=? ORDER BY report_date",
                          (fund.upper(),))
        return [r[0] for r in rows]

    def accession(self, fund: str, accession: str) -> str | None:
        """The report date already stored from ``accession``, if any."""
        rows = self._read("SELECT report_date FROM fund_filings WHERE fund=? AND accession=?",
                          (fund.upper(), accession))
        return rows[0][0] if rows else None

    def touch(self, fund: str, report_date: str, fetched_at: datetime) -> None:
        """Mark a stored report as checked now (no newer filing existed)."""
        self._write(lambda: self._conn.execute(
            "UPDATE fund_filings SET fetched_at=? WHERE fund=? AND report_date=?",
            (fetched_at.strftime(_TIME_FORMAT), fund.upper(), report_date)), "a fetch time")

    # ------------------------------------------------------------ helpers
    def _write(self, work, what: str) -> None:
        with self._lock:
            try:
                with self._conn:
                    work()
            except sqlite3.Error as exc:
                raise StorageError(f"could not save {what}: {exc}") from exc

    def _read(self, sql: str, arguments: tuple = ()) -> list[tuple]:
        with self._lock:
            try:
                return self._conn.execute(sql, arguments).fetchall()
            except sqlite3.Error as exc:
                raise StorageError(f"holdings read failed: {exc}") from exc
