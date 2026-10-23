r"""Regime checks: the owner's rules, measured and graded (W7).

The derivation below was reviewed before the code (approved 2026-10-23),
as W2's and W3's were. The rules are the owner's ``REGIME.md`` (sections
1–8). This module answers, at each review:
- Which rules hold, and which are breached, each with its number?
- When a rule reads an estimate, is the breach evidence or noise?
- Has a stream failed long enough to count against the thesis?
- Is a holding's variance one day's doing?
- Is the market's state calm, and what is volatility expected to be?

The checks are pure functions of a rule set and the measures. Everything
around them is engineering, in WORKSTATION_PLAN.md's W7:
- reading the rules from ``REGIME.md`` (``importers/regime_md.py``)
- storing each version (``data/regime.py``, ``storage/regime_store.py``,
  schema v6)
- the review's job chain and journal (``services/review.py``)

Rules and measures
------------------
A **rule** is one row of ``REGIME.md``:
- the measure it reads
- a limit: an upper bound U, a lower bound L, or a band [L, U]
- its unit
- the owner's grade on breach g ∈ {fail, warn, note}

A blank value (``___``) turns the rule off. A value the parser can't read
is a loud error that names the line, never a guess.

A **measure** is (m̂, s): an estimate and its standard error. s is absent
for quantities read exactly from the snapshot or computed from it without
sampling error:
- weights, cash, effective holdings N_eff = 1 / Σ wᵢ²
- premium at risk, short options, days to expiry
- a sleeve's weight against its intended range

The estimated measures come from W2–W6 with their own errors:
- β: Newey–West (W2)
- α, the growth margin, and the sizes c_mv and c*: W3's errors
- the residual correlation ρ: Fisher z, s_z = 1 / √(T − 3) (W2)

Some estimates have no error in the models yet: volatility, Euler risk
shares, VaR, ES and drawdown. They're checked as exact until one is
derived. The review says so beside each.

A breach, and how often noise causes one
----------------------------------------
For an upper limit U and the owner's evidence level k ≥ 0 (REGIME.md's
"by ___ SE"; k = 0 means the estimate alone):

    breach  ⇔  m̂ − k·s > U

When m̂ > U without that, the review shows the rule as *near*: visible,
not graded. A lower limit mirrors this, and a band checks both sides. An
exact measure breaches when m̂ > U.

For a correlation limit |ρ| ≤ U, the same rule applies on Fisher's scale.
With z = atanh ρ̂:

    breach  ⇔  tanh(|z| − k·s_z) > U

How often noise alone breaches: take m̂ ~ N(m, s²) with s known.

    P(breach) = 1 − Φ((U + k·s − m) / s)

At the boundary, m = U, that's 1 − Φ(k):

| k | 0 | 1 | 1.645 | 2 |
|---|---|---|---|---|
| Share of reviews breached when the true value sits on the limit | 50% | 16% | 5% | 2.3% |

A true excess of δ above the limit is caught with probability
1 − Φ(k − δ/s). Raising k trades false breaches for missed ones.

Significance isn't size. At T = 500 days, any |ρ̂| above
1.96 / √497 ≈ 0.088 is statistically nonzero. So a correlation limit
should be a size the owner cares about, with k deciding how sure to be.

Persistence: a delay, not a filter
----------------------------------
Section 1 counts a stream's test as a *fail* after N reviews in a row, and
as a *warn* before that. The run length is counted over reviews, one per
trading day (that day's last):

    R_n = R_{n−1} + 1 if review n breaches, else 0
    warn if 1 ≤ R_n < N;  fail if R_n ≥ N

Consecutive reviews on a lookback of W days share W − 1 of them. For a
mean over the window, consecutive estimates have correlation
(W − 1) / W ≈ 0.998 at W = 500. So N daily reviews add only N days of new
evidence:
- A breach caused by estimation noise persists for months, whatever N is.
  The filter for noise is k.
- What N does filter is a jump: a day entering or leaving the window.
  It is a delay measured in days, so the owner should set N as one.

One day dominating a series
---------------------------
For a holding's daily returns r_t over the window:

    D = max_t r_t² / Σ_t r_t²

D is the largest day's share of the sum of squares, which is the variance
for a mean near zero.
- **A huge day pushes D to 1.** One day of size x, among T − 1 days of
  variance σ², gives D = x² / (x² + (T − 1)σ²). For example, a +1,000%
  day in a stock that otherwise moves 3% a day, with T = 500, gives
  D ≈ 0.996.
- **Variance-based measures are then that day's.** Volatility, risk
  shares, R², Kelly and the growth figures all follow it. Loss-based ones
  (VaR, ES, drawdown) aren't moved by an upside jump, though a crash moves
  both.

What D is under the null, at T = 500 iid days:

| Null | Median | 95% | 99% | 99.9% |
|---|---|---|---|---|
| Gaussian | 0.020 | 0.030 | 0.035 | 0.045 |
| Student t(3) | 0.099 | 0.364 | 0.624 | 0.875 |

The Gaussian row is from 20,000 runs. The t(3) row is from the
calibration's 100,000 runs (seed 20261023); its 99.9% point has a 99.8%
band of [0.860, 0.897]. D is scale-free, so t(3)'s variance doesn't
matter.

The Gaussian null is far too thin for equities. SPY's own D from
2024-10-24 to 2026-10-21 is 0.20, from one +10.5% day. Eight other
stocks and ETFs measured over that window ran from 0.04 to 0.20.

The flag: D above the t(3) null's 99.9% quantile at the series' length.
That's about 0.88 at T = 500. It's calibrated by a fixed-seed simulation
on a grid of T (``DOMINANCE_NULL``), interpolated in log T, and tested. A
flagged holding's variance-based measures are shown twice: as measured,
and with that day removed. Which of the two is graded is the owner's
choice.

Market-state gates (section 7)
------------------------------
Each review refits the models in ``models/markov.py`` and
``models/garch.py`` to the benchmark's daily log returns over the lookback.
- **Calm-regime probability.** A K-regime Gaussian HMM. The calm regime is
  the one with the lowest variance. The gate reads the *filtered*
  probability α_T(calm) = P(s_T = calm | r_1..r_T). At T it equals the
  smoothed one. The gate's history, for its chart and for persistence,
  uses α_t for every t, because the smoothed γ_t uses r_{t+1..T}, which
  looks ahead. Viterbi does too.
- **Regime odds ahead.** P(s_{T+h} = k) = (α_T Pʰ)_k for h = 5 and 20
  days (``regime_forecast``).
- **Volatility forecast.** A GARCH(1,1) with persistence φ = a + b and
  long-run variance σ̄² = ω / (1 − φ). Since
  E_T[σ²_{T+j}] = σ̄² + φ^{j−1}(σ²_{T+1} − σ̄²), the h-day average is

      σ̂²_h = σ̄² + (σ²_{T+1} − σ̄²)(1 − φʰ) / (h(1 − φ))

  annualized as √(252 σ̂²_h) (``average_variance_forecast``).
- **A closed gate changes the checklist, never a broker.** Its effects
  (D12):
  - *block adds*: the review says no new risk today
  - *suggest hedge*: it names the hedge sleeve
  - *note*: recorded only

Universe and screens (section 8)
--------------------------------
These read W6's outputs:
- the margin of safety 1 − P / V
- the terminal share PV(TV) / EV
- implied growth against c × the trailing revenue CAGR

The reverse DCF can return several roots. When it does, the check reports
*ambiguous* (a note) rather than choosing one; when the root is unique, it
uses that root. Missing filing fields are counted from W5's loud-failure
list.

API
---
- ``check(rule, measure) -> Check``: pass, near, breach or no data, with
  how far past the limit (in units, and in standard errors).
- ``breach_rate(k, shift=0)``: 1 − Φ(k − shift).
- ``run_length(reviews)`` and ``persistence(run, required)``: section 1's
  count and its grade.
- ``dominance(returns) -> Dominance``, ``dominance_threshold(T)`` and
  ``dominance_null(T, runs, seed)``.
- ``calm_regime(log_returns, regimes) -> CalmGate`` and
  ``volatility_forecast(log_returns, horizon)``: section 7's gates.
- ``implied_growth_check(rule, roots, trailing_cagr) -> Check``: section 8's
  reverse-DCF rule.

The records rules and measures are made of (``Rule``, ``Limit``,
``Measure``, ``Grade``, ``Effect``) live in ``data/regime.py``.

Tests, written with the code
----------------------------
- The breach rate at the boundary, simulated, matches 1 − Φ(k) for k ∈
  {0, 1, 1.645, 2}; the power matches 1 − Φ(k − δ/s).
- The Fisher-scale breach agrees with W2's interval at k = 1.96.
- Run lengths: resets, several reviews in one day, gaps.
- D: the null quantiles reproduce the table at the fixed seed, and one
  huge day gives D = x² / (x² + (T − 1)σ²).
- The gate history is filtered: it equals the smoothed probability at T
  and differs before T on a simulated two-regime path.
- The GARCH average matches a Monte Carlo of the recursion.
- The parser: every template row round-trips, a blank turns its rule off,
  and an unreadable value names its line.

For the owner to decide
-----------------------
1. **Evidence k** for each estimated rule. k = 0 is the estimate alone,
   which breaches half the time on the boundary.
2. **N for section 1, in days**, given that persistence is a delay.
3. **Dominated series:** grade the variance-based measures as measured,
   or with the day removed.
4. **Unchanged from REGIME.md:** sections 4–8's values, and section 7's K
   and horizon.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from statistics import NormalDist

import numpy as np

from quant_hub.data.regime import Consequence, Grade, Limit, Measure, Rule
from quant_hub.models.garch import average_variance_forecast, fit_garch11, next_variance
from quant_hub.models.markov import HMMFit, fit_hmm, hmm_filter, regime_forecast
from quant_hub.models.portfolio import MIN_DAYS, TRADING_DAYS

_NORMAL = NormalDist()

DOMINANCE_QUANTILE = 0.999
DOMINANCE_SEED = 20261023
DOMINANCE_NULL: tuple[tuple[int, float], ...] = (
    (60, 0.952), (125, 0.935), (250, 0.907), (500, 0.875),
    (750, 0.863), (1000, 0.852), (1500, 0.848), (2500, 0.798),
)
"""(T, the 99.9% quantile of D) for iid t(3) days: ``calibrate_dominance()``,
100,000 runs per T from one generator seeded ``DOMINANCE_SEED``, in this
order (module docstring, One day dominating)."""


class Outcome(Enum):
    PASS = "pass"
    NEAR = "near"              # past the limit, but not by k errors: shown, not graded
    BREACH = "breach"
    AMBIGUOUS = "ambiguous"    # several values fit (a reverse DCF's roots)
    MISSING = "no data"


@dataclass(frozen=True)
class Check:
    """One rule against one measure (module docstring, A breach)."""

    rule: Rule
    measure: Measure | None
    outcome: Outcome
    bound: float | None = None     # the side of the limit it is past
    excess: float | None = None    # how far past, in the measure's units
    z: float | None = None         # the same in standard errors (Fisher's, for ρ)
    reason: str = ""

    @property
    def consequence(self) -> Consequence | None:
        """The rule's grade or gate effect, when breached."""
        return self.rule.consequence if self.outcome is Outcome.BREACH else None


