"""Volatility context for an underlying: realized vols, a GARCH forecast, and
the variance risk premium.

The model-side half of the Volatility Lab's term-structure comparison. For
a named source (``synthetic``, or ``ibkr`` via the SQLite-first cache) it
loads daily history and returns:

* realized vol over the trailing 21/63/126/252 trading days (annualized
  close-to-close, ``data/stats.annualized_volatility``);
* a GARCH(1,1) fit to the full lookback, whose average-variance forecast
  (``models/garch.average_variance_forecast``) gives the model's
  expected-vol term structure.

Implied vols price exactly that expectation (plus a risk premium), so this
is what the lab draws beside the ATM IV term structure.

Horizons: option maturities are calendar days and GARCH periods are trading
days. ``garch_vol`` draws a smooth curve with ``n = calendar_days ×
252/365``. The premium counts the window's NYSE trading days exactly
(VARIANCE decision 6).

The variance risk premium (VARIANCE I2, ``load_variance_premium``)
------------------------------------------------------------------
From a fitted chain, the stored chains of its underlying and the daily bars
of another symbol (SPX's chains with SPY's bars, decision 3), all on the
worker thread:

* **Today's**, from the fit it's given: the one on the Volatility Lab, so
  the card describes the chain the page draws (D15). Each expiry's fitted
  K_var (``models/implied_variance``) against GARCH's expectation over its
  window and against the realized variance of the same length before it.
  The 30-day tenor against GARCH, and its quote sum against trailing
  realized. GARCH is fitted on the lookback's bars up to the chain's day,
  and returned as a ``VolContext``, so the lab's overlay draws the same fit
  the card reads.
* **The history**, one stored snapshot per trading day (the day's first,
  which the daily collector takes after the open): each one's 30-day
  implied variance against the realized variance that followed (ex post),
  and against the trailing realized. The mean ex-post premium carries its
  Newey–West error. Today's trailing premium is placed among the earlier
  days'. A chain with no stored snapshots (a synthetic one) has no history.

**The history is the quote sum.** Each stored day is valued with the quote
sums alone (``quote_tenor``, the VIX's own number), not a fitted smile. A
fit takes about 0.3 s a snapshot, so a year of days would take over a
minute; the sum takes well under a millisecond, and needs no model. With
the wide variance request (decision 2) it holds about 98% of the variance.
Today's fitted value, wings included, is shown beside it, so the gap
between the two is visible.

**No look-ahead.** Only completed sessions count: a bar dated today is
dropped, since the session may still be open. GARCH sees only the bars up
to the fitted chain's day. A window whose last bar doesn't exist yet is
pending, never averaged. Split-sized gaps (D57) are named, and their
windows left out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from quant_hub.data.splits import SplitGap
from quant_hub.data.stats import TRADING_DAYS_PER_YEAR, annualized_volatility
from quant_hub.models.carry import implied_carry
from quant_hub.models.garch import (
    GARCH11Parameters,
    average_variance_forecast,
    fit_garch11,
    next_variance,
)
from quant_hub.models.implied_variance import (
    HEADLINE_DAYS,
    QuoteTenor,
    SmileVariance,
    TenorVariance,
    implied_variance,
    quote_tenor,
)
from quant_hub.models.variance_premium import (
    ImpliedPoint,
    Premium,
    PremiumSeries,
    against_garch,
    ex_post_series,
    percentile,
    trailing_series,
    window_after,
)
from quant_hub.models.vol_surface import SurfaceFit
from quant_hub.services.market_service import MarketService
from quant_hub.services.option_chains import ChainService
from quant_hub.storage.chain_store import ChainSnapshotSummary

REALIZED_WINDOWS = (21, 63, 126, 252)
"""Trailing windows in trading days (~1, 3, 6, 12 months)."""

CALENDAR_DAYS_PER_YEAR = 365.0


@dataclass(frozen=True)
class VolContext:
    symbol: str
    source: str
    history_end: datetime
    returns_used: int
    realized: tuple[tuple[int, float], ...]
    """``(window in trading days, annualized vol)`` for each window that fits."""
    garch: GARCH11Parameters
    next_variance: float

    def garch_vol(self, calendar_days: np.ndarray) -> np.ndarray:
        """Annualized GARCH expected vol over each horizon (calendar days)."""
        trading = np.asarray(calendar_days, dtype=float) * TRADING_DAYS_PER_YEAR / CALENDAR_DAYS_PER_YEAR
        average = average_variance_forecast(self.garch, self.next_variance, trading)
        return np.sqrt(average * TRADING_DAYS_PER_YEAR)

    @staticmethod
    def calendar_days(trading_days: int) -> float:
        return trading_days * CALENDAR_DAYS_PER_YEAR / TRADING_DAYS_PER_YEAR


def _garch(returns: np.ndarray) -> tuple[GARCH11Parameters, float]:
    """A GARCH(1,1) fit and the next day's variance after the last return."""
    fit = fit_garch11(returns)
    h_next = next_variance(fit.parameters, float(returns[-1]), float(fit.conditional_variances[-1]))
    if not math.isfinite(h_next) or h_next <= 0:
        raise ValueError("GARCH forecast is not positive")
    return fit.parameters, h_next


