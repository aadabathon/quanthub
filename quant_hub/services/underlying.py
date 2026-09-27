r"""Underlying snapshot: turn any market-data source into option-pricing inputs.

This is the seam between the Options Lab and real data. The pricing models
consume a plain ``MarketInputs(spot, rate, sigma, q)`` and never learn where
the numbers came from; this service fills the two that a data source can
supply:

* **spot**: the source's latest quote. If the source cannot quote the
  symbol (an IBKR entitlement gap, D22), it falls back to the last daily
  close. The snapshot records which one was used, so provenance stays
  visible (D23).
* **realized volatility**: the annualized sample standard deviation of daily
  log returns over the lookback (``data/stats.annualized_volatility``,
  252 periods/year). It is the *historical* stand-in for Black-Scholes'
  :math:`\sigma`, which is forward-looking. Implied volatility from option
  prices replaces it once option chains arrive (Volatility Lab / data phase).

The source is named on every call (D23). ``synthetic`` gives a reproducible
GBM world, and ``ibkr`` goes through the SQLite-first ``CachedSource``. Rate
and dividend yield have no source yet and stay manual inputs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from quant_hub.broker.types import BrokerError
from quant_hub.data.stats import annualized_volatility
from quant_hub.services.market_service import MarketService

_logger = logging.getLogger(__name__)

MIN_RETURNS = 20
"""Fewest daily returns accepted for a realized-vol estimate (~1 trading month)."""


@dataclass(frozen=True)
class UnderlyingSnapshot:
    """Spot and realized vol for one symbol, with where each came from."""

    symbol: str
    source: str
    spot: float
    spot_basis: str
    spot_time: datetime
    realized_vol: float
    returns_used: int
    history_start: datetime
    history_end: datetime


def load_underlying(
    market: MarketService,
    symbol: str,
    source: str,
    lookback_days: int = 365,
    now: datetime | None = None,
) -> UnderlyingSnapshot:
    """Fetch history (for realized vol) and a quote (for spot) from ``source``.

    Blocking; call from the TaskRunner worker, never the GUI thread.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if lookback_days < 2:
        raise ValueError("lookback must be at least 2 days")
    end = now or datetime.now()
    history = market.get_history(symbol, end - timedelta(days=lookback_days), end, source=source)
    log_returns = history.log_returns()
    if len(log_returns) < MIN_RETURNS:
        raise ValueError(
            f"{symbol}: only {len(log_returns)} returns in the lookback; "
            f"need {MIN_RETURNS} for a realized-vol estimate"
        )
    realized = annualized_volatility(log_returns)
    if not math.isfinite(realized) or realized <= 0:
        raise ValueError(f"{symbol}: realized volatility is not positive")

    try:
        quote = market.get_quote(symbol, source=source)
        spot, basis, spot_time = quote.price, f"quote · {quote.mode.value}", quote.timestamp
    except BrokerError as exc:
        _logger.info("%s: no quote from %s (%s); using last close", symbol, source, exc)
        spot = float(history.closes.iloc[-1])
        basis, spot_time = "last close", history.end

    return UnderlyingSnapshot(
        symbol=history.symbol,
        source=history.source,
        spot=spot,
        spot_basis=basis,
        spot_time=spot_time,
        realized_vol=realized,
        returns_used=len(log_returns),
        history_start=history.start,
        history_end=history.end,
    )
