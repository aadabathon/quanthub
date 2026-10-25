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

Safety posture: read-only. No order methods exist here. It reads quotes,
history, chains and, since E1, accounts, positions and balances. Orders,
from E2, live only in ``broker/orders.py`` (D52, hard rule 1). No automatic
reconnection: a dropped connection flips state and logs; reconnecting is a
deliberate user action.

``ibapi`` versions: PyPI ships the old 9.81 build; IBKR distributes the
current 10.x API from its own site. Two callbacks differ between them:

* ``error()``: TWS API 10.33 added ``errorTime`` (epoch milliseconds) as
  the *second* argument, before ``errorCode``. The installed wrapper's own
  signature says which form arrives (``_ERROR_HAS_TIME``). Guessing from
  the values could misread a timestamp as an error code, and every error
  rule here keys on the code.
* ``tickOptionComputation()``: newer APIs put ``tickAttrib`` first; the
  argument count tells them apart.

Quantities (bar volume, position size) are ``Decimal`` in 10.x and float in
9.81; both are converted with ``float()``.
"""

from __future__ import annotations

import inspect
import logging
import math
import threading
import time
from importlib.metadata import PackageNotFoundError, version
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from quant_hub.broker.base import ConnectionEvent, ConnectionState
from quant_hub.broker.types import (
    AccountSummary,
    BrokerError,
    BrokerPosition,
    BrokerUnavailableError,
    ContractSpec,
    HistoricalBar,
    InvalidContractError,
    NoHistoricalDataError,
    NoMarketDataError,
    NotConnectedError,
    OptionContractSpec,
    OptionParams,
    OptionQuoteResult,
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
    # TWS API 10.33+ passes errorTime before errorCode (module docstring).
    _ERROR_HAS_TIME = "errorTime" in inspect.signature(EWrapper.error).parameters
except ImportError:
    _HAVE_IBAPI = False
    _ERROR_HAS_TIME = False

# TWS error codes that are informational status messages, not failures.
_INFO_CODES = {2104, 2106, 2107, 2108, 2119, 2158}

# Entitlement ADVISORIES tied to a market-data request. Verified live
# (2026-08-14, TWS 7496): 10167 ("Displaying delayed market data") is
# followed ~300ms later by delayed ticks — failing the request on it was
# the bug that broke the watchlist. 10089/10090 are recorded the same way;
# if no ticks ever follow (observed for AAPL under 10089), the request
# times out into NoMarketDataError carrying the advisory text.
# 10091 ("Part of requested market data requires additional subscription
# for API ... Delayed market data") arrives with SPY option quotes whose
# delayed ticks all follow (C0 spike, 2026-10-15).
_ADVISORY_CODES = {10167, 10089, 10090, 10091}
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
# Option quotes need both sides: 1/2 = BID/ASK, 66/67 = DELAYED_BID/ASK. A
# bid of 0 or -1 is TWS's "no bid", still an answer, so option requests
# record these ticks whatever their value.
_OPTION_BID_TICKS = (1, 66)
_OPTION_ASK_TICKS = (2, 67)
# tickOptionComputation types carrying the broker's MODEL values
# (13 live, 83 delayed); 10-12 / 80-82 are bid/ask/last-implied.
_OPTION_MODEL_TICKS = (13, 83)
# Option quote lines per batch: TWS usually allows 100 concurrent market
# data lines, and the account's other streams need room too.
OPTION_QUOTE_BATCH = 50
# After the first usable price tick, wait briefly for the rest of the same
# burst (close/open/timestamp/mode arrive within milliseconds — observed
# live) before reading the buffers.
_QUOTE_BURST_GRACE_S = 0.35
# Account summary tags read in E1: what an account is worth, its cash, and
# what its positions are worth (net liquidation ≈ cash + positions).
SUMMARY_TAGS = ("NetLiquidation", "TotalCashValue", "GrossPositionValue")
# Positions have no request id in the API; their pending slot lives in the
# same map under a key no request id can take (ids count up from 1).
_POSITIONS_KEY = -100
_OPTION_RIGHTS = {"C": "C", "CALL": "C", "P": "P", "PUT": "P"}


def ibapi_version() -> str | None:
    """The installed ``ibapi`` distribution's version, or None."""
    try:
        return version("ibapi")
    except PackageNotFoundError:
        return None


