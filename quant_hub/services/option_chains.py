"""Option chains by named source, every live fetch saved (D33, D34).

Pages ask for a chain by symbol and *named* source, exactly as they ask
``MarketService`` for history (D23); there is no global active provider.

* **Live sources** (IBKR today) implement ``data.options.OptionChainSource``.
  Every chain they return is saved to the ``ChainStore`` before the page
  sees it, so the app accumulates its own option history (D33).
* **"stored"** serves the newest saved snapshot for a symbol. It needs no
  broker connection, so anyone can work with chains fetched earlier, or
  copied in from elsewhere (D34).

Runs on the task-worker thread: a live fetch blocks for 15–40 s.
"""

from __future__ import annotations

import logging

from quant_hub.data.options import ChainRequest, OptionChain, OptionChainSource
from quant_hub.models.carry import implied_carry
from quant_hub.models.market_surface import MarketSurface
from quant_hub.models.vol_surface import fit_surface
from quant_hub.storage.chain_store import ChainStore

_logger = logging.getLogger(__name__)

STORED = "stored"


class UnknownChainSourceError(KeyError):
    """No chain source is registered under that name."""


class NoStoredChainError(LookupError):
    """The store holds no snapshot for that symbol."""


class ChainService:
    def __init__(self, sources: list[OptionChainSource], store: ChainStore) -> None:
        self._sources = {source.name: source for source in sources}
        if STORED in self._sources:
            raise ValueError(f"{STORED!r} is reserved for saved snapshots")
        self._store = store

    @property
    def source_names(self) -> list[str]:
        return [*self._sources, STORED]

    def get_chain(self, symbol: str, source: str,
                  request: ChainRequest | None = None) -> tuple[OptionChain, int]:
        """The chain and its snapshot id. Live chains are saved first."""
        symbol = symbol.upper().strip()
        if source == STORED:
            summaries = self._store.snapshots(symbol)
            if not summaries:
                raise NoStoredChainError(f"no saved {symbol} chain yet: fetch one from a live "
                                         f"source first")
            return self._store.load(summaries[0].id), summaries[0].id
        try:
            provider = self._sources[source]
        except KeyError:
            raise UnknownChainSourceError(
                f"unknown chain source {source!r}; available: {self.source_names}") from None
        chain = provider.get_chain(symbol, request or ChainRequest())
        snapshot = self._store.save(chain)
        _logger.info("%s chain from %s saved as snapshot #%d", symbol, source, snapshot)
        return chain, snapshot


def load_market_surface(chains: ChainService, symbol: str) -> tuple[MarketSurface, int]:
    """The newest saved chain for ``symbol`` as a market surface (C4): implied
    carry, then the default independent SVI fit (D35). Worker thread."""
    chain, snapshot = chains.get_chain(symbol, STORED)
    return MarketSurface(fit_surface(implied_carry(chain).chain)), snapshot
