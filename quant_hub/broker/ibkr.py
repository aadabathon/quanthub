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
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from quant_hub.broker.base import ConnectionEvent, ConnectionState
from quant_hub.broker.types import (
    BrokerError,
    BrokerUnavailableError,
    ContractSpec,
    HistoricalBar,
    InvalidContractError,
    NoHistoricalDataError,
    NoMarketDataError,
    NotConnectedError,
    PacingViolationError,
    QuoteResult,
    RequestTimeoutError,
)
from quant_hub.config import AppConfig
from quant_hub.data.types import MarketDataMode
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

# Entitlement ADVISORIES tied to a market-data request. Verified live
# (2026-08-14, TWS 7496): 10167 ("Displaying delayed market data") is
# followed ~300ms later by delayed ticks — failing the request on it was
# the bug that broke the watchlist. 10089/10090 are recorded the same way;
# if no ticks ever follow (observed for AAPL under 10089), the request
# times out into NoMarketDataError carrying the advisory text.
_ADVISORY_CODES = {10167, 10089, 10090}
# 300 = "can't find EId": our own cancel of an already-dead stream.
_IGNORABLE_CODES = {300}

# Bar sizes accepted for history requests. Deliberately the session-date
# family only: the storage/time convention treats bars as naive session
# dates (see data/bars.py), which holds for daily-and-coarser bars but NOT
# for intraday, whose timezone policy is a future milestone.
_VALID_BAR_SIZES = ("1 day", "1 week", "1 month")
# Tick types accepted as "the last price", in order of preference.
# 4=LAST, 68=DELAYED_LAST, 9=CLOSE, 75=DELAYED_CLOSE. Live and delayed ids
# are normalized here — nothing outside this file sees tick codes.
_PRICE_TICK_PRIORITY = (4, 68, 9, 75)
# tickString types carrying the last-trade time as epoch seconds:
# 45=LAST_TIMESTAMP, 88=DELAYED_LAST_TIMESTAMP.
_TIMESTAMP_TICKS = (45, 88)
# marketDataType callback values -> provenance (IBKR's own vocabulary).
_MDT_TO_MODE = {
    1: MarketDataMode.LIVE,
    2: MarketDataMode.FROZEN,
    3: MarketDataMode.DELAYED,
    4: MarketDataMode.DELAYED_FROZEN,
}
# After the first usable price tick, wait briefly for the rest of the same
# burst (close/open/timestamp/mode arrive within milliseconds — observed
# live) before reading the buffers.
_QUOTE_BURST_GRACE_S = 0.35


