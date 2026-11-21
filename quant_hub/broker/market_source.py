"""Adapter presenting a ``Broker`` as a ``DataSource``.

Lives in the broker package (not ``data``) because in ports-and-adapters
terms the dependency must point inward: infrastructure implements the domain's
port. ``quant_hub.data`` never imports broker code.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant_hub.broker.base import Broker
from quant_hub.broker.option_chain import underlying_spec
from quant_hub.broker.types import HistoricalBar
from quant_hub.data.types import PriceBasis, PriceHistory, Quote

#: IBKR reports historical volume for US stocks in lots of 100 shares
#: (verified against live TWS 2026-08-14: SPY daily volume arrived as
#: ~850k lots ≈ 85M shares). The canonical representation stores *shares*,
#: so the adapter multiplies out the broker's unit. US equities only —
#: revisit if other security types are added.
VOLUME_LOT_SIZE = 100


def bars_to_frame(bars: list[HistoricalBar]) -> pd.DataFrame:
    """Convert broker bars to the canonical OHLCV frame (possibly empty).

    Pure, unit-tested. An empty frame is a legal result: "the window held
    no sessions" is information, not an error, and the caching layer needs
    to see it as such.
    """
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


_WHAT_TO_SHOW = {PriceBasis.TRADED: "TRADES", PriceBasis.ADJUSTED: "ADJUSTED_LAST"}


class IBKRSource:
    """IBKR as a data provider: implements both ``DataSource`` (validated
    ``PriceHistory`` for direct consumers) and ``BarSource`` (raw frames,
    possibly empty, for the caching layer)."""

    name = "ibkr"

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def fetch_bars(
        self, symbol: str, start: datetime, end: datetime, bar_size: str = "1 day",
        basis: PriceBasis = PriceBasis.TRADED,
    ) -> pd.DataFrame:
        """Raw OHLCV bars in [start, end]; empty if the window has no sessions.

        Traded prices are IBKR's TRADES; adjusted ones its ADJUSTED_LAST (D38).

        A listed index (SPX, VIX: ``option_chain.underlying_spec``) is
        requested as an index contract (HEDGE U2, D69). Its level has no
        dividends or splits to adjust, so both bases are TRADES, and it has
        no volume: IBKR's −1 stays negative and is read as missing.
        """
        symbol = symbol.upper().strip()
        spec = underlying_spec(symbol)
        index = spec.sec_type == "IND"
        bars = self._broker.get_history(
            spec, start, end, bar_size=bar_size,
            what_to_show="TRADES" if index else _WHAT_TO_SHOW[basis],
        )
        frame = bars_to_frame(bars)
        if not index:
            frame["volume"] *= VOLUME_LOT_SIZE  # broker lots -> shares
        # The broker may over-fetch (durations round up, D11); trim to the window.
        return frame.loc[start:end]

    def get_history(self, symbol: str, start: datetime, end: datetime,
                    basis: PriceBasis = PriceBasis.TRADED) -> PriceHistory:
        symbol = symbol.upper().strip()
        frame = self.fetch_bars(symbol, start, end, basis=basis)
        if len(frame) == 0:
            raise ValueError(
                f"IBKR returned no daily bars for {symbol} in "
                f"{start:%Y-%m-%d}..{end:%Y-%m-%d}"
            )
        return PriceHistory(symbol=symbol, source=self.name, frame=frame, basis=basis)

    def get_quote(self, symbol: str) -> Quote:
        symbol = symbol.upper().strip()
        result = self._broker.get_market_quote(underlying_spec(symbol))
        return Quote(
            symbol=symbol,
            price=result.price,
            timestamp=result.at,
            source=self.name,
            mode=result.mode,
        )

    class AlpacaSource:
        def __init__(self, broker: Broker) -> None:
            self._broker = broker
            
