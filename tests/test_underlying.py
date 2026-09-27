"""The underlying-snapshot service: spot + realized vol from any source."""

from datetime import datetime

import pytest

from quant_hub.broker.types import NoMarketDataError
from quant_hub.data.sources import SyntheticSource
from quant_hub.data.stats import annualized_volatility
from quant_hub.services.market_service import MarketService
from quant_hub.services.underlying import load_underlying

NOW = datetime(2026, 9, 25)


class _NoQuoteSource(SyntheticSource):
    """A source whose quotes are unavailable, like an unentitled IBKR symbol."""

    name = "noquote"

    def get_quote(self, symbol):
        raise NoMarketDataError("requires additional subscription")


def test_synthetic_snapshot_uses_quote_and_realized_vol():
    market = MarketService([SyntheticSource()])
    snap = load_underlying(market, " spy ", "synthetic", lookback_days=365, now=NOW)
    history = SyntheticSource().get_history("SPY", datetime(2025, 9, 25), NOW)
    assert snap.symbol == "SPY" and snap.source == "synthetic"
    assert snap.spot_basis == "quote · synthetic"
    assert snap.realized_vol == pytest.approx(annualized_volatility(history.log_returns()))
    assert snap.returns_used == len(history) - 1


def test_unavailable_quote_falls_back_to_last_close():
    market = MarketService([_NoQuoteSource()])
    snap = load_underlying(market, "QQQ", "noquote", now=NOW)
    history = SyntheticSource().get_history("QQQ", datetime(2025, 9, 25), NOW)
    assert snap.spot_basis == "last close"
    assert snap.spot == pytest.approx(float(history.closes.iloc[-1]))
    assert snap.spot_time == history.end


@pytest.mark.parametrize("symbol,lookback", [("", 365), ("SPY", 1), ("SPY", 10)])
def test_rejects_unusable_requests(symbol, lookback):
    with pytest.raises(ValueError):
        load_underlying(MarketService([SyntheticSource()]), symbol, "synthetic", lookback, now=NOW)
