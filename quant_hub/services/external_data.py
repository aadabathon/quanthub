"""External series for the models: fetched, stored, served offline (D60).

The data batch (D59) accumulates free series in the store. This service
fetches them, through the providers, and serves them back from the store.

* ``refresh_french`` brings Ken French's daily files. A dataset is fetched
  when its stored copy is more than ``FRENCH_STALE_AFTER`` old, or always
  with ``force``. The library rebuilds the files monthly, so a week-old
  copy has missed at most one rebuild. Each dataset succeeds or fails on its
  own, and a failure keeps the stored copy, saying so in the report.
* ``import_shiller`` reads the Shiller CSV you saved from ``ie_data.xls``
  (``importers/shiller_csv.py``). Each import is a whole snapshot: Shiller
  revises history (CPI, recent earnings), so it replaces the stored series,
  and the file's SHA-256 says which import a number came from.
* ``series`` and ``catalog`` read the store only, so the models work
  offline once a series has been fetched or imported.

Runs on the task-worker thread (network and database).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from quant_hub.data.series import StoredSeries
from quant_hub.importers.shiller_csv import SOURCE as SHILLER, ShillerImport, parse_shiller_csv
from quant_hub.providers.ken_french import (
    DATASETS,
    SOURCE as FRENCH,
    FrenchClient,
    FrenchError,
    parse_dataset,
)
from quant_hub.storage.series_store import CatalogRow, SeriesStore

_logger = logging.getLogger(__name__)

FRENCH_STALE_AFTER = timedelta(days=7)      # the files are rebuilt monthly


@dataclass(frozen=True)
class FetchOutcome:
    dataset: str          # "ff3"
    status: str           # "fetched" | "fresh" | "failed"
    series: int = 0       # series stored by this fetch
    values: int = 0
    message: str = ""

    def __str__(self) -> str:
        if self.status == "fetched":
            return f"{self.dataset}: {self.series} series, {self.values:,} values"
        if self.status == "fresh":
            return f"{self.dataset}: stored copy is fresh ({self.message})"
        return f"{self.dataset}: failed, {self.message}"


class ExternalDataService:
    def __init__(self, store: SeriesStore,
                 french_client_factory: Callable[[], FrenchClient] = FrenchClient,
                 now: Callable[[], datetime] = datetime.now) -> None:
        self._store = store
        self._french_factory = french_client_factory
        self._french: FrenchClient | None = None
        self._now = now

    def refresh_french(self, force: bool = False) -> list[FetchOutcome]:
        """Fetch each stale French dataset (module docstring); one outcome each."""
        outcomes = []
        for key, dataset in DATASETS.items():
            fetched = self._store.fetched_at(FRENCH, f"{key}.")
            if not force and fetched is not None and self._now() - fetched <= FRENCH_STALE_AFTER:
                outcomes.append(FetchOutcome(key, "fresh", message=f"fetched {fetched:%Y-%m-%d}"))
                continue
            try:
                payload = self._client().fetch(dataset)
                parsed = parse_dataset(dataset, payload)
                kept = self._store.save(parsed, self._now(), hashlib.sha256(payload).hexdigest())
                outcomes.append(FetchOutcome(key, "fetched", len(parsed), kept))
            except FrenchError as exc:
                held = "; the stored copy stays" if fetched is not None else ""
                outcomes.append(FetchOutcome(key, "failed", message=f"{exc}{held}"))
        for outcome in outcomes:
            log = _logger.warning if outcome.status == "failed" else _logger.info
            log("Ken French %s", outcome)
        return outcomes

    def import_shiller(self, path: str | Path) -> ShillerImport:
        """Store the Shiller CSV at ``path`` (module docstring). Raises
        ``ShillerError`` naming what's wrong, or ``OSError``."""
        payload = Path(path).read_bytes()
        parsed = parse_shiller_csv(payload)
        self._store.save(parsed.series, self._now(), hashlib.sha256(payload).hexdigest())
        _logger.info("Shiller: %d series, %s to %s; %s left out as provisional; earnings through %s",
                     len(parsed.series), parsed.first[:7], parsed.last[:7], parsed.dropped[:7],
                     parsed.earnings_through[:7] or "none")
        return parsed

    def series(self, source: str, series: str) -> StoredSeries:
        """A stored series. Raises ``LookupError`` saying how to fetch it."""
        stored = self._store.series(source, series)
        if stored is None:
            hint = {FRENCH: " (Ctrl+K: Fetch Ken French factors)",
                    SHILLER: " (Ctrl+K: Import Shiller CSV…)"}.get(source, "")
            raise LookupError(f"{source}/{series} hasn't been fetched yet{hint}")
        return stored

    def catalog(self, source: str | None = None) -> list[CatalogRow]:
        return self._store.catalog(source)

    def _client(self) -> FrenchClient:
        if self._french is None:
            self._french = self._french_factory()
        return self._french
