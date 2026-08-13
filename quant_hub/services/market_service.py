"""Market data facade: one call site, pluggable sources behind it.

Callers ask the service for history/quotes and never learn whether the data
came from the synthetic world, IBKR, or a future cached/local provider. This
is deliberately thin in v0.1 -- it exists because it is where caching,
fallback between sources, and normalization will land later without touching
any caller.
"""

from __future__ import annotations

import logging
from datetime import datetime

from quant_hub.data.sources import DataSource
from quant_hub.data.types import PriceHistory, Quote

_logger = logging.getLogger(__name__)


class UnknownSourceError(KeyError):
    """Raised when a source name is not registered."""


class MarketService:
    """Routes market-data requests to the currently active ``DataSource``."""

    def __init__(self, sources: list[DataSource], active: str) -> None:
        if not sources:
            raise ValueError("MarketService requires at least one source")
        self._sources: dict[str, DataSource] = {s.name: s for s in sources}
        if len(self._sources) != len(sources):
            raise ValueError("duplicate source names")
        self.active_source = active  # validates via the setter

    @property
    def source_names(self) -> list[str]:
        return list(self._sources)

    @property
    def active_source(self) -> str:
        return self._active

    @active_source.setter
    def active_source(self, name: str) -> None:
        if name not in self._sources:
            raise UnknownSourceError(
                f"unknown data source {name!r}; available: {list(self._sources)}"
            )
        self._active = name
        _logger.info("Active data source: %s", name)

    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        source: str | None = None,
    ) -> PriceHistory:
        """Daily history for ``symbol`` from the active (or named) source."""
        return self._resolve(source).get_history(symbol, start, end)

    def get_quote(self, symbol: str, source: str | None = None) -> Quote:
        """Latest available price for ``symbol``."""
        return self._resolve(source).get_quote(symbol)

    def _resolve(self, source: str | None) -> DataSource:
        name = source if source is not None else self._active
        if name not in self._sources:
            raise UnknownSourceError(f"unknown data source {name!r}")
        return self._sources[name]