def check(rule: Rule, measure: Measure | None) -> Check:
    """``rule`` against ``measure``: breach when the estimate is past the
    limit by more than k standard errors, near when past it by less, and
    for an exact measure, breach when past it at all (module docstring)."""
    return _compare(rule, rule.limit, measure)


def _compare(rule: Rule, limit: Limit, measure: Measure | None) -> Check:
    if measure is None:
        return Check(rule, None, Outcome.MISSING, reason="not measured")
    if measure.correlation:
        return _correlation(rule, limit, measure)
    for bound, sign in ((limit.upper, 1.0), (limit.lower, -1.0)):
        if bound is None:
            continue
        excess = sign * (measure.estimate - bound)
        if excess <= 0:
            continue
        if not measure.error:                  # exact (or an error of zero)
            return Check(rule, measure, Outcome.BREACH, bound, excess)
        z = excess / measure.error
        outcome = Outcome.BREACH if z > rule.evidence else Outcome.NEAR
        return Check(rule, measure, outcome, bound, excess, z)
    return Check(rule, measure, Outcome.PASS)


def _correlation(rule: Rule, limit: Limit, measure: Measure) -> Check:
    """|ρ| ≤ U on Fisher's scale: breach iff atanh|ρ̂| − k/√(T − 3) > atanh U."""
    if limit.lower is not None or limit.upper is None:
        raise ValueError(f"{rule.key}: a correlation rule limits |ρ| from above only")
    if not 0.0 <= limit.upper < 1.0:
        raise ValueError(f"{rule.key}: a limit on |ρ| must be in [0, 1), not {limit.upper}")
    size = abs(measure.estimate)
    excess = size - limit.upper
    if excess <= 0:
        return Check(rule, measure, Outcome.PASS)
    error = 1.0 / math.sqrt(measure.observations - 3)
    z = (math.atanh(size) - math.atanh(limit.upper)) / error
    outcome = Outcome.BREACH if z > rule.evidence else Outcome.NEAR
    return Check(rule, measure, outcome, limit.upper, excess, z)


