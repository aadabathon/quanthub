"""Interactive Brokers implementation of the ``Broker`` protocol.

Threading model (the whole point of this file)
==============================================
The official ``ibapi`` is callback-based: after connecting, a network loop
(``EClient.run()``) must run on a dedicated thread, and every response arrives
as an ``EWrapper`` callback *on that thread*.

This class confines that model entirely within this file by converting it to
blocking calls:

* Each outgoing request gets a request id and a ``_Pending`` slot containing a
  ``threading.Event`` plus result buffers.
* The calling thread (the GUI's task worker -- never the GUI thread itself)
  sends the request and blocks on ``event.wait(timeout)``.
* The network thread's callbacks fill the buffers and set the event.

Callers therefore see ordinary functions that return data or raise
``BrokerError`` -- no callbacks, request ids, or threads leak out. State
changes are pushed to the GUI via ``ConnectionEvent`` on the event bus (the
one sanctioned thread crossing).

Safety posture: read-only. No order methods exist. No automatic reconnection:
a dropped connection flips state and logs; reconnecting is a deliberate user
action.

``ibapi`` note: PyPI currently ships the old 9.81 build, while IBKR
distributes the current API at https://interactivebrokers.github.io. Callback
signatures differ slightly between the two (e.g. ``error()`` gained
parameters), so the wrapper methods below accept ``*extra`` to tolerate both.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from quant_hub.broker.base import ConnectionEvent, ConnectionState
from quant_hub.broker.types import (
    BrokerError,
    BrokerUnavailableError,
    ContractSpec,
    HistoricalBar,
    NotConnectedError,
    RequestTimeoutError,
)
from quant_hub.config import AppConfig
from quant_hub.events import EventBus

if TYPE_CHECKING:
    from ibapi.common import BarData

_logger = logging.getLogger(__name__)

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper

    _HAVE_IBAPI = True
except ImportError:
    _HAVE_IBAPI = False

# TWS error codes that are informational status messages, not failures.
_INFO_CODES = {2104, 2106, 2107, 2108, 2119, 2158}
# Tick types accepted as "the last price", in order of preference.
# 4=LAST, 68=DELAYED_LAST, 9=CLOSE, 75=DELAYED_CLOSE.
_PRICE_TICK_PRIORITY = (4, 68, 9, 75)


def duration_string(start: datetime, end: datetime) -> str:
    """Convert a [start, end] window to an IBKR durationStr.

    IBKR expresses history requests as "duration back from the end time".
    Durations up to a year are expressed in days (inclusive of both ends);
    longer windows use years, rounded UP so the window is always covered --
    the ``IBKRSource`` adapter slices the returned frame back down to
    [start, end], so over-fetching is harmless. Pure function, unit-tested
    without ibapi.
    """
    if start >= end:
        raise ValueError(f"start ({start}) must be before end ({end})")
    days = (end - start).days
    if days < 365:
        return f"{days + 1} D"
    years = math.ceil(days / 365.25)
    return f"{years} Y"


def parse_ib_date(raw: str) -> datetime:
    """Parse the date field of a historical bar.

    Daily bars arrive as ``YYYYMMDD``; intraday bars as
    ``YYYYMMDD HH:MM:SS`` optionally followed by a timezone name (newer API
    versions). The timezone suffix is ignored -- v0.1 only requests daily
    bars. Pure function, unit-tested without ibapi.
    """
    text = raw.strip()
    parts = text.split()
    if len(parts) >= 2 and ":" in parts[1]:
        return datetime.strptime(f"{parts[0]} {parts[1]}", "%Y%m%d %H:%M:%S")
    return datetime.strptime(parts[0], "%Y%m%d")


class _Pending:
    """Rendezvous slot between the requesting thread and the network thread."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.bars: list[HistoricalBar] = []
        self.ticks: dict[int, float] = {}
        self.error: BrokerError | None = None