def error_fields(args: Sequence[Any], has_time: bool,
                 named: dict[str, Any] | None = None) -> tuple[int, str]:
    """``(errorCode, errorString)`` from ``error()``'s arguments after
    ``reqId``. Before TWS API 10.33 they are ``(errorCode, errorString[,
    advancedOrderRejectJson])``; from 10.33, ``(errorTime, errorCode,
    errorString, advancedOrderRejectJson)``. Keyword arguments, if a caller
    uses them, win."""
    rest = list(args[1:] if has_time else args)
    named = named or {}
    code = named.get("errorCode", rest[0] if rest else 0)
    message = named.get("errorString", rest[1] if len(rest) > 1 else "")
    return int(code), str(message)


def broker_position(account: str, contract: Any, position: Any,
                    average_cost: Any) -> BrokerPosition:
    """One ``position()`` callback as a ``BrokerPosition``. Raises
    ``ValueError`` on a field it can't read."""
    sec_type = str(contract.secType)
    right = strike = expiry = multiplier = None
    if sec_type in ("OPT", "FOP"):
        right = _OPTION_RIGHTS.get(str(contract.right).upper())
        if right is None:
            raise ValueError(f"unknown option right {contract.right!r}")
        strike = float(contract.strike)
        expiry = datetime.strptime(str(contract.lastTradeDateOrContractMonth)[:8],
                                   "%Y%m%d").date()
        multiplier = float(contract.multiplier or 100)
    return BrokerPosition(
        account=str(account), symbol=str(contract.symbol), sec_type=sec_type,
        quantity=float(position), average_cost=float(average_cost),
        con_id=int(contract.conId), currency=str(contract.currency), right=right,
        strike=strike, expiry=expiry, multiplier=multiplier)


