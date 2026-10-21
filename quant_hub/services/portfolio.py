"""Portfolio: positions imported and kept, and their risk (P2, P3).

* ``PortfolioService.import_positions`` reads a broker's positions CSV, or
  one per account from the same day, into one snapshot
  (``importers/positions_csv.py``) and keeps it (``storage/position_store``).
* ``example_snapshot`` is a small stock and ETF portfolio, so the page works
  before anything is imported (D34).
* ``analyse`` turns a snapshot into the risk model's inputs and runs it
  (``models/portfolio.py``):

  - **Values** are the export's own (its price and value columns), so the
    weights are the account's as of the file. A holding without them is
    valued at the source's last close.
  - **Returns** are adjusted closes from the named source, over the
    lookback (D38; traded where an import has no adjusted prices). The
    benchmark's too.
  - **Options** are priced at their underlying's price in the same file
    when it has one (the snapshot stays self-consistent), else the source's
    last close; at the underlying's realized vol (annualized log-return sd
    over the lookback), the page's rate, and calendar days to expiry from
    the snapshot's date. Expired ones are left out and named.
  - A holding whose history won't load is left out of the risk model and
    named, with the share of equity it leaves unmodelled. Its value still
    counts in the equity.

Runs on the task-worker thread (database, files, maybe IBKR).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant_hub.data.positions import Holding, Position, PositionSnapshot
from quant_hub.data.types import PriceBasis
from quant_hub.importers.positions_csv import read_positions_files
from quant_hub.models.portfolio import (
    TRADING_DAYS,
    Exposures,
    OptionExposure,
    RiskReport,
    risk_report,
    simple_returns,
)
from quant_hub.services.market_service import MarketService
from quant_hub.storage.position_store import PositionSnapshotSummary, PositionStore

_logger = logging.getLogger(__name__)

# A plain stock-and-ETF mix for the example, as shares of $100,000: sized by
# dollars, so the weights hold whatever the source's price levels.
_EXAMPLE = (("SPY", 0.30), ("QQQ", 0.15), ("AAPL", 0.10), ("MSFT", 0.10), ("TLT", 0.20),
            ("GLD", 0.10))
_EXAMPLE_EQUITY = 100_000.0
_EXAMPLE_CASH = 0.05


@dataclass(frozen=True)
class HoldingRow:
    """One underlying in the risk model, for the page's table."""

    symbol: str
    value: float              # stock and ETF market value, dollars
    option_delta: float       # its options' delta-equivalent dollars
    weight: float             # exposure / equity
    vol: float                # annualized
    beta: float | None
    risk_share: float         # of the portfolio's vol


@dataclass(frozen=True)
class PortfolioView:
    """The page's whole picture of one snapshot (module docstring)."""

    snapshot: PositionSnapshot
    account: str | None
    source: str
    benchmark: str
    basis: PriceBasis
    report: RiskReport
    rows: tuple[HoldingRow, ...]
    cash: float
    left_out: tuple[str, ...]          # holdings outside the model, with why
    unmodelled: float                   # their share of equity
    options: int                        # option positions in the model

    @property
    def equity(self) -> float:
        return self.report.equity


