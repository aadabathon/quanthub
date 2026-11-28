"""The desk's test fixtures: a made-up book, its history and daily closes
(D1, D96). ``conftest.py`` serves ``parts`` as a fixture; the rest is imported.

The book: AAA 10 and a put on it, $500 in a money-market fund, exported on
Nov 10; then a buy of 4 BBB on Nov 12 and a sale of 2 AAA on Nov 13.
"""

from datetime import date, datetime

import numpy as np
import pandas as pd

from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot
from quant_hub.data.sources import DataSource
from quant_hub.data.types import PriceHistory
from quant_hub.services.activity import ActivityService
from quant_hub.services.desk import DeskService
from quant_hub.services.market_service import MarketService
from quant_hub.storage.activity_store import ActivityStore
from quant_hub.storage.desk_store import DeskStore
from quant_hub.storage.position_store import PositionStore

NOW = datetime(2026, 11, 24, 17, 0)
HISTORY = ("date,account,kind,symbol,quantity,price,commission,fees,amount\n"
           "2026-11-12,Brokerage,buy,BBB,4,25.00,0,0,-100.00\n"
           "2026-11-13,Brokerage,sell,AAA,-2,40.00,0,0,80.00\n")


class Bars(DataSource):
    """Daily closes ending on a chosen day per symbol; anything else is missing."""

    name = "test"

    def __init__(self, ends: dict[str, str], sessions: int = 300) -> None:
        self._ends, self._sessions = ends, sessions

    def get_history(self, symbol, start, end, basis):
        if symbol not in self._ends:
            raise ValueError(f"no bars for {symbol}")
        index = pd.bdate_range(end=self._ends[symbol], periods=self._sessions)
        rng = np.random.default_rng(len(symbol))
        closes = 40 * np.exp(np.cumsum(rng.normal(0, 0.01, len(index))))
        return PriceHistory(symbol, self.name, pd.DataFrame(
            {"open": closes, "high": closes, "low": closes, "close": closes,
             "volume": np.full(len(index), 1e6)}, index=index), basis)

    def get_quote(self, symbol):
        raise NotImplementedError


def build_parts(tmp_path):
    """The activity service over the made-up book, an empty desk store, and
    what to close after: ``conftest.parts``."""
    activity_store, positions = ActivityStore(":memory:"), PositionStore(":memory:")
    activity = ActivityService(activity_store, positions)
    put = OptionContract("AAA", "put", 30.0, date(2026, 12, 18))
    export = PositionSnapshot((Position("AAA", Holding.EQUITY, 10.0, "Brokerage"),
                               Position("AAA261218P30", Holding.OPTION, 1.0, "Brokerage",
                                        option=put),
                               Position("MMFXX", Holding.CASH, 500.0, "Brokerage", value=500.0)),
                              datetime(2026, 11, 10), "Generic", "nov10.csv")
    positions.save(export, datetime(2026, 11, 10, 18), "aa")
    path = tmp_path / "history.csv"
    path.write_text(HISTORY, encoding="utf-8")
    activity.import_activity(path)
    store = DeskStore(":memory:")
    return activity, store, (activity_store, positions, store)


def service(parts, ends, **kw) -> DeskService:
    """A desk over ``parts`` whose closes end on ``ends`` per symbol."""
    activity, store, folder = parts
    return DeskService(store, MarketService([Bars(ends)]), activity, folder / "desk", **kw)
