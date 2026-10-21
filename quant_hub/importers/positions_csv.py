"""Positions from CSV: a generic layout, and two Fidelity presets (P2, D34).

Every broker exports holdings as CSV. This module reads such a file into a
``data.positions.PositionSnapshot``; nothing else (no prices fetched, no
storage).

The generic layout
------------------
::

    symbol,quantity,price,cost_basis,account
    AAPL,50,227.52,7500.00,Brokerage
    VTI,120,289.10,,Brokerage
    -AAPL260116C250,-1,4.15,,Brokerage
    CASH,1250.00,,,Brokerage

* ``symbol`` and ``quantity`` are required. ``price``, ``value``,
  ``cost_basis``, ``account`` and ``description`` are optional.
* ``CASH``, or a symbol ending in ``**`` (Fidelity's mark for its core
  money-market fund), is cash, its quantity in dollars.
* An option is written ``ROOT YYMMDD C|P STRIKE`` run together, with or
  without a leading dash: ``-AAPL260116C250`` is the AAPL 250 call expiring
  2026-01-16. Quantity counts contracts, negative when short; price is per
  share, as quoted.

Fidelity
--------
Fidelity's Portfolio Positions download, recognized by its header
(``Account Number``, ``Symbol``, ``Quantity``, ``Current Value``, ...):

* money as ``$1,234.56`` (``--`` when empty);
* the core money-market fund (``SPAXX**``) with no quantity, only a value,
  and a ``Pending Activity`` row, which is skipped;
* options in the form above;
* after the rows, a blank line, disclaimers, and ``Date downloaded
  Oct-16-2026 ...``, which dates the snapshot.

The account is the file's account name (its number when there's no name),
so a multi-account download stays separable.

Fidelity's Positions view
-------------------------
The Positions page's own download (the owner's exports since June 2026),
recognized by its header (``Symbol``, ``Quantity``, ``Last``, ``Value``,
``Basis``, ...):

* a preamble cell before the header: the title, the view's account name
  (``All Accounts`` for the combined view), and ``as of 10/21/2026 at
  11:18:16 AM``, which dates the snapshot (the date only, as above);
* plain numbers (``1,250.00``; ``--`` when empty);
* cash as ``Cash (SPAXX)``, one per account's core fund, and ``Cash (USD)``;
* a ``Totals`` row at the end, in some exports, which is skipped.

It has **no account column**: a holding in two accounts is two rows, and
nothing says which is which. So every row belongs to the view's account,
and the risk model adds the rows up. Separate accounts need the Portfolio
Positions download above, or one export per account.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO

from quant_hub.data.positions import Holding, OptionContract, Position, PositionSnapshot

_OPTION = re.compile(r"^-?\s*([A-Z][A-Z0-9.]*?)(\d{6})([CP])(\d+(?:\.\d+)?)$")
_DOWNLOADED = re.compile(r"date downloaded\s+([A-Za-z]{3}-\d{1,2}-\d{4})", re.IGNORECASE)
_AS_OF = re.compile(r"as of\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
_CASH = re.compile(r"^cash\s*\(([^)]*)\)$", re.IGNORECASE)          # "Cash (SPAXX)"
_SKIPPED = re.compile(r"^(pending\b|totals?$)", re.IGNORECASE)       # not holdings
_EMPTY = {"", "--", "n/a", "na"}
_FIDELITY = {"account number", "symbol", "quantity", "current value"}
_FIDELITY_VIEW = {"symbol", "quantity", "last", "value", "basis"}


@dataclass(frozen=True)
class _Layout:
    """Which columns hold a layout's last price, market value and cost basis."""

    name: str
    price: str
    value: str
    cost: str


_GENERIC_LAYOUT = _Layout("Generic", "price", "value", "cost_basis")
_FIDELITY_LAYOUT = _Layout("Fidelity", "last price", "current value", "cost basis total")
_FIDELITY_VIEW_LAYOUT = _Layout("Fidelity positions view", "last", "value", "basis")


class PositionFileError(ValueError):
    """The file can't be read as positions; the message says why."""


def parse_option_symbol(symbol: str) -> OptionContract | None:
    """``-AAPL260116C250`` -> the AAPL 2026-01-16 250 call; None if it isn't one."""
    match = _OPTION.match(symbol.strip().upper())
    if match is None:
        return None
    root, yymmdd, kind, strike = match.groups()
    try:
        expiry = datetime.strptime(yymmdd, "%y%m%d").date()
    except ValueError:
        return None
    return OptionContract(root, "call" if kind == "C" else "put", float(strike), expiry)


