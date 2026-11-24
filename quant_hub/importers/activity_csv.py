"""Activity from CSV: a broker's transaction history (TR1, D90).

This module reads a history file into a ``data.activity.ActivityFile``;
nothing else (no prices fetched, no storage). Brokers' own layouts are
recognized by their header and come one at a time, each from a real export
(Fidelity's is next, TR1b). Until then, any history can be written in the
generic layout.

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

Every row is checked (``data.activity.Transaction``), and a row that fails
is reported with its line and kept out; the rest of the file is still
read. Whether a file with a bad row is stored at all is the caller's
choice (``services/activity.py`` stores nothing).
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from pathlib import Path
from typing import IO

from quant_hub.data.activity import ActivityFile, Kind, RowProblem, Transaction
from quant_hub.importers.positions_csv import parse_option_symbol

_EMPTY = {"", "--", "n/a", "na"}
_REQUIRED = ("date", "kind", "amount")
_GENERIC = "Generic"
_LAYOUT_HINT = "date,account,kind,symbol,quantity,price,commission,fees,amount"


class ActivityFileError(ValueError):
    """The file can't be read as activity; the message says why."""


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
    header_at = next((i for i, line in enumerate(lines)
                      if set(_REQUIRED) <= set(_cells(line))), None)
    if header_at is None:
        raise ActivityFileError(
            f"no header row with {', '.join(_REQUIRED)} columns (the generic layout is "
            f"{_LAYOUT_HINT})")

    reader = csv.reader(io.StringIO("\n".join(lines[header_at:])))
    columns = [cell.strip().lower() for cell in next(reader)]
    transactions: list[Transaction] = []
    problems: list[RowProblem] = []
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        line = header_at + reader.line_num
        try:
            transactions.append(_transaction(dict(zip(columns, row))))
        except ValueError as exc:
            problems.append(RowProblem(line, str(exc)))
    if not transactions and not problems:
        raise ActivityFileError("no transactions in the file")
    return ActivityFile(tuple(transactions), _GENERIC, name, tuple(problems))


def _transaction(record: dict[str, str]) -> Transaction:
    kind_text = record.get("kind", "").strip().lower().replace(" ", "_")
    try:
        kind = Kind(kind_text)
    except ValueError:
        known = ", ".join(k.value for k in Kind)
        raise ValueError(f"unknown kind {kind_text!r} (one of {known})") from None
    amount = _number(record.get("amount"), "amount")
    if amount is None:
        raise ValueError("no amount")
    symbol = record.get("symbol", "").strip().upper()
    option = parse_option_symbol(symbol) if symbol else None
    if option is not None:
        symbol = symbol.lstrip("-").strip()
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
