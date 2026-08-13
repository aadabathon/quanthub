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
from quant_hub.data.types import PriceHistory, Quote


@runtime_checkable
class DataSource(Protocol):
    """Anything that can serve price history and quotes for a symbol."""

    name: str

    def get_history(self, symbol: str, start: datetime, end: datetime) -> PriceHistory:
        """Return daily history covering [start, end]. Raises on failure."""
        ...

    def get_quote(self, symbol: str) -> Quote:
        """Return the most recent available price. Raises on failure."""
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

    def get_history(self, symbol: str, start: datetime, end: datetime) -> PriceHistory:
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
        return PriceHistory(symbol=symbol.upper().strip(), source=self.name, frame=window)

    def get_quote(self, symbol: str) -> Quote:
        now = datetime.now()
        history = self.get_history(symbol, self._base_date, now)
        return Quote(
            symbol=symbol.upper().strip(),
            price=float(history.closes.iloc[-1]),
            timestamp=history.end,
            source=self.name,
        )
