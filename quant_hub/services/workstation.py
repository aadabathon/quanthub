"""Workstation overview data: what the local store holds, windowed history,
and a GBM forecast fan for the Dashboard's model sandbox.

Everything here reads the local SQLite store only — never the network — so
the Dashboard renders the same with IBKR connected or not. Calls are still
blocking I/O, so the GUI submits them through the TaskRunner.

Windows ("1M", "1Y", …) are anchored at the **last stored bar**, not at
today: the store holds acquired history that may end weeks ago, and a "1M"
window ending today would then be empty. Calendar offsets (``pd.DateOffset``)
are used so "1M" means one calendar month, matching how traders read it.

GBM forecast (the model sandbox). Given the trailing history's daily log
returns :math:`r_t`, ``data.stats.estimate_gbm`` gives the annualized
:math:`\\hat\\mu, \\hat\\sigma` (drift of the SDE, :math:`\\hat\\mu = \\bar r p +
\\hat\\sigma^2/2` with :math:`p = 252`). Paths are then simulated forward from
the last close :math:`S_0` with the exact discretization in
``data.synthetic.GeometricBrownianMotion``:

.. math::

    S_{k+1} = S_k \\exp\\big((\\hat\\mu - \\tfrac12\\hat\\sigma^2)\\Delta t
              + \\hat\\sigma\\sqrt{\\Delta t}\\,Z_k\\big),\\quad \\Delta t = 1/252.

The fan reports per-step percentiles across paths. Their closed forms are
:math:`S_0\\exp((\\hat\\mu-\\hat\\sigma^2/2)t + z_q\\hat\\sigma\\sqrt t)`, which the
tests check. Assumption worth stating on screen: :math:`\\hat\\mu` from one
year of data has standard error :math:`\\approx\\hat\\sigma/\\sqrt{T}` — about
the size of :math:`\\hat\\sigma` itself — so the fan is an illustration of the
fitted model, not a prediction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR, GBMEstimate, estimate_gbm
from quant_hub.data.synthetic import GeometricBrownianMotion
from quant_hub.data.types import PriceHistory
from quant_hub.storage.bar_store import BarStore, InstrumentKey, StoredSeriesSummary

# Window label -> calendar offset back from the last stored bar (None = all).
WINDOWS: dict[str, pd.DateOffset | None] = {
    "1M": pd.DateOffset(months=1),
    "3M": pd.DateOffset(months=3),
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "5Y": pd.DateOffset(years=5),
    "ALL": None,
}

FAN_PERCENTILES = (5, 25, 50, 75, 95)


@dataclass(frozen=True)
class SeriesSnapshot:
    """One stored series, summarized for the watchlist and store table."""

    summary: StoredSeriesSummary
    last_close: float
    change_1d: float          # simple return of the last bar; NaN with one bar
    spark: np.ndarray = field(repr=False)   # trailing-window closes, oldest first

    @property
    def symbol(self) -> str:
        return self.summary.symbol


@dataclass(frozen=True)
class WorkstationOverview:
    series: tuple[SeriesSnapshot, ...]

    @property
    def total_bars(self) -> int:
        return sum(s.summary.n_bars for s in self.series)

    @property
    def last_synced(self) -> datetime | None:
        return max((s.summary.last for s in self.series), default=None)


@dataclass(frozen=True)
class GBMForecast:
    """A GBM fan: the history it was fitted to, then simulated paths forward.

    ``dates[0]`` is the last historical date and every path starts at the
    last close, so the fan joins the history line without a gap.
    """

    symbol: str
    history: PriceHistory = field(repr=False)
    estimate: GBMEstimate
    dates: pd.DatetimeIndex = field(repr=False)
    paths: np.ndarray = field(repr=False)          # (n_paths, horizon + 1)
    percentiles: dict[int, np.ndarray] = field(repr=False)

    @property
    def n_paths(self) -> int:
        return self.paths.shape[0]


class WorkstationService:
    """Read-only views of the local bar store for the Dashboard."""

    def __init__(self, store: BarStore) -> None:
        self._store = store

    def overview(self, spark_window: str = "1Y") -> WorkstationOverview:
        snapshots = []
        for summary in self._store.summary():
            frame = self._frame(summary, spark_window)
            closes = frame["close"].dropna().to_numpy(dtype=float)
            if len(closes) == 0:
                continue
            change = closes[-1] / closes[-2] - 1.0 if len(closes) >= 2 else math.nan
            snapshots.append(SeriesSnapshot(
                summary=summary, last_close=float(closes[-1]),
                change_1d=float(change), spark=closes,
            ))
        return WorkstationOverview(series=tuple(snapshots))

    def history(self, summary: StoredSeriesSummary, window: str) -> PriceHistory:
        frame = self._frame(summary, window)
        return PriceHistory(symbol=summary.symbol, source=summary.source, frame=frame)

    def _frame(self, summary: StoredSeriesSummary, window: str) -> pd.DataFrame:
        if window not in WINDOWS:
            raise ValueError(f"unknown window {window!r}; expected one of {list(WINDOWS)}")
        offset = WINDOWS[window]
        start = summary.first if offset is None else max(summary.first, summary.last - offset)
        return self._store.query_bars(
            InstrumentKey(summary.symbol), summary.source, summary.bar_size,
            start, summary.last,
        )


def gbm_forecast(
    history: PriceHistory,
    horizon_days: int = TRADING_DAYS_PER_YEAR,
    n_paths: int = 1000,
    seed: int | None = 7,
) -> GBMForecast:
    """Fit GBM to ``history`` and simulate ``n_paths`` forward (see module doc)."""
    if horizon_days < 1:
        raise ValueError(f"horizon_days must be >= 1, got {horizon_days}")
    if n_paths < 2:
        raise ValueError(f"n_paths must be >= 2 for percentiles, got {n_paths}")
    estimate = estimate_gbm(history.log_returns())
    if not (math.isfinite(estimate.mu_hat) and math.isfinite(estimate.sigma_hat)):
        raise ValueError(f"{history.symbol}: need at least 3 closes to fit GBM")

    s0 = float(history.closes.iloc[-1])
    model = GeometricBrownianMotion(
        initial_price=s0, mu=estimate.mu_hat, sigma=estimate.sigma_hat,
        dt=1 / TRADING_DAYS_PER_YEAR, seed=seed,
    )
    paths = model.simulate_paths(horizon_days, n_paths)
    dates = pd.bdate_range(history.end, periods=horizon_days + 1)
    bands = np.percentile(paths, FAN_PERCENTILES, axis=0)    # (len(q), horizon + 1)
    percentiles = dict(zip(FAN_PERCENTILES, bands, strict=True))
    return GBMForecast(
        symbol=history.symbol, history=history, estimate=estimate,
        dates=dates, paths=paths, percentiles=percentiles,
    )
