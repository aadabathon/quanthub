"""Canonical market-data types shared by every layer of the application.

``PriceHistory`` is the contract of the data layer: every data source
(synthetic, IBKR, future providers) produces one, and every consumer (GUI,
stats, future models) accepts one. Validation therefore lives here, once,
instead of being re-done defensively in every model.

Schema: a pandas DataFrame with a sorted, unique ``DatetimeIndex``. The
``close`` column is required (finite, strictly positive). ``open``, ``high``,
``low`` and ``volume`` are optional -- real bars have them, synthetic close
paths do not, and inventing fake OHLC structure would be worse than admitting
its absence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

REQUIRED_COLUMNS = ("close",)
OPTIONAL_COLUMNS = ("open", "high", "low", "volume")


@dataclass(frozen=True)
class Quote:
    """A single observed price for a symbol at a point in time."""

    symbol: str
    price: float
    timestamp: datetime
    source: str

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("Quote.symbol must be non-empty")
        if not np.isfinite(self.price) or self.price <= 0:
            raise ValueError(f"Quote.price must be finite and > 0, got {self.price}")


@dataclass(frozen=True)
class PriceHistory:
    """A validated per-symbol price series.

    The frame is copied and sorted at construction so instances are
    effectively immutable value objects; the dataclass is frozen and the
    internal frame is never handed out for mutation by reference intent.
    """

    symbol: str
    source: str
    frame: pd.DataFrame = field(repr=False)

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("PriceHistory.symbol must be non-empty")

        frame = self.frame
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("PriceHistory.frame must have a DatetimeIndex")
        if len(frame) == 0:
            raise ValueError(f"PriceHistory for {self.symbol!r} is empty")
        for col in REQUIRED_COLUMNS:
            if col not in frame.columns:
                raise ValueError(f"PriceHistory.frame missing required column {col!r}")
        if frame.index.has_duplicates:
            raise ValueError("PriceHistory.frame index contains duplicate timestamps")

        frame = frame.sort_index().copy()

        close = frame["close"].to_numpy(dtype=float)
        if not np.all(np.isfinite(close)):
            raise ValueError(f"PriceHistory for {self.symbol!r} has non-finite closes")
        if np.any(close <= 0):
            raise ValueError(f"PriceHistory for {self.symbol!r} has non-positive closes")

        # Frozen dataclass: normalized frame must be installed via object.__setattr__.
        object.__setattr__(self, "frame", frame)

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def closes(self) -> pd.Series:
        return self.frame["close"]

    @property
    def start(self) -> datetime:
        return self.frame.index[0].to_pydatetime()

    @property
    def end(self) -> datetime:
        return self.frame.index[-1].to_pydatetime()

    def simple_returns(self) -> pd.Series:
        """Period-over-period simple returns r_t = S_t / S_{t-1} - 1."""
        return self.closes.pct_change().dropna()

    def log_returns(self) -> pd.Series:
        """Period-over-period log returns x_t = ln(S_t / S_{t-1})."""
        return np.log(self.closes / self.closes.shift(1)).dropna()
