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
  - A holding whose history won't load, or holds fewer than the model's
    ``MIN_DAYS`` returns in the lookback (a recent listing, a thin OTC
    name), is left out of the risk model and named, with the share of
    equity it leaves unmodelled. Its value still counts in the equity. Left
    in, one short history would shrink every holding's window to its own,
    since returns are aligned on the days every holding traded.
  - When no holding loads at all, the error says so and gives the first
    reason (for IBKR, usually that the app isn't connected: TWS being open
    isn't enough). An account of nothing but cash has no market risk to
    model, and says that.
  - **Regressions** (W2, ``models/regression.py``): each modelled holding,
    and the portfolio, on the benchmark's excess returns, plus any extra
    factor ETFs (their betas are then partial: each holds the others fixed),
    optionally with a lag of the benchmark (the Dimson sum beta). A factor
    without history is left out and named. The portfolio's series is the replay's
    P&L over equity, with its cash earning the rate, so that for stocks its
    coefficients are exactly the weighted holdings'. Plus rolling betas and
    the residual correlations between holdings.
  - **Growth** (W3, ``models/growth.py``): from the replay position by
    position (options repriced in full), each position's excess series
    over cash at the rate; sleeves are underlyings (shares plus their
    options) unless the review names the owner's (W7). Plus the options
    book's greeks and carry.
  - **Offline** (V0): when the source can't be reached, its stored bars
    are used. The view says why, and how many weekday sessions the data
    lacks since its last day (``data.ranges.weekdays_missed``).

Runs on the task-worker thread (database, files, maybe IBKR).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant_hub.data.positions import Holding, Position, PositionSnapshot
from quant_hub.data.ranges import weekdays_missed
from quant_hub.data.types import PriceBasis
from quant_hub.importers.positions_csv import read_positions_files
from quant_hub.models.portfolio import (
    MIN_DAYS,
    TRADING_DAYS,
    Exposures,
    OptionExposure,
    RiskReport,
    risk_report,
    simple_returns,
)
from quant_hub.models.growth import BookGreeks, GrowthReport, book_greeks, growth_report
from quant_hub.models.regression import (
    NEWEY_WEST,
    ROLLING_WINDOW,
    Regression,
    design,
    lag_name,
    regress_returns,
    residual_correlation,
    rolling_regression,
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


PORTFOLIO_ROW = "Portfolio"
_EXACT_FIT = 1.0 - 1e-9
"""R² at which a fit is exact to rounding (a holding regressed on itself)."""


@dataclass(frozen=True)
class Regressions:
    """Each holding, and the portfolio (``PORTFOLIO_ROW``), on the benchmark
    (module docstring, Regressions)."""

    benchmark: str
    factors: tuple[str, ...]              # extra factors beside the benchmark, as fitted
    dimson_lags: int
    fits: dict[str, Regression]           # holdings in risk-model order, then the portfolio
    rolling: dict[str, pd.DataFrame]      # beta and beta se by window end; empty if too short
    window: int
    correlation: pd.DataFrame             # residuals, between holdings
    lower: pd.DataFrame                   # Fisher-z 95% bounds
    upper: pd.DataFrame

    def beta(self, key: str) -> tuple[float, float]:
        """The benchmark's beta and its Newey–West error: the Dimson sum
        when lags are on."""
        fit = self.fits[key]
        names = (self.benchmark,
                 *(lag_name(self.benchmark, j) for j in range(1, self.dimson_lags + 1)))
        return fit.total(names, NEWEY_WEST)

    def factor_beta(self, key: str, factor: str) -> tuple[float, float]:
        """An extra factor's (partial) beta and its Newey–West error."""
        fit = self.fits[key]
        return fit.value(factor), fit.error(factor, NEWEY_WEST)

    def lag_beta(self, key: str) -> tuple[float, float] | None:
        """The first lag's own coefficient and error, when lags are on."""
        if not self.dimson_lags:
            return None
        fit = self.fits[key]
        name = lag_name(self.benchmark, 1)
        return fit.value(name), fit.error(name, NEWEY_WEST)


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
    regressions: Regressions | None = None
    regression_note: str = ""           # why there are none
    growth: GrowthReport | None = None
    growth_note: str = ""               # why there is none
    book: BookGreeks | None = None      # when the book holds options
    returns: pd.DataFrame | None = None  # the risk model's daily returns, benchmark included
    offline_reason: str | None = None   # set when prices came from the store (V0)
    sessions_missed: int = 0            # weekday sessions since the data's last day

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
                now: datetime | None = None, dimson_lags: int = 0,
                factors: tuple[str, ...] = (), sleeves: Mapping[str, str] | None = None,
                exclude: Sequence[date] = ()) -> PortfolioView:
        """The risk of ``snapshot`` (one account, or all with ``None``).
        ``sleeves`` names an underlying's sleeve for the growth report (W7;
        an underlying it doesn't name is its own). ``exclude`` drops those
        days from every return series, e.g. a day that dominates a
        holding's variance (``models/regime.py``)."""
        now = now or datetime.now()
        start = now - timedelta(days=lookback_days)
        positions = snapshot.for_account(account)
        if not positions:
            raise ValueError(f"no positions in account {account!r}")
        left_out: list[str] = []
        unmodelled = 0.0

        underlyings = sorted({p.symbol for p in positions if p.kind is Holding.EQUITY}
                             | {p.option.underlying for p in positions if p.option})
        closes, basis, offline = {}, PriceBasis.ADJUSTED, None
        for symbol in [*underlyings, benchmark]:
            try:
                history = self._market.get_return_history(symbol, start, now, source=source)
            except Exception as exc:          # per symbol: one gap can't sink the portfolio
                _logger.warning("portfolio: no %s history from %s: %s", symbol, source, exc)
                if symbol != benchmark:
                    left_out.append(f"{symbol}: no history from {source} ({exc})")
                continue
            if len(history.closes) <= MIN_DAYS:          # returns are one fewer than closes
                reason = (f"{symbol}: only {len(history.closes) - 1} days of history from "
                          f"{source} in the lookback; the model needs {MIN_DAYS}")
                _logger.warning("portfolio: %s", reason)
                if symbol != benchmark:
                    left_out.append(reason)
                continue
            closes[symbol] = history.closes
            if history.basis is PriceBasis.TRADED:
                basis = PriceBasis.TRADED
            offline = offline or history.offline_reason

        if not underlyings:
            raise ValueError("this account holds only cash: there's no market risk to model")
        if not any(symbol in closes for symbol in underlyings):
            first = left_out[0] if left_out else f"no history from {source}"
            hint = (" Press Connect IBKR (top right): TWS being open isn't enough."
                    if "not connected" in first.lower() else "")
            raise ValueError(f"No holding's price history loaded from {source}, so there's "
                             f"nothing to model. {first}.{hint}")

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
                file_prices.get(underlying, last), _realized_vol(closes[underlying]), rate,
                value=p.market_value))
        if equity <= 0:
            raise ValueError("the positions add up to no equity")

        modelled = sorted({*stocks, *(o.underlying for o in options)})
        returns = simple_returns({s: closes[s] for s in [*modelled, benchmark] if s in closes})
        if exclude:
            dropped = set(exclude)
            returns = returns[[t.date() not in dropped for t in returns.index]]
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
        regressions, regression_note = None, ""
        if bench is None:
            left_out.append(f"{benchmark} (the benchmark): no history from {source}, so no beta")
            regression_note = f"No {benchmark} history, so no regressions."
        else:
            # Factor returns stand apart from the risk model's: a factor with
            # less history shortens the regressions' sample, not the VaR's.
            factor_closes, missing = {}, []
            for factor in dict.fromkeys(f for f in factors if f != benchmark):
                if factor in closes:
                    factor_closes[factor] = closes[factor]
                    continue
                try:
                    factor_closes[factor] = self._market.get_return_history(
                        factor, start, now, source=source).closes
                except Exception as exc:
                    _logger.warning("portfolio: no %s factor history: %s", factor, exc)
                    missing.append(factor)
            if missing:
                regression_note = (f"No history from {source} for {', '.join(missing)}: "
                                   "left out of the factors.")
            extra = simple_returns(factor_closes) if factor_closes else None
            try:
                regressions = _regressions(returns, report, benchmark, rate, dimson_lags, extra)
            except ValueError as exc:
                regression_note = f"No regressions: {exc}."
        growth, growth_note = None, ""
        try:
            growth = _growth(report, rate, sleeves)
        except ValueError as exc:
            growth_note = f"No growth report: {exc}."
        missed = weekdays_missed(returns.index[-1], now) if offline else 0
        return PortfolioView(snapshot, account, source, benchmark, basis, report, rows, cash,
                             tuple(left_out), unmodelled / equity, len(options),
                             regressions, regression_note, growth, growth_note,
                             book_greeks(options) if options else None, returns,
                             offline_reason=offline, sessions_missed=missed)


