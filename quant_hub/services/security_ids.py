"""Each held symbol's ISIN, kept in the store (ALLOCATION A1 part 2, D66).

The look-through count matches a core fund's N-PORT lines to the stocks
held directly. The lines carry ISINs, and the exports carry tickers. The
bridge is IBKR's contract details, the owner's choice (2026-11-04): only
the stocks held directly are looked up, a few requests rather than one
per fund line, and the match is exact.

``isins`` serves, for each symbol:
1. **The stored ISIN**, offline. A stored one is asked again only when
   it's older than ``STALE_AFTER`` and TWS is connected, because a ticker
   can pass to another company after a delisting.
2. **Otherwise IBKR's**, when TWS is connected. A valid ISIN (form and
   ISO 6166 check digit) is stored. A refusal (IBKR knows no such
   contract, or the symbol is ambiguous), an invalid ISIN, or none listed
   leaves the symbol unmatched, with why.
3. **Otherwise nothing**, with why: IBKR off in settings, or TWS not
   connected. The review never connects on the owner's behalf.

Runs on the task-worker thread: each lookup blocks for one request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from typing import Protocol

from quant_hub.broker.base import ConnectionState
from quant_hub.broker.types import BrokerError, ContractSpec
from quant_hub.data.holdings import valid_isin
from quant_hub.storage.holdings_store import HoldingsStore

_logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(days=365)
SOURCE = "ibkr"


class IsinSource(Protocol):
    """What the lookup needs of a broker: its state and one read."""

    @property
    def state(self) -> ConnectionState: ...

    def get_isin(self, spec: ContractSpec) -> str | None: ...


class SecurityIdService:
    def __init__(self, store: HoldingsStore, source: IsinSource | None,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._store = store
        self._source = source
        self._now = now

    def isins(self, symbols: Iterable[str]) -> tuple[dict[str, str], dict[str, str]]:
        """(ISIN by symbol, why there's none by symbol), per the module
        docstring. Symbols are upper-cased."""
        connected = self._source is not None and self._source.state is ConnectionState.CONNECTED
        found: dict[str, str] = {}
        missing: dict[str, str] = {}
        for symbol in sorted({s.strip().upper() for s in symbols if s.strip()}):
            stored = self._store.security_id(symbol)
            if stored is not None and (not connected or self._now() - stored[1] <= STALE_AFTER):
                found[symbol] = stored[0]
                continue
            if not connected:
                missing[symbol] = ("IBKR is off in settings" if self._source is None
                                   else "TWS isn't connected")
                continue
            reason = self._look_up(symbol)
            if reason is None:
                found[symbol] = self._store.security_id(symbol)[0]
            elif stored is not None:                 # a failed refresh keeps the old one
                found[symbol] = stored[0]
            else:
                missing[symbol] = reason
        return found, missing

    def _look_up(self, symbol: str) -> str | None:
        """Ask IBKR and store a valid answer. None when stored, else why not."""
        try:
            isin = self._source.get_isin(ContractSpec(symbol))
        except BrokerError as exc:
            _logger.info("%s: no ISIN from IBKR (%s)", symbol, exc)
            return f"IBKR: {exc}"
        if isin is None:
            return "IBKR lists no ISIN for it"
        if not valid_isin(isin):
            _logger.warning("%s: IBKR's ISIN %r fails the check digit; not stored", symbol, isin)
            return f"IBKR's ISIN {isin} fails its check digit"
        self._store.save_security_id(symbol, isin, SOURCE, self._now())
        _logger.info("%s: ISIN %s from IBKR, stored", symbol, isin)
        return None
