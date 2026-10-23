"""The owner's regime as data: rules, limits and measures (W7).

``REGIME.md``'s sections 4–8 are rules. Each reads one measure and compares
it with a limit; ``models/regime.py`` grades the comparison. This module
holds the plain records that the checks, the parser and the store share:
``Rule``, ``Limit`` and ``Measure``, and a whole version of the file as a
``RuleSet`` (settings, sleeves, rules, the market gates' parameters, the
universe, the routine).

Units are each rule's own:
- a fraction for a percentage (0.15 for 15%)
- annual where the row says "a year"
- dollars where it says $

A measure's standard error is in the same units as its estimate. A
correlation carries its sample size instead, because its error lives on
Fisher's scale (``models/regime.py``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Grade(Enum):
    """What a breached rule means (REGIME.md's "On breach")."""

    FAIL = "fail"      # stop and look
    WARN = "warn"      # worth a look
    NOTE = "note"      # recorded only


class Effect(Enum):
    """What a closed market-state gate means (REGIME.md section 7). Never an
    order (D12): the review's checklist changes, nothing else."""

    BLOCK_ADDS = "block adds"          # no new risk today
    SUGGEST_HEDGE = "suggest hedge"    # names the hedge sleeve
    NOTE = "note"


Consequence = Grade | Effect


@dataclass(frozen=True)
class Limit:
    """An upper bound, a lower bound, or a band: a rule holds while
    ``lower <= value <= upper``."""

    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if self.lower is None and self.upper is None:
            raise ValueError("a limit needs a lower bound, an upper bound, or both")
        for bound in (self.lower, self.upper):
            if bound is not None and not math.isfinite(bound):
                raise ValueError("limits must be finite")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError(f"the lower bound {self.lower} is above the upper {self.upper}")


@dataclass(frozen=True)
class Measure:
    """A number a rule reads: an estimate and its standard error, or an exact
    value (``error`` None), or a correlation with its sample size."""

    estimate: float
    error: float | None = None
    observations: int | None = None     # a correlation's T
    correlation: bool = False
    note: str = ""                      # e.g. "no standard error yet"

    def __post_init__(self) -> None:
        if not math.isfinite(self.estimate):
            raise ValueError("a measure's estimate must be finite")
        if self.error is not None and not (math.isfinite(self.error) and self.error >= 0):
            raise ValueError("a standard error must be finite and not negative")
        if self.correlation:
            if self.error is not None:
                raise ValueError("a correlation's error comes from its sample size")
            if self.observations is None or self.observations <= 3:
                raise ValueError("a correlation needs more than three observations")
            if not -1.0 < self.estimate < 1.0:
                raise ValueError("a correlation must lie strictly between -1 and 1")

    @classmethod
    def exact(cls, value: float, note: str = "") -> Measure:
        return cls(float(value), note=note)

    @classmethod
    def estimated(cls, value: float, error: float, note: str = "") -> Measure:
        return cls(float(value), float(error), note=note)

    @classmethod
    def of_correlation(cls, rho: float, observations: int, note: str = "") -> Measure:
        return cls(float(rho), observations=int(observations), correlation=True, note=note)


@dataclass(frozen=True)
class Rule:
    """One row of REGIME.md: the measure it reads, its limit and unit, what a
    breach means, and the evidence k (in standard errors) a breach of an
    estimated measure needs."""

    key: str                    # e.g. "4.max-weight", unique in a rule set
    label: str                  # the row's own words
    measure: str                # which measure it reads
    limit: Limit
    consequence: Consequence
    evidence: float = 0.0       # k; 0 is the estimate alone
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.measure:
            raise ValueError("a rule needs a key and a measure")
        if not (math.isfinite(self.evidence) and self.evidence >= 0):
            raise ValueError("evidence k must be finite and not negative")


class SleeveKind(Enum):
    CORE = "core"
    HEDGE = "hedge"
    STREAM = "stream"
    OTHER = "other"


@dataclass(frozen=True)
class SleeveRow:
    """One holding's line in section 3. A stream carries its own name; the
    others are named by their kind. ``kind`` is None until the owner
    assigns one."""

    symbol: str
    kind: SleeveKind | None
    name: str = ""
    why: str = ""
    intended: Limit | None = None       # a range: checked
    target: float | None = None         # a single number: shown, not graded

    @property
    def sleeve(self) -> str | None:
        """The sleeve's label: ``core``, ``hedge``, ``other`` or
        ``stream: <name>``."""
        if self.kind is None:
            return None
        return f"stream: {self.name}" if self.kind is SleeveKind.STREAM else self.kind.value


@dataclass(frozen=True)
class Settings:
    """Section 2. None where the owner left a value blank."""

    accounts: tuple[str, ...] = ()
    refresh: str = ""
    history_source: str | None = None
    benchmark: str | None = None
    cash_rate: float | None = None      # a fraction, a year
    lookback_days: int | None = None
    regression_window: str = ""


@dataclass(frozen=True)
class MarketSettings:
    """Section 7's parameters, beside its gate rules."""

    regimes: int | None = None              # the calm gate's K
    volatility_horizon: int | None = None   # days
    odds_horizon: int | None = None         # days
    indicators: tuple[str, ...] = ()        # the owner's own, ticked by hand


@dataclass(frozen=True)
class Universe:
    """Section 8's text rows, beside its screen rules."""

    tickers: tuple[str, ...] = ()
    excluded: str = ""
    refresh: str = ""


@dataclass(frozen=True)
class RuleSet:
    """One version of the owner's REGIME.md, read (``importers/regime_md.py``)."""

    version: str
    written: date | None
    reason: str
    thesis_runs: int | None             # section 1's N: failing reviews in a row
    settings: Settings
    sleeves: tuple[SleeveRow, ...]
    sleeve_consequence: Grade | None    # a holding outside its intended range
    rules: tuple[Rule, ...]
    market: MarketSettings
    universe: Universe
    routine: tuple[tuple[str, str, str], ...] = ()   # (when, steps, journal)
    change_policy: str = ""
    without_dominant_day: bool = False  # grade return-based numbers without a flagged day

    def __post_init__(self) -> None:
        keys = [rule.key for rule in self.rules]
        if len(set(keys)) != len(keys):
            raise ValueError("rule keys must be unique in a rule set")
        symbols = [row.symbol for row in self.sleeves]
        if len(set(symbols)) != len(symbols):
            raise ValueError("each holding belongs to one sleeve")

    def rule(self, key: str) -> Rule | None:
        return next((rule for rule in self.rules if rule.key == key), None)

    def sleeve_of(self, symbol: str) -> str | None:
        row = next((row for row in self.sleeves if row.symbol == symbol), None)
        return row.sleeve if row else None