def breach_rate(evidence: float, shift: float = 0.0) -> float:
    """The chance an estimate N(m, s²) breaches its limit at evidence k when
    m sits ``shift`` standard errors past the limit: 1 − Φ(k − shift)."""
    if not (math.isfinite(evidence) and evidence >= 0):
        raise ValueError("evidence k must be finite and not negative")
    if not math.isfinite(shift):
        raise ValueError("shift must be finite")
    return 1.0 - _NORMAL.cdf(evidence - shift)


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def run_length(reviews: Sequence[tuple[date | datetime, bool]]) -> int:
    """Section 1's R for the latest review: how many reviews in a row have
    breached, ending with it. One review counts per day, that day's last.
    Days without a review neither break a run nor extend it (module
    docstring, Persistence). ``reviews`` are (when, breached) in time order."""
    days: dict[date, bool] = {}
    previous = None
    for when, breached in reviews:
        # A bare date is that day's start, so dates and timestamps can mix.
        moment = when if isinstance(when, datetime) else datetime.combine(when, time.min)
        if previous is not None and moment < previous:
            raise ValueError("reviews must be in time order")
        previous = moment
        days[moment.date()] = bool(breached)
    run = 0
    for breached in reversed(list(days.values())):
        if not breached:
            break
        run += 1
    return run


