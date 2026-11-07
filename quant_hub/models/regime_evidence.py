r"""The evidence run: which regime inputs earn their place (REGIME_MODEL Q3b).

The derivation below was reviewed and approved before the code
(2026-11-05), as Q2's was. The points the owner reviewed are marked
**Review:**. Each was kept as proposed, and what building it changed is in
section 7.

Q3's output is a list of the inputs that stay, decided by a protocol fixed
here, before any run. The model that ships is the one the evidence keeps,
not the one the plan lists. Everything below runs walk-forward on data each
decision could have seen (Q1, Q2), and is saved as Research recorded runs
(Q3a, D80).

0. What's judged, and on what
-----------------------------
**Two kinds of input, two yardsticks:**
- **μ̂'s inputs** set how much market to hold. They are judged by
  *timing α*: does a β path that leans on them earn more than its market
  exposure explains?
- **The tail's inputs** forecast a fall. They are judged by *Brier skill*:
  do their probabilities beat the base rate?

σ̂ is judged as a baseline of its own: vol targeting.

**Why timing α, not growth, decides.** On 2026-10-25 the test was the
classical t of α in

    y_t = β_{t−1} · x_t = α + b x_t + e_t,     x_t = R_t − RF_t (the market's excess),

and so it is here, for three reasons:
- **It's comparable** with that session's 31 rules, which the family below
  must count.
- **It doesn't depend on f.** Multiplying β by c multiplies α and its error
  by c, so the t of an unclipped rule is the same for every Kelly fraction
  f. Inputs can be judged before decision 5 sets f.
- **It's the timing:** constant exposure has α = 0 by construction, so α
  is what the β path's movement earned.

Growth is still reported (section 4), at a provisional f, labelled as
such.

**Review:** the plan's text says an input stays if it "raises
out-of-sample growth". Timing α is that question, freed of f. Growth at a
given f follows once Q4 fixes f.

1. Data, decisions and the window
---------------------------------
- **The market:** French's daily Mkt-RF and RF from 1926-07. Cash earns
  RF each day, through Q3a's cash series.
- **When β is decided:** at each day's close, and held for the next day,
  as the 2026-10-24 rules were.
  - σ̂ moves daily: Q1's walk-forward GARCH at 21 days.
  - μ̂ and the tail move monthly: Q2's origins, each forecast held until
    the next month-end.
- **Costs:** Research's defaults, 1 bp half-spread and 1 bp commission on
  each trade, from the engine (D41). The harness reports what costs took.
- **The common window:** every variant is scored on the same days. They
  start when the last input in any variant can forecast: GARCH from 1937,
  plus μ̂'s ten years of known origins. That's about 1948, and the run
  reports the exact first day.

2. The provisional rule (until Q4)
----------------------------------
Q4 builds β* from the growth term and the chance constraint, and needs
decisions 4 and 5. Q3 needs only the growth term, Merton's ratio:

    β_t = f · μ_t / σ̂²_{t,21},     μ_t = μ̂_t + ½ σ̂²_{t,252}

- **The arithmetic premium:** μ_t converts Q2's log forecast to an
  arithmetic premium, as Q2's review noted. It's above zero, since μ̂ is
  floored at 0.
- **Unclipped, at f = 1, for the test:** clipping would make the t depend
  on f and the cap.
- **For growth's report:** f = ½, capped at β ≤ 1 (long and cash, no
  leverage), labelled provisional.

**Review:** the test's path can ask for a lot of leverage (β ≈ 3 at
today's readings). It's a statistic, not a book: nothing holds it.

3. The variants, fixed before any run (the ledger)
--------------------------------------------------
Every variant is a daily β path, saved as a recorded run (Q3a) tagged
``ledger = "regime"``, with its specification and input series. Nothing
is deleted, and losers are kept.

**The 2026-10-24/25 session's 31 rules come first.** They were tried, so
the family counts them.
- **Ported** from ``spikes/target`` (beta_timing.py, valuation_timing.py,
  combo_timing.py), on French's market as there.
- **The port must reproduce that session's t table,** to the second
  decimal.

**Then Q3's own, pre-registered:**

=====  =======================================  ============================
id     variant                                  μ̂'s inputs
=====  =======================================  ============================
B1     vol targeting, Moreira & Muir            none: μ̂ = μ̄ (the mean)
B2     T2: ECY × vol (the 10-25 rule)           ported, not Q2's μ̂
F      the full model                           every input
F−j    the full model without input j           all but j (one per input)
S_j    one input alone                          j (one per input)
=====  =======================================  ============================

With 8 inputs that makes 1 + 1 + 1 + 8 + 8 = 19 rule paths.

**Each input's own evidence** is the *difference path* Δ_j = β^F − β^{F−j}:
does adding j to the rest add timing? Each Δ_j is in the family too: 8
more.

**The family: 31 + 19 + 8 = 58 paths.** A later variant joins the ledger
and the family. It never replaces anything.

**Review:**
- whether to port all 31 rules or name them only. Naming them can't count
  them in the shift test, so porting is the honest choice.
- whether the S_j "alone" variants are worth their place in the family.
  Each one tested costs every other some power.

4. The measures, per variant
----------------------------
- **Timing α:** α ± Newey–West SE (``regression.ols``, plug-in lags),
  annualized as 252α, with its classical t for the shift test, as on
  10-25.
- **Growth, provisional:** the mean of ln(1 + r) − ln(1 + r_c), annualized,
  ± NW SE. Here r is the rule at f = ½ capped at 1, and r_c is constant
  exposure at that rule's own average β over the window. This is
  exposure-matched, so it isolates the timing.
  - **Review:** your constant-β level (REGIME.md) as a second baseline,
    read at run time and never written into code.
- **Drawdown:** the worst, and each of the nine crises of 2026-10-24
  (1929–32, 1937–38, 1973–74, 1987, 2000–02, 2007–09, 2020, 2022, 2025).
  Each is measured peak to trough on the market's own dates.
- **μ̂'s R²_OS** against the historical mean (Campbell & Thompson), for
  each variant's forecast.
- **The tail:**
  - **Brier skill:** 1 − Brier / Brier(base rate), for each threshold
    and for each single-input forecast and the pool
  - **A calibration table** in deciles
- **By era:** 1926–49 (where the window allows), 1950–92 and 1993 on.
- **After publication:** each input is measured only after its paper's
  year, from the table below.

**Review: the publication years.** Each paper is verified before it's
canon:

=======================  =====================================================
input                    after
=======================  =====================================================
valuation.ecy            2020, Shiller, Black & Jivraj
valuation.cape           1998, Campbell & Shiller
trend.excess_12m         2012, Moskowitz, Ooi & Pedersen
trend.average_200d       1992, Brock, Lakonishok & LeBaron
volatility.realized_21d  2017, Moreira & Muir
volatility.garch         2017, Moreira & Muir (GARCH itself 1986)
turbulence.calm          2002, Ang & Bekaert (regimes for allocation)
correlation.stock_bond   none found: reported by era only
=======================  =====================================================

5. The family-wise test (the 10-25 circular shift)
--------------------------------------------------
- **The statistic:** for each of the M = 58 paths, t_j is the classical t
  of α (section 0) over the common window.
- **The shift:** every path is shifted by the same k days against the
  market's returns, with k drawn uniformly from [5 years, T − 5 years]
  without replacement. That's 2,000 draws, with the seed recorded.
  Shifting keeps each path's persistence and its correlation with the
  others, and destroys only the timing.
- **One path's p:** p_j = #{k : t*_j(k) ≥ t_j} / 2000, one-sided.
- **The family's p:** p_FW = #{k : max_i t*_i(k) ≥ max_i t_i} / 2000.
- **Each path's adjusted p:** the share of shifts whose best t* across all
  M reaches t_j. That's the single-step max-t adjustment (Westfall &
  Young 1993); Romano & Wolf's step-down could only lower it.
- **An input stays when all of these hold:**
  - its difference path Δ_j has adjusted p ≤ 0.05 (decision 6)
  - after its paper's year, Δ_j's α is positive, with its NW interval
    reported
  - for a tail input, it also passes the tail test below

**Review: the post-publication rule.** Decision 6, as D80 records it,
keeps an input "only if it still clears the bar after publication".
- **The problem:** for ECY (2020) that's six years, and for momentum
  (2012) fourteen. No test at p ≤ 0.05 passes on so little.
- **Proposed instead:**
  - the bar applies to the whole window
  - after publication, α must stay positive, with its interval shown
  - McLean & Pontiff expect about half the effect to survive, so a
    positive α with a wide interval is what survival looks like
- **The stricter reading** would keep almost nothing.

**The tail's shift test.** The same idea on the forecasts.
- **How:** each tail forecast's probability path is shifted by k months
  against the events, from 5 years to n − 5 years.
- **Without refitting:** shifting the forecast, not refitting on a shifted
  input, costs nothing and asks the same question. Is the forecast's
  timing what beat the base rate?
- **The statistic:** Brier skill at each threshold. The family is every
  tail variant at every threshold.
- **A tail input stays** when its own forecast's skill has adjusted
  p ≤ 0.05, and stays positive after its publication.

**What the test can't do:**
- **Literature selection:** these inputs were chosen because papers found
  them. The post-publication rule is the only guard, and McLean & Pontiff
  (2016) expect about half of an effect to survive.
- **Episodes:** the tail rests on roughly a dozen large falls. The shift
  test answers "would this many hits happen by luck", not "will it hold".

6. What Q3c saves
-----------------
- **Every variant:** a recorded run per path (ledger "regime"), with its
  decisions, cash, input series and specification. They're replayable
  from Research's log.
- **The evidence:** one more recorded run, the kept model, whose details
  hold
  - the ledger's ids
  - every t, p_j and adjusted p
  - p_FW
  - the tail's table
  - the shift seed
  - the list of inputs kept

  Section 10's "a research run supporting it" names this run.
- **Cost:** about 4 minutes for Q1's inputs. Then the 19 rules' forecasts
  (μ̂ ≈ 0.3 s and the tail ≈ 4 s each) and 2,000 × 58 classical t's (fast,
  as on 10-25). It's a worker-thread job.

API (for review, to build after approval)
-----------------------------------------
- ``timing_t(beta, excess) -> float`` and ``timing_alpha(beta, excess)``:
  section 0.
- ``growth_rule(mean, sigma21, sigma252, f, cap) -> pd.Series``: section 2.
- ``shift_test(paths, excess, draws, min_shift, seed) -> ShiftTest``, with
  each path's t, p and adjusted p, and p_FW: section 5.
- ``tail_shift_test(forecasts, events, ...)``: the tail's version.
- ``crisis_drawdowns(returns, crises)`` and ``by_era(...)``: section 4.
- The ports of the 10-25 rules, in a module of their own.

Tests (with the code)
---------------------
- **Invariance:** timing t is unchanged when β is scaled, and constant β
  has α = 0.
- **Planted timing:** a β path that leans on a planted predictor of next
  day's return passes the shift test.
- **Noise:** across 200 seeds, a pure-noise family's p_FW ≤ 0.05 at about
  a 5% rate (calibration).
- **The port** reproduces the 10-25 t table on French's data.
- **The tail's shift:** a planted skill is found, and noise isn't.
- **No look-ahead:** every path at day t uses only Q1's and Q2's values
  known by t.

7. As built (D81)
-----------------
- **The family is 57 paths, not 58.** B2 is October's "ECY × vol" itself,
  so it's counted once: 31 + 18 + 8.
- **The common window** is 1937-07-01 to 2026-09-30, 23,064 days, not
  about 1948. Every variant forecasts once the historical mean has ten
  years of known targets, and each input joins as it qualifies.
- **One recorded run per evidence run,** not one per path.
  - **What it holds:** each path's id, kind, SHA-256 and statistics. The
    paths themselves are rebuilt from the stored series by their
    specification.
  - **Why:** 57 century-long runs would add about 85 MB to the store. The
    evidence run takes about 5 MB.
- **The tail's null shifts each forecast's deviation from the base rate,**
  not the forecast: shifted = base_t + (p − base) from k months earlier,
  clipped to [0, 1].
  - **Why:** the base rate is an expanding mean, and it drifts over the
    century. Shifting the whole forecast shifted that drift out of line
    too, so a worthless forecast lost accuracy when shifted and looked
    skilled. On French, a forecast that was almost the base rate (skill
    +0.0002) read p = 0.02.
  - **The fix:** the deviation's timing is all the null destroys.
- **A tail forecast with nothing to test is left out with p = 1.** That
  covers an exact base rate (no events in the window, where a NaN skill
  had read as p = 0), and a forecast that never leaves the base rate.
- **A tail input stays** if it passes at any threshold. The family already
  counts every threshold.
- **Passing means three things:**
  - positive skill, so the forecast beats the base rate
  - an adjusted p ≤ 0.05
  - positive skill after its paper's year
- **Why the first is needed:** under this null, a forecast with
  information can still be worse than the base rate. On French, ECY at
  30% had skill −0.007 and p 0.009: deviations placed at random cost
  accuracy, so even a below-base forecast can beat the shifted ones.
- **The port is exact.** On the scripts' own files, all 31 paths match bit
  for bit, and all 31 t's to two decimals.
- **The tests:** the noise check runs 100 seeds, not 200.

**Decision 6, amended (2026-11-06, D82).** The 2026-11-05 run kept no
input.
- **What happened under rule 1:** ECY's difference path cleared the bar
  (adjusted p 0.009), but its α since 2021 was −0.10% ± 0.77%, not
  positive.
- **The owner chose rule 2:** after publication, the 95% interval must
  only *reach* positive α (α + 1.96 SE > 0). Six years can't tell a
  surviving half-effect from none, and this asks only that the data not
  rule one out.
- **It was chosen after seeing the result,** so the code keeps both rules
  (``KEEP_RULES``). Each run records what each keeps, and the evidence run
  says which rule decided.
- **The β paths don't change,** so the family is the same 57. The rule
  changes which input is kept, not what was tried.
- **The tail's condition is unchanged:** skill after publication has no
  error to give it an interval.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.models.regression import ALPHA, NEWEY_WEST, ols

TRADING_DAYS = 252
SHIFTS = 2000
MIN_SHIFT_DAYS = 252 * 5
MIN_SHIFT_MONTHS = 12 * 5
SEED = 20261105
BAR = 0.05
"""Decision 6's family-wise p."""
Z_95 = 1.959963984540054
KEEP_RULES = {
    1: "after its paper's year, α is positive (approved 2026-11-05, D81)",
    2: "after its paper's year, the 95% interval reaches positive α: α + 1.96 SE > 0 "
       "(chosen 2026-11-06 after D81's run kept nothing under rule 1; D82)",
}
"""Decision 6's after-publication condition, by version. Rule 2 was chosen
after seeing rule 1's result, and the run reports what each keeps."""
KEEP_RULE = 2
GROWTH_F = 0.5
GROWTH_CAP = 1.0
"""The provisional growth report's Kelly fraction and cap (section 2)."""

