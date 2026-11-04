"""A fund's holdings as filed: one N-PORT report (D62).

ALLOCATION A1 looks through an index fund to count your independent bets
(D59). That needs each fund's constituents and their weights, which every
US fund files with the SEC on Form N-PORT. This is the record a filing
becomes (``providers/sec_nport.py``) and the store keeps
(``storage/holdings_store.py``).

* ``Holding`` is one line of the portfolio as filed: the issuer's name,
  every identifier the filing gives (LEI, CUSIP, ISIN, ticker; any can be
  blank), the balance and its units, the value in USD, and the weight
  ``pct`` as a **fraction of net assets** (the filing's percent ÷ 100). The
  categories are N-PORT's codes: asset (``EC`` equity common, ``DBT``
  debt, …), issuer (``CORP``, ``UST``, …), and ``payoff`` (``Long``,
  ``Short``, ``N/A``).
* ``FundHoldings`` is one report: which fund (the ticker you asked for,
  the trust's CIK, the series id, blank for a unit trust like SPY), the
  report date (the "as of" date, ``repPdDate``), the filing's accession and
  date, net assets, and its holdings in filing order.

Holdings carry CUSIP and ISIN but rarely a ticker. Matching a fund's line
to a stock you hold directly needs a bridge between them. A1 chose IBKR's
contract details for the stocks held directly (``services/security_ids``).
``valid_isin`` checks an ISIN's form and check digit (ISO 6166) before
it's stored or matched. Nothing here guesses a match.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Holding:
    name: str
    lei: str = ""
    cusip: str = ""
    isin: str = ""
    ticker: str = ""
    balance: float | None = None
    units: str = ""            # NS shares, PA principal, NC contracts, OU other
    value_usd: float | None = None
    pct: float | None = None   # fraction of net assets
    asset_cat: str = ""
    issuer_cat: str = ""
    country: str = ""
    payoff: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("a holding needs a name")
        for field in ("balance", "value_usd", "pct"):
            value = getattr(self, field)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{self.name}: {field} must be finite")


@dataclass(frozen=True)
class FundHoldings:
    fund: str                  # the ticker asked for
    cik: int                   # the filing trust's CIK
    series_id: str             # "S000004310"; "" for a unit trust
    fund_name: str
    report_date: str           # ISO, the portfolio's "as of" date
    accession: str             # "0001410368-26-089410"
    filed: str                 # ISO filing date
    net_assets: float | None
    holdings: tuple[Holding, ...]

    def __post_init__(self) -> None:
        for field in ("report_date", "filed"):
            if not _ISO.fullmatch(getattr(self, field)):
                raise ValueError(f"{self.fund}: {field} {getattr(self, field)!r} is not an ISO date")
        if not self.holdings:
            raise ValueError(f"{self.fund}: a report with no holdings")

    @property
    def weight_covered(self) -> float:
        """The sum of the stated weights: about 1, less cash and other assets."""
        return sum(h.pct for h in self.holdings if h.pct is not None)


_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")


def isin_check_digit(body: str) -> int:
    """ISO 6166's check digit for an ISIN's first eleven characters.

    Letters become two digits (A = 10 … Z = 35), then the Luhn sum runs
    over the digit string from the right, doubling the rightmost digit and
    every second one after it (the check digit, not yet there, would sit to
    its right). The check digit brings the sum to a multiple of 10.
    """
    if not re.fullmatch(r"[A-Z0-9]{11}", body):
        raise ValueError(f"an ISIN's body is 11 letters or digits, not {body!r}")
    digits = "".join(str(int(c, 36)) for c in body)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10


def valid_isin(text: str) -> bool:
    """A country code, nine letters or digits, and a correct check digit."""
    return (bool(_ISIN.fullmatch(text))
            and isin_check_digit(text[:11]) == int(text[11]))
