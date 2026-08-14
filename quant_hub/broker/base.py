"""The ``Broker`` protocol and connection-state machinery.

Pure interfaces: importable by the GUI (for the state enum/event) and by
services without pulling in ibapi.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from quant_hub.broker.types import ContractSpec, HistoricalBar, QuoteResult


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass(frozen=True)
class ConnectionEvent:
    """Published on the event bus whenever the broker's state changes."""

    state: ConnectionState
    detail: str = ""


class Broker(Protocol):
    """What the rest of the application is allowed to know about a broker.

    All methods are blocking with internal timeouts and raise ``BrokerError``
    subclasses on failure; callers on the GUI thread must go through the
    ``TaskRunner`` rather than calling these directly.
    """

    @property
    def state(self) -> ConnectionState: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def get_history(
        self,
        spec: ContractSpec,
        start: datetime,
        end: datetime,
        bar_size: str = "1 day",
    ) -> list[HistoricalBar]: ...

    def get_market_quote(self, spec: ContractSpec) -> QuoteResult: ...