def summaries_from_rows(rows: Sequence[tuple[str, str, str, str]]) -> list[AccountSummary]:
    """``accountSummary()`` rows ``(account, tag, value, currency)`` grouped
    into one ``AccountSummary`` per account, in the order accounts first
    appear. A value that isn't a number is None."""
    by_account: dict[str, dict[str, Any]] = {}
    for account, tag, value, currency in rows:
        entry = by_account.setdefault(account, {"currency": ""})
        try:
            entry[tag] = float(value)
        except (TypeError, ValueError):
            entry[tag] = None
        if tag == "NetLiquidation" or not entry["currency"]:
            entry["currency"] = currency
    return [AccountSummary(account, entry["currency"], entry.get("NetLiquidation"),
                           entry.get("TotalCashValue"), entry.get("GrossPositionValue"))
            for account, entry in by_account.items()]


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
        self.option = False  # an option quote: record bid/ask ticks of any value
        self.model_iv: float | None = None
        self.contracts: list[Any] = []  # ibapi Contract objects from contractDetails
        self.option_params: list[OptionParams] = []
        self.positions: list[BrokerPosition] = []
        self.summary_rows: list[tuple[str, str, str, str]] = []


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

        def error(self, reqId: int, *args: Any, **kwargs: Any) -> None:
            # The installed API's own signature says where errorCode sits
            # (module docstring); a 10.33 timestamp is never read as a code.
            code, message = error_fields(args, _ERROR_HAS_TIME, kwargs)
            self._owner._on_error(reqId, code, message)

        # -- accounts (E1, read-only) ---------------------------------------
        def managedAccounts(self, accountsList: str) -> None:
            self._owner._on_managed_accounts(accountsList)

        def position(self, account: str, contract: Any, position: Any, avgCost: float) -> None:
            self._owner._on_position(account, contract, position, avgCost)

        def positionEnd(self) -> None:
            self._owner._on_request_done(_POSITIONS_KEY)

        def accountSummary(self, reqId: int, account: str, tag: str, value: str,
                           currency: str) -> None:
            self._owner._on_account_summary(reqId, account, tag, value, currency)

        def accountSummaryEnd(self, reqId: int) -> None:
            self._owner._on_request_done(reqId)

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

        def tickOptionComputation(self, reqId: int, tickType: int, *args: Any) -> None:
            # 9.81 sends (impliedVol, delta, optPrice, pvDividend, gamma, vega,
            # theta, undPrice); newer APIs put tickAttrib first.
            values = args[1:] if len(args) == 9 else args
            self._owner._on_option_computation(reqId, tickType, values[0] if values else None)

        # -- contracts and option parameters --------------------------------
        def contractDetails(self, reqId: int, contractDetails: Any) -> None:
            self._owner._on_contract_details(reqId, contractDetails)

        def contractDetailsEnd(self, reqId: int) -> None:
            self._owner._on_request_done(reqId)

        def securityDefinitionOptionParameter(self, reqId: int, exchange: str,
                                              underlyingConId: int, tradingClass: str,
                                              multiplier: str, expirations: Any,
                                              strikes: Any) -> None:
            self._owner._on_option_params(reqId, exchange, tradingClass, multiplier,
                                          expirations, strikes)

        def securityDefinitionOptionParameterEnd(self, reqId: int) -> None:
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
        self._accounts: tuple[str, ...] = ()
        self._accounts_ready = threading.Event()
        self._positions_lock = threading.Lock()  # positions have no request id

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
        with self._lock:
            self._accounts = ()            # TWS sends this login's list on connect
        self._accounts_ready.clear()
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
        _logger.info("ibapi %s, error callback %s", ibapi_version() or "unknown",
                     "with errorTime (10.33+)" if _ERROR_HAS_TIME else "without errorTime")
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
        what_to_show: str = "TRADES",
    ) -> list[HistoricalBar]:
        """Fetch bars at ``bar_size`` (daily-or-coarser only).

        ``what_to_show`` is ``TRADES`` (as traded, adjusted for splits) or
        ``ADJUSTED_LAST`` (adjusted for dividends too; TRADES times a factor
        that steps at each ex-date, 1 at the latest bar). Verified live
        2026-10-15 (D38). ADJUSTED_LAST needs an end time of "now", which
        every request here has.

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
        if what_to_show not in ("TRADES", "ADJUSTED_LAST"):
            raise ValueError(f"unsupported price series {what_to_show!r}")
        req_id, pending = self._register_request()
        contract = self._to_contract(spec)
        self._app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime="",  # now
            durationStr=duration_string(start, max(end, datetime.now())),
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
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

    # ------------------------------------------------- accounts (E1, read-only)
    def get_managed_accounts(self) -> tuple[str, ...]:
        """The accounts this login can see. TWS sends the list right after the
        handshake (``managedAccounts``); it's asked for again if it hasn't
        arrived."""
        if self.state is not ConnectionState.CONNECTED:
            raise NotConnectedError("not connected to IBKR")
        with self._lock:
            accounts = self._accounts
        if accounts:
            return accounts
        self._app.reqManagedAccts()
        if not self._accounts_ready.wait(timeout=self._config.request_timeout_s):
            raise RequestTimeoutError("IBKR sent no account list")
        with self._lock:
            return self._accounts

    def get_positions(self) -> list[BrokerPosition]:
        """Every position in every account this login sees, read once.

        ``reqPositions`` is a subscription: it sends every position, then
        ``positionEnd``, then updates. It's cancelled as soon as the list is
        complete. Positions carry no request id, so one positions request
        runs at a time. Read-only.
        """
        if self.state is not ConnectionState.CONNECTED:
            raise NotConnectedError("not connected to IBKR")
        with self._positions_lock:
            pending = _Pending()
            with self._lock:
                self._pending[_POSITIONS_KEY] = pending
            try:
                self._app.reqPositions()
                if not pending.done.wait(timeout=self._config.request_timeout_s):
                    raise RequestTimeoutError(
                        f"IBKR positions timed out after {self._config.request_timeout_s:.0f}s")
                if pending.error is not None:
                    raise pending.error
                return list(pending.positions)
            finally:
                with self._lock:
                    self._pending.pop(_POSITIONS_KEY, None)
                try:
                    self._app.cancelPositions()
                except Exception:
                    _logger.debug("cancelPositions failed", exc_info=True)

    def get_account_summary(self) -> list[AccountSummary]:
        """Net liquidation, cash and position value for every account this
        login sees (``reqAccountSummary`` over the "All" group), read once:
        the subscription is cancelled when the first full answer is in.
        Read-only."""
        req_id, pending = self._register_request()
        self._app.reqAccountSummary(req_id, "All", ",".join(SUMMARY_TAGS))
        try:
            self._await(req_id, pending, cancel=lambda _req_id: None)
        finally:
            try:
                self._app.cancelAccountSummary(req_id)
            except Exception:
                _logger.debug("cancelAccountSummary failed for %d", req_id, exc_info=True)
        return summaries_from_rows(pending.summary_rows)

    # ------------------------------------------------------------ option chains
    def get_contract_id(self, spec: ContractSpec) -> int:
        """IBKR's contract id for an instrument (needed to list its options)."""
        req_id, pending = self._register_request()
        self._app.reqContractDetails(req_id, self._to_contract(spec))
        self._await(req_id, pending, cancel=lambda _req_id: None)
        if not pending.contracts:
            raise InvalidContractError(f"IBKR knows no {spec.sec_type} {spec.symbol}")
        return int(pending.contracts[0].conId)

    def get_option_strikes(self, symbol: str, trading_class: str,
                           expiries: Sequence[date]) -> dict[date, tuple[float, ...]]:
        """The strikes actually listed for each expiry of one trading class.

        ``get_option_params`` gives the union over all expiries, but far-dated
        and quarterly expiries list far fewer strikes (C1, live 2026-10-15).
        Contract details with the strike and right left open return every
        listed contract of an expiry. That's about 4–5 s per expiry, so all
        requests go out together and are awaited together."""
        slots: list[tuple[int, _Pending, date]] = []
        try:
            for expiry in expiries:
                req_id, pending = self._register_request()
                contract = Contract()
                contract.symbol = symbol.upper().strip()
                contract.secType = "OPT"
                contract.exchange = "SMART"
                contract.currency = "USD"
                contract.lastTradeDateOrContractMonth = expiry.strftime("%Y%m%d")
                contract.tradingClass = trading_class
                slots.append((req_id, pending, expiry))
                self._app.reqContractDetails(req_id, contract)
            deadline = time.monotonic() + self._config.request_timeout_s
            for _, pending, expiry in slots:
                if not pending.done.wait(timeout=max(0.0, deadline - time.monotonic())):
                    raise RequestTimeoutError(
                        f"IBKR listed no {symbol} {expiry} strikes within "
                        f"{self._config.request_timeout_s:.0f}s")
                if pending.error is not None:
                    raise pending.error
        finally:
            with self._lock:
                for req_id, _, _ in slots:
                    self._pending.pop(req_id, None)
        return {expiry: tuple(sorted({float(c.strike) for c in pending.contracts}))
                for _, pending, expiry in slots}

    def get_option_params(self, spec: ContractSpec) -> list[OptionParams]:
        """Every exchange/trading class listing options on ``spec``, with its
        expiries and (union of) strikes (``reqSecDefOptParams``)."""
        con_id = self.get_contract_id(spec)
        req_id, pending = self._register_request()
        self._app.reqSecDefOptParams(req_id, spec.symbol.upper().strip(), "", spec.sec_type,
                                     con_id)
        self._await(req_id, pending, cancel=lambda _req_id: None)
        return list(pending.option_params)

    def get_option_quotes(self, contracts: Sequence[OptionContractSpec],
                          batch_size: int = OPTION_QUOTE_BATCH) -> list[OptionQuoteResult | None]:
        """Bid and ask for each contract, in order; ``None`` where no usable
        quote came (an unlisted strike, no ask, no entitlement).

        Batches of ``batch_size`` stay under TWS's market-data line limit.
        Each batch *streams*: never a regulatory snapshot, which IBKR bills per
        request (D33). It waits until every contract has both sides or
        ``quote_timeout_s`` passes, lets the model-IV ticks land, then cancels
        every line. Entitlement advisories don't fail a contract (D22).
        """
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        results: list[OptionQuoteResult | None] = []
        for start in range(0, len(contracts), batch_size):
            results.extend(self._quote_option_batch(contracts[start:start + batch_size]))
        return results

    def _quote_option_batch(self, batch: Sequence[OptionContractSpec]
                            ) -> list[OptionQuoteResult | None]:
        slots: list[tuple[int, _Pending, OptionContractSpec]] = []
        try:
            for spec in batch:
                req_id, pending = self._register_request()
                pending.option = True
                slots.append((req_id, pending, spec))
                self._app.reqMktData(req_id, self._to_option_contract(spec), "", False, False,
                                     [])
            deadline = time.monotonic() + self._config.quote_timeout_s
            while time.monotonic() < deadline and not all(
                    pending.error is not None or _has_both_sides(pending)
                    for _, pending, _ in slots):
                time.sleep(0.05)
            time.sleep(_QUOTE_BURST_GRACE_S)  # model-IV ticks trail the prices
        finally:
            for req_id, _, _ in slots:
                try:
                    self._app.cancelMktData(req_id)
                except Exception:
                    _logger.debug("cancelMktData failed for %d", req_id, exc_info=True)
                with self._lock:
                    self._pending.pop(req_id, None)
        return [_option_result(spec, pending) for _, pending, spec in slots]

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

    @staticmethod
    def _to_option_contract(spec: OptionContractSpec) -> "Contract":
        contract = Contract()
        contract.symbol = spec.symbol.upper().strip()
        contract.secType = "OPT"
        contract.exchange = spec.exchange
        contract.currency = spec.currency
        contract.lastTradeDateOrContractMonth = spec.expiry.strftime("%Y%m%d")
        contract.strike = float(spec.strike)
        contract.right = spec.right
        contract.multiplier = spec.multiplier
        contract.tradingClass = spec.trading_class
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
        if pending is not None and pending.option:
            if tick_type in _OPTION_BID_TICKS or tick_type in _OPTION_ASK_TICKS:
                pending.ticks[tick_type] = float(price)
            return
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

    def _on_option_computation(self, req_id: int, tick_type: int, iv: Any) -> None:
        if tick_type not in _OPTION_MODEL_TICKS:
            return
        with self._lock:
            pending = self._pending.get(req_id)
        # TWS sends -1 or a huge sentinel when it has no model value.
        if pending is not None and isinstance(iv, (int, float)) and 0 < iv < 10:
            pending.model_iv = float(iv)

    def _on_contract_details(self, req_id: int, details: Any) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.contracts.append(details.contract)

    def _on_option_params(self, req_id: int, exchange: str, trading_class: str,
                          multiplier: str, expirations: Any, strikes: Any) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is None:
            return
        try:
            pending.option_params.append(OptionParams(
                exchange=str(exchange), trading_class=str(trading_class),
                multiplier=str(multiplier),
                expiries=tuple(sorted(datetime.strptime(e, "%Y%m%d").date()
                                      for e in expirations)),
                strikes=tuple(sorted(float(k) for k in strikes))))
        except (ValueError, TypeError) as exc:
            pending.error = BrokerError(f"unparseable option parameters from IBKR: {exc}")
            pending.done.set()

    def _on_request_done(self, req_id: int) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.done.set()

    def _on_managed_accounts(self, accounts_list: str) -> None:
        accounts = tuple(a.strip() for a in str(accounts_list).split(",") if a.strip())
        with self._lock:
            self._accounts = accounts
        self._accounts_ready.set()

    def _on_position(self, account: str, contract: Any, position: Any, average_cost: Any) -> None:
        with self._lock:
            pending = self._pending.get(_POSITIONS_KEY)
        if pending is None:
            return                  # an update after the request ended
        try:
            pending.positions.append(broker_position(account, contract, position, average_cost))
        except (ValueError, TypeError, AttributeError) as exc:
            pending.error = BrokerError(f"unreadable position from IBKR: {exc}")
            pending.done.set()

    def _on_account_summary(self, req_id: int, account: str, tag: str, value: str,
                            currency: str) -> None:
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is not None:
            pending.summary_rows.append((str(account), str(tag), str(value), str(currency)))


def _has_both_sides(pending: _Pending) -> bool:
    ticks = pending.ticks
    return (any(t in ticks for t in _OPTION_BID_TICKS)
            and any(t in ticks for t in _OPTION_ASK_TICKS))


def _option_result(spec: OptionContractSpec, pending: _Pending) -> OptionQuoteResult | None:
    """A usable quote needs an ask; a missing or non-positive bid is "no bid"."""
    if pending.error is not None:
        _logger.debug("no quote for %s %s %g%s: %s", spec.symbol, spec.expiry, spec.strike,
                      spec.right, pending.error)
        return None
    ticks = pending.ticks
    ask = next((ticks[t] for t in _OPTION_ASK_TICKS if t in ticks), None)
    if ask is None or ask <= 0:
        return None
    bid = next((ticks[t] for t in _OPTION_BID_TICKS if t in ticks), 0.0)
    bid = max(bid, 0.0)
    if bid > ask:
        return None  # crossed: not a usable market
    return OptionQuoteResult(
        contract=spec, bid=bid, ask=ask,
        mode=_MDT_TO_MODE.get(pending.market_data_type, MarketDataMode.UNKNOWN),
        model_iv=pending.model_iv)