def load_vol_context(
    market: MarketService, symbol: str, source: str, lookback_days: int = 1825,
    now: datetime | None = None,
) -> VolContext:
    """Blocking (history fetch + GARCH fit): run on the TaskRunner worker."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    if lookback_days < 60:
        raise ValueError("lookback must be at least 60 days for a GARCH fit")
    end = now or datetime.now()
    history = market.get_return_history(symbol, end - timedelta(days=lookback_days), end,
                                        source=source)               # return math (D38)
    return _context(history.symbol, history.source, history.log_returns(), history.end)


def _context(symbol: str, source: str, returns: pd.Series, end: datetime) -> VolContext:
    """GARCH and the trailing realized vols from daily log returns."""
    parameters, h_next = _garch(returns.to_numpy())
    realized = tuple(
        (window, annualized_volatility(returns.iloc[-window:]))
        for window in REALIZED_WINDOWS
        if len(returns) >= window
    )
    return VolContext(
        symbol=symbol, source=source, history_end=end, returns_used=len(returns),
        realized=realized, garch=parameters, next_variance=h_next,
    )


# ------------------------------------------------------ the variance premium

@dataclass(frozen=True)
class ExpiryPremium:
    """One fitted expiry: its K_var against GARCH, and against the realized
    variance of the same length before the chain (``None`` when there's no
    comparison: GARCH failed, or that window lacks a bar)."""

    dte: float
    implied: SmileVariance
    garch: Premium | None
    trailing: Premium | None
    trading_days: int


@dataclass(frozen=True)
class VariancePremiumView:
    """Everything the variance-premium cards show (module docstring)."""

    symbol: str
    realized_symbol: str
    source: str
    as_of: datetime
    tenor_days: float
    tenor_trading_days: int
    """NYSE sessions in the tenor's window after the chain (decision 6)."""
    days_with_snapshots: int
    expiries: tuple[ExpiryPremium, ...]
    tenor: TenorVariance | None
    """The fitted K_var at the tenor; ``None`` when no two expiries bracket it."""
    quote_tenor: QuoteTenor | None
    """The quote sums alone at the tenor, for the fitted chain."""
    garch_tenor: Premium | None
    trailing: Premium | None
    """The quote-sum tenor against trailing realized: the history's measure."""
    trailing_percentile: float | None
    ex_post: PremiumSeries
    trailing_history: PremiumSeries
    split_gaps: tuple[SplitGap, ...]
    notes: tuple[str, ...]
    context: VolContext | None = None
    """GARCH and trailing realized as of the chain: the fit behind the GARCH
    columns, for the term structure to draw (``None`` when GARCH failed)."""