def duration_string(start: datetime, end: datetime) -> str:
    """Convert a [start, end] window to an IBKR durationStr.

    IBKR expresses history requests as "duration back from the end time".
    Durations up to a year are expressed in days (inclusive of both ends,
    so ``start == end`` is a legal single-session window: "1 D"); longer
    windows use years, rounded UP so the window is always covered -- the
    ``IBKRSource`` adapter slices the returned frame back down to
    [start, end], so over-fetching is harmless. Pure function, unit-tested
    without ibapi.
    """
    if start > end:
        raise ValueError(f"start ({start}) must not be after end ({end})")
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
    """Rendezvous slot between the requesting thread and the network thread.

    ``done`` means "something decisive happened": history complete, a usable
    price tick, a fatal error, or connection loss. Advisory entitlement
    notices accumulate in ``notices`` without waking the waiter.
    """

    def __init__(self) -> None:
        self.done = threading.Event()
        self.bars: list[HistoricalBar] = []
        self.ticks: dict[int, float] = {}
        self.error: BrokerError | None = None
        self.notices: list[str] = []
        self.market_data_type: int | None = None
        self.trade_time: float | None = None  # epoch seconds, from tickString


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

        # -- market data (streaming quote, cancelled by the requester) ------
        def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:
            self._owner._on_tick_price(reqId, tickType, price)

        def tickString(self, reqId: int, tickType: int, value: str) -> None:
            self._owner._on_tick_string(reqId, tickType, value)

        def marketDataType(self, reqId: int, marketDataType: int) -> None:
            self._owner._on_market_data_type(reqId, marketDataType)

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
        """Close the connection. Safe to call in any state.

        State is flipped *before* the socket teardown: closing the socket
        makes ibapi fire ``connectionClosed`` on the network thread, and
        ``_on_connection_closed`` must see the user-initiated state so a
        deliberate disconnect is not reported as an ERROR (observed against
        live TWS, 2026-08-14).
        """
        self._set_state(ConnectionState.DISCONNECTED, "disconnected by user")
        self._teardown()

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
        self,
        spec: ContractSpec,
        start: datetime,
        end: datetime,
        bar_size: str = "1 day",
    ) -> list[HistoricalBar]:
        """Fetch TRADES bars at ``bar_size`` (daily-or-coarser only).

        Limitation (D11): the request always ends "now" -- IBKR anchors
        history as a duration back from an end time, and pinning historical
        end times needs per-version timezone formatting. So the duration
        must reach from ``start`` to *now*, not to ``end``: a window ending
        in the past (a cached series' leading or interior gap) measured to
        ``end`` comes back anchored at now and misses its own beginning
        (found live, 2026-10-15). Over-fetch is harmless; callers slice to
        [start, end].
        """
        if bar_size not in _VALID_BAR_SIZES:
            raise ValueError(
                f"unsupported bar size {bar_size!r}; supported: {_VALID_BAR_SIZES} "
                "(intraday sizes need a timezone policy first)"
            )
        req_id, pending = self._register_request()
        contract = self._to_contract(spec)
        self._app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime="",  # now
            durationStr=duration_string(start, max(end, datetime.now())),
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=1,
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[],
        )
        self._await(req_id, pending, cancel=self._app.cancelHistoricalData)
        return list(pending.bars)

    def get_market_quote(self, spec: ContractSpec) -> QuoteResult:
        """One-shot quote with provenance (live/delayed/frozen) and trade time.

        Uses a brief *streaming* ``reqMktData`` (snapshot mode holds the
        line open ~11s under delayed entitlement — observed live), completes
        on the first usable price tick plus a short burst grace, and always
        cancels the stream. Entitlement advisories (10167/10089) never fail
        the request; if no price arrives within ``quote_timeout_s`` the
        advisory text becomes the ``NoMarketDataError`` reason.
        """
        req_id, pending = self._register_request()
        self._app.reqMktData(req_id, self._to_contract(spec), "", False, False, [])
        try:
            decisive = pending.done.wait(timeout=self._config.quote_timeout_s)
            if decisive and pending.error is None:
                # Let the rest of the tick burst (close/timestamp/mode) land.
                time.sleep(_QUOTE_BURST_GRACE_S)
            try:
                self._app.cancelMktData(req_id)
            except Exception:
                _logger.debug("cancelMktData failed for %d", req_id, exc_info=True)
            if pending.error is not None:
                raise pending.error

            price = None
            for tick_type in _PRICE_TICK_PRIORITY:
                candidate = pending.ticks.get(tick_type)
                if candidate is not None and candidate > 0:
                    price = candidate
                    break
            if price is None:
                reason = pending.notices[-1] if pending.notices else (
                    f"no ticks within {self._config.quote_timeout_s:.0f}s"
                )
                raise NoMarketDataError(f"no market data for {spec.symbol}: {reason}")

            mode = _MDT_TO_MODE.get(pending.market_data_type, MarketDataMode.UNKNOWN)
            at = (
                datetime.fromtimestamp(pending.trade_time)
                if pending.trade_time
                else datetime.now()
            )
            return QuoteResult(price=price, mode=mode, at=at)
        finally:
            with self._lock:
                self._pending.pop(req_id, None)

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

    @staticmethod
    def _classify_error(code: int, message: str) -> BrokerError:
        """Map TWS error codes to the typed error tree (best effort).

        Code 162 is overloaded by TWS: it covers both "no data for the
        query" and pacing violations, distinguishable only by message text.
        """
        text = f"IBKR error {code}: {message}"
        lowered = message.lower()
        if code == 200:
            return InvalidContractError(text)
        if code == 162:
            if "pacing" in lowered:
                return PacingViolationError(text)
            return NoHistoricalDataError(text)
        if code == 420:
            return PacingViolationError(text)
        return BrokerError(text)

    def _on_error(self, req_id: int, code: int, message: str) -> None:
        if code in _INFO_CODES:
            _logger.info("IBKR status %d: %s", code, message)
            return
        if code in _IGNORABLE_CODES:
            _logger.debug("IBKR %d (reqId=%d): %s", code, req_id, message)
            return
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            if code in _ADVISORY_CODES:
                # Entitlement notice, NOT a failure: delayed ticks may still
                # follow. Never touches connection state.
                pending.notices.append(message)
                _logger.info("IBKR advisory %d (reqId=%d): %s", code, req_id, message)
                return
            pending.error = self._classify_error(code, message)
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
            if tick_type in _PRICE_TICK_PRIORITY:
                pending.done.set()  # a usable price arrived: wake the waiter

    def _on_tick_string(self, req_id: int, tick_type: int, value: str) -> None:
        if tick_type not in _TIMESTAMP_TICKS:
            return
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            try:
                pending.trade_time = float(value)
            except (TypeError, ValueError):
                _logger.debug("unparseable timestamp tick %r", value)

    def _on_market_data_type(self, req_id: int, market_data_type: int) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.market_data_type = market_data_type

    def _on_request_done(self, req_id: int) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.done.set()
