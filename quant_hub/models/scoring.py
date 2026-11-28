r"""Scoring the desk: what happened, how each claim did, and whether a voice
beats the baseline (D4).

The derivation below was reviewed and approved before the code
(2026-11-25, D104). The points that were reviewed are marked
**Review:**; each was kept as proposed.

A snapshot's claims are forecasts with a known resolve day
(``data/desk.resolves_on``). Once it has passed, each claim gets a
**score**. Each voice gets a **record**: its scores beside the baseline's
on the same claims, whether its edge is more than luck, how honest its
bands were, and all of it again by the market's regime at the time.

1. What happened (resolution)
-----------------------------
**Prices.** At resolution, the snapshot's own source serves each name's
adjusted closes on every NYSE session from ``as_of`` through the resolve
day: :math:`P_0, P_1, \dots, P_h`.

**Review:** every ratio comes from **one series fetched at resolution**,
not from the price stored in the snapshot. A dividend that goes ex after
``as_of`` rescales the whole adjusted past, so the stored :math:`P_0` and
a later :math:`P_h` would sit on different bases. Within one series,
:math:`P_t / P_0` is the total return, dividends in, whatever is adjusted
later. The stored prices still give the book's weights, which is what the
claims were about.

**A symbol's path** is :math:`V_t = P_t / P_0`. **The book's path** holds
the snapshot's units, with dividends reinvested and cash idle:

.. math::  \frac{V_t}{V_0} = \sum_i w_i\,\frac{P_{i,t}}{P_{i,0}} + w_c,
           \qquad w_i = \frac{n_i P_i}{V}, \quad w_c = \frac{C}{V}

with :math:`n_i, P_i, C, V` as stored. From a path, the three targets:

.. math::  \text{return} = \frac{V_h}{V_0} - 1, \qquad
           \text{vol} = \sqrt{\frac{252}{h}\sum_{t=1}^{h}
                        \Bigl(\ln\frac{V_t}{V_{t-1}}\Bigr)^2}, \qquad
           \text{fall} = \max_{0 \le u \le v \le h}\Bigl(1 - \frac{V_v}{V_u}\Bigr)

The fall is read as the voices read theirs (``baseline.deepest_falls``).

**Review:** every session needs a close for every name in the target. A
missing bar leaves the claim **unresolved**: it's retried by the next run,
since a provider's bar can arrive a day late. Ten sessions past due, it's
recorded as unresolvable, with the reason (a delisting, say). It's never
filled or guessed.

**Review:** outcomes are **stored once** (a new table, schema v13,
append-only, like the claims). Each row holds the snapshot, the target,
the horizon, the three numbers, the source and when it was resolved. A
record must not move because a provider revised a bar after the fact.

2. How a claim did (scores, lower is better)
--------------------------------------------
Both rules are **proper** (Gneiting and Raftery 2007): a forecaster
expects its best score by stating what it actually believes.

- **Quantiles:** the quantile score, averaged over the five levels,

  .. math::  QS = \frac{1}{5}\sum_{\tau} \bigl(\mathbb 1\{y < q_\tau\}
             - \tau\bigr)\,(q_\tau - y)

  in the target's own units. A book return's score times V is dollars;
  the ratios below don't care.

  **Review:** five levels make it a coarse version of the continuous
  ranked probability score, not CRPS itself. It scores exactly what was
  claimed, no more.
- **An event:** the Brier score :math:`(p - \mathbb 1\{E\})^2`, which is
  the headline, and the log score :math:`-\ln p` if E happened, else
  :math:`-\ln(1 - p)`.

  **Review:** the log score clips p to :math:`[10^{-4}, 1 - 10^{-4}]`. A
  simulated "0 of 20,000 paths" that happens costs 9.2, not infinity.

3. Does a voice beat the baseline? (skill and its test)
-------------------------------------------------------
Pair voice v with the baseline on every snapshot where both made the same
claim (same target, horizon and form) and it resolved. Then

.. math::  \text{skill} = 1 - \frac{\sum_s S_{v,s}}{\sum_s S_{b,s}},
           \qquad d_s = S_{v,s} - S_{b,s}

Positive skill means fewer points lost than the no-edge voice.

**Is it more than luck?** Diebold and Mariano (1995) test the loss
differential :math:`d_s` itself, whatever the loss. The snapshots are
daily and a claim spans h sessions, so neighbours share h − 1 sessions and
the :math:`d_s` are autocorrelated. The reviewed plan widened the variance
for that (Newey and West's weights to lag h − 1) and applied Harvey,
Leybourne and Newbold's (1997) small-sample correction.

**As built: only non-overlapping claims are tested.** A simulation (tests)
of MA(h − 1) differences with no edge rejected at 5% far too often:

- with Newey–West's weights, 12% of the time (h = 5, 60 snapshots), and
  12% with h = 21 and 250;
- with Diebold and Mariano's own equal weights, 8% in both;
- either one 15–18% with only two or three horizons' worth of snapshots.

Re-weighting can't fix overlap in samples this small. So the test keeps a
set of snapshots whose windows don't overlap. Starting from the latest,
each kept snapshot is at least h sessions before the one after it. Their
differences are independent under the null, and the test is a plain t on
them:

.. math::  t = \frac{\bar d}{s_d / \sqrt{m}}, \qquad
           \text{two-sided against Student's } t_{m-1}

with m the independent windows kept. Its size is what it says. The cost
is power: one window per h sessions, so a 21-session claim adds about one
a month and a year's claim one a year. The skill above still uses every
paired claim; only the test is restricted.

- Each (target, horizon, form) is tested on its own series, never pooled
  across horizons.
- No p-value is shown until there are ``MIN_WINDOWS`` = 5 independent
  windows; before that the record says "too few". For h = 252 that's five
  years, and the page says so.
- Voices are paired by name across versions. A version change is listed
  beside the record, not hidden.

4. Were the bands honest? (calibration)
---------------------------------------
- **Quantiles:** for each level τ, the share of outcomes at or below
  :math:`q_\tau`. An honest voice's 5% line is crossed 5% of the time.
  It's shown with the number of independent windows it rests on, about
  n/h, and not tested, because overlap makes a binomial test lie.
- **Events:** the average claimed probability beside the share that
  happened.

5. By regime
------------
Each snapshot is labeled at its ``as_of``, so nothing looks ahead, by the
market's volatility then: the baseline's market σ̂ (63 sessions,
annualized), against the terciles of the same statistic over French's
century. The labels are **calm**, **middling** and **stormy**. Skill and
calibration are reported overall and within each label. Snapshots taken
before the market was priced (D2b) are unlabeled.

**Review:** the century's terciles, not the last few years', are the
yardstick, so a label means the same thing in every snapshot. The other
choice is the regimes voice's own most likely state. That would make the
label depend on a voice being scored.

6. Your decisions
-----------------
A decision's ``expects`` are claims too. They're scored the same way,
under the author's name, and paired with the baseline's claims on the
snapshot the decision cites. Your judgment gets a skill score beside the
models'. Only a claim the baseline also made can be compared.

7. When
-------
The daily run resolves what's due, then takes the new snapshot. The Desk
page shows the record (D4b).

8. Head to head (A5)
--------------------
Section 3 pairs a voice with the baseline. The scorecard pairs any author
A with any other B the same way. A is you or an agent (a decision's
author), and B is a voice or the other of you. They're paired on every
snapshot where both made the same claim (target, horizon, form) and it
resolved:

.. math::  \text{skill}_{A|B} = 1 - \frac{\sum_s S_{A,s}}{\sum_s S_{B,s}},
           \qquad d_s = S_{A,s} - S_{B,s}

- **What the numbers mean.** Positive skill means A lost fewer points
  than B on the claims they share. **Wins** counts the snapshots where A's
  score was lower (ties count for neither).
- **The test** is section 3's: a plain t on the non-overlapping
  differences, nothing shown below ``MIN_WINDOWS``. Each (target,
  horizon, form) is tested on its own, overall and within each regime.
- **Section 3 is the case B = baseline.**
- **Several claims on one snapshot.** A decision's author can claim on a
  snapshot more than once, for instance two decisions the same day. Then
  that author's scores on the snapshot are averaged first, so each
  snapshot counts once.
- **Different samples, different yardsticks.** You and an agent claim on
  fewer snapshots than the voices. Comparing your skill against the
  baseline with a voice's would compare different samples. Head to head
  uses only the snapshots both made, which is why it's the fair
  comparison between two authors.

**Review:** three papers join the canon, each crossref-checked:
- Diebold and Mariano (1995), testing the loss differential;
- Harvey, Leybourne and Newbold (1997), whose correction the simulation
  found too weak here: it stays in the canon as the reason for the
  non-overlapping test, not as code;
- Gneiting and Raftery (2007), strictly proper scoring rules.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from quant_hub.data.desk import LEVELS, Claim, Outcome, event_happened
from quant_hub.data.trading_calendar import trading_days
from quant_hub.models.baseline import TRADING_DAYS, deepest_falls

BASELINE = "baseline"
LOG_CLIP = 1e-4
GIVE_UP_SESSIONS = 10            # past the resolve day, a missing bar is final
REGIME_WINDOW = 63               # sessions, as the baseline's σ̂
REGIMES = ("calm", "middling", "stormy")
MIN_WINDOWS = 5                  # independent windows before a p-value


# ------------------------------------------------------------ 1. resolution
def path_values(closes: pd.DataFrame, weights: Mapping[str, float],
                cash: float = 0.0) -> np.ndarray:
    """:math:`V_t / V_0` on the rows of ``closes`` (``as_of``'s first), for
    units held, dividends in (adjusted closes) and cash idle (section 1)."""
    names = list(weights)
    prices = closes[names].to_numpy(dtype=float)
    ratios = prices / prices[0]
    return ratios @ np.array([weights[n] for n in names], dtype=float) + cash


def resolve_path(values: np.ndarray) -> tuple[float, float, float]:
    """The return, realized volatility and deepest fall of a value path
    (section 1). Raises ValueError for a path that reaches zero."""
    values = np.asarray(values, dtype=float)
    h = len(values) - 1
    if h < 1:
        raise ValueError("a path needs at least one session after as_of")
    if not (values > 0).all():
        raise ValueError("the value reached zero or less")
    logs = np.diff(np.log(values))
    vol = math.sqrt(TRADING_DAYS / h * float(logs @ logs))
    fall = float(deepest_falls(logs[:, None], (h,))[h][0])
    return float(values[-1] / values[0] - 1.0), vol, fall


# ------------------------------------------------------------ 2. scores
def quantile_score(quantiles: Sequence[float], y: float) -> float:
    """The quantile score averaged over ``LEVELS`` (section 2)."""
    return sum(((1.0 if y < q else 0.0) - tau) * (q - y)
               for tau, q in zip(LEVELS, quantiles)) / len(LEVELS)


def brier(p: float, happened: bool) -> float:
    return (p - (1.0 if happened else 0.0)) ** 2


def log_score(p: float, happened: bool) -> float:
    """Clipped to [LOG_CLIP, 1 − LOG_CLIP] (section 2)."""
    p = min(max(p, LOG_CLIP), 1.0 - LOG_CLIP)
    return -math.log(p if happened else 1.0 - p)


@dataclass(frozen=True)
class Scored:
    """One resolved claim: what was said, what happened, its score."""

    snapshot_id: int
    as_of: date
    voice: str
    version: str
    claim: Claim
    y: float
    score: float                 # the quantile score, or the Brier score
    log_score: float | None      # events only
    regime: str | None

    @property
    def form(self) -> str:
        return "quantiles" if self.claim.kind == "quantiles" else self.claim.event


def score(claim: Claim, outcome: Outcome) -> tuple[float, float, float | None] | None:
    """(y, score, log score) for ``claim`` against its subject's outcome, or
    None when the outcome isn't resolved."""
    if outcome.status != "resolved":
        return None
    y = outcome.value(claim.target.rpartition(".")[2])
    if y is None:
        return None
    if claim.kind == "quantiles":
        return y, quantile_score(claim.quantiles, y), None
    happened = event_happened(claim.event, y)
    return y, brier(claim.probability, happened), log_score(claim.probability, happened)


# ------------------------------------------------------------ 3. the test
def incomplete_beta(a: float, b: float, x: float) -> float:
    """The regularized incomplete beta function :math:`I_x(a, b)`, by its
    continued fraction (modified Lentz) on the side where it converges."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    if x > (a + 1.0) / (a + b + 2.0):
        return 1.0 - incomplete_beta(b, a, 1.0 - x)
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    tiny = 1e-300
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (tiny if abs(d) < tiny else d)
    h = d
    for m in range(1, 10_000):
        for numerator in (m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
                          -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1))):
            d = 1.0 + numerator * d
            d = 1.0 / (tiny if abs(d) < tiny else d)
            c = 1.0 + numerator / c
            c = tiny if abs(c) < tiny else c
            h *= d * c
        if abs(d * c - 1.0) < 1e-15:
            break
    return front * h / a


def student_t_cdf(t: float, df: float) -> float:
    """Student's t distribution function, through the incomplete beta."""
    tail = 0.5 * incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


@dataclass(frozen=True)
class SkillTest:
    """Section 3's test on one series of differences :math:`d_s`."""

    n: int                       # independent windows tested
    mean_difference: float       # their mean difference
    t: float | None
    p: float | None              # two-sided
    reason: str                  # why there's no t, else ""


def independent(as_ofs: Sequence[date], h: int) -> list[int]:
    """Indices (into ``as_ofs``, ascending) of snapshots whose h-session
    windows don't overlap: from the latest back, each kept one at least h
    sessions before the one kept after it (section 3)."""
    keep: list[int] = []
    later: date | None = None
    for i in range(len(as_ofs) - 1, -1, -1):
        if later is None or len(trading_days(as_ofs[i] + timedelta(days=1), later)) >= h:
            keep.append(i)
            later = as_ofs[i]
    return keep[::-1]


def skill_test(differences: Sequence[float], as_ofs: Sequence[date], h: int) -> SkillTest:
    """The t-test on the non-overlapping differences (section 3)."""
    d = np.asarray(differences, dtype=float)
    kept = d[independent(as_ofs, h)] if len(d) else d
    m = len(kept)
    mean = float(kept.mean()) if m else 0.0
    if m < MIN_WINDOWS:
        return SkillTest(m, mean, None, None,
                         f"too few: {m} of {MIN_WINDOWS} independent windows")
    sd = float(kept.std(ddof=1))
    if not sd > 1e-15 * max(1.0, abs(mean)):
        return SkillTest(m, mean, None, None, "no variation in the differences")
    t = mean / (sd / math.sqrt(m))
    p = 2.0 * (1.0 - student_t_cdf(abs(t), m - 1))
    return SkillTest(m, mean, t, min(1.0, max(0.0, p)), "")


# ------------------------------------------------------------ 4, 5 and the record
def regime_cutoffs(market_total: np.ndarray, window: int = REGIME_WINDOW) -> tuple[float, float]:
    """The terciles of the market's annualized volatility over rolling
    ``window`` sessions, from its daily simple total returns (section 5)."""
    logs = pd.Series(np.log1p(np.asarray(market_total, dtype=float)))
    rolling = logs.rolling(window).std(ddof=1).dropna().to_numpy() * math.sqrt(TRADING_DAYS)
    if len(rolling) < window:
        raise ValueError("too little of the market's history for regime cutoffs")
    low, high = np.quantile(rolling, [1 / 3, 2 / 3])
    return float(low), float(high)


def regime_label(annual_vol: float | None, cutoffs: tuple[float, float] | None) -> str | None:
    """A snapshot's label from the market's volatility at its as_of."""
    if annual_vol is None or cutoffs is None:
        return None
    if annual_vol < cutoffs[0]:
        return REGIMES[0]
    return REGIMES[1] if annual_vol < cutoffs[1] else REGIMES[2]


@dataclass(frozen=True)
class Series:
    """One voice's record on one (target, horizon, form), against the
    baseline's on the same snapshots (section 3)."""

    voice: str
    target: str
    horizon: int
    form: str
    regime: str | None           # None: every snapshot
    n: int                       # scored claims
    score: float                 # their mean score
    paired: int                  # snapshots where the baseline made it too
    baseline: float | None       # the baseline's mean on those
    skill: float | None
    test: SkillTest | None
    versions: tuple[str, ...]


@dataclass(frozen=True)
class Calibration:
    """Section 4 for one (voice, target, horizon, form)."""

    voice: str
    target: str
    horizon: int
    form: str
    n: int
    windows: float               # about n / h independent windows
    coverage: tuple[float, ...] | None   # quantiles: share with y <= q at each level
    claimed: float | None        # events: the mean claimed probability
    happened: float | None       # events: the share that happened


def _groups(scored: Sequence[Scored]) -> dict[tuple, list[Scored]]:
    groups: dict[tuple, list[Scored]] = defaultdict(list)
    for s in sorted(scored, key=lambda s: (s.as_of, s.snapshot_id)):
        groups[(s.voice, s.claim.target, s.claim.horizon, s.form)].append(s)
    return groups


def record(scored: Sequence[Scored], regime: str | None = None) -> list[Series]:
    """Every voice's series, paired with the baseline's, overall or within
    one regime label (sections 3 and 5)."""
    rows = [s for s in scored if regime is None or s.regime == regime]
    base = {(s.snapshot_id, s.claim.target, s.claim.horizon, s.form): s.score
            for s in rows if s.voice == BASELINE}
    out = []
    for (voice, target, horizon, form), group in _groups(rows).items():
        versions = tuple(dict.fromkeys(s.version for s in group))
        mean = float(np.mean([s.score for s in group]))
        if voice == BASELINE:
            out.append(Series(voice, target, horizon, form, regime, len(group), mean,
                              len(group), mean, None, None, versions))
            continue
        pairs = [(s.score, base[(s.snapshot_id, target, horizon, form)], s.as_of)
                 for s in group if (s.snapshot_id, target, horizon, form) in base]
        if not pairs:
            out.append(Series(voice, target, horizon, form, regime, len(group), mean, 0, None,
                              None, None, versions))
            continue
        mine = np.array([p[0] for p in pairs])
        theirs = np.array([p[1] for p in pairs])
        as_ofs = [p[2] for p in pairs]
        total = float(theirs.sum())
        skill = 1.0 - float(mine.sum()) / total if total > 0 else None
        out.append(Series(voice, target, horizon, form, regime, len(group), mean, len(pairs),
                          float(theirs.mean()), skill,
                          skill_test(mine - theirs, as_ofs, horizon),
                          versions))
    return out


@dataclass(frozen=True)
class Duel:
    """Section 8 for one (author, against, target, horizon, form)."""

    author: str
    against: str
    target: str
    horizon: int
    form: str
    regime: str | None           # None: every snapshot
    paired: int                  # snapshots where both made the claim
    score: float                 # the author's mean on them
    against_score: float         # the other's mean on them
    skill: float | None
    wins: int                    # snapshots where the author's score was lower
    test: SkillTest


def _by_snapshot(rows: Sequence[Scored], author: str) -> dict[tuple, tuple[float, date]]:
    """``author``'s score per (snapshot, target, horizon, form), averaged
    when it claimed more than once there (section 8)."""
    acc: dict[tuple, list] = defaultdict(list)
    for s in rows:
        if s.voice == author:
            acc[(s.snapshot_id, s.claim.target, s.claim.horizon, s.form)].append(s)
    return {k: (float(np.mean([s.score for s in v])), v[0].as_of) for k, v in acc.items()}


def head_to_head(scored: Sequence[Scored], author: str, against: str,
                 regime: str | None = None) -> list[Duel]:
    """``author`` against ``against`` on every claim both made (section 8),
    overall or within one regime label."""
    rows = [s for s in scored if regime is None or s.regime == regime]
    mine, theirs = _by_snapshot(rows, author), _by_snapshot(rows, against)
    series: dict[tuple, list] = defaultdict(list)
    for key in sorted(set(mine) & set(theirs), key=lambda k: (mine[k][1], k[0])):
        series[key[1:]].append((mine[key][0], theirs[key][0], mine[key][1]))
    out = []
    for (target, horizon, form), pairs in sorted(series.items()):
        a = np.array([p[0] for p in pairs])
        b = np.array([p[1] for p in pairs])
        total = float(b.sum())
        out.append(Duel(author, against, target, horizon, form, regime, len(pairs),
                        float(a.mean()), float(b.mean()),
                        1.0 - float(a.sum()) / total if total > 0 else None,
                        int((a < b).sum()), skill_test(a - b, [p[2] for p in pairs], horizon)))
    return out


def calibration(scored: Sequence[Scored]) -> list[Calibration]:
    """Section 4 for every (voice, target, horizon, form)."""
    out = []
    for (voice, target, horizon, form), group in _groups(scored).items():
        n = len(group)
        if form == "quantiles":
            hits = np.array([[s.y <= q for q in s.claim.quantiles] for s in group], dtype=float)
            out.append(Calibration(voice, target, horizon, form, n, n / horizon,
                                   tuple(float(x) for x in hits.mean(axis=0)), None, None))
        else:
            happened = [event_happened(s.claim.event, s.y) for s in group]
            out.append(Calibration(voice, target, horizon, form, n, n / horizon, None,
                                   float(np.mean([s.claim.probability for s in group])),
                                   float(np.mean(happened))))
    return out