def persistence(run: int, required: int) -> Grade | None:
    """Section 1's grade for a run of ``run`` breached reviews: none, a warn
    below ``required`` (N), a fail at N or more."""
    if required < 1:
        raise ValueError("N must be at least one review")
    if run < 0:
        raise ValueError("a run length can't be negative")
    if run == 0:
        return None
    return Grade.FAIL if run >= required else Grade.WARN


# --------------------------------------------------------------------------
# One day dominating
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Dominance:
    """The largest day's share of a series' sum of squares (module docstring)."""

    share: float           # D
    day: int               # the largest day's position in the series
    value: float           # its return
    observations: int      # T
    threshold: float       # the t(3) null's 99.9% quantile at T

    @property
    def flagged(self) -> bool:
        return self.share > self.threshold


def dominance(returns) -> Dominance:
    """D = max r² / Σ r² for one holding's daily returns, with its flag."""
    r = np.asarray(returns, dtype=float)
    if r.ndim != 1:
        raise ValueError("returns must be one series")
    if len(r) < MIN_DAYS:
        raise ValueError(f"{len(r)} days; the dominance check needs {MIN_DAYS}")
    if not np.isfinite(r).all():
        raise ValueError("returns must be finite")
    squares = r * r
    total = float(squares.sum())
    if total <= 0:
        raise ValueError("a series with no movement has no dominant day")
    day = int(np.argmax(squares))
    return Dominance(float(squares[day] / total), day, float(r[day]), len(r),
                     dominance_threshold(len(r)))


def dominance_threshold(observations: int) -> float:
    """The t(3) null's 99.9% quantile of D at T days: ``DOMINANCE_NULL``
    interpolated in log T, and held at its last value beyond 2,500 days."""
    if observations < DOMINANCE_NULL[0][0]:
        raise ValueError(f"the threshold is calibrated from {DOMINANCE_NULL[0][0]} days, "
                         f"not {observations}")
    days = np.log([t for t, _ in DOMINANCE_NULL])
    quantiles = [q for _, q in DOMINANCE_NULL]
    return float(np.interp(math.log(observations), days, quantiles))