CRISES = (
    ("1929–32", "1929-01-01", "1932-12-31"), ("1937–38", "1937-01-01", "1938-12-31"),
    ("1973–74", "1973-01-01", "1974-12-31"), ("1987", "1987-06-01", "1987-12-31"),
    ("2000–02", "2000-01-01", "2002-12-31"), ("2007–09", "2007-06-01", "2009-06-30"),
    ("2020", "2020-01-01", "2020-06-30"), ("2022", "2022-01-01", "2022-12-31"),
    ("2025", "2025-01-01", "2025-06-30"))
"""2026-10-24's crises: search ranges, with the peak and trough found in the
market's own wealth."""
ERAS = (("1926–49", None, "1949-12-31"), ("1950–92", "1950-01-01", "1992-12-31"),
        ("1993 on", "1993-01-01", None))
PUBLISHED = {
    "valuation.ecy": 2020, "valuation.cape": 1998, "trend.excess_12m": 2012,
    "trend.average_200d": 1992, "volatility.realized_21d": 2017, "volatility.garch": 2017,
    "turbulence.calm": 2002}
"""Section 4's table: each input is measured after its paper's year. The
stock–bond correlation has none, and is reported by era only."""


# ----------------------------------------------------------------- timing α

def _lagged(beta: pd.Series | pd.DataFrame, excess: pd.Series):
    """β decided at close t, earning t + 1's excess return."""
    return beta.shift(1).reindex(excess.index)


