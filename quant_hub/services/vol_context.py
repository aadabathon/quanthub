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
From the stored chains of one underlying and the daily bars of another
(SPX's chains with SPY's bars, decision 3), all on the worker thread:

* **Today's**, from the newest snapshot: each expiry's fitted K_var
  (``models/implied_variance``) against GARCH's expectation over its window,
  and the 30-day tenor against GARCH and against trailing realized.
* **The history**, one snapshot per trading day (the day's first, which the
  daily collector takes after the open): each one's 30-day implied variance
  against the realized variance that followed (ex post), and against the
  trailing realized. The mean ex-post premium carries its Newey–West error.
  Today's trailing premium is placed among the earlier ones.

**The history is the quote sum.** Each stored day is valued with the quote
sums alone (``quote_tenor``, the VIX's own number), not a fitted smile. A
fit takes about 0.3 s a snapshot, so a year of days would take over a
minute; the sum takes well under a millisecond, and needs no model. With
the wide variance request (decision 2) it holds about 98% of the variance.
Today's fitted value, wings included, is shown beside it, so the gap
between the two is visible.

**No look-ahead.** Only completed sessions count: a bar dated today is
dropped, since the session may still be open. GARCH sees only the bars up
to the newest snapshot's day. A window whose last bar doesn't exist yet is
pending, never averaged. Split-sized gaps (D57) are named, and their
windows left out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np

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
from quant_hub.models.vol_surface import fit_surface
from quant_hub.services.market_service import MarketService
from quant_hub.services.option_chains import ChainService

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
    returns = history.log_returns()
    parameters, h_next = _garch(returns.to_numpy())
    realized = tuple(
        (window, annualized_volatility(returns.iloc[-window:]))
        for window in REALIZED_WINDOWS
        if len(returns) >= window
    )
    return VolContext(
        symbol=history.symbol, source=history.source, history_end=history.end,
        returns_used=len(returns), realized=realized, garch=parameters,
        next_variance=h_next,
    )


# ------------------------------------------------------ the variance premium

@dataclass(frozen=True)
class ExpiryPremium:
    """One expiry of the newest snapshot: its fitted K_var against GARCH."""

    dte: float
    implied: SmileVariance
    garch: Premium
    trading_days: int


@dataclass(frozen=True)
class VariancePremiumView:
    """Everything the variance-premium card shows (module docstring)."""

    symbol: str
    realized_symbol: str
    source: str
    as_of: datetime
    tenor_days: float
    days_with_snapshots: int
    expiries: tuple[ExpiryPremium, ...]
    tenor: TenorVariance | None
    """The fitted K_var at the tenor; ``None`` when no two expiries bracket it."""
    quote_tenor: QuoteTenor | None
    """The quote sums alone at the tenor, for today's snapshot."""
    garch_tenor: Premium | None
    trailing: Premium | None
    """Today's quote-sum tenor against trailing realized: the history's measure."""
    trailing_percentile: float | None
    ex_post: PremiumSeries
    trailing_history: PremiumSeries
    split_gaps: tuple[SplitGap, ...]
    notes: tuple[str, ...]


def load_variance_premium(
    market: MarketService, chains: ChainService, symbol: str, source: str,
    realized_symbol: str | None = None, tenor_days: float = HEADLINE_DAYS,
    lookback_days: int = 1825, now: datetime | None = None,
) -> VariancePremiumView:
    """Blocking (snapshots, history, a fit and GARCH): run on the TaskRunner."""
    symbol = symbol.strip().upper()
    realized = (realized_symbol or symbol).strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    end = now or datetime.now()
    summaries = sorted(chains.snapshots(symbol), key=lambda s: (s.as_of, s.id))
    if not summaries:
        raise ValueError(f"no stored {symbol} chains yet: fetch one on the Volatility Lab, "
                         f"or add {symbol} to [collect] chains")
    notes: list[str] = []

    # Today's: the newest snapshot, fitted.
    newest = summaries[-1]
    term = implied_variance(fit_surface(implied_carry(chains.load(newest.id)).chain))

    # The history: each trading day's first snapshot, valued by its quote sums.
    first_of_day: dict[date, int] = {}
    for s in summaries:
        first_of_day.setdefault(s.as_of.date(), s.id)
    by_id = {s.id: s for s in summaries}
    points: list[ImpliedPoint] = []
    unbracketed = 0
    today_quotes: QuoteTenor | None = None
    for day, snapshot_id in sorted(first_of_day.items()):
        quoted = quote_tenor(implied_carry(chains.load(snapshot_id)).chain, tenor_days)
        if quoted is None or not quoted.mid > 0:
            unbracketed += 1
            continue
        points.append(ImpliedPoint(by_id[snapshot_id].as_of, tenor_days, quoted.mid))
        if day == newest.as_of.date():
            today_quotes = quoted
    if unbracketed:
        notes.append(f"{unbracketed} stored day(s) had no two expiries with a quote sum "
                     f"around {tenor_days:g} days, and are left out.")

    # Bars: completed sessions only, from before the first window to today.
    start = min(end - timedelta(days=lookback_days),
                summaries[0].as_of - timedelta(days=tenor_days + 10))
    history = market.get_return_history(realized, start, end, source=source)
    if history.offline_reason:
        notes.append(f"{realized} bars are stored ones only: {history.offline_reason}")
    closes = history.closes[np.array([ts.date() < end.date() for ts in history.closes.index])]
    gaps = history.split_gaps
    gap_days = {g.day.date() for g in gaps}
    if gaps:
        notes.append(f"{realized} has {len(gaps)} split-sized gap(s) (D57); windows over them "
                     "are left out.")

    # GARCH, on the bars up to the newest snapshot's day.
    day0 = newest.as_of.date()
    seen = closes[np.array([ts.date() <= day0 for ts in closes.index])]
    expiries: list[ExpiryPremium] = []
    garch_tenor: Premium | None = None
    returns = np.log(seen.to_numpy()[1:] / seen.to_numpy()[:-1])
    try:
        parameters, h_next = _garch(returns)
    except ValueError as exc:
        notes.append(f"No GARCH comparison: {exc}.")
    else:
        last_bar = seen.index[-1].date()
        for e in term.expiries:
            point = ImpliedPoint(newest.as_of, e.dte, e.total)
            expiries.append(ExpiryPremium(
                e.dte, e.smile, against_garch(point, parameters, h_next, last_bar),
                len(window_after(day0, e.dte))))
        tenor = term.tenor(tenor_days)
        if tenor is not None:
            garch_tenor = against_garch(ImpliedPoint(newest.as_of, tenor_days, tenor.smile.total),
                                        parameters, h_next, last_bar)

    ex_post = ex_post_series(points, closes, gap_days)
    trailing = trailing_series(points, closes, gap_days)
    today = next((p for p in trailing.points if p.as_of.date() == day0), None)
    earlier = [p.premium.variance for p in trailing.points if p.as_of.date() < day0]
    return VariancePremiumView(
        symbol=symbol, realized_symbol=realized, source=history.source, as_of=newest.as_of,
        tenor_days=tenor_days, days_with_snapshots=len(first_of_day),
        expiries=tuple(expiries), tenor=term.tenor(tenor_days), quote_tenor=today_quotes,
        garch_tenor=garch_tenor, trailing=today.premium if today else None,
        trailing_percentile=percentile(today.premium.variance, earlier) if today else None,
        ex_post=ex_post, trailing_history=trailing, split_gaps=gaps, notes=tuple(notes))
