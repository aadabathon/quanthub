"""Company fundamentals: the contract between a filing source and the DCF (W4).

Ported from the owner's ``dcfmodeler`` (``schema.py``). Any source of
financial statements fills this shape; the DCF (``models/dcf.py``) reads only
this shape (D34: every acquired data kind is a port whose shape lives in
``data/``). The SEC adapter arrives in W5.

**The loud-failure contract.** Every line item is optional, and is either:

* present, with a provenance tag in ``sources[field]`` (which concept, which
  filing), or
* absent, with a human-readable reason in ``missing[field]``.

A source never fills a gap with a guess, and the DCF refuses to run on a
missing *required* input (latest revenue, diluted shares, net debt), naming
the reasons, unless the user overrides it.

Units: monetary values in the company's reporting currency (``currency``),
in absolute units (already un-scaled from the filing's thousands or
millions); shares as a count; ``period_end`` an ISO date (``YYYY-MM-DD``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from typing import Protocol

_MONEY = ("revenue", "operating_income", "income_before_tax", "income_tax_expense",
          "depreciation_amortization", "capex", "current_assets", "current_liabilities",
          "cash_and_equivalents", "marketable_securities", "total_debt")


@dataclass
class YearData:
    """One fiscal year of line items (module docstring for units).

    ``operating_income`` is the EBIT proxy. ``capex`` and
    ``depreciation_amortization`` are stored as positive amounts.
    """

    fiscal_year: int
    period_end: str

    revenue: float | None = None
    operating_income: float | None = None
    income_before_tax: float | None = None
    income_tax_expense: float | None = None
    depreciation_amortization: float | None = None
    capex: float | None = None
    current_assets: float | None = None
    current_liabilities: float | None = None
    cash_and_equivalents: float | None = None
    marketable_securities: float | None = None
    total_debt: float | None = None

    def __post_init__(self) -> None:
        if isinstance(self.fiscal_year, bool) or not isinstance(self.fiscal_year, int):
            raise ValueError("fiscal_year must be an integer")
        for name in _MONEY:
            value = getattr(self, name)
            if value is not None:
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError(f"{name} must be finite when present")
                setattr(self, name, value)

    def effective_tax_rate(self) -> float | None:
        """Tax over pretax income; None when either is missing or pretax
        income isn't positive (a rate on a loss means nothing)."""
        if self.income_tax_expense is None or self.income_before_tax is None:
            return None
        if self.income_before_tax <= 0:
            return None
        return self.income_tax_expense / self.income_before_tax

    def net_working_capital(self) -> float | None:
        """Current assets less cash, less current liabilities: working capital
        excluding cash (the owner's simple, explicit approximation)."""
        if self.current_assets is None or self.current_liabilities is None:
            return None
        return (self.current_assets - (self.cash_and_equivalents or 0.0)
                - self.current_liabilities)


@dataclass
class FinancialHistory:
    """A company's fiscal years, with provenance and null reasons (module
    docstring). ``years`` may arrive in any order; ``ordered`` sorts them."""

    cik: str | None = None
    company: str | None = None
    ticker: str | None = None
    currency: str = "USD"
    shares_diluted: float | None = None
    years: list[YearData] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.shares_diluted is not None and not math.isfinite(float(self.shares_diluted)):
            raise ValueError("shares_diluted must be finite when present")

    def ordered(self) -> list[YearData]:
        return sorted(self.years, key=lambda y: y.fiscal_year)

    def latest(self) -> YearData | None:
        """The most recent fiscal year, whatever order ``years`` holds."""
        ordered = self.ordered()
        return ordered[-1] if ordered else None

    def note_missing(self, field_name: str, reason: str) -> None:
        self.missing[field_name] = reason

    def note_source(self, field_name: str, tag: str) -> None:
        self.sources[field_name] = tag


LINE_ITEMS = tuple(f.name for f in fields(YearData) if f.name not in ("fiscal_year", "period_end"))


class FundamentalsSource(Protocol):
    """The port a filing source implements (D34); the SEC adapter is W5's."""

    def history(self, identifier: str, years: int) -> FinancialHistory:
        """Up to ``years`` fiscal years for a ticker or CIK, loudly incomplete."""
        ...
