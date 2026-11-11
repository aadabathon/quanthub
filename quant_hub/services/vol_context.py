"""Volatility context for an underlying: realized vols and a GARCH forecast.

The model-side half of the Volatility Lab's term-structure comparison. For
a named source (``synthetic``, or ``ibkr`` via the SQLite-first cache) it
loads daily history and returns:

* realized vol over the trailing 21/63/126/252 trading days (annualized
  close-to-close, ``data/stats.annualized_volatility``);
* a GARCH(1,1) fit to the full lookback, whose average-variance forecast
  (``models/garch.average_variance_forecast``) gives the model's
  expected-vol term structure.

Implied vols price exactly that expectation (plus a risk premium), so this
is what the lab draws beside the ATM IV term structure. It works on real
history today. The chain side is synthetic until chain data arrives.

Horizons: option maturities are calendar days and GARCH periods are trading
days, so ``n = calendar_days × 252/365``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR, annualized_volatility
from quant_hub.models.garch import (
    GARCH11Parameters, average_variance_forecast, fit_garch11, next_variance,
)
from quant_hub.services.market_service import MarketService

REALIZED_WINDOWS = (21, 63, 126, 252)
"""Trailing windows in trading days (~1, 3, 6, 12 months)."""

CALENDAR_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class VolContext:
    symbol: str
    source: str
    history_end: datetime
    returns_used: int
    realized: tuple[tuple[int, float], ...]
    """``(window in trading days, annualized vol)`` for each window that fits."""
    garch: GARCH11Parameters
    next_variance: float

    def garch_vol(self, calendar_days: np.ndarray) -> np.ndarray:
        """Annualized GARCH expected vol over each horizon (calendar days)."""
        trading = np.asarray(calendar_days, dtype=float) * TRADING_DAYS_PER_YEAR / CALENDAR_DAYS_PER_YEAR
        average = average_variance_forecast(self.garch, self.next_variance, trading)
        return np.sqrt(average * TRADING_DAYS_PER_YEAR)

    @staticmethod
    def calendar_days(trading_days: int) -> float:
        return trading_days * CALENDAR_DAYS_PER_YEAR / TRADING_DAYS_PER_YEAR


def load_vol_context(
    market: MarketService, symbol: str, source: str, lookback_days: int = 1825,
    now: datetime | None = None,
) -> VolContext:
    """Blocking (history fetch + GARCH fit): run on the TaskRunner worker."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if lookback_days < 60:
        raise ValueError("lookback must be at least 60 days for a GARCH fit")
    end = now or datetime.now()
    history = market.get_return_history(symbol, end - timedelta(days=lookback_days), end,
                                        source=source)               # return math (D38)
    returns = history.log_returns()
    fit = fit_garch11(returns.to_numpy())
    realized = tuple(
        (window, annualized_volatility(returns.iloc[-window:]))
        for window in REALIZED_WINDOWS
        if len(returns) >= window
    )
    h_next = next_variance(fit.parameters, float(returns.iloc[-1]),
                           float(fit.conditional_variances[-1]))
    if not math.isfinite(h_next) or h_next <= 0:
        raise ValueError("GARCH forecast is not positive")
    return VolContext(
        symbol=history.symbol, source=history.source, history_end=history.end,
        returns_used=len(returns), realized=realized, garch=fit.parameters,
        next_variance=h_next,
    )
