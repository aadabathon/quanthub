"""Broker-neutral value types and errors (no ibapi imports here)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from quant_hub.data.sources import SourceOfflineError
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
class OptionContractSpec:
    """One listed option on ``symbol`` (the underlying). ``trading_class``
    and ``multiplier`` come from the broker's option parameters; SPX lists
    both SPX (monthly, AM-settled) and SPXW (weekly, PM-settled) classes."""

    symbol: str
    expiry: date
    strike: float
    right: str  # "C" or "P"
    trading_class: str = ""
    multiplier: str = "100"
    exchange: str = "SMART"
    currency: str = "USD"


@dataclass(frozen=True)
class OptionParams:
    """What is listed for one exchange and trading class.

    ``strikes`` is the union over all of its expiries, so a given strike
    need not exist for every expiry."""

    exchange: str
    trading_class: str
    multiplier: str
    expiries: tuple[date, ...]
    strikes: tuple[float, ...]


@dataclass(frozen=True)
class OptionQuoteResult:
    """A quoted option: bid (0 = no bid) and ask, provenance, and the
    broker's own model implied vol when it sends one."""

    contract: OptionContractSpec
    bid: float
    ask: float
    mode: MarketDataMode
    model_iv: float | None = None


@dataclass(frozen=True)
class BrokerPosition:
    """One position as the broker reports it (E1, read-only).

    ``quantity`` is signed: shares, or contracts for options. ``average_cost``
    is IBKR's convention: per share for a stock, per *contract* (price times
    multiplier) for an option. The option fields are set for options only.
    """

    account: str
    symbol: str
    sec_type: str            # IBKR's: "STK" (stocks and ETFs), "OPT", "FUT", ...
    quantity: float
    average_cost: float
    con_id: int
    currency: str
    right: str | None = None          # "C" or "P"
    strike: float | None = None
    expiry: date | None = None
    multiplier: float | None = None


@dataclass(frozen=True)
class AccountSummary:
    """One account's balances from the broker (E1), in ``currency``, the
    account's base currency. A value the broker didn't send is None."""

    account: str
    currency: str
    net_liquidation: float | None
    total_cash: float | None
    gross_position_value: float | None


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


class NotConnectedError(BrokerError, SourceOfflineError):
    """An operation requiring a live connection was attempted while disconnected.

    A ``SourceOfflineError``: the cache may answer from stored bars (V0)."""


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
