"""Canonical OHLCV bar-frame validation.

Every externally *acquired* bar series (IBKR today; files or other vendors
later) passes through :func:`validate_ohlcv_frame` before it is persisted or
wrapped in a ``PriceHistory``. Synthetic data does not come through here — it
is generated under our own invariants. This module is the single place where
"what counts as a usable bar" is defined, so the storage layer and every
future provider share one notion of validity.

Two failure severities, deliberately distinct
---------------------------------------------
* **Structural** problems make the whole frame unusable (no ``close``
  column, an index that is not datetime-like, a timezone-aware index) and
  raise :class:`MalformedBarsError`. There is nothing sensible to salvage.
* **Row-level** problems (a non-positive close, ``high < low``, a
  non-numeric value) drop *that row only*; every dropped row is recorded in
  the returned :class:`ValidationReport` so callers can log it. Nothing is
  discarded silently.

One **repair** exists: IBKR reports volume as ``-1`` when it has no volume
data for a bar, so negative volume becomes missing (``NaN``) and the row is
kept — the prices are still real observations.

Invariants enforced on surviving rows
-------------------------------------
* ``close`` is finite and strictly positive (equities; matches
  ``PriceHistory``'s own contract).
* ``open``/``high``/``low``, *where present*, are finite and strictly
  positive. A missing (NaN) optional value is legal — ``close``-only rows
  are first-class citizens per the ``PriceHistory`` schema.
* ``high >= low``, ``high >= open``, ``high >= close``, ``low <= open``,
  ``low <= close`` — checked only where both operands are present.
* Timestamps are unique (duplicates keep the *last* occurrence — a vendor
  re-send supersedes the original) and the result is sorted ascending.

Time convention: daily bars are exchange **session dates** (naive
timestamps at midnight). A timezone-aware index is rejected as structural
until a future intraday milestone defines the timezone policy explicitly —
guessing here would manufacture off-by-one-day bugs.

Columns outside the canonical OHLCV set are dropped (vendor extras such as
IBKR's ``barCount``/``wap`` carry no meaning downstream).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.data.types import OPTIONAL_COLUMNS, REQUIRED_COLUMNS

#: Canonical column order for validated frames (present columns only).
CANONICAL_COLUMNS = ("open", "high", "low", "close", "volume")


class MalformedBarsError(ValueError):
    """The bar frame is structurally unusable (not row-level noise)."""


@dataclass(frozen=True)
class BarIssue:
    """One row-level problem, tied to the timestamp of the offending bar."""

    timestamp: pd.Timestamp
    reason: str


@dataclass(frozen=True)
class ValidationReport:
    """What validation did: counts plus every dropped/repaired row."""

    n_input: int
    n_valid: int
    dropped: tuple[BarIssue, ...]
    repaired: tuple[BarIssue, ...]

    @property
    def clean(self) -> bool:
        return not self.dropped and not self.repaired

    def summary(self) -> str:
        """One log-friendly line; per-row details stay on the report."""
        text = f"{self.n_input} bars in, {self.n_valid} valid"
        if self.dropped:
            reasons = "; ".join(
                f"{issue.timestamp:%Y-%m-%d}: {issue.reason}"
                for issue in self.dropped[:3]
            )
            more = ", …" if len(self.dropped) > 3 else ""
            text += f", {len(self.dropped)} dropped ({reasons}{more})"
        if self.repaired:
            text += f", {len(self.repaired)} repaired"
        return text


def validate_ohlcv_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, ValidationReport]:
    """Validate and normalize an acquired OHLCV frame.

    Returns ``(clean_frame, report)``. The result has a sorted, unique,
    timezone-naive ``DatetimeIndex``, float columns restricted to the
    canonical OHLCV set, and every surviving row satisfying the module
    invariants. An empty result is legal (e.g. an empty vendor response
    validates to an empty frame); wrapping in ``PriceHistory`` is the
    caller's decision because *that* type rejects emptiness.

    Raises :class:`MalformedBarsError` for structural problems.
    """
    for col in REQUIRED_COLUMNS:
        if col not in frame.columns:
            raise MalformedBarsError(f"bar frame missing required column {col!r}")

    if isinstance(frame.index, pd.DatetimeIndex):
        index = frame.index
    else:
        try:
            index = pd.DatetimeIndex(pd.to_datetime(frame.index))
        except (ValueError, TypeError) as exc:
            raise MalformedBarsError(f"bar index is not datetime-like: {exc}") from exc
    if index.tz is not None:
        raise MalformedBarsError(
            "timezone-aware bar index is not supported: daily bars are naive "
            "session dates (intraday timezone policy is a future milestone)"
        )

    n_input = len(frame)
    keep = [c for c in CANONICAL_COLUMNS if c in frame.columns]
    frame = frame[keep].set_axis(index).copy()
    # Non-numeric cells become NaN here and fall to the row checks below,
    # so "close was the string 'x'" is reported, not crashed on.
    for col in frame.columns:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(float)

    dropped: list[BarIssue] = []
    repaired: list[BarIssue] = []

    frame = frame.sort_index(kind="stable")
    duplicate = frame.index.duplicated(keep="last")
    dropped.extend(
        BarIssue(ts, "duplicate timestamp (kept last occurrence)")
        for ts in frame.index[duplicate]
    )
    frame = frame[~duplicate]

    if "volume" in frame.columns:
        negative_volume = frame["volume"] < 0  # IBKR's "-1 = unavailable"
        repaired.extend(
            BarIssue(ts, "negative volume treated as missing")
            for ts in frame.index[negative_volume]
        )
        frame.loc[negative_volume, "volume"] = np.nan

    # Row checks, in priority order; each bad row is reported once with the
    # first reason that condemned it.
    checks: list[tuple[pd.Series, str]] = []
    close = frame["close"]
    checks.append(
        (~np.isfinite(close) | (close <= 0), "close missing, non-finite, or <= 0")
    )
    for col in ("open", "high", "low"):
        if col in frame.columns:
            value = frame[col]
            checks.append(
                (
                    value.notna() & (~np.isfinite(value) | (value <= 0)),
                    f"{col} non-finite or <= 0",
                )
            )
    for upper, lower in (
        ("high", "low"),
        ("high", "open"),
        ("high", "close"),
    ):
        if upper in frame.columns and lower in frame.columns:
            checks.append(
                (
                    frame[upper].notna()
                    & frame[lower].notna()
                    & (frame[upper] < frame[lower]),
                    f"{upper} < {lower}",
                )
            )
    for lower, upper in (("low", "open"), ("low", "close")):
        if lower in frame.columns and upper in frame.columns:
            checks.append(
                (
                    frame[lower].notna()
                    & frame[upper].notna()
                    & (frame[lower] > frame[upper]),
                    f"{lower} > {upper}",
                )
            )

    drop_mask = pd.Series(False, index=frame.index)
    for mask, reason in checks:
        newly_bad = mask & ~drop_mask
        dropped.extend(BarIssue(ts, reason) for ts in frame.index[newly_bad])
        drop_mask |= mask
    frame = frame[~drop_mask]

    report = ValidationReport(
        n_input=n_input,
        n_valid=len(frame),
        dropped=tuple(sorted(dropped, key=lambda issue: issue.timestamp)),
        repaired=tuple(repaired),
    )
    return frame, report
