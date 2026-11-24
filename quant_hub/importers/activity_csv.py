"""Activity from CSV: a broker's transaction history (TR1, TR1b, D90, D91).

This module reads a history file into a ``data.activity.ActivityFile``;
nothing else (no prices fetched, no storage). A broker's own layout is
recognized by its header, and each one is built from a real export. Any
other history can be written in the generic layout.

The generic layout
------------------
::

    date,account,kind,symbol,quantity,price,commission,fees,amount
    2026-03-02,Brokerage,buy,AAPL,10,180.00,0,0,-1800.00
    2026-03-20,Brokerage,sell,AAPL,-10,190.00,0,0.02,1899.98
    2026-03-31,Brokerage,deposit,,,,,,500.00

* ``date``, ``kind`` and ``amount`` are required. ``account``, ``symbol``,
  ``quantity`` (empty is 0), ``price``, ``commission`` and ``fees`` (empty
  is 0), ``settle_date`` and ``description`` are optional. Other columns
  are ignored.
* ``kind`` is one of ``data.activity.Kind``'s values, in any case, with a
  space or an underscore (``transfer in``, ``TRANSFER_IN``).
* Signs follow ``data.activity``: quantity is the change in shares (a sell
  is negative), amount the change in cash (a buy is negative), net of
  commission and fees.
* Dates are ``2026-03-02`` or ``03/02/2026``. Money may be written
  ``$1,234.56``, ``-$12.30`` or ``(12.30)``; ``--`` is empty.
* An option is written as in positions files, ``ROOT YYMMDD C|P STRIKE``
  run together (``AAPL260116C250``), with its price per share.

Fidelity
--------
The Activity & Orders history download, recognized by its header (``Run
Date``, ``Action``, ``Amount ($)``, ...):

* two blank lines before the header; after the rows, a blank line,
  disclaimers, and ``Date downloaded ...``, which are skipped;
* newest first; one row per transaction, every account in one file, the
  account by name (``Account``, its number when there's no name);
* plain numbers (``-1800.00``), quoted, and ``""`` when empty; signs already
  as ``data.activity`` has them (a sale's quantity is negative);
* the price rounded to the cent, which ``data.activity``'s tolerance allows
  for;
* the kind is in the ``Action`` wording, which is kept as the description.

**The action map holds only wording seen in a real export:**

=========================  ==========
Action begins              kind
=========================  ==========
``YOU BOUGHT``             buy
``YOU SOLD``               sell
``REINVESTMENT``           reinvest
``DIVIDEND RECEIVED``      dividend
=========================  ==========

Anything else is read as ``other``: kept, shown
(``ActivityFile.unrecognized``), and with no effect on P&L until an export
shows what it is and the map grows. So are a buy or sell worded as more
than a plain trade (``OPENING TRANSACTION``, ``CLOSING TRANSACTION``,
``SHORT``: an option's opening sale, say, isn't a sale of shares held), a
row whose ``Type`` is ``Short``, and a trade with accrued interest (a bond,
whose amount isn't q·p). That guard only ever widens ``other``; it never
assigns a kind from wording no export has shown.

Every row is checked (``data.activity.Transaction``), and a row that fails
is reported with its line and kept out; the rest of the file is still
read. Whether a file with a bad row is stored at all is the caller's
choice (``services/activity.py`` stores nothing).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import IO

from quant_hub.data.activity import ActivityFile, Kind, RowProblem, Transaction
from quant_hub.data.positions import OptionContract
from quant_hub.importers.positions_csv import parse_option_symbol

_EMPTY = {"", "--", "n/a", "na"}
_LAYOUT_HINT = "date,account,kind,symbol,quantity,price,commission,fees,amount"

# Fidelity's action wording, each seen in a real export, and the kind it means.
_FIDELITY_ACTIONS = (
    ("YOU BOUGHT ", Kind.BUY),
    ("YOU SOLD ", Kind.SELL),
    ("REINVESTMENT ", Kind.REINVEST),
    ("DIVIDEND RECEIVED ", Kind.DIVIDEND),
)
# A buy or sell worded as more than a plain trade stays "other" until seen.
_NOT_PLAIN = ("OPENING TRANSACTION", "CLOSING TRANSACTION", "SHORT ")


class ActivityFileError(ValueError):
    """The file can't be read as activity; the message says why."""


@dataclass(frozen=True)
class _Layout:
    name: str
    columns: frozenset[str]           # the header names that identify it
    read: Callable[[dict[str, str]], Transaction]
    stops_at_blank: bool              # the rows end at the first blank line


