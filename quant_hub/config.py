"""Static application configuration.

A single frozen dataclass instead of a config-file system: v0.1 has too few
settings to justify parsing anything. When persistent user settings become
necessary, this is the one type that grows a ``load()``/``save()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


def _default_db_path() -> Path:
    """Per-user application data, outside the repository: acquired market
    data is a local asset, not source code."""
    return Path.home() / ".quant_hub" / "market_data.db"


@dataclass(frozen=True)
class AppConfig:
    """All tunable application settings, with safe defaults.

    IBKR ports: 7496 is live TWS, 7497 is TWS paper trading, 4001/4002 are
    IB Gateway live/paper. The default is the owner's live TWS **with
    Read-Only API enabled in TWS itself** — safe because this application
    contains no order code (D12) and the account cannot be modified through
    a read-only socket.
    """

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7496
    ibkr_client_id: int = 17

    connect_timeout_s: float = 10.0
    request_timeout_s: float = 30.0
    # Quotes complete on the first usable tick (typically <1s live-verified);
    # this bounds how long an unavailable instrument holds up a refresh.
    quote_timeout_s: float = 5.0

    # SQLite store for acquired historical bars (created on first use).
    db_path: Path = field(default_factory=_default_db_path)

    default_symbol: str = "SPY"
    default_range: str = "1Y"

    # Dashboard simulation defaults (annualized decimals; 252 steps = 1 year).
    default_initial_price: float = 100.0
    default_mu: float = 0.08
    default_sigma: float = 0.20
    default_sim_days: int = 252
    default_seed: int = 42
    default_n_paths: int = 500
    default_vol_window: int = 21

    # How often the GUI drains the event bus and task-result queue.
    pump_interval_ms: int = 50
