r"""Massive's daily bars as a ``BarSource``, on both price bases (D97).

``providers/massive.py`` fetches; this turns its rows into the bars
``CachedSource`` stores and serves under the source name ``massive``,
exactly as it does IBKR's.

**Traded** bars are Massive's split-adjusted ones, the same basis as
IBKR's TRADES (D38): prices as traded, rescaled at each split so a split
isn't a fake fall.

**Adjusted** bars (D38: adjusted for splits *and* dividends, the basis
return math uses) are built here, because Massive's ``adjusted=true`` covers
splits only. The usual construction, the one IBKR's and Yahoo's adjusted
closes use: each ex-dividend date e scales every earlier price by

.. math::  f_e = 1 - \frac{D_e}{P_{e^-}}

where :math:`D_e` is the cash per share and :math:`P_{e^-}` the traded
close on the last session before e, both split-adjusted, so on one scale
(the ratio is the same as with both as paid). The adjusted price is the
traded one times every factor still to come:

.. math::  A_t = P_t \prod_{e > t} f_e

That keeps prices on today's scale, as adjusted closes are. A return
across an ex-date then carries the dividend:
:math:`A_e / A_{e^-} = S_e / (S_{e^-} f_e)`. Open, high and low take the same
factor as the close (D38); volume stays as split-adjusted.

To have :math:`P_{e^-}` for every dividend that affects the window, the
adjusted fetch reads bars from a week before the window through today,
then trims to the window: two requests (the bars and the dividends), against
one for traded. ``CachedSource`` refetches the adjusted span whole once a
day anyway, since each new dividend rescales everything before it.

No time-of-day quote: the free plan is end of day, so ``get_quote`` says
so, and callers use the last close (``services/underlying.py``).

Runs on the task-worker thread, or from the command line.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from quant_hub.broker.types import NoMarketDataError
from quant_hub.data.sources import SourceOfflineError
from quant_hub.data.types import PriceBasis, PriceHistory, Quote
from quant_hub.providers.massive import (
    SOURCE,
    Bar,
    Dividend,
    MassiveClient,
    MassiveError,
    MassiveUnreachable,
)

_LEAD = timedelta(days=7)


class MassiveOffline(SourceOfflineError):
    """Massive can't be reached: stored bars may still answer (V0)."""


def dividend_adjusted(traded: list[Bar], dividends: list[Dividend]) -> list[Bar]:
    """Traded bars carried to the adjusted basis (module docstring). A
    dividend with no close before it in ``traded`` is skipped: it affects no
    bar here. So is one not yet ex by the last bar: adjusted prices are on
    the latest bar's scale, and a dividend still to come doesn't change it."""
    closes = sorted((b.day, b.close) for b in traded)
    last = closes[-1][0] if closes else None
    factors: list[tuple[date, float]] = []
    for d in sorted(dividends, key=lambda d: d.ex_date):
        if last is None or d.ex_date > last:
            continue
        before = [c for day, c in closes if day < d.ex_date]
        if not before or d.cash <= 0:
            continue
        if d.cash >= before[-1]:
            raise MassiveError(f"a dividend of {d.cash} on {d.ex_date} is not below the "
                               f"close before it ({before[-1]})")
        factors.append((d.ex_date, 1.0 - d.cash / before[-1]))
    out = []
    for b in traded:
        f = 1.0
        for ex_date, factor in factors:
            if ex_date > b.day:
                f *= factor
        out.append(Bar(b.day, b.open * f, b.high * f, b.low * f, b.close * f, b.volume))
    return out


def bars_frame(bars: list[Bar]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {"open": [b.open for b in bars], "high": [b.high for b in bars],
         "low": [b.low for b in bars], "close": [b.close for b in bars],
         "volume": [b.volume for b in bars]},
        index=pd.DatetimeIndex([pd.Timestamp(b.day) for b in bars]))
    return frame.sort_index()


class MassiveSource:
    """A ``BarSource`` (module docstring)."""

    name = SOURCE

    def __init__(self, client: MassiveClient, today=date.today) -> None:
        self._client = client
        self._today = today

    def fetch_bars(self, symbol: str, start: datetime, end: datetime, bar_size: str = "1 day",
                   basis: PriceBasis = PriceBasis.TRADED) -> pd.DataFrame:
        if bar_size != "1 day":
            raise ValueError(f"Massive serves daily bars here, not {bar_size!r}")
        symbol = symbol.upper().strip()
        try:
            if basis is PriceBasis.TRADED:
                bars = self._client.bars(symbol, start.date(), end.date())
            else:
                lead, today = start.date() - _LEAD, max(end.date(), self._today())
                traded = self._client.bars(symbol, lead, today)
                bars = dividend_adjusted(traded, self._client.dividends(symbol, lead, today))
        except MassiveUnreachable as exc:
            raise MassiveOffline(str(exc)) from None
        if not bars:
            return bars_frame([])
        return bars_frame(bars).loc[pd.Timestamp(start.date()):pd.Timestamp(end.date())]

    def get_history(self, symbol: str, start: datetime, end: datetime,
                    basis: PriceBasis = PriceBasis.TRADED) -> PriceHistory:
        frame = self.fetch_bars(symbol, start, end, basis=basis)
        if len(frame) == 0:
            raise ValueError(f"Massive returned no daily bars for {symbol.upper()} in "
                             f"{start:%Y-%m-%d}..{end:%Y-%m-%d}")
        return PriceHistory(symbol=symbol.upper().strip(), source=self.name, frame=frame,
                            basis=basis)

    def get_quote(self, symbol: str) -> Quote:
        raise NoMarketDataError("Massive's free plan is end of day: no live quote")