def read_activity_csv(source: str | Path | IO[str], name: str = "") -> ActivityFile:
    """Read an activity CSV (module docstring). Raises ``ActivityFileError``
    when the file has no usable layout or no rows; a bad row is a
    ``RowProblem`` in the result instead."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        name = name or path.name
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            raise ActivityFileError(f"couldn't read {path.name}: {exc}") from exc
    else:
        text = source.read().lstrip("﻿")
    lines = text.splitlines()
    found = next(((i, layout) for i, line in enumerate(lines) for layout in _LAYOUTS
                  if layout.columns <= set(_cells(line))), None)
    if found is None:
        raise ActivityFileError(
            "no header row of a known layout: Fidelity's history download, or the generic "
            f"layout ({_LAYOUT_HINT})")
    header_at, layout = found
    end = len(lines)
    if layout.stops_at_blank:
        end = next((i for i in range(header_at + 1, len(lines))
                    if not lines[i].strip().strip(",").strip()), len(lines))

    reader = csv.reader(io.StringIO("\n".join(lines[header_at:end])))
    columns = [cell.strip().lower() for cell in next(reader)]
    transactions: list[Transaction] = []
    problems: list[RowProblem] = []
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        line = header_at + reader.line_num
        try:
            transactions.append(layout.read(dict(zip(columns, row))))
        except ValueError as exc:
            problems.append(RowProblem(line, str(exc)))
    if not transactions and not problems:
        raise ActivityFileError("no transactions in the file")
    return ActivityFile(tuple(transactions), layout.name, name, tuple(problems))


def _generic(record: dict[str, str]) -> Transaction:
    kind_text = record.get("kind", "").strip().lower().replace(" ", "_")
    try:
        kind = Kind(kind_text)
    except ValueError:
        known = ", ".join(k.value for k in Kind)
        raise ValueError(f"unknown kind {kind_text!r} (one of {known})") from None
    amount = _number(record.get("amount"), "amount")
    if amount is None:
        raise ValueError("no amount")
    symbol, option = _symbol(record.get("symbol", ""))
    settle = record.get("settle_date", "").strip()
    return Transaction(
        trade_date=_date(record.get("date", "")),
        kind=kind,
        amount=amount,
        account=record.get("account", "").strip(),
        symbol=symbol,
        quantity=_number(record.get("quantity"), "quantity") or 0.0,
        price=_number(record.get("price"), "price"),
        commission=_number(record.get("commission"), "commission") or 0.0,
        fees=_number(record.get("fees"), "fees") or 0.0,
        settle_date=_date(settle) if settle else None,
        description=record.get("description", "").strip(),
        option=option,
    )


def _fidelity(record: dict[str, str]) -> Transaction:
    action = " ".join(record.get("action", "").split())
    amount = _number(record.get("amount ($)"), "amount")
    if amount is None:
        raise ValueError(f"{action or 'a row'}: no amount")
    accrued = _number(record.get("accrued interest ($)"), "accrued interest") or 0.0
    symbol, option = _symbol(record.get("symbol", ""))
    settle = record.get("settlement date", "").strip()
    return Transaction(
        trade_date=_date(record.get("run date", "")),
        kind=_fidelity_kind(action, record.get("type", ""), accrued),
        amount=amount,
        account=(record.get("account") or record.get("account number") or "").strip(),
        symbol=symbol,
        quantity=_number(record.get("quantity"), "quantity") or 0.0,
        price=_number(record.get("price ($)"), "price"),
        commission=_number(record.get("commission ($)"), "commission") or 0.0,
        fees=_number(record.get("fees ($)"), "fees") or 0.0,
        settle_date=_date(settle) if settle else None,
        description=action,
        option=option,
    )


def _fidelity_kind(action: str, holding_type: str, accrued: float) -> Kind:
    """The kind an action's wording means, or ``other`` (module docstring)."""
    wording = action.upper()
    for prefix, kind in _FIDELITY_ACTIONS:
        if not wording.startswith(prefix):
            continue
        if kind in (Kind.BUY, Kind.SELL) and (
                wording[len(prefix):].startswith(_NOT_PLAIN)
                or holding_type.strip().lower() == "short" or accrued != 0):
            return Kind.OTHER
        return kind
    return Kind.OTHER


_LAYOUTS = (
    _Layout("Fidelity", frozenset({"run date", "action", "amount ($)"}), _fidelity, True),
    _Layout("Generic", frozenset({"date", "kind", "amount"}), _generic, False),
)


def _symbol(text: str) -> tuple[str, OptionContract | None]:
    """The symbol in capitals and its option contract if it is one
    (``-AAPL260116C250`` is the contract ``AAPL260116C250``)."""
    symbol = text.strip().upper()
    option = parse_option_symbol(symbol) if symbol else None
    return (symbol.lstrip("-").strip() if option else symbol), option


def _cells(line: str) -> list[str]:
    return [cell.strip().lower() for cell in next(csv.reader([line]), [])]


def _date(text: str) -> date:
    cleaned = text.strip()
    for layout in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(cleaned, layout).date()
        except ValueError:
            pass
    raise ValueError(f"{cleaned!r} isn't a date (2026-03-02 or 03/02/2026)")


def _number(text: str | None, label: str) -> float | None:
    """``$1,234.56``, ``-$12.30``, ``(12.30)``, ``--`` -> float or None."""
    if text is None:
        return None
    cleaned = text.strip()
    if cleaned.lower() in _EMPTY:
        return None
    negative = cleaned.startswith("-") or cleaned.startswith("(")
    digits = re.sub(r"[^0-9.]", "", cleaned)
    if not digits or digits.count(".") > 1 or digits == ".":
        raise ValueError(f"the {label} {text!r} isn't a number")
    value = float(digits)
    return -value if negative else value
