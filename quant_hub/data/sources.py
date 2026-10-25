"""The ``DataSource`` port and the synthetic implementation.

``DataSource`` is a ``typing.Protocol`` (structural typing): an implementation
conforms by having the right attributes and method signatures, without
inheriting from anything. This keeps the dependency arrow pointing inward --
the IBKR adapter in ``quant_hub.broker.market_source`` implements this
protocol without this module ever importing broker code.
"""

from __future__ import annotations

import zlib
from datetime import datetime
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from quant_hub.data.synthetic import GeometricBrownianMotion
from quant_hub.data.types import MarketDataMode, PriceBasis, PriceHistory, Quote


class BasisUnavailableError(LookupError):
    """The source holds the symbol, but not on the requested price basis (an
    import with traded prices only, say)."""


class SourceOfflineError(Exception):
    """The provider can't be reached right now: disconnected, or no network.

    Unlike every other failure, this one says nothing about the data, so
    bars already stored can still answer. ``CachedSource`` serves them,
    marked with ``PriceHistory.offline_reason`` (V0). Adapters raise it (or a
    subclass) for "not reachable" only, never for a refused or bad request.
    """


@runtime_checkable
class DataSource(Protocol):
    """Anything that can serve price history and quotes for a symbol."""

    name: str

    def get_history(self, symbol: str, start: datetime, end: datetime,
                    basis: PriceBasis = PriceBasis.TRADED) -> PriceHistory:
        """Return daily history covering [start, end] on ``basis`` (D38).
        Raises on failure."""
        ...

    def get_quote(self, symbol: str) -> Quote:
        """Return the most recent available price. Raises on failure."""
        ...


@runtime_checkable
class BarSource(Protocol):
    """A provider of *raw* historical bars, one level below ``DataSource``.

    ``get_history`` promises a validated, non-empty ``PriceHistory`` — which
    is the right contract for consumers but cannot express "the window
    contained no sessions" (a weekend, a holiday), a perfectly legal outcome
    while synchronizing a cache. ``fetch_bars`` therefore returns a plain
    OHLCV frame, possibly empty and *not yet validated*; the caching layer
    validates it once (``data/bars.py``) before persisting.
    """

    name: str

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, bar_size: str,
        basis: PriceBasis = PriceBasis.TRADED,
    ) -> pd.DataFrame:
        """Raw bars covering [start, end] on ``basis``; may be empty. Raises on failure."""
        ...


class SyntheticSource:
    """Deterministic synthetic market: every symbol is its own GBM world.

    Per-symbol parameters (S0, mu, sigma, seed) are derived from a CRC32 of
    the symbol string -- stable across runs and machines, unlike Python's
    ``hash`` which is salted per process. Each symbol's full path is simulated
    from a fixed ``base_date`` and then sliced, so requesting different windows
    of the same symbol yields *consistent* overlapping prices, exactly as a
    real data vendor would.

    Only ``close`` is generated: synthesizing fake OHLC microstructure would
    add columns without adding information.
    """

    name = "synthetic"

    def __init__(self, base_date: datetime | None = None) -> None:
        self._base_date = base_date or datetime(2015, 1, 2)

    def _generator_for(self, symbol: str) -> GeometricBrownianMotion:
        symbol = symbol.upper().strip()
        seed = zlib.crc32(symbol.encode("utf-8"))
        param_rng = np.random.default_rng(seed)
        return GeometricBrownianMotion(
            initial_price=float(param_rng.uniform(20.0, 400.0)),
            mu=float(param_rng.uniform(0.02, 0.12)),
            sigma=float(param_rng.uniform(0.12, 0.35)),
            dt=1 / 252,
            seed=seed,
        )

    def get_history(self, symbol: str, start: datetime, end: datetime,
                    basis: PriceBasis = PriceBasis.TRADED) -> PriceHistory:
        """Synthetic paths pay no dividends: both bases are the same path."""
        if start >= end:
            raise ValueError(f"start ({start:%Y-%m-%d}) must be before end ({end:%Y-%m-%d})")
        if start < self._base_date:
            raise ValueError(
                f"synthetic data begins {self._base_date:%Y-%m-%d}; "
                f"requested start {start:%Y-%m-%d} is earlier"
            )

        dates = pd.bdate_range(self._base_date, end)
        prices = self._generator_for(symbol).simulate(len(dates) - 1)
        frame = pd.DataFrame({"close": prices}, index=dates)

        window = frame.loc[start:end]
        if len(window) == 0:
            raise ValueError(f"no business days in requested range {start} .. {end}")
        return PriceHistory(symbol=symbol.upper().strip(), source=self.name, frame=window,
                            basis=basis)

    def get_quote(self, symbol: str) -> Quote:
        now = datetime.now()
        history = self.get_history(symbol, self._base_date, now)
        return Quote(
            symbol=symbol.upper().strip(),
            price=float(history.closes.iloc[-1]),
            timestamp=history.end,
            source=self.name,
            mode=MarketDataMode.SYNTHETIC,
        )
