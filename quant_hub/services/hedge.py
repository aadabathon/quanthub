"""The hedge lab's job: the books, the candidates, today's prices (HEDGE U3, D70).

``models/hedge.py`` is the math. This service feeds it the stored data,
offline only: nothing here fetches behind the owner's back (V0). Whatever
is missing is named, with how to get it. Runs on the task-worker thread.

**Two books** (HEDGE_PLAN §1):
- ``proxy_book(beta)``: ALLOCATION A2's β proxy over the longest history,
  R_B = r + b·(R_m − r), with Ken French's daily market and bills from
  1926. It holds every crash since, and it's a floor for a book of
  stocks.
- ``mix_book(view, rate)``: today's mix as Portfolio replays it, the
  replay's P&L on today's equity (options repriced in full), plus the
  cash line's interest at the cash rate. It's exact, but it covers only
  the lookback.

**The option underlying.** HEDGE decision 4 chose IBKR's index for the
option candidates, on the premise that French's series has no price
levels to strike against. Its total-return level is one: a put on it with
no dividend has the same economics as a put on the price index with its
dividend, to a fraction of a percent of moneyness a quarter. It covers
the whole VIX window, from 1990, so U3 uses it. IBKR's SPX history (U2)
is the cross-check. Today's mix uses its benchmark's own returns the
same way.

**Today's shape** (§3). From the newest stored chain of the index (SPX,
else SPY), fitted as the Volatility Lab fits it (``load_market_surface``):
- σ_ATM,30, the 30-day at-the-money vol, and ρ = σ_ATM,30 / VIX, with
  today's VIX from FRED
- the smile s(k, T) = σ(k, T) / σ_ATM,30 on a grid of log-moneyness and
  tenor (``models/hedge.grid_smile``)
- the half-spread c (decision 6): the median of (ask − bid)/(ask + bid)
  over the chain's out-of-the-money puts

Without a stored chain, the shape is flat with ρ = 1 and no costs, and it
says so. VIX is a variance strike, which sits above the ATM vol, so ρ = 1
overstates every put a little.

**The candidates** (decision 2, ``v1_candidates``):
- puts 5, 10 and 20% out of the money, over 1, 3 and 12 months
- put spreads: 95/85 over a month, 90/80 over three
- a zero-cost collar on the 10% put over three months
- trend: the 200-day average and 12-month momentum
- vol targeting at the book's own long-run vol
- cash
- any stored stream the caller names (a Treasury fund, a trend fund)

Each runs at sizes h of 25, 50 and 100%. Each is assessed on the days it
has data for: an option program needs VIX, so it starts in 1990, and a
stream starts with its own history. Each result says its window.

**Today's prices** (§6): ``today`` gives each put or put spread's cost a
year at today's surface, and its percentile among the same program's
VIX-based costs over the window. That answers "is protection cheap or
dear right now?"
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime

import numpy as np
import pandas as pd

from quant_hub.data.types import PriceBasis
from quant_hub.models import hedge as H
from quant_hub.services.external_data import ExternalDataService
from quant_hub.services.option_chains import ChainService, NoStoredChainError, load_market_surface
from quant_hub.services.portfolio import PORTFOLIO_ROW, PortfolioView
from quant_hub.storage.bar_store import BarStore, InstrumentKey

_logger = logging.getLogger(__name__)

MARKET = ("french", "ff3.mkt_rf")
BILLS = ("french", "ff3.rf")
VIX = ("fred", "vixcls")
SIZES = (0.25, 0.5, 1.0)
INDEX_SURFACES = ("SPX", "SPY")
_GRID_K = np.linspace(-0.8, 0.4, 61)
_GRID_DAYS = np.array([7.0, 14.0, 30.0, 60.0, 91.0, 182.0, 273.0, 365.0, 548.0])


class HedgeDataError(LookupError):
    """Data the hedge lab needs isn't stored; the message says how to get it."""


@dataclass(frozen=True)
class Candidate:
    name: str
    kind: str                                  # cash, stream, trend, vol target, option
    program: H.OptionProgram | None = None
    rule: str = ""                             # a trend rule
    symbol: str = ""                           # a stream's symbol


