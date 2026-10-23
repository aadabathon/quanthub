"""Application configuration: code defaults, overridden by a user settings
file (D34, F1).

``AppConfig`` holds every setting with a safe default. ``load_config``
reads ``~/.quant_hub/settings.toml`` (or the file named by the
``QUANT_HUB_SETTINGS`` environment variable) over those defaults, with
stdlib ``tomllib``. The first run writes a commented template there, so the
file is easy to find and edit. What a user can set (D34):

* ``[store] path``: the SQLite database for acquired data (D17). ``~`` is
  expanded, and a relative path is relative to the settings file.
* ``[ibkr] enabled, host, port, client_id``: Interactive Brokers is one
  optional provider. Turned off, the app registers no IBKR sources and
  hides the connection controls, so it's complete on synthetic data and
  imports.
* ``[sec] identity``: the SEC asks every program that downloads its filings
  to say who is asking, a name and an email (its fair-access policy). Only
  the Valuation page's company data needs it (W5, D47); empty, the page
  says how to set it.
* ``[regime] path``: the owner's filled-in ``REGIME.md``, which the Review
  page reads (W7). It defaults to ``~/.quant_hub/REGIME.md``, outside the
  repository, because the repository is public.
* ``[positions] folder``: where the owner saves broker exports. The review
  reads the newest one there, by the date inside it (W7); unset, it uses
  the newest snapshot imported on the Portfolio page.

A bad file never stops the app. Each problem (a syntax error, an unknown
section or key, a wrong type) is reported, and that setting keeps its
default. Scripts and tests pass an ``AppConfig`` directly and never read the
file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

SETTINGS_ENV = "QUANT_HUB_SETTINGS"


def _default_db_path() -> Path:
    """Per-user application data, outside the repository: acquired market
    data is a local asset, not source code."""
    return Path.home() / ".quant_hub" / "market_data.db"


def _default_regime_path() -> Path:
    return Path.home() / ".quant_hub" / "REGIME.md"


def default_settings_path() -> Path:
    override = os.environ.get(SETTINGS_ENV)
    return Path(override).expanduser() if override else Path.home() / ".quant_hub" / "settings.toml"


@dataclass(frozen=True)
class AppConfig:
    """All tunable application settings, with safe defaults.

    IBKR ports: 7496 is live TWS, 7497 is TWS paper trading, 4001/4002 are
    IB Gateway live/paper. The default is TWS's own live default, **with
    Read-Only API enabled in TWS itself**: safe because this application
    contains no order code (D12) and the account cannot be modified through
    a read-only socket. Paper and Gateway users change it in settings.toml.
    """

    ibkr_enabled: bool = True
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

    # The SEC's required User-Agent, "Name email@example.com" (D47).
    sec_identity: str = ""

    # The owner's filled-in REGIME.md, and the folder of broker exports (W7).
    regime_path: Path = field(default_factory=_default_regime_path)
    positions_folder: Path | None = None

    default_symbol: str = "SPY"

    # Simulator defaults (annualized decimals; 252 steps = 1 year).
    default_initial_price: float = 100.0
    default_mu: float = 0.08
    default_sigma: float = 0.20
    default_sim_days: int = 252
    default_seed: int = 42
    default_n_paths: int = 500
    default_vol_window: int = 21


TEMPLATE = """\
# Quant Hub settings. Delete a line, or the whole file, to use the default.
# Read at startup; restart the app after editing.

[store]
# The SQLite database for acquired market data. ~ is your home folder; a
# relative path is relative to this file.
path = "~/.quant_hub/market_data.db"

[ibkr]
# Interactive Brokers is optional: set enabled = false if you don't use it,
# and its connection controls and data sources disappear.
enabled = true
host = "127.0.0.1"
# TWS: 7496 live, 7497 paper. IB Gateway: 4001 live, 4002 paper.
# Enable the read-only API in TWS; Quant Hub never places orders.
port = 7496
client_id = 17

[sec]
# The SEC asks programs that download its filings to say who is asking:
# your name and email. Needed only for the Valuation page's company data.
# identity = "Your Name you@example.com"

[regime]
# Your filled-in REGIME.md, which the Review page checks. Keep it outside the
# repository if the repository is public.
path = "~/.quant_hub/REGIME.md"

[positions]
# The folder you save broker exports to. The review reads the newest one by
# the date inside it; without a folder, it uses the newest imported snapshot.
# folder = "~/Documents/exports"
"""


def _path(value: Any, base: Path) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a path in quotes")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def _boolean(value: Any, _base: Path) -> bool:
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _host(value: Any, _base: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a host name in quotes")
    return value.strip()


def _port(value: Any, _base: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ValueError("must be a whole number from 1 to 65535")
    return value


def _client_id(value: Any, _base: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("must be a whole number, 0 or more")
    return value


def _identity(value: Any, _base: Path) -> str:
    if not isinstance(value, str) or "@" not in value or not value.strip():
        raise ValueError('must be your name and email in quotes, e.g. "Jane Doe jane@example.com"')
    return value.strip()


# [section] key -> (AppConfig field, parser)
_SETTINGS = {
    "store": {"path": ("db_path", _path)},
    "ibkr": {"enabled": ("ibkr_enabled", _boolean), "host": ("ibkr_host", _host),
             "port": ("ibkr_port", _port), "client_id": ("ibkr_client_id", _client_id)},
    "sec": {"identity": ("sec_identity", _identity)},
    "regime": {"path": ("regime_path", _path)},
    "positions": {"folder": ("positions_folder", _path)},
}


def load_config(path: Path | None = None) -> tuple[AppConfig, list[str]]:
    """The settings file over the defaults, and every problem found in it.

    A missing file is written from ``TEMPLATE`` (the defaults, commented).
    """
    path = path if path is not None else default_settings_path()
    if not path.exists():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(TEMPLATE, encoding="utf-8")
        except OSError as exc:
            return AppConfig(), [f"could not write a settings template to {path}: {exc}"]
        return AppConfig(), []
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return AppConfig(), [f"{path}: {exc}; using every default"]

    values: dict[str, Any] = {}
    problems: list[str] = []
    for section, body in data.items():
        keys = _SETTINGS.get(section)
        if keys is None or not isinstance(body, dict):
            problems.append(f"{path.name}: unknown section [{section}], ignored")
            continue
        for key, value in body.items():
            if key not in keys:
                problems.append(f"{path.name}: unknown setting {key!r} in [{section}], ignored")
                continue
            name, parse = keys[key]
            try:
                values[name] = parse(value, path.parent)
            except ValueError as exc:
                problems.append(f"{path.name}: [{section}] {key} {exc}; using the default")
    return replace(AppConfig(), **values), problems