if _HAVE_IBAPI:

    class _IBApp(EWrapper, EClient):  # type: ignore[misc]
        """ibapi glue object. Every method here runs on the network thread and
        must only touch thread-safe primitives on the owning broker."""

        def __init__(self, owner: "IBKRBroker") -> None:
            EClient.__init__(self, self)
            self._owner = owner

        # -- lifecycle -------------------------------------------------------
        def nextValidId(self, orderId: int) -> None:
            # First callback after a successful handshake: the API is ready.
            self._owner._on_ready()

        def connectionClosed(self) -> None:
            self._owner._on_connection_closed()

        def error(self, reqId: int, errorCode: int = 0, errorString: str = "", *extra: Any) -> None:
            # Signature varies across ibapi versions; *extra absorbs new params.
            self._owner._on_error(reqId, errorCode, str(errorString))

        # -- historical data -------------------------------------------------
        def historicalData(self, reqId: int, bar: "BarData") -> None:
            self._owner._on_bar(reqId, bar)

        def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:
            self._owner._on_request_done(reqId)

        # -- market data snapshot -------------------------------------------
        def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:
            self._owner._on_tick_price(reqId, tickType, price)

        def tickSnapshotEnd(self, reqId: int) -> None:
            self._owner._on_request_done(reqId)