def _growth(report: RiskReport, rate: float,
            names: Mapping[str, str] | None = None) -> GrowthReport:
    """The module docstring's growth report: each position's excess series,
    grouped by underlying, or by the sleeve ``names`` gives an underlying."""
    equity = report.equity
    shares = report.position_value / equity
    excess = report.position_pnl / equity - shares * rate / TRADING_DAYS
    sleeves: dict[str, list[str]] = {}
    for position, underlying in report.position_underlying.items():
        sleeve = (names or {}).get(str(underlying), str(underlying))
        sleeves.setdefault(sleeve, []).append(str(position))
    return growth_report(excess, rate, sleeves, values=shares.to_dict())


def _regressions(returns: pd.DataFrame, report: RiskReport, benchmark: str, rate: float,
                 dimson_lags: int, extra: pd.DataFrame | None = None) -> Regressions:
    """The module docstring's regressions, on the risk model's own returns,
    with ``extra`` factor returns beside the benchmark's (on common days)."""
    cash = rate / TRADING_DAYS
    series = returns[list(report.symbols)].copy()
    series[PORTFOLIO_ROW] = (report.pnl / report.equity
                             + (1.0 - report.net_exposure) * cash)
    factors = returns[[benchmark]]
    if extra is not None:
        factors = factors.join(extra, how="inner")
    fits = regress_returns(series, factors, rate, dimson_lags=dimson_lags)
    X = design(factors - cash, dimson_lags)
    names = (benchmark, *(lag_name(benchmark, j) for j in range(1, dimson_lags + 1)))
    rolling = {}
    if len(X) >= ROLLING_WINDOW:
        for key in series.columns:
            frame = rolling_regression(series.loc[X.index, key] - cash, X, ROLLING_WINDOW,
                                       total=names)
            rolling[key] = frame[["total", "total se"]].set_axis(["beta", "se"], axis=1)
    # A holding that is the benchmark itself fits exactly: its "residual" is
    # rounding noise, not a stream, so it has no correlations (NaN).
    residuals = pd.DataFrame({s: fits[s].residuals if not fits[s].r2 >= _EXACT_FIT else np.nan
                              for s in report.symbols}, index=X.index)
    correlation, lower, upper = residual_correlation(residuals)
    return Regressions(benchmark, tuple(str(c) for c in factors.columns[1:]), dimson_lags, fits,
                       rolling, ROLLING_WINDOW, correlation, lower, upper)


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
