"""Adapter presenting a ``Broker`` as a ``DataSource``.

Lives in the broker package (not ``data``) because in ports-and-adapters
terms the dependency must point inward: infrastructure implements the domain's
port. ``quant_hub.data`` never imports broker code.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_hub.broker.base import Broker
from quant_hub.broker.types import ContractSpec, HistoricalBar
from quant_hub.data.types import PriceHistory, Quote


def bars_to_frame(bars: list[HistoricalBar]) -> pd.DataFrame:
    """Convert broker bars to the canonical OHLCV frame. Pure, unit-tested."""
    if not bars:
        raise ValueError("no bars to convert")
    frame = pd.DataFrame(
        {
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        },
        index=pd.DatetimeIndex([b.date for b in bars]),
    )
    return frame


class IBKRSource:
    """``DataSource`` backed by a live (read-only) broker connection."""

    name = "ibkr"

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def get_history(self, symbol: str, start: datetime, end: datetime) -> PriceHistory:
        symbol = symbol.upper().strip()
        bars = self._broker.get_history(ContractSpec(symbol=symbol), start, end)
        # The broker may over-fetch (durations round up); trim to the window.
        frame = bars_to_frame(bars).loc[start:end]
        return PriceHistory(symbol=symbol, source=self.name, frame=frame)

    def get_quote(self, symbol: str) -> Quote:
        symbol = symbol.upper().strip()
        price = self._broker.get_last_price(ContractSpec(symbol=symbol))
        return Quote(
            symbol=symbol, price=price, timestamp=datetime.now(), source=self.name
        )
