"""Market data facade: one call site, pluggable sources behind it.

Callers name their source **explicitly on every call** — there is no
"active source" (removed in M6, D23): a process-global mutable source meant
one page's combobox silently changed another page's data, which is exactly
the coupling class this application must not have. Pages own their source
choice; this service just routes and centralizes where caching/fallback
logic lands later.
"""

from __future__ import annotations

import logging
from datetime import datetime

from quant_hub.data.sources import BasisUnavailableError, DataSource
from quant_hub.data.types import PriceBasis, PriceHistory, Quote

_logger = logging.getLogger(__name__)


class UnknownSourceError(KeyError):
    """Raised when a source name is not registered."""


class MarketService:
    """Routes market-data requests to an explicitly named ``DataSource``."""

    def __init__(self, sources: list[DataSource]) -> None:
        if not sources:
            raise ValueError("MarketService requires at least one source")
        self._sources: dict[str, DataSource] = {s.name: s for s in sources}
        if len(self._sources) != len(sources):
            raise ValueError("duplicate source names")

    @property
    def source_names(self) -> list[str]:
        return list(self._sources)

    def get_history(
        self, symbol: str, start: datetime, end: datetime, source: str, *, basis: PriceBasis
    ) -> PriceHistory:
        """Daily history for ``symbol`` from the named source, on the named
        price basis: every caller says which, as it says the source (D23,
        D38). Return math wants ``ADJUSTED``; levels want ``TRADED``."""
        return self._resolve(source).get_history(symbol, start, end, basis)

    def get_return_history(self, symbol: str, start: datetime, end: datetime,
                           source: str) -> PriceHistory:
        """History for return math on pages with no Prices choice (D38):
        adjusted, or traded where the source holds no adjusted prices for
        the symbol (an import without ``adj_close``). The history's
        ``basis`` says which it is."""
        try:
            return self.get_history(symbol, start, end, source, basis=PriceBasis.ADJUSTED)
        except BasisUnavailableError as exc:
            _logger.info("%s: %s; using traded prices for returns", symbol, exc)
            return self.get_history(symbol, start, end, source, basis=PriceBasis.TRADED)

    def get_quote(self, symbol: str, source: str) -> Quote:
        """Latest available price for ``symbol`` from the named source."""
        return self._resolve(source).get_quote(symbol)

    def _resolve(self, source: str) -> DataSource:
        if source not in self._sources:
            raise UnknownSourceError(
                f"unknown data source {source!r}; available: {list(self._sources)}"
            )
        return self._sources[source]