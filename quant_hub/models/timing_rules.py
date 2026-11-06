r"""The 31 timing rules of 2026-10-24/25, ported so the family can count them
(REGIME_MODEL Q3c, D81).

That session tried 31 β rules on Ken French's market in scratch scripts
(``spikes/target``, local). They were tried, so Q3's family-wise test must
count them (``regime_evidence``, section 3). This module rebuilds each one's
daily β path exactly as the scripts did. The live check (D81) reproduces
their t table on the same files.

Conventions, as the scripts had them
------------------------------------
- **Returns:** the market's daily *simple* total return R_t (Mkt-RF + RF),
  not Q1's log returns.
- **The vol rules' σ:** √(252 · mean(R², last 21 days)), zero-mean.
- **The band:** β moves between a floor of 0.7 and a cap of 1.0.
- **A path is decided at close t** and applied to t + 1. The test lags it.
- **Valuation:** CAPE and the excess CAPE yield as Q1 builds them, with
  the scripts' lags: price, GS10 and CPI known after their month, and
  earnings 3 months later. Their real-time percentiles need 120 earlier
  months. Day t in month m reads month m − 1's value, as Q1's known-from
  index does.

The rules (lo, hi are vol levels; u = clip((σ − lo)/(hi − lo), 0, 1))
----------------------------------------------------------------------
- **contrarian:** 0.7 + 0.3u. Add β as vol rises.
- **mirror:** 1.0 − 0.3u. Cut β as vol rises.
- **ratchet:** contrarian, but β only rises until σ falls below lo, which
  resets it to the floor.
- **after spike:** 1.0 once σ is under ⅔ of its 63-day peak, after a peak
  of at least hi. Back to the floor below lo.
- **vol target:** min(1, 12%/σ).
- **CAPE cheap:** band((mid − p_CAPE)/(mid − cheap)), at the floor before a
  percentile exists.
- **ECY cheap:** band((p_ECY − (1 − mid))/(mid − cheap)), the same rule
  mirrored, since a high ECY is cheap.
- **dip:** band((dip − lo)/(hi − lo)), where dip is the fall from the
  252-day high of the market's wealth.
- **ECY × vol:** ECY cheap × min(1, 12%/σ). Floored, it's
  max(0.7, ECY cheap × min(1, 12%/σ)).

band(u) = 0.7 + 0.3·clip(u, 0, 1).

The family: 5 vol rules at (15%, 30%), 4 more at each of (12%, 25%),
(20%, 40%) and (25%, 50%), 3 valuation rules at (cheap 20%, mid 50%), CAPE
and ECY at three more (cheap, mid) pairs, the dip at three more ramps, and
the two combinations: 31.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FLOOR, CAP = 0.7, 1.0
VOL_TARGET = 0.12
VOL_WINDOW = 21
SPIKE_LAGS = {"price": 0, "gs10": 0, "cpi": 0, "earnings": 3, "dividend": 3}
"""The scripts' publication lags (Q1's are later: CPI 1, earnings 4)."""
MIN_MONTHS = 120


def spike_vol(total: pd.Series) -> pd.Series:
    """The scripts' σ: zero-mean, on simple returns, 21 days."""
    return np.sqrt(252.0 * (total**2).rolling(VOL_WINDOW).mean())


def _ramp(sigma: pd.Series, lo: float, hi: float) -> pd.Series:
    return ((sigma - lo) / (hi - lo)).clip(0.0, 1.0)


def band(u: pd.Series) -> pd.Series:
    return FLOOR + (CAP - FLOOR) * u.clip(0.0, 1.0)


def _ratchet(sigma: pd.Series, lo: float, hi: float) -> pd.Series:
    target = (FLOOR + (CAP - FLOOR) * _ramp(sigma, lo, hi)).to_numpy()
    s = sigma.to_numpy()
    out = np.empty_like(target)
    previous = FLOOR
    for t in range(len(s)):
        previous = FLOOR if not s[t] >= lo else max(previous, target[t])   # NaN → the floor
        out[t] = previous
    return pd.Series(out, index=sigma.index)


