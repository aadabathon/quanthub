"""Broker-neutral value types and errors (no ibapi imports here)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


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
