"""Static application configuration.

A single frozen dataclass instead of a config-file system: v0.1 has too few
settings to justify parsing anything. When persistent user settings become
necessary, this is the one type that grows a ``load()``/``save()``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppConfig:
    """All tunable application settings, with safe defaults.

    IBKR ports: 7497 is the TWS *paper trading* default, 7496 is live TWS,
    4002/4001 are IB Gateway paper/live. v0.1 deliberately defaults to paper.
    """

    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 7497
    ibkr_client_id: int = 17

    connect_timeout_s: float = 10.0
    request_timeout_s: float = 30.0

    default_symbol: str = "SPY"
    default_range: str = "1Y"

    # How often the GUI drains the event bus and task-result queue.
    pump_interval_ms: int = 50