def _after_spike(sigma: pd.Series, lo: float, hi: float) -> pd.Series:
    s = sigma.to_numpy()
    peak = sigma.rolling(63, min_periods=1).max().to_numpy()
    out = np.empty_like(s)
    on = False
    for t in range(len(s)):
        if not s[t] >= lo:
            on = False
        elif peak[t] >= hi and s[t] <= peak[t] * 2.0 / 3.0:
            on = True
        out[t] = CAP if on else FLOOR
    return pd.Series(out, index=sigma.index)


def vol_rules(sigma: pd.Series, lo: float = 0.15, hi: float = 0.30) -> dict[str, pd.Series]:
    u = _ramp(sigma, lo, hi)
    return {"contrarian": FLOOR + (CAP - FLOOR) * u, "ratchet": _ratchet(sigma, lo, hi),
            "after spike": _after_spike(sigma, lo, hi), "mirror": CAP - (CAP - FLOOR) * u,
            "vol target": (VOL_TARGET / sigma).clip(upper=CAP)}


def valuation_rules(total: pd.Series, cape_pct: pd.Series, ecy_pct: pd.Series,
                    cheap: float = 0.2, mid: float = 0.5, dip_lo: float = 0.10,
                    dip_hi: float = 0.30) -> dict[str, pd.Series]:
    """``cape_pct``/``ecy_pct`` are on the market's days, as known then."""
    wealth = (1.0 + total).cumprod()
    dip = 1.0 - wealth / wealth.rolling(252, min_periods=1).max()
    return {"CAPE cheap": band((mid - cape_pct) / (mid - cheap)).where(cape_pct.notna(), FLOOR),
            "ECY cheap": band((ecy_pct - (1 - mid)) / (mid - cheap)).where(ecy_pct.notna(), FLOOR),
            "dip": band((dip - dip_lo) / (dip_hi - dip_lo))}


def family(total: pd.Series, cape_pct: pd.Series, ecy_pct: pd.Series) -> dict[str, pd.Series]:
    """All 31 β paths, named as the scripts named them."""
    sigma = spike_vol(total)
    rules: dict[str, pd.Series] = dict(vol_rules(sigma))
    for lo, hi in [(0.12, 0.25), (0.20, 0.40), (0.25, 0.50)]:
        more = vol_rules(sigma, lo, hi)
        for name in ("contrarian", "ratchet", "after spike", "mirror"):
            rules[f"{name} {lo:.0%}/{hi:.0%}"] = more[name]
    valuation = valuation_rules(total, cape_pct, ecy_pct)
    rules.update(valuation)
    for cheap, mid in [(0.1, 0.4), (0.3, 0.6), (0.2, 0.8)]:
        more = valuation_rules(total, cape_pct, ecy_pct, cheap, mid)
        rules[f"CAPE cheap {cheap:.0%}/{mid:.0%}"] = more["CAPE cheap"]
        rules[f"ECY cheap {cheap:.0%}/{mid:.0%}"] = more["ECY cheap"]
    for lo, hi in [(0.05, 0.20), (0.15, 0.40), (0.20, 0.50)]:
        rules[f"dip {lo:.0%}/{hi:.0%}"] = valuation_rules(total, cape_pct, ecy_pct,
                                                         dip_lo=lo, dip_hi=hi)["dip"]
    scale = (VOL_TARGET / sigma).clip(upper=CAP)
    rules["ECY × vol"] = valuation["ECY cheap"] * scale
    rules["ECY × vol, floored"] = (valuation["ECY cheap"] * scale).clip(lower=FLOOR)
    if len(rules) != 31:
        raise AssertionError(f"the family has {len(rules)} rules, not 31")
    return rules
