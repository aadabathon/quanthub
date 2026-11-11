"""IBKR option chains: listed contracts → a selected, quoted ``OptionChain``.

Implements ``data.options.OptionChainSource`` (D34) on the read-only
broker. Acquisition only: the chain carries the request's flat rate and
dividend yield, and estimating each expiry's carry is the model layer's job,
so nothing here does any modeling.

Flow:

1. **Underlying.** Fetch the spot (a delayed last where not entitled) and
   the listed option parameters. One trading class is chosen: SPXW for SPX
   (PM-settled weeklies, which also covers the monthly dates), otherwise
   the class named like the symbol, else the class with the most expiries.
2. **Expiries.** Take the listed ones nearest the request's targets
   (``data.options.select_expiries``).
3. **Each expiry's own strikes.** The option parameters list the union
   of strikes over all expiries, but quarterly and far-dated expiries list
   far fewer. Contract-details requests for the chosen expiries get the
   strikes that really exist there. TWS takes about 2.5–5 s per expiry
   however they're sent (measured live), and a listing doesn't change
   within a trading day. So each adapter remembers them for the day: the
   first chain of the day takes about 40 s, and later ones about 15 s.
4. **Pass 1, near the money.** Quote calls and puts at the
   ``parity_pairs`` listed strikes nearest spot: both sides, for put–call
   parity. Their median IBKR model IV sets the expiry's strike band;
   without one, ``vol_guess`` does.
5. **Pass 2, the wings.** Quote band strikes (``data.options.band_strikes``)
   on the out-of-the-money side only (puts below spot, calls at or above),
   skipping contracts pass 1 already quoted.

Time to expiry: US equity and PM-settled index options stop trading at
16:00 New York. ``dte`` is the fractional calendar days from now until
then, which matters for the 2–7 day expiries.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from quant_hub.broker.types import (
    ContractSpec,
    NoMarketDataError,
    OptionContractSpec,
    OptionParams,
    OptionQuoteResult,
)
from quant_hub.data.options import (
    ChainRequest,
    OptionChain,
    OptionQuote,
    band_strikes,
    nearest_strikes,
    select_expiries,
)

_logger = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
_EXPIRY_TIME = time(16, 0)

# Index underlyings and their listing exchange; everything else is a stock
# routed SMART. Extend only with symbols verified live (SPX: C0, 2026-10-15).
_INDEX_EXCHANGES = {"SPX": "CBOE"}
# Preferred trading class where a symbol lists several (SPX: SPX monthly
# AM-settled vs SPXW weekly PM-settled; PM settlement matches the 16:00
# expiry convention above).
_PREFERRED_CLASS = {"SPX": "SPXW"}


def underlying_spec(symbol: str) -> ContractSpec:
    """The broker contract for an underlying: an index where listed above,
    otherwise a SMART-routed stock or ETF."""
    symbol = symbol.upper().strip()
    if symbol in _INDEX_EXCHANGES:
        return ContractSpec(symbol=symbol, sec_type="IND", exchange=_INDEX_EXCHANGES[symbol])
    return ContractSpec(symbol=symbol)


def choose_class(symbol: str, params: list[OptionParams]) -> OptionParams:
    """The SMART-routed listing to quote (module docstring, step 1)."""
    smart = [p for p in params if p.exchange == "SMART"] or params
    if not smart:
        raise NoMarketDataError(f"IBKR lists no options on {symbol}")
    preferred = _PREFERRED_CLASS.get(symbol, symbol)
    for listing in smart:
        if listing.trading_class == preferred:
            return listing
    return max(smart, key=lambda p: len(p.expiries))


def days_to_expiry(expiry: date, now: datetime) -> float:
    """Fractional calendar days from ``now`` (timezone-aware) to 16:00 New
    York on the expiry date."""
    close = datetime.combine(expiry, _EXPIRY_TIME, tzinfo=NEW_YORK)
    return (close - now).total_seconds() / 86_400.0


class IBKROptionChainSource:
    """``OptionChainSource`` over a connected ``IBKRBroker`` (module docstring)."""

    name = "ibkr"

    def __init__(self, broker, now: Callable[[], datetime] | None = None) -> None:
        self._broker = broker
        self._now = now or (lambda: datetime.now(NEW_YORK))
        # (symbol, trading class, expiry) -> (listed strikes, the day fetched)
        self._strikes: dict[tuple[str, str, date], tuple[tuple[float, ...], date]] = {}

    def get_chain(self, symbol: str, request: ChainRequest | None = None) -> OptionChain:
        request = request or ChainRequest()
        symbol = symbol.upper().strip()
        underlying = underlying_spec(symbol)
        spot = self._broker.get_market_quote(underlying).price
        listing = choose_class(symbol, self._broker.get_option_params(underlying))
        now = self._now()
        days = {expiry: days_to_expiry(expiry, now) for expiry in listing.expiries}
        wanted = set(select_expiries(days.values(), request))
        expiries = sorted(e for e, d in days.items() if d in wanted)
        if not expiries:
            raise NoMarketDataError(
                f"no {symbol} expiry at least {request.min_dte:g} days out")

        def contract(expiry: date, strike: float, right: str) -> OptionContractSpec:
            return OptionContractSpec(symbol, expiry, strike, right, listing.trading_class,
                                      listing.multiplier)

        strikes = self._listed_strikes(symbol, listing.trading_class, expiries, now.date())

        # Pass 1: both sides near the money.
        first = [contract(e, k, right) for e in expiries
                 for k in nearest_strikes(strikes[e], spot, request.parity_pairs)
                 for right in ("C", "P")]
        first_quotes = self._broker.get_option_quotes(first)

        # Pass 2: the out-of-the-money wings, banded by each expiry's ATM vol.
        quoted = {(c.expiry, c.strike, c.right) for c in first}
        second = []
        for expiry in expiries:
            model_ivs = [q.model_iv for q in first_quotes
                         if q is not None and q.contract.expiry == expiry and q.model_iv]
            vol = statistics.median(model_ivs) if model_ivs else request.vol_guess
            for strike in band_strikes(strikes[expiry], spot, vol, days[expiry] / 365.0,
                                       request):
                right = "P" if strike < spot else "C"
                if (expiry, strike, right) not in quoted:
                    second.append(contract(expiry, strike, right))
        second_quotes = self._broker.get_option_quotes(second)

        results = [q for q in (*first_quotes, *second_quotes) if q is not None]
        quotes = tuple(_to_quote(q, days[q.contract.expiry]) for q in results)
        requested = len(first) + len(second)
        _logger.info("%s chain: %d of %d contracts quoted across %d expiries (class %s)",
                     symbol, len(quotes), requested, len(expiries), listing.trading_class)
        if not quotes:
            raise NoMarketDataError(f"no usable {symbol} option quotes")
        return OptionChain(
            symbol=symbol, source=self.name, as_of=now.astimezone().replace(tzinfo=None),
            spot=spot, rate=request.rate, dividend_yield=request.dividend_yield,
            quotes=quotes)

    def _listed_strikes(self, symbol: str, trading_class: str, expiries: list[date],
                        today: date) -> dict[date, tuple[float, ...]]:
        """Each expiry's listed strikes, fetched at most once per day (step 3)."""
        def key(expiry: date) -> tuple[str, str, date]:
            return symbol, trading_class, expiry

        stale = [e for e in expiries if self._strikes.get(key(e), ((), None))[1] != today]
        if stale:
            fetched = self._broker.get_option_strikes(symbol, trading_class, stale)
            for expiry in stale:
                self._strikes[key(expiry)] = (fetched.get(expiry, ()), today)
        return {e: self._strikes[key(e)][0] for e in expiries}


def _to_quote(result: OptionQuoteResult, dte: float) -> OptionQuote:
    return OptionQuote(
        option_type="call" if result.contract.right == "C" else "put",
        strike=result.contract.strike, dte=dte, bid=result.bid, ask=result.ask,
        reference_iv=result.model_iv)