def read_positions_csv(source: str | Path | IO[str], name: str = "") -> PositionSnapshot:
    """Read a positions CSV (module docstring). Raises ``PositionFileError``."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        name = name or path.name
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise PositionFileError(f"couldn't read {path.name}: {exc}") from exc
    else:
        text = source.read().lstrip("﻿")
    lines = text.splitlines()
    header_at = next((i for i, line in enumerate(lines)
                      if "symbol" in line.lower() and "quantity" in line.lower()), None)
    if header_at is None:
        raise PositionFileError("no header row with symbol and quantity columns")
    end = next((i for i in range(header_at + 1, len(lines))
                if not lines[i].strip().strip(",").strip()), len(lines))
    rows = list(csv.reader(io.StringIO("\n".join(lines[header_at:end]))))
    columns = [column.strip().lower() for column in rows[0]]
    records = [dict(zip(columns, row)) for row in rows[1:] if any(cell.strip() for cell in row)]

    if _FIDELITY <= set(columns):
        layout = _FIDELITY_LAYOUT
    elif _FIDELITY_VIEW <= set(columns):
        layout = _FIDELITY_VIEW_LAYOUT
    else:
        layout = _GENERIC_LAYOUT
    preamble = [line.strip().strip('"').strip() for line in lines[:header_at]]
    view_account = (next((text for text in preamble if text and text.lower() != "positions"
                          and not _AS_OF.search(text)), "")
                    if layout is _FIDELITY_VIEW_LAYOUT else "")
    positions = [p for p in (_position(record, layout, view_account) for record in records) if p]
    if not positions:
        raise PositionFileError("no positions in the file")
    downloaded = next((m.group(1) for line in lines[end:] if (m := _DOWNLOADED.search(line))),
                      None)
    stamped = next((m.group(1) for text in preamble if (m := _AS_OF.search(text))), None)
    if downloaded:
        as_of = datetime.strptime(downloaded, "%b-%d-%Y")
    elif stamped:
        as_of = datetime.strptime(stamped, "%m/%d/%Y")
    else:
        as_of = datetime.now()
    return PositionSnapshot(tuple(positions), as_of, layout.name, name)


def _position(record: dict[str, str], layout: _Layout, view_account: str) -> Position | None:
    symbol = record.get("symbol", "").strip()
    if not symbol or _SKIPPED.match(symbol):
        return None
    account = (record.get("account name") or record.get("account")
               or record.get("account number") or view_account).strip()
    description = record.get("description", "").strip()
    quantity = _number(record.get("quantity"))
    price = _number(record.get(layout.price))
    value = _number(record.get(layout.value))
    cost = _number(record.get(layout.cost))

    cash = _CASH.match(symbol)
    if (cash or symbol.upper() == "CASH" or symbol.endswith("**")
            or "money market" in description.lower()):
        dollars = value if value is not None else quantity
        if dollars is None:
            raise PositionFileError(f"{symbol}: a cash line needs an amount")
        fund = (cash.group(1) if cash else symbol.rstrip("*")).strip().upper()
        return Position(fund if fund and fund != "USD" else "CASH", Holding.CASH, dollars,
                        account, value=dollars, description=description)
    if quantity is None:
        raise PositionFileError(f"{symbol}: no quantity")
    option = parse_option_symbol(symbol)
    if option is not None:
        return Position(symbol.lstrip("-").strip().upper(), Holding.OPTION, quantity, account,
                        price, value, cost, description, option)
    return Position(symbol.upper(), Holding.EQUITY, quantity, account, price, value, cost,
                    description)


def _number(text: str | None) -> float | None:
    """``$1,234.56``, ``-$12.30``, ``(12.30)``, ``--`` -> float or None."""
    if text is None:
        return None
    cleaned = text.strip()
    if cleaned.lower() in _EMPTY:
        return None
    negative = cleaned.startswith("-") or cleaned.startswith("(")
    digits = re.sub(r"[^0-9.]", "", cleaned)
    if not digits or digits == ".":
        raise PositionFileError(f"{text!r} isn't a number")
    value = float(digits)
    return -value if negative else value