def timing_t(B: np.ndarray, x: np.ndarray) -> np.ndarray:
    """The classical t of α in y_j = α_j + b_j x + e, with y_j = B[:, j]·x:
    the 2026-10-25 statistic, for many paths at once."""
    T = len(x)
    Y = B * x[:, None]
    x_bar, y_bar = x.mean(), Y.mean(axis=0)
    xc = x - x_bar
    sxx = xc @ xc
    b = (xc @ Y) / sxx
    a = y_bar - b * x_bar
    syy = ((Y - y_bar) ** 2).sum(axis=0)
    s2 = np.maximum(syy - b**2 * sxx, 0.0) / (T - 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = a / np.sqrt(s2 * (1.0 / T + x_bar**2 / sxx))
    return np.where(s2 > 0, t, 0.0)      # an exact fit (constant β) has no timing: t = 0


@dataclass(frozen=True)
class TimingAlpha:
    alpha: float         # annualized, 252α
    error: float         # its Newey–West error, annualized
    t_classical: float
    days: int
    first: pd.Timestamp | None
    last: pd.Timestamp | None

    @property
    def t(self) -> float:
        return self.alpha / self.error if self.error > 0 else float("nan")


def timing_alpha(beta: pd.Series, excess: pd.Series, start=None, end=None) -> TimingAlpha:
    """Section 0's α with its Newey–West error, over [start, end]."""
    b = _lagged(beta, excess)
    both = pd.concat([b, excess], axis=1).dropna().loc[start:end]
    if len(both) < 30:
        return TimingAlpha(float("nan"), float("nan"), float("nan"), len(both), None, None)
    x = both.iloc[:, 1].to_numpy()
    y = both.iloc[:, 0].to_numpy() * x
    t = float(timing_t(both.iloc[:, [0]].to_numpy(), x)[0])
    if np.ptp(both.iloc[:, 0].to_numpy()) == 0:                  # no timing at all
        return TimingAlpha(0.0, 0.0, 0.0, len(both), both.index[0], both.index[-1])
    fit = ols(y, np.column_stack([np.ones(len(x)), x]), (ALPHA, "market"))
    return TimingAlpha(TRADING_DAYS * fit.value(ALPHA),
                       TRADING_DAYS * fit.error(ALPHA, NEWEY_WEST), t, len(both),
                       both.index[0], both.index[-1])


# --------------------------------------------------------------- the rule

def growth_rule(mean: pd.Series, sigma_21: pd.Series, sigma_252: pd.Series, f: float = 1.0,
                cap: float | None = None) -> pd.Series:
    """β = f(μ̂ + ½σ̂²₂₅₂)/σ̂²₂₁, ≥ 0, optionally capped (section 2). The σ's
    are annualized volatilities; μ̂ the annual log forecast."""
    if f <= 0:
        raise ValueError("f must be positive")
    days = sigma_21.index
    mu = mean.reindex(days) + 0.5 * sigma_252.reindex(days) ** 2
    beta = (f * mu / sigma_21**2).clip(lower=0.0)
    return beta.clip(upper=cap) if cap is not None else beta


def on_days(monthly: pd.Series, days: pd.DatetimeIndex) -> pd.Series:
    """A month-end forecast held from its origin's close to the next one's."""
    return monthly.dropna().reindex(monthly.dropna().index.union(days)).ffill().reindex(days)


# --------------------------------------------------- the family-wise shift

@dataclass(frozen=True)
class ShiftTest:
    names: tuple[str, ...]
    t: np.ndarray            # observed classical t, per path
    p: np.ndarray            # one path's shift p
    adjusted: np.ndarray     # single-step max-t adjusted p
    p_family: float
    first: pd.Timestamp
    last: pd.Timestamp
    days: int
    draws: int
    seed: int

    def row(self, name: str) -> dict:
        j = self.names.index(name)
        return {"t": float(self.t[j]), "p": float(self.p[j]), "adjusted": float(self.adjusted[j])}


def shift_test(paths: pd.DataFrame, excess: pd.Series, draws: int = SHIFTS,
               min_shift: int = MIN_SHIFT_DAYS, seed: int = SEED) -> ShiftTest:
    """Section 5: every path shifted by the same k days against the returns,
    on the days every path exists."""
    B = _lagged(paths, excess)
    keep = B.notna().all(axis=1) & excess.notna()
    B, x = B[keep], excess[keep]
    T, M = B.shape
    if T - 2 * min_shift < draws:
        raise ValueError(f"{T} common days leave too few shifts for {draws} draws")
    values, returns = B.to_numpy(), x.to_numpy()
    observed = timing_t(values, returns)
    ks = np.random.default_rng(seed).choice(np.arange(min_shift, T - min_shift), size=draws,
                                            replace=False)
    null = np.empty((draws, M))
    for i, k in enumerate(ks):
        null[i] = timing_t(np.roll(values, k, axis=0), returns)
    best = null.max(axis=1)
    return ShiftTest(tuple(paths.columns), observed, (null >= observed).mean(axis=0),
                     (best[:, None] >= observed).mean(axis=0),
                     float((best >= observed.max()).mean()), B.index[0], B.index[-1], T,
                     draws, seed)


@dataclass(frozen=True)
class TailShiftTest:
    names: tuple[str, ...]
    skill: np.ndarray        # Brier skill against the base rate, per forecast
    p: np.ndarray
    adjusted: np.ndarray
    months: int
    draws: int
    seed: int

    untestable: tuple[str, ...] = ()
    """Forecasts with nothing to test, left out of the family with p = 1:
    the base rate is exact over the window (skill undefined), or the
    forecast never leaves the base rate."""

    def row(self, name: str) -> dict:
        j = self.names.index(name)
        return {"skill": float(self.skill[j]), "p": float(self.p[j]),
                "adjusted": float(self.adjusted[j]), "testable": name not in self.untestable}


def brier_skill(forecast: np.ndarray, base: np.ndarray, events: np.ndarray) -> np.ndarray:
    """1 − Brier / Brier(base rate), for each column of ``forecast``; NaN when
    the base rate makes no error, which leaves nothing to beat."""
    model = ((forecast - events[:, None]) ** 2).sum(axis=0)
    reference = float(((base - events) ** 2).sum())
    if reference == 0:
        return np.full(model.shape, np.nan)
    return 1 - model / reference


def tail_shift_test(forecasts: Mapping[str, tuple[pd.Series, pd.Series, pd.Series]],
                    draws: int = SHIFTS, min_shift: int = MIN_SHIFT_MONTHS,
                    seed: int = SEED) -> TailShiftTest:
    """Section 5's tail test, as built (section 7): each forecast's deviation
    from the base rate shifted by k months against its events, the base rate
    left in place. ``forecasts`` maps a name to (forecast, base rate,
    events) on the same origins."""
    names = tuple(forecasts)
    frame = {}
    for name, (p, base, events) in forecasts.items():
        frame[(name, "p")], frame[(name, "b")], frame[(name, "e")] = p, base, events
    df = pd.DataFrame(frame).dropna()
    n = len(df)
    if n - 2 * min_shift < 1:
        raise ValueError(f"{n} common origins are too few for the tail's shift")
    P = np.column_stack([df[(name, "p")].to_numpy() for name in names])
    base = np.column_stack([df[(name, "b")].to_numpy() for name in names])
    skill = np.array([brier_skill(P[:, [j]], base[:, j], df[(name, "e")].to_numpy())[0]
                      for j, name in enumerate(names)])
    # A forecast that never leaves the base rate (an input restricted or
    # unfitted throughout) has nothing to test.
    testable = np.isfinite(skill) & (np.abs(P - base) > 1e-12).any(axis=0)
    # The null shifts each forecast's deviation from the base rate, not the
    # forecast: the base rate drifts over a century, and shifting that drift
    # out of line would make a worthless forecast look skilled.
    deviation = P - base
    shifts = np.arange(min_shift, n - min_shift)
    ks = np.random.default_rng(seed).choice(shifts, size=min(draws, len(shifts)), replace=False)
    null = np.full((len(ks), len(names)), -np.inf)
    for i, k in enumerate(ks):
        shifted = np.clip(base + np.roll(deviation, k, axis=0), 0.0, 1.0)
        for j in np.flatnonzero(testable):
            null[i, j] = brier_skill(shifted[:, [j]], base[:, j],
                                     df[(names[j], "e")].to_numpy())[0]
    best = null.max(axis=1)                  # over the testable forecasts only
    p = np.where(testable, (null >= skill).mean(axis=0), 1.0)
    adjusted = np.where(testable, (best[:, None] >= skill).mean(axis=0), 1.0)
    return TailShiftTest(names, skill, p, adjusted, n, len(ks), seed,
                         tuple(name for name, ok in zip(names, testable) if not ok))


# ----------------------------------------------------------------- reports

def portfolio_returns(beta: pd.Series, total: pd.Series, bills: pd.Series) -> pd.Series:
    """β of the market and the rest in bills, β from the previous close; no
    costs (the recorded run charges them)."""
    b = beta.shift(1).reindex(total.index)
    return (b * total + (1 - b) * bills.reindex(total.index)).dropna()


def growth_gain(beta: pd.Series, total: pd.Series, bills: pd.Series, start=None,
                end=None) -> tuple[float, float]:
    """Section 4: annualized mean log growth over constant exposure at the
    rule's own average β, ± Newey–West SE."""
    rule = portfolio_returns(beta, total, bills).loc[start:end]
    if len(rule) < 30:
        return float("nan"), float("nan")
    level = float(beta.shift(1).reindex(rule.index).mean())
    constant = level * total.reindex(rule.index) + (1 - level) * bills.reindex(rule.index)
    gap = (np.log1p(rule) - np.log1p(constant)).to_numpy()
    fit = ols(gap, np.ones((len(gap), 1)), ("mean",))
    return TRADING_DAYS * fit.value("mean"), TRADING_DAYS * fit.error("mean", NEWEY_WEST)


def crisis_drawdowns(returns: Mapping[str, pd.Series], total: pd.Series,
                     crises: Sequence[tuple[str, str, str]] = CRISES) -> pd.DataFrame:
    """Each path's fall from the market's peak to its trough in each crisis."""
    wealth = (1 + total).cumprod()
    rows = {}
    for label, a, b in crises:
        window = wealth.loc[a:b]
        if window.empty:
            continue
        falls = window / window.cummax() - 1
        trough = falls.idxmin()
        peak = window.loc[:trough].idxmax()
        rows[label] = {name: float(w.asof(trough) / w.asof(peak) - 1)
                       for name, w in ((n, (1 + r).cumprod()) for n, r in returns.items())
                       if w.index[0] <= peak}
    return pd.DataFrame(rows).T


def max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns).cumprod()
    return float((wealth / wealth.cummax() - 1).min())


def survives_publication(after: Mapping | None, rule: int = KEEP_RULE) -> bool:
    """Decision 6's after-publication condition (``KEEP_RULES``). ``after``
    holds α and its error after the paper's year; ``None`` means no
    publication year, so the input is reported by era only."""
    if rule not in KEEP_RULES:
        raise ValueError(f"no keep rule {rule}; have {sorted(KEEP_RULES)}")
    if after is None:
        return True
    if rule == 1:
        return after["alpha"] > 0
    return after["alpha"] + Z_95 * after["error"] > 0


def after_year(year: int) -> str:
    return f"{year + 1}-01-01"