class PortfolioService:
    def __init__(self, market: MarketService, store: PositionStore) -> None:
        self._market = market
        self._store = store

    def import_positions(self, paths: str | Path | Sequence[str | Path]
                         ) -> tuple[int, PositionSnapshot]:
        """Read one positions file, or several of the same day (one account
        each) into one snapshot, and keep it."""
        files = [paths] if isinstance(paths, (str, Path)) else list(paths)
        snapshot = read_positions_files(files)
        return self._store.save(snapshot), snapshot

    def snapshots(self) -> list[PositionSnapshotSummary]:
        return self._store.snapshots()

    def load(self, snapshot_id: int) -> PositionSnapshot:
        return self._store.load(snapshot_id)

    def analyse(self, snapshot: PositionSnapshot, account: str | None, source: str,
                benchmark: str, lookback_days: int, rate: float,
                now: datetime | None = None) -> PortfolioView:
        """The risk of ``snapshot`` (one account, or all with ``None``)."""
        now = now or datetime.now()
        start = now - timedelta(days=lookback_days)
        positions = snapshot.for_account(account)
        if not positions:
            raise ValueError(f"no positions in account {account!r}")
        left_out: list[str] = []
        unmodelled = 0.0

        underlyings = sorted({p.symbol for p in positions if p.kind is Holding.EQUITY}
                             | {p.option.underlying for p in positions if p.option})
        closes, basis = {}, PriceBasis.ADJUSTED
        for symbol in [*underlyings, benchmark]:
            try:
                history = self._market.get_return_history(symbol, start, now, source=source)
            except Exception as exc:          # per symbol: one gap can't sink the portfolio
                _logger.warning("portfolio: no %s history from %s: %s", symbol, source, exc)
                if symbol != benchmark:
                    left_out.append(f"{symbol}: no history from {source} ({exc})")
                continue
            closes[symbol] = history.closes
            if history.basis is PriceBasis.TRADED:
                basis = PriceBasis.TRADED

        equity = sum(p.market_value or 0.0 for p in positions)
        cash = sum(p.market_value or 0.0 for p in positions if p.kind is Holding.CASH)
        stocks: dict[str, float] = {}
        options: list[OptionExposure] = []
        file_prices = {p.symbol: p.price for p in positions
                       if p.kind is Holding.EQUITY and p.price}
        for p in positions:
            if p.kind is Holding.CASH:
                continue
            underlying = p.option.underlying if p.option else p.symbol
            if underlying not in closes:
                unmodelled += abs(p.market_value or 0.0)
                continue
            last = float(closes[underlying].iloc[-1])
            if p.kind is Holding.EQUITY:
                value = p.market_value if p.market_value is not None else p.quantity * last
                stocks[p.symbol] = stocks.get(p.symbol, 0.0) + value
                if p.market_value is None:
                    equity += value
                continue
            days = p.option.days_to_expiry(snapshot.as_of.date())
            if days <= 0:
                left_out.append(f"{p.symbol}: expired {p.option.expiry}")
                continue
            options.append(OptionExposure(
                underlying, p.option.option_type, p.option.strike, days, p.quantity,
                file_prices.get(underlying, last), _realized_vol(closes[underlying]), rate))
        if equity <= 0:
            raise ValueError("the positions add up to no equity")

        modelled = sorted({*stocks, *(o.underlying for o in options)})
        returns = simple_returns({s: closes[s] for s in [*modelled, benchmark] if s in closes})
        bench = returns[benchmark] if benchmark in returns.columns else None
        report = risk_report(Exposures(equity, stocks, tuple(options)), returns, bench, rate)

        deltas: dict[str, float] = {}
        for option in options:
            deltas[option.underlying] = deltas.get(option.underlying, 0.0) + option.delta_dollars()
        rows = tuple(
            HoldingRow(symbol, stocks.get(symbol, 0.0), deltas.get(symbol, 0.0),
                       float(report.weights[i]),
                       float(report.asset_vols[i]) * math.sqrt(TRADING_DAYS),
                       None if report.betas is None else float(report.betas[i]),
                       float(report.risk_shares[i]))
            for i, symbol in enumerate(report.symbols))
        if bench is None:
            left_out.append(f"{benchmark} (the benchmark): no history from {source}, so no beta")
        return PortfolioView(snapshot, account, source, benchmark, basis, report, rows, cash,
                             tuple(left_out), unmodelled / equity, len(options))


def example_snapshot(market: MarketService, source: str) -> PositionSnapshot:
    """A small stock and ETF portfolio, $100,000 at ``source``'s last closes."""
    now = datetime.now()
    positions = []
    for symbol, share in _EXAMPLE:
        history = market.get_history(symbol, now - timedelta(days=10), now, source=source,
                                     basis=PriceBasis.TRADED)
        price = float(history.closes.iloc[-1])
        shares = math.floor(share * _EXAMPLE_EQUITY / price)
        positions.append(Position(symbol, Holding.EQUITY, shares, "Example", price,
                                  shares * price))
    cash = _EXAMPLE_EQUITY - sum(p.value for p in positions)
    positions.append(Position("CASH", Holding.CASH, cash, "Example", value=cash))
    return PositionSnapshot(tuple(positions), now, "Example", "example portfolio")


def _realized_vol(closes: pd.Series) -> float:
    """Annualized sd of daily log returns."""
    log_returns = np.diff(np.log(closes.to_numpy(dtype=float)))
    return float(np.std(log_returns, ddof=1) * math.sqrt(TRADING_DAYS))