def v1_candidates(streams: Sequence[str] = ()) -> tuple[Candidate, ...]:
    """HEDGE decision 2's list (module docstring)."""
    out: list[Candidate] = []
    for otm in (0.05, 0.10, 0.20):
        for months, tenor in ((1, 30), (3, 91), (12, 365)):
            out.append(Candidate(f"Put {otm:.0%} OTM, {months} mo", "option",
                                 H.OptionProgram(1 - otm, tenor)))
    out += [Candidate("Put spread 95/85, 1 mo", "option", H.OptionProgram(0.95, 30, short_put=0.85)),
            Candidate("Put spread 90/80, 3 mo", "option", H.OptionProgram(0.90, 91, short_put=0.80)),
            Candidate("Collar on the 10% put, 3 mo", "option", H.OptionProgram(0.90, 91, collar=True)),
            Candidate("Trend: 200-day average", "trend", rule="sma"),
            Candidate("Trend: 12-month momentum", "trend", rule="momentum"),
            Candidate("Vol targeting", "vol target"),
            Candidate("Cash", "cash")]
    out += [Candidate(f"Hold {s.upper()}", "stream", symbol=s.upper()) for s in streams]
    return tuple(out)


@dataclass(frozen=True)
class Book:
    """A book's daily returns with the market data its hedges need, on one
    calendar (``vix`` NaN where FRED has none)."""

    name: str
    beta: float
    returns: pd.Series          # R_B
    market: pd.Series           # the market's daily total return
    rf: pd.Series               # the bill's daily return
    vix: pd.Series              # a fraction; NaN where missing

    @property
    def spot(self) -> pd.Series:
        """The option underlying: the market's total-return level."""
        return 100.0 * (1.0 + self.market).cumprod()

    @property
    def rate(self) -> pd.Series:
        """The annual continuously compounded bill rate."""
        return np.log1p(self.rf) * H.TRADING_DAYS


@dataclass(frozen=True)
class Shape:
    """Today's vol shape for the option marks (module docstring)."""

    smile: H.Smile
    rho: float
    atm30: float | None
    half_spread: float
    label: str
    symbol: str = ""               # the chain's index, for whole contracts
    spot: float | None = None      # its spot when the chain was stored


@dataclass(frozen=True)
class Result:
    candidate: Candidate
    size: float
    first: date | None
    last: date | None
    assessment: H.Assessment | None = None
    program: H.ProgramRun | None = None
    missing: str = ""                          # why there's no assessment
    hedged: pd.Series | None = None            # R_H on the window
    unhedged: pd.Series | None = None          # R_B on the same days
    market: pd.Series | None = None            # R_m on the same days


@dataclass(frozen=True)
class HedgeRun:
    book: str
    beta: float
    shape: str
    results: tuple[Result, ...]


@dataclass(frozen=True)
class TodayCost:
    candidate: Candidate
    annual: float                  # a year, a fraction of the hedged notional
    percentile: float | None       # among the window's VIX-based costs
    first: date | None = None
    last: date | None = None


@dataclass(frozen=True)
class _Window:
    """One candidate's days: the book, its market and bills, and the VIX
    (options) or the stream (streams) it needs."""

    returns: pd.Series
    market: pd.Series
    rf: pd.Series
    vix: pd.Series | None = None
    other: pd.Series | None = None


