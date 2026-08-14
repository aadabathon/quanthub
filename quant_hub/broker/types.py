"""Broker-neutral value types and errors (no ibapi imports here)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant_hub.data.types import MarketDataMode


@dataclass(frozen=True)
class ContractSpec:
    """Broker-neutral description of a tradable instrument.

    Defaults describe a US stock routed via IBKR SmartRouting; other security
    types (options, futures) will extend this in later versions.
    """

    symbol: str
    sec_type: str = "STK"
    exchange: str = "SMART"
    currency: str = "USD"


@dataclass(frozen=True)
class QuoteResult:
    """A broker's answer to a quote request: price + provenance + trade time.

    The symbol/source labeling happens in the adapter (``market_source``);
    this is just what the wire produced, normalized away from tick codes.
    """

    price: float
    mode: MarketDataMode
    at: datetime


@dataclass(frozen=True)
class HistoricalBar:
    """One OHLCV bar as returned by a broker, before normalization."""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class BrokerError(Exception):
    """Base class for everything that can go wrong talking to a broker."""


class BrokerUnavailableError(BrokerError):
    """The broker backend (e.g. the ibapi package) is not installed."""


class NotConnectedError(BrokerError):
    """An operation requiring a live connection was attempted while disconnected."""


class RequestTimeoutError(BrokerError):
    """The broker did not answer a request within the configured timeout."""


class InvalidContractError(BrokerError):
    """The broker does not recognize the requested instrument (IBKR code 200)."""


class NoHistoricalDataError(BrokerError):
    """The broker has no historical data for the request (IBKR code 162)."""


class PacingViolationError(BrokerError):
    """Historical-data requests are being made too fast; wait and retry."""


class NoMarketDataError(BrokerError):
    """No usable quote could be obtained for the instrument.

    Distinct from ``NotConnectedError``: the connection is fine, but this
    instrument produced no ticks (typically a market-data entitlement gap —
    the message carries TWS's stated reason).
    """