def dominance_null(observations: int, runs: int, rng=None, *, chunk: int = 10_000
                   ) -> np.ndarray:
    """D for ``runs`` series of ``observations`` iid t(3) days, drawn
    ``chunk`` series at a time. ``rng`` is a seed or a Generator."""
    if observations < 2 or runs < 1 or chunk < 1:
        raise ValueError("observations must be at least 2, runs and chunk at least 1")
    generator = np.random.default_rng(rng)
    shares = []
    for start in range(0, runs, chunk):
        squares = generator.standard_t(3, (min(chunk, runs - start), observations)) ** 2
        shares.append(squares.max(axis=1) / squares.sum(axis=1))
    return np.concatenate(shares)


def calibrate_dominance(runs: int = 100_000, seed: int = DOMINANCE_SEED,
                        grid: Sequence[int] = tuple(t for t, _ in DOMINANCE_NULL)
                        ) -> tuple[tuple[int, float], ...]:
    """``DOMINANCE_NULL`` from scratch: one generator across the grid, in
    order, each quantile to three places. About 20 seconds at the defaults."""
    generator = np.random.default_rng(seed)
    return tuple((int(t), round(float(np.quantile(dominance_null(t, runs, generator),
                                                  DOMINANCE_QUANTILE)), 3))
                 for t in grid)


# --------------------------------------------------------------------------
# Market-state gates
# --------------------------------------------------------------------------

@dataclass(frozen=True, eq=False)
class CalmGate:
    """Section 7's calm-regime gate on one series (module docstring)."""

    probability: float             # α_T(calm), today
    history: np.ndarray            # α_t(calm) for every day: filtered, never smoothed
    odds: dict[int, float]         # horizon in days → P(s_{T+h} = calm)
    fit: HMMFit


def calm_regime(log_returns, regimes: int, horizons: Sequence[int] = (5, 20)) -> CalmGate:
    """Fit K regimes to the series and read the calm one's filtered
    probability, its history, and its odds ``horizons`` days ahead."""
    if regimes < 2:
        raise ValueError("a calm regime needs at least two regimes")
    ahead = tuple(int(h) for h in horizons)
    if not ahead or min(ahead) < 1:
        raise ValueError("horizons must be one day or more")
    r = np.asarray(log_returns, dtype=float)
    fit = fit_hmm(r, regimes)
    filtered = hmm_filter(fit.model, r).filtered
    calm = int(np.argmin(fit.model.sigmas))
    forecast = regime_forecast(fit.model, filtered[-1], max(ahead))
    history = filtered[:, calm].copy()
    return CalmGate(float(history[-1]), history,
                    {h: float(forecast.probabilities[h - 1, calm]) for h in ahead}, fit)


def volatility_forecast(log_returns, horizon: int) -> float:
    """Section 7's volatility gate: GARCH(1,1)'s expected average variance
    over the next ``horizon`` days, annualized as √(252 σ̂²_h)."""
    if horizon < 1:
        raise ValueError("the horizon must be one day or more")
    r = np.asarray(log_returns, dtype=float)
    fit = fit_garch11(r)
    following = next_variance(fit.parameters, float(r[-1]), float(fit.conditional_variances[-1]))
    average = average_variance_forecast(fit.parameters, following, np.array([float(horizon)]))
    return math.sqrt(TRADING_DAYS * float(average[0]))


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

def implied_growth_check(rule: Rule, roots: Sequence[float], trailing_cagr: float) -> Check:
    """Section 8: the growth today's price implies against c × the trailing
    revenue CAGR, with c the rule's upper limit. Several roots are reported
    as ambiguous, never chosen between (module docstring)."""
    if rule.limit.upper is None or rule.limit.lower is not None:
        raise ValueError(f"{rule.key}: the implied-growth rule is an upper multiple, c")
    if not math.isfinite(trailing_cagr):
        raise ValueError("the trailing revenue CAGR must be finite")
    fits = tuple(float(g) for g in roots)
    if not fits:
        return Check(rule, None, Outcome.MISSING, reason="no growth rate fits the price")
    if len(fits) > 1:
        return Check(rule, None, Outcome.AMBIGUOUS,
                     reason=f"{len(fits)} growth rates fit the price: "
                            + ", ".join(f"{g:+.1%}" for g in fits))
    implied = Measure.exact(fits[0])
    if trailing_cagr <= 0:
        return Check(rule, implied, Outcome.MISSING,
                     reason="the trailing revenue CAGR isn't positive, so a multiple of it "
                            "isn't a limit")
    return _compare(rule, Limit(upper=rule.limit.upper * trailing_cagr), implied)