def load_variance_premium(
    market: MarketService, chains: ChainService, fit: SurfaceFit, source: str,
    realized_symbol: str, tenor_days: float = HEADLINE_DAYS, lookback_days: int = 1825,
    now: datetime | None = None, history: bool = True,
) -> VariancePremiumView:
    """Blocking (snapshots, history and GARCH): run on the TaskRunner.

    ``fit`` is today's surface: its chain is the one priced (carry implied,
    for a market chain), and its symbol names the stored history. Bars come
    from ``realized_symbol`` at ``source``. ``history=False`` skips the
    stored snapshots (a synthetic chain has none)."""
    chain = fit.chain
    symbol, realized = chain.symbol.upper(), realized_symbol.strip().upper()
    if not realized:
        raise ValueError("a symbol for the realized side is required")
    if lookback_days < 60:
        raise ValueError("lookback must be at least 60 days for a GARCH fit")
    end = now or datetime.now()
    as_of, day0 = chain.as_of, chain.as_of.date()
    term = implied_variance(fit)
    notes: list[str] = []

    # The history: each trading day's first stored snapshot, valued by its
    # quote sums. Today's is the fitted chain's own.
    summaries = sorted(chains.snapshots(symbol) if history else [],
                       key=lambda s: (s.as_of, s.id))
    first_of_day: dict[date, ChainSnapshotSummary] = {}
    for s in summaries:
        first_of_day.setdefault(s.as_of.date(), s)
    points: list[ImpliedPoint] = []
    unbracketed = 0
    for s in first_of_day.values():
        quoted = quote_tenor(implied_carry(chains.load(s.id)).chain, tenor_days)
        if quoted is None or not quoted.mid > 0:
            unbracketed += 1
            continue
        points.append(ImpliedPoint(s.as_of, tenor_days, quoted.mid))
    if history and not summaries:
        notes.append(f"No stored {symbol} chains, so no history: fetch one a day on the "
                     f"Volatility Lab, or add {symbol} to [collect] chains.")
    if unbracketed:
        notes.append(f"{unbracketed} stored day(s) had no two expiries with a quote sum "
                     f"around {tenor_days:g} days, and are left out.")
    today_quotes = quote_tenor(chain, tenor_days)
    if today_quotes is not None and not today_quotes.mid > 0:
        today_quotes = None
    tenor = term.tenor(tenor_days)
    if tenor is None:
        notes.append(f"No two fitted expiries bracket {tenor_days:g} days.")

    # Bars: completed sessions only, from before the earliest window (the
    # GARCH lookback, the longest expiry's trailing window, the history's
    # first) to today.
    earliest = min(s.as_of for s in summaries) if summaries else as_of
    longest = max(e.dte for e in term.expiries)
    start = min(as_of - timedelta(days=lookback_days), as_of - timedelta(days=longest + 10),
                earliest - timedelta(days=tenor_days + 10))
    history = market.get_return_history(realized, start, end, source=source)
    if history.offline_reason:
        notes.append(f"{realized} bars are stored ones only: {history.offline_reason}")
    closes = history.closes[np.array([ts.date() < end.date() for ts in history.closes.index])]
    gaps = history.split_gaps
    gap_days = {g.day.date() for g in gaps}
    if gaps:
        notes.append(f"{realized} has {len(gaps)} split-sized gap(s) (D57); windows over them "
                     "are left out.")

    # GARCH and the trailing vols, on the lookback's bars up to the chain's day.
    first = (as_of - timedelta(days=lookback_days)).date()
    seen = closes[np.array([first <= ts.date() <= day0 for ts in closes.index])]
    context: VolContext | None = None
    garch: tuple[GARCH11Parameters, float, date] | None = None
    try:
        if len(seen) < 3:
            raise ValueError(f"{len(seen)} {realized} bar(s) up to {day0:%Y-%m-%d}")
        returns = pd.Series(np.log(seen.to_numpy()[1:] / seen.to_numpy()[:-1]),
                            index=seen.index[1:])
        context = _context(realized, history.source, returns, seen.index[-1].to_pydatetime())
        garch = (context.garch, context.next_variance, seen.index[-1].date())
    except ValueError as exc:
        notes.append(f"No GARCH comparison: {exc}.")

    def against(days: float, total: float) -> tuple[Premium | None, Premium | None]:
        point = ImpliedPoint(as_of, days, total)
        trailing = trailing_series([point], closes, gap_days)
        return (against_garch(point, *garch) if garch is not None else None,
                trailing.points[0].premium if trailing.points else None)

    expiries = tuple(ExpiryPremium(e.dte, e.smile, *against(e.dte, e.total),
                                   len(window_after(day0, e.dte)))
                     for e in term.expiries)
    if any(e.trailing is None for e in expiries):
        short = ", ".join(f"{e.dte:.0f}d" for e in expiries if e.trailing is None)
        notes.append(f"No trailing realized for {short}: the window lacks {realized} bars "
                     "or holds a split-sized gap.")
    garch_tenor = against(tenor_days, tenor.smile.total)[0] if tenor is not None else None
    today = against(tenor_days, today_quotes.mid)[1] if today_quotes is not None else None

    ex_post = ex_post_series(points, closes, gap_days)
    trailing_history = trailing_series(points, closes, gap_days)
    earlier = [p.premium.variance for p in trailing_history.points if p.as_of.date() < day0]
    return VariancePremiumView(
        symbol=symbol, realized_symbol=realized, source=history.source, as_of=as_of,
        tenor_days=tenor_days, tenor_trading_days=len(window_after(day0, tenor_days)),
        days_with_snapshots=len(first_of_day), expiries=expiries,
        tenor=tenor, quote_tenor=today_quotes, garch_tenor=garch_tenor, trailing=today,
        trailing_percentile=percentile(today.variance, earlier) if today else None,
        ex_post=ex_post, trailing_history=trailing_history, split_gaps=gaps,
        notes=tuple(notes), context=context)