class HedgeService:
    def __init__(self, external: ExternalDataService, chains: ChainService, bars: BarStore,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._external = external
        self._chains = chains
        self._bars = bars
        self._now = now

    # ------------------------------------------------------------- books
    def proxy_book(self, beta: float) -> Book:
        """A2's β proxy on French's market (module docstring). Raises
        ``HedgeDataError`` without French's series."""
        try:
            excess = self._external.series(*MARKET).values
            bills = self._external.series(*BILLS).values
        except LookupError as exc:
            raise HedgeDataError(str(exc)) from None
        frame = pd.concat({"x": excess, "r": bills}, axis=1, join="inner")
        market = frame["x"] + frame["r"]
        returns = H.beta_book(market, frame["r"], beta)
        return Book(f"β proxy at {beta:.2f}", beta, returns, market, frame["r"],
                    self._vix(market.index))

    def mix_book(self, view: PortfolioView, rate: float) -> Book:
        """Today's mix, as Portfolio replays it, plus its cash's interest."""
        if view.returns is None or view.benchmark not in view.returns.columns:
            raise HedgeDataError(f"no {view.benchmark} history in the replay")
        if not math.isfinite(rate):
            raise ValueError("the cash rate must be finite")
        report = view.report
        bills = pd.Series(rate / H.TRADING_DAYS, index=report.pnl.index)
        returns = report.pnl / report.equity + (view.cash / report.equity) * bills
        market = view.returns[view.benchmark].reindex(returns.index)
        beta = (view.regressions.beta(PORTFOLIO_ROW)[0] if view.regressions is not None
                else float(report.beta if report.beta is not None else 1.0))
        return Book("today's mix", beta, returns, market, bills, self._vix(returns.index))

    def stream(self, symbol: str) -> pd.Series:
        """A stored symbol's daily returns, adjusted where stored, read
        offline from the longest stored series."""
        symbol = symbol.strip().upper()
        stored = [s for s in self._bars.summary() if s.symbol == symbol and s.bar_size == "1 day"]
        if not stored:
            raise HedgeDataError(f"no {symbol} bars are stored: sync it on Markets first")
        best = max(stored, key=lambda s: (s.basis is PriceBasis.ADJUSTED, s.n_bars))
        frame = self._bars.query_bars(InstrumentKey(symbol), best.source, "1 day", best.first,
                                      best.last, best.basis)
        return frame["close"].pct_change().dropna()

    # ------------------------------------------------------------- shape
    def shape(self, flat: bool = False) -> Shape:
        """Today's shape from the newest stored index chain, or flat."""
        today_vix = self._latest_vix()
        for symbol in INDEX_SURFACES:
            try:
                surface, _ = load_market_surface(self._chains, symbol)
                chain, _ = self._chains.get_chain(symbol, "stored")
            except (NoStoredChainError, ValueError):
                continue
            atm30 = surface.point(surface.point(surface.spot, 30).forward, 30).vol
            spread = _half_spread(chain)
            rho = atm30 / today_vix if today_vix else 1.0
            when = surface.as_of_text
            if flat:
                return Shape(H.flat_smile, rho, atm30, spread,
                             f"flat smile, ρ {rho:.2f} from the {symbol} surface of {when}",
                             symbol, surface.spot)
            forwards = [surface.point(surface.spot, d).forward for d in _GRID_DAYS]
            grid = np.array([[surface.point(f * math.exp(k), d).vol / atm30 for k in _GRID_K]
                             for f, d in zip(forwards, _GRID_DAYS)])
            return Shape(H.grid_smile(_GRID_K, _GRID_DAYS, grid), rho, atm30, spread,
                         f"the {symbol} surface of {when}, ρ {rho:.2f}, half-spread {spread:.1%}",
                         symbol, surface.spot)
        return Shape(H.flat_smile, 1.0, None, 0.0,
                     "flat smile, ρ = 1, no costs: no stored SPX or SPY chain")

    # --------------------------------------------------------------- run
    def run(self, book: Book, candidates: Sequence[Candidate], sizes: Sequence[float] = SIZES,
            shape: Shape | None = None) -> HedgeRun:
        """Every candidate at every size on ``book``, each on its own window."""
        shape = shape if shape is not None else Shape(H.flat_smile, 1.0, None, 0.0, "flat, ρ = 1")
        results: list[Result] = []
        long_run = float(book.returns.std(ddof=1) * math.sqrt(H.TRADING_DAYS))
        for candidate in candidates:
            try:
                window = self._window(book, candidate)
            except HedgeDataError as exc:
                results += [Result(candidate, h, None, None, missing=str(exc)) for h in sizes]
                continue
            first, last = window.returns.index[0].date(), window.returns.index[-1].date()
            for h in sizes:
                try:
                    hedged, program = self._hedged(book, window, candidate, h, shape, long_run)
                    results.append(Result(candidate, h, first, last,
                                          H.assess(window.returns, hedged, window.market), program,
                                          hedged=hedged, unhedged=window.returns,
                                          market=window.market))
                except ValueError as exc:
                    results.append(Result(candidate, h, first, last, missing=str(exc)))
        _logger.info("hedge lab: %d candidates × %d sizes on %s (%s)", len(candidates), len(sizes),
                     book.name, shape.label)
        return HedgeRun(book.name, book.beta, shape.label, tuple(results))

    def today(self, book: Book, candidate: Candidate, shape: Shape) -> TodayCost:
        """§6: a put's or a put spread's cost a year at today's vol, and its
        percentile among the window's VIX-based costs."""
        program = candidate.program
        if program is None or program.collar:
            raise ValueError("today's cost is for a put or a put spread")
        window = self._window(book, candidate)
        rate = np.log1p(window.rf.to_numpy()) * H.TRADING_DAYS
        history = H.roll_cost(program, rate, 0.0, window.vix.to_numpy() * shape.rho, shape.smile)
        if shape.atm30 is None:
            today = float(history[-1])
            percentile = None
        else:
            today = float(H.roll_cost(program, rate[-1:], 0.0, np.array([shape.atm30]),
                                      shape.smile)[0])
            percentile = float((history < today).mean() + 0.5 * (history == today).mean())
        return TodayCost(candidate, today, percentile, window.returns.index[0].date(),
                         window.returns.index[-1].date())

    # ----------------------------------------------------------- helpers
    def _vix(self, index: pd.DatetimeIndex) -> pd.Series:
        try:
            vix = self._external.series(*VIX).values / 100.0
        except LookupError:
            return pd.Series(np.nan, index=index)
        return vix.reindex(index)

    def _latest_vix(self) -> float | None:
        try:
            vix = self._external.series(*VIX).values
        except LookupError:
            return None
        return float(vix.iloc[-1]) / 100.0 if len(vix) else None

    def _window(self, book: Book, candidate: Candidate) -> _Window:
        returns, market, rf = book.returns, book.market, book.rf
        days = returns.index[returns.notna() & market.notna()]
        window = _Window(returns.loc[days], market.loc[days], rf.loc[days])
        if candidate.kind == "option":
            have = book.vix.loc[days].dropna().index
            if len(have) < 2:
                raise HedgeDataError("FRED's VIX isn't stored for these days (Ctrl+K: Fetch "
                                     "FRED series)")
            days = days[days.isin(have) & (days >= have[0])]
            vix = book.vix.loc[days].ffill()
            window = _Window(returns.loc[days], market.loc[days], rf.loc[days], vix)
        elif candidate.kind == "stream":
            other = self.stream(candidate.symbol)
            days = days.intersection(other.index)
            if len(days) < 2:
                raise HedgeDataError(f"{candidate.symbol}'s history doesn't overlap the book's")
            window = _Window(returns.loc[days], market.loc[days], rf.loc[days],
                             other=other.loc[days])
        if len(window.returns) < 3:
            raise HedgeDataError("fewer than three days to assess")
        return window

    def _hedged(self, book: Book, w: _Window, candidate: Candidate, h: float, shape: Shape,
                long_run: float) -> tuple[pd.Series, H.ProgramRun | None]:
        if candidate.kind == "cash":
            return H.cash(w.returns, w.rf, h), None
        if candidate.kind == "stream":
            return H.blend(w.returns, w.other, h), None
        if candidate.kind == "trend":
            return H.scaled(w.returns, w.rf, H.trend_exposure(w.market, w.rf, candidate.rule), h), None
        if candidate.kind == "vol target":
            return H.scaled(w.returns, w.rf, H.vol_target_exposure(w.returns, long_run), h), None
        program = candidate.program
        if shape.half_spread and not program.half_spread:
            program = replace(program, half_spread=shape.half_spread)
        spot = 100.0 * (1.0 + w.market).cumprod()
        rate = np.log1p(w.rf) * H.TRADING_DAYS
        run = H.option_program(w.returns, spot, rate, w.vix, program, h, book.beta,
                               rho=shape.rho, smile=shape.smile)
        return run.returns, run


def _half_spread(chain) -> float:
    """The median relative half-spread of the chain's out-of-the-money puts."""
    spreads = [(q.ask - q.bid) / (q.ask + q.bid) for q in chain.quotes
               if q.option_type == "put" and q.strike < chain.spot and q.bid > 0 and q.ask > q.bid]
    return float(statistics.median(spreads)) if spreads else 0.0
