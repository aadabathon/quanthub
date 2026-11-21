"""Made-up holdings for tests that need a book: a positions export and two
years of daily bars. Every symbol, quantity and price here is invented."""

from datetime import datetime

import numpy as np
import pandas as pd

from quant_hub.models.portfolio import correlated_returns
from quant_hub.storage.bar_store import BarStore, InstrumentKey

NOW = datetime(2026, 10, 23, 9, 0)
HEADER = ("Symbol,Quantity,Last,$ Chg,% Chg,Volume,$ Day G/L,% Day G/L,$ Total G/L,"
          "% Total G/L,Value,Basis,Day Range,52W Range,Earnings Date,Div Amt,Div Ex-Date")


def export(day: str, holdings: dict[str, float], cash: float) -> str:
    """A made-up Positions-view download as of ``day`` (MM/DD/YYYY), at $100."""
    lines = [f'"Positions \nAll Accounts \nas of {day} at 4:00:00 PM"', "", HEADER]
    for symbol, quantity in holdings.items():
        lines.append(f"{symbol},{quantity},100.00,0,0,0,--,--,--,--,{quantity * 100:.2f},"
                     "--,--,--,--,--,--")
    lines.append(f"Cash (SPAXX),{cash:.2f},1.00,0,0,0,--,--,--,--,{cash:.2f},--,--,--,--,--,--")
    return "\n".join(lines) + "\n"


def bars(store: BarStore | None = None, end="2026-10-22") -> tuple[BarStore, pd.Timestamp]:
    """560 sessions of made-up daily closes ending at ``end``, under the
    "file" source, and the day of CCC's planted +500% jump."""
    store = store if store is not None else BarStore(":memory:")
    days = pd.bdate_range(end=end, periods=560)
    vols = np.array([0.010, 0.012, 0.015, 0.018])
    corr = np.array([[1, .6, .5, .2], [.6, 1, .5, .2], [.5, .5, 1, .1], [.2, .2, .1, 1]])
    r = correlated_returns(vols, corr, len(days), np.full(4, 0.0003), seed=21)
    frame = pd.DataFrame(r, index=days, columns=["SPY", "AAA", "BBB", "CCC"])
    jump = days[300]
    frame.loc[jump, "CCC"] = 5.0
    for symbol in frame.columns:
        closes = 100.0 * (1.0 + frame[symbol]).cumprod()
        store.insert_bars(InstrumentKey(symbol), "file", "1 day", pd.DataFrame({"close": closes}))
    return store, jump