class IBKRBroker:
    """Blocking, read-only IBKR client. See module docstring for the design."""

    def __init__(self, config: AppConfig, bus: EventBus) -> None:
        self._config = config
        self._bus = bus
        self._lock = threading.Lock()  # guards state, req ids, pending map
        self._state = ConnectionState.DISCONNECTED
        self._app: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._next_req_id = 1
        self._pending: dict[int, _Pending] = {}

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> ConnectionState:
        with self._lock:
            return self._state

    def _set_state(self, state: ConnectionState, detail: str = "") -> None:
        with self._lock:
            if state is self._state:
                return
            self._state = state
        _logger.info("IBKR connection: %s%s", state.value, f" ({detail})" if detail else "")
        self._bus.publish(ConnectionEvent(state=state, detail=detail))

    # ------------------------------------------------------------- connection
    def connect(self) -> None:
        """Connect to TWS/Gateway. Blocking; call from a worker thread."""
        if not _HAVE_IBAPI:
            raise BrokerUnavailableError(
                "the 'ibapi' package is not installed -- install with "
                "'pip install quant-hub[ibkr]' or from interactivebrokers.github.io"
            )
        if self.state is ConnectionState.CONNECTED:
            return

        cfg = self._config
        self._set_state(ConnectionState.CONNECTING, f"{cfg.ibkr_host}:{cfg.ibkr_port}")
        self._ready.clear()
        self._app = _IBApp(self)
        try:
            self._app.connect(cfg.ibkr_host, cfg.ibkr_port, cfg.ibkr_client_id)
        except Exception as exc:
            self._set_state(ConnectionState.ERROR, str(exc))
            raise BrokerError(f"could not open socket to TWS: {exc}") from exc

        self._thread = threading.Thread(
            target=self._app.run, name="ibkr-network", daemon=True
        )
        self._thread.start()

        if not self._ready.wait(timeout=cfg.connect_timeout_s):
            self._teardown()
            self._set_state(
                ConnectionState.ERROR,
                f"no handshake within {cfg.connect_timeout_s:.0f}s "
                "(is TWS/Gateway running with the API enabled?)",
            )
            raise BrokerError("IBKR connection timed out")

        # Accept delayed data so the app works without market-data
        # subscriptions. 3 = delayed (free), falling back automatically
        # to real-time where entitled.
        self._app.reqMarketDataType(3)
        self._set_state(ConnectionState.CONNECTED)

    def disconnect(self) -> None:
        """Close the connection. Safe to call in any state."""
        self._teardown()
        self._set_state(ConnectionState.DISCONNECTED, "disconnected by user")

    def _teardown(self) -> None:
        if self._app is not None:
            try:
                self._app.disconnect()
            except Exception:
                _logger.exception("error while disconnecting from IBKR")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._fail_all_pending(NotConnectedError("connection closed"))

    # --------------------------------------------------------------- requests
    def get_history(
        self, spec: ContractSpec, start: datetime, end: datetime
    ) -> list[HistoricalBar]:
        """Fetch daily TRADES bars.

        Limitation (v0.1): the request always ends "now" -- IBKR anchors
        history as a duration back from an end time, and pinning historical
        end times needs per-version timezone formatting. The window length is
        honored via ``duration_string(start, end)``.
        """
        req_id, pending = self._register_request()
        contract = self._to_contract(spec)
        self._app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime="",  # now
            durationStr=duration_string(start, end),
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=1,
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[],
        )
        self._await(req_id, pending, cancel=self._app.cancelHistoricalData)
        return list(pending.bars)

    def get_last_price(self, spec: ContractSpec) -> float:
        """One-shot snapshot of the most recent price for ``spec``."""
        req_id, pending = self._register_request()
        self._app.reqMktData(req_id, self._to_contract(spec), "", True, False, [])
        try:
            self._await(req_id, pending, cancel=self._app.cancelMktData)
        except RequestTimeoutError:
            # Snapshots sometimes deliver ticks but never a snapshot-end;
            # accept whatever arrived before giving up.
            if not pending.ticks:
                raise
        for tick_type in _PRICE_TICK_PRIORITY:
            price = pending.ticks.get(tick_type)
            if price is not None and price > 0:
                return price
        raise BrokerError(f"no usable price ticks received for {spec.symbol}")

    # ---------------------------------------------------------- request plumbing
    def _register_request(self) -> tuple[int, _Pending]:
        if self.state is not ConnectionState.CONNECTED:
            raise NotConnectedError("not connected to IBKR")
        pending = _Pending()
        with self._lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            self._pending[req_id] = pending
        return req_id, pending

    def _await(self, req_id: int, pending: _Pending, cancel: Any) -> None:
        """Block until the network thread completes the request, or time out."""
        try:
            if not pending.done.wait(timeout=self._config.request_timeout_s):
                try:
                    cancel(req_id)
                except Exception:
                    _logger.debug("cancel failed for request %d", req_id, exc_info=True)
                raise RequestTimeoutError(
                    f"IBKR request {req_id} timed out after "
                    f"{self._config.request_timeout_s:.0f}s"
                )
            if pending.error is not None:
                raise pending.error
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

    def _fail_all_pending(self, error: BrokerError) -> None:
        with self._lock:
            pending_now = list(self._pending.values())
            self._pending.clear()
        for pending in pending_now:
            pending.error = error
            pending.done.set()

    @staticmethod
    def _to_contract(spec: ContractSpec) -> "Contract":
        contract = Contract()
        contract.symbol = spec.symbol.upper().strip()
        contract.secType = spec.sec_type
        contract.exchange = spec.exchange
        contract.currency = spec.currency
        return contract

    # ------------------------------------------- callbacks (network thread!)
    # These run on the ibapi network thread and must only touch locks,
    # events, pending slots, and the event bus -- never the GUI.

    def _on_ready(self) -> None:
        self._ready.set()

    def _on_connection_closed(self) -> None:
        was_connected = self.state is ConnectionState.CONNECTED
        self._fail_all_pending(NotConnectedError("connection closed"))
        if was_connected:
            self._set_state(ConnectionState.ERROR, "connection closed by TWS")

    def _on_error(self, req_id: int, code: int, message: str) -> None:
        if code in _INFO_CODES:
            _logger.info("IBKR status %d: %s", code, message)
            return
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.error = BrokerError(f"IBKR error {code}: {message}")
            pending.done.set()
            return
        # Errors not tied to a request: connectivity problems, etc.
        _logger.warning("IBKR error %d (reqId=%d): %s", code, req_id, message)
        if code in (502, 504):  # couldn't connect / not connected
            self._set_state(ConnectionState.ERROR, message)

    def _on_bar(self, req_id: int, bar: "BarData") -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is None:
            return
        try:
            pending.bars.append(
                HistoricalBar(
                    date=parse_ib_date(bar.date),
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume),
                )
            )
        except (ValueError, TypeError) as exc:
            pending.error = BrokerError(f"unparseable bar from IBKR: {exc}")
            pending.done.set()

    def _on_tick_price(self, req_id: int, tick_type: int, price: float) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None and price > 0:
            pending.ticks[tick_type] = float(price)

    def _on_request_done(self, req_id: int) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.done.set()
