"""The regime model's inputs from the stored series (REGIME_MODEL Q1).

Reads Ken French's daily market and bills (D60) and Shiller's monthly data
(D61) from the series store, and builds every input of
REGIME_MODEL_PLAN's section 1 as it was known at the time
(``models/regime_inputs.py``). Each one is raw, as a real-time percentile
and as a real-time z.

| input                     | from              | clock   |
|---------------------------|-------------------|---------|
| realized volatility, 21d  | French            | daily   |
| GARCH expected volatility | French, refitted  | daily   |
| HMM calm probability      | French, refitted  | daily   |
| 12-month excess return    | French            | daily   |
| against the 200-day mean  | French            | daily   |
| CAPE, excess CAPE yield   | Shiller, lagged   | monthly |
| stock–bond correlation    | Shiller, lagged   | monthly |

**Not here yet:**
- **The credit spread** (Moody's Baa less the 10-year). FRED serves Moody's
  BAA from 1919, but Moody's notice forbids storing it "for subsequent
  use" without consent. That's the owner's terms call (D34).
- **A daily stock–bond correlation:** it needs FRED's DGS10.

Blocking (a century of walk-forward refits took 218 s on French's market):
run on the TaskRunner, or in Q3's evidence run. Without French or Shiller, the
inputs that need them are left out, and the result says how to fetch them.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant_hub.data.series import StoredSeries
from quant_hub.models.regime_inputs import (
    AVERAGE_DAYS,
    CORRELATION_MONTHS,
    GARCH_HORIZON,
    LAGS,
    MIN_DAYS,
    MIN_MONTHS,
    REALIZED_DAYS,
    REFIT_YEARS,
    REGIMES,
    RegimeInput,
    WalkForward,
    excess_cape_yield,
    real_time_cape,
    realized_vol,
    stock_bond_correlation,
    trend_average,
    trend_excess,
    walk_forward,
)
from quant_hub.services.external_data import ExternalDataService

MARKET = ("french", "ff3.mkt_rf")
BILLS = ("french", "ff3.rf")
SHILLER = {"price": "sp.price", "dividend": "sp.dividend", "earnings": "sp.earnings",
           "cpi": "cpi", "gs10": "gs10"}


@dataclass(frozen=True)
class RegimeInputSet:
    inputs: tuple[RegimeInput, ...]
    walk: WalkForward | None
    sources: tuple[str, ...]
    """Each series read, with its vintage note."""
    missing: tuple[str, ...]
    """Why an input is left out, and how to fetch what it needs."""

    def get(self, name: str) -> RegimeInput:
        for item in self.inputs:
            if item.name == name:
                return item
        raise KeyError(f"no regime input {name!r}; have {[i.name for i in self.inputs]}")

    def on_days(self, days: pd.DatetimeIndex, scale: str = "percentile") -> pd.DataFrame:
        """Every input on a daily grid, on one scale ("raw", "percentile" or
        "z"): each day holds the latest value known by then."""
        if scale not in ("raw", "percentile", "z"):
            raise ValueError(f"unknown scale {scale!r}")
        return pd.DataFrame({i.name: i.on_days(days)[scale] for i in self.inputs}, index=days)


def load_regime_inputs(external: ExternalDataService, *, refit_years: int = REFIT_YEARS,
                       regimes: int = REGIMES, horizon: int = GARCH_HORIZON,
                       lags: dict[str, int] = LAGS, walk: bool = True) -> RegimeInputSet:
    """Blocking: build every input the stored series allow. ``walk=False``
    skips the GARCH and HMM refits, the slow part."""
    inputs: list[RegimeInput] = []
    sources: list[str] = []
    missing: list[str] = []
    walked: WalkForward | None = None

    def read(source: str, series: str) -> pd.Series:
        stored: StoredSeries = external.series(source, series)
        sources.append(f"{source}/{series}" + (f" ({stored.meta.note})" if stored.meta.note
                                                else ""))
        return stored.values

    try:
        excess, bills = read(*MARKET), read(*BILLS)
    except LookupError as exc:
        missing.append(f"The daily inputs need Ken French's factors: {exc}")
    else:
        bills = bills.reindex(excess.index)
        total = (excess + bills).dropna()
        day = "the day's close"
        inputs += [
            RegimeInput.build("volatility.realized_21d",
                              f"Realized volatility, {REALIZED_DAYS} days", "daily", day,
                              realized_vol(total), MIN_DAYS),
            RegimeInput.build("trend.excess_12m", "12-month excess return (log)", "daily", day,
                              trend_excess(total, bills), MIN_DAYS),
            RegimeInput.build("trend.average_200d",
                              f"Total-return index against its {AVERAGE_DAYS}-day average",
                              "daily", day, trend_average(total), MIN_DAYS),
        ]
        if walk:
            walked = walk_forward(total, refit_years, horizon, regimes)
            refitted = f"the day's close; refitted each January on {refit_years} years"
            inputs += [
                RegimeInput.build("volatility.garch",
                                  f"GARCH expected volatility, next {horizon} days", "daily",
                                  refitted, walked.garch_vol, MIN_DAYS),
                RegimeInput.build("turbulence.calm",
                                  f"HMM calm-regime probability, {regimes} regimes", "daily",
                                  refitted, walked.calm, MIN_DAYS),
            ]
            failed = [r for r in walked.refits if r.problem]
            if failed:
                missing.append("Refits that failed, left missing: " + "; ".join(
                    f"{r.day:%Y}: {r.problem}" for r in failed))

    try:
        shiller = {key: read("shiller", series) for key, series in SHILLER.items()}
    except LookupError as exc:
        missing.append(f"Valuation and the stock–bond correlation need Shiller's data: {exc}")
    else:
        lagged = (f"from the 1st after the month; CPI {lags['cpi']} month later, "
                  f"earnings {lags['earnings']}")
        inputs += [
            RegimeInput.build("valuation.cape", "CAPE, real time", "monthly", lagged,
                              real_time_cape(shiller["price"], shiller["earnings"],
                                             shiller["cpi"], lags), MIN_MONTHS),
            RegimeInput.build("valuation.ecy", "Excess CAPE yield, real time", "monthly",
                              lagged,
                              excess_cape_yield(shiller["price"], shiller["earnings"],
                                                shiller["cpi"], shiller["gs10"], lags),
                              MIN_MONTHS),
            RegimeInput.build("correlation.stock_bond",
                              f"Stock–bond correlation, {CORRELATION_MONTHS} months",
                              "monthly",
                              f"from the 1st after the month; dividends {lags['dividend']} "
                              "months late",
                              stock_bond_correlation(shiller["price"], shiller["dividend"],
                                                     shiller["gs10"], lags=lags),
                              MIN_MONTHS),
        ]
    return RegimeInputSet(tuple(inputs), walked, tuple(sources), tuple(missing))
