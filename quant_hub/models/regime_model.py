r"""The regime model's three estimates: σ̂, μ̂ and the tail (REGIME_MODEL Q2).

The derivation below was reviewed and approved before the code
(2026-11-05), as W2's, W3's and B1's were. The points the owner reviewed
are marked **Review:**. Each was kept as proposed.

The "regime" is not a label. It's the distribution of the market's excess
return over the next horizon, read at the close of each month from Q1's
inputs (``models/regime_inputs.py``), each as it was known then. Three
parts, from best to least well estimated:

- **σ̂, the volatility ahead,** from GARCH. This is what the data knows
  best.
- **μ̂, the excess return ahead,** from a combination of one-input
  forecasts (Rapach, Strauss & Zhou 2010), restricted as Campbell &
  Thompson (2008) do.
- **P(D ≥ d), the chance of a large fall** within the horizon, from
  logistic regressions, combined the same way.

**The horizons** (decision 3, settled 2026-11-05): 21 trading days for σ̂,
and 12 months for μ̂ and the tail.

0. Data, origins and what's known when
--------------------------------------
- **The market:** French's daily total return R_s = Mkt-RF_s + RF_s, the
  bill RF_s, and the total-return index L_s = Π (1 + R).
- **Origins:** the last trading day t of each month, from July 1926. A
  month of origins, not a day of them, because the 12-month targets would
  otherwise overlap 251 days in 252, and valuation moves monthly.
- **The inputs at an origin:** each of Q1's inputs as known at t's close
  (``RegimeInput.on_days``), on the raw scale. x_{j,t} is input j at
  origin t.
- **The targets,** over the next h = 12 months, from t's close to the
  close of the last trading day 12 months later:
  - the log excess return y_t = Σ_{s ∈ (t, t+h]} [ln(1 + R_s) − ln(1 + RF_s)]
  - the deepest fall D_t = max_{t ≤ u ≤ v ≤ t+h} (1 − L_v / L_u). It
    includes a fall from t's own level.
- **Known only at t + h.** So at origin t, a model is fitted on the
  origins s with s + h ≤ t: the last one is 12 months back. Nothing else
  is used. This is the rule every test checks.
- **Overlap:** consecutive targets share 11 of their 12 months. Errors
  below use Newey–West with L = 2(h − 1) = 22 monthly lags, twice the
  overlap, as D76 measured for its windows. Coverage is measured, not
  assumed (tests).

**Review:** the inputs enter raw, as in Goyal & Welch's and RSZ's
regressions. An expanding z rescales an input over time, so its slope
would change meaning as the scale moves. A percentile flattens the tails
where the signal may be. Q3's ledger can try either as a variant.

1. σ̂: GARCH's average volatility ahead
---------------------------------------
Q1's walk-forward GARCH is already σ̂. On each day it holds the year's
parameters (ω, α, β, μ), with persistence φ and long-run variance σ̄², and
h_{t+1}, the variance after day t's return. For n trading days ahead:

    σ̂²_{t,n} = 252 · [ σ̄² + (h_{t+1} − σ̄²)(1 − φⁿ) / (n(1 − φ)) ]

- n = 21 is decision 3's horizon. n = 252 gives the 12-month variance
  that μ̂ is set against in Q4.
- Q1's ``WalkForward`` now keeps h_{t+1} and each year's parameters, and
  ``regime_inputs.garch_vol_ahead(walk, n)`` reads any n. Its 21-day value
  is Q1's input exactly.
- **No error is shown:** GARCH's parameter uncertainty isn't derived here.
  The card says so.

2. μ̂: the combination forecast
-------------------------------
**One input's forecast.** At origin t, input j's estimation sample S_j(t)
is the n_j origins s with s + h ≤ t at which x_j and y are both known. On
it, OLS gives the slope

    θ̂_j = Σ (x_{j,s} − x̄_j)(y_s − ȳ_j) / Σ (x_{j,s} − x̄_j)²,

with bars as means over S_j(t). The forecast is the plan's form:

    μ̂_{j,t} = μ̄_t + θ̃_j (x_{j,t} − x̄_j)

- **μ̄_t, the benchmark,** is the mean of y over all n₀ origins known by t
  (S₀(t)): the forecast when the input sits at its average.
- **When S_j = S₀,** this is exactly input j's OLS forecast. When input j
  starts later (GARCH from 1937), only the intercept uses the longer
  history, which estimates the unconditional mean better.

**Restriction 1: the sign** (Campbell & Thompson). θ̃_j = θ̂_j when its
sign matches the one theory expects for input j, and 0 otherwise, so that
input falls back to the benchmark. An input with no expected sign is left
free.

**Review: the expected signs.**

=====================  ==========  ===========  =================================
input                  μ̂ sign      tail sign     why
=====================  ==========  ===========  =================================
valuation.ecy          +           −            cheap pays; cheap falls less
valuation.cape         −           +            dear pays less
trend.excess_12m       +           −            time-series momentum (MOP 2012)
trend.average_200d     +           −            the same, faster
volatility.realized    none        +            risk-return says +, data say −
volatility.garch       none        +            likewise; high vol clusters
turbulence.calm        none        −            a calm state falls less
correlation.stock_bond none        none         no theory either way
=====================  ==========  ===========  =================================

**The combination:** with J_t the inputs that have a forecast at t (at
least N_min = 120 origins, ten years),

    μ̂_t = (1/J_t) Σ_j μ̂_{j,t} = μ̄_t + (1/J_t) Σ_j θ̃_j (x_{j,t} − x̄_j)

Equal weights, as RSZ's headline combination. They're the robust choice:
estimated weights refit the noise the combination exists to average away.

**Restriction 2: the floor.** μ̂_t is floored at 0 (Campbell & Thompson).
It applies to the combination, not to each input, so that one input's
negative forecast can still offset another's positive one.

**Review:** the floor is in log terms. A log excess return of 0 is an
arithmetic premium of about ½σ², roughly 1% a year. Q4 converts with
μ_arith ≈ μ̂ + ½σ̂²_{t,252} before β^g = f μ / σ². Flooring the arithmetic
premium at 0 instead would mean μ̂_t ≥ −½σ̂²_{t,252}.

**The error of μ̂_t** (for the card), by the delta method on influence
functions:
- **What it covers:** μ̂_t is linear in μ̄_t, each θ̂_j and each x̄_j, and
  all are means over the same origins. Its estimation error is therefore
  a sum over origins:

      μ̂_t − E[μ̂_t] ≈ Σ_s u_s,
      u_s = (y_s − μ̄_t)/n₀
            + (1/J_t) Σ_j [ (x_{j,t} − x̄_j) ψ_{j,s} − θ̃_j (x_{j,s} − x̄_j)/n_j ]·1{s ∈ S_j}

  with the slope's influence ψ_{j,s} = (x_{j,s} − x̄_j) e_{j,s} / Σ (x_j − x̄_j)²,
  and e the OLS residual. The last term is x̄_j's own error. A
  restricted input (θ̃_j = 0) adds nothing: its slope isn't used.
- **Its variance:** Var(μ̂_t) = (n₀/(n₀ − k)) Σ_{|l| ≤ L} (1 − |l|/(L+1)) Σ_s u_s u_{s−l}.
  That's ``regression.ols``'s Newey–West kernel and small-sample factor.
  k is 1 plus the number of slopes used: restricted inputs estimate
  none.
- **The check:** with one input on the benchmark's own sample, this is
  exactly ``ols``'s Newey–West variance of the forecast, c′Vc with
  c = (1, x_{j,t}). With no inputs, it's that of the mean. Both are
  tests.
- **The floor isn't in it:** the error is of the unfloored forecast, and
  the card says when the floor binds.

**Out-of-sample R²** (Campbell & Thompson), Q3's measure, over the origins
where both forecasts exist:

    R²_OS = 1 − Σ_t (y_t − μ̂_t)² / Σ_t (y_t − μ̄_t)²

It's positive only when the forecast beats the historical mean, which most
predictors don't (Goyal & Welch 2008).

3. The tail: P(D ≥ d) within 12 months
--------------------------------------
- **Events:** E_{t,d} = 1{D_t ≥ d} for d ∈ {10%, 20%, 30%, 40%}, known at
  t + h. They are fitted only on the origins known by t, as for μ̂.
- **One input's model,** for each threshold: a logistic regression
  P(E = 1 | x) = Λ(a + b x), with Λ(z) = 1/(1 + e^{−z}), by maximum
  likelihood.
- **The fit** is iteratively reweighted least squares, in numpy:

      p_s = Λ(η_s),  w_s = p_s(1 − p_s),  z_s = η_s + (E_s − p_s)/w_s,
      (a, b) ← (X′WX)⁻¹ X′W z

  - It starts from a = logit(Ē), b = 0, and stops when the step is below
    10⁻¹⁰.
  - **When it can't fit:** with perfect separation the maximum is at
    infinity. Then, and with too few events, the input falls back to the
    base rate Ē.
  - **Too few events:** fewer than 3 *episodes*, which are runs of
    consecutive event origins. One crash makes about 12 event origins in
    a row, so episodes, not origins, count the evidence.
- **The sign restriction,** as for μ̂, using the tail column of the table:
  a b with the wrong sign is set to 0, and that input's forecast becomes
  the base rate Ē. Its intercept refitted alone would give its own
  sample's rate; the benchmark's is used, as μ̄ is for μ̂.
- **The combination** is the plain average of the inputs' probabilities
  (a linear pool), with the base rate Ē_t as the benchmark:

      p̂_{t,d} = (1/J_t) Σ_j Λ(â_j + b̃_j x_{j,t})

  Averaging probabilities keeps the pool calibrated when its members
  are. Averaging log-odds would be sharper, and wrong more confidently.
- **Monotone in d.** Separate fits can cross: a 30% fall can come out
  likelier than a 20% one. The four probabilities are projected onto the
  non-increasing sequences, by pool-adjacent-violators (the least-squares
  projection). It changes nothing when they're already in order.
- **The interval:** the delta method again, on the logistic's influence
  ψ_{j,s} = I_j⁻¹ (1, x_{j,s})′ (E_s − p_s), with I_j = Σ w (1, x)(1, x)′,
  and the pool's gradient p_{j,t}(1 − p_{j,t})(1, x_{j,t}) / J_t.
  - **An input at the base rate,** whether restricted, separated or
    short of episodes, contributes Ē's own influence, (E_s − Ē)/n₀.
  - **The variance** uses the same Newey–West sum as μ̂'s.
  - **The interval** is formed on the logit scale and mapped back, so it
    stays inside (0, 1).
- **The measures** (Q3's): the Brier score Σ(p̂ − E)² / n against the base
  rate's, and a calibration table.

**What it can't settle:** since 1926 the market has fallen 20% within a
year in roughly a dozen episodes, and 40% in about five. Every tail
interval will be wide, and the card shows it.

4. What Q2 doesn't do
---------------------
- **The rules** (β^g, β^d, β*, cash, the hedge mix): Q4.
- **The readable label** ("calm · dear · up") and the card: Q5.
- **Which inputs stay:** Q3's evidence decides. Q2 takes any input set.

API
---
- ``origins(total, bills) -> Origins``: the month-end origins with y_t,
  D_t and when each becomes known (section 0).
- ``inputs_at(origins, inputs)``: Q1's inputs, raw, at each origin.
- ``regime_inputs.garch_vol_ahead(walk, days)``: σ̂ (section 1).
- ``mean_forecast(origins, X, signs) -> MeanForecast``: per origin, μ̄_t,
  μ̂_t (floored and not), each input's θ̂ and whether its sign held, and
  ``error(t)`` (section 2).
- ``r2_out_of_sample(y, forecast, benchmark)``.
- ``fit_logistic(E, x) -> Logistic``: IRLS. It raises ``Separation`` when
  the maximum is at infinity.
- ``monotone_decreasing(p)``: pool-adjacent-violators.
- ``tail_forecast(origins, X, signs, thresholds) -> TailForecast``: per
  origin, p̂_{t,d} pooled and made monotone, the base rate, and
  ``interval(t, d)`` (section 3).
- ``brier(p, E)``.

Tests, against planted truths and closed forms
----------------------------------------------
- **Planted truths:**
  - a predictor planted with slope θ is recovered: θ̂ within its error,
    and R²_OS > 0
  - a useless one gets R²_OS ≤ 0 on average over seeds
- **No look-ahead:**
  - each origin's forecast uses only origins at least h back
  - truncating the data changes no earlier forecast
- **The restrictions hold:**
  - a wrong-sign slope gives the benchmark
  - a negative combination is floored
  - an input with no expected sign is left free
- **The errors:** μ̂'s equals ``ols``'s c′Vc with one input on the same
  sample, and the mean's error with none. Coverage over seeds is
  measured and reported, like D76's.

  **Measured coverage of the ±1.96 SE intervals** (300 seeds each). On
  overlapping targets, Newey–West with L = 22 runs a little short, as
  D76's did:

  =====================================  =============  ========
  interval                               known targets  coverage
  =====================================  =============  ========
  μ̄, 12-month sums sampled monthly       29 years       89%
                                         50 years       91%
                                         90 years       92%
  the tail, 12-month events, overlapping 50 years       91%
                                         90 years       92%
  the tail, one-month events, no overlap 400 months     94%
  =====================================  =============  ========
- **IRLS:**
  - with one binary regressor, the MLE is closed form:
    a = logit(p₀), b = logit(p₁) − logit(p₀)
  - separation is detected
- **The tail:**
  - the probabilities are monotone in d
  - PAVA leaves an ordered sequence alone
- **Calibration:** on events simulated with a known logistic rate, the
  pooled forecast's binned frequencies match within binomial error.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quant_hub.models.regime_inputs import RegimeInput

HORIZON_MONTHS = 12
"""μ̂'s and the tail's horizon (decision 3)."""
SIGMA_DAYS = 21
"""σ̂'s horizon in trading days (decision 3)."""
MIN_ORIGINS = 120
"""Origins an input's sample needs before it forecasts: ten years."""
MIN_EPISODES = 3
"""Runs of event origins a tail model needs before it's fitted."""
THRESHOLDS = (0.10, 0.20, 0.30, 0.40)
LAG_PER_OVERLAP = 2
"""Newey–West lags per month of overlap (section 0)."""
Z_95 = 1.959963984540054

MEAN_SIGNS: Mapping[str, int] = {
    "valuation.ecy": 1, "valuation.cape": -1, "trend.excess_12m": 1, "trend.average_200d": 1}
"""The sign theory expects of each input's slope for μ̂ (section 2's table);
an input not named is left free."""
TAIL_SIGNS: Mapping[str, int] = {
    "valuation.ecy": -1, "valuation.cape": 1, "trend.excess_12m": -1, "trend.average_200d": -1,
    "volatility.realized_21d": 1, "volatility.garch": 1, "turbulence.calm": -1}
"""The same for the tail's slopes."""


# ----------------------------------------------------------------- origins

@dataclass(frozen=True)
class Origins:
    """Month-end origins and their targets over the next ``months`` (section 0).
    A target is NaN, and its ``known`` date NaT, until its window completes."""

    days: pd.DatetimeIndex
    y: pd.Series            # the log excess return over the next `months`
    drawdown: pd.Series     # D_t, the deepest fall within them
    known: pd.Series        # the day both become known
    months: int

    @property
    def lags(self) -> int:
        return LAG_PER_OVERLAP * max(self.months - 1, 0)


def origins(total: pd.Series, bills: pd.Series, months: int = HORIZON_MONTHS) -> Origins:
    """The last trading day of each month, with the log excess return and the
    deepest fall of French's market over the next ``months`` (section 0)."""
    if months < 1:
        raise ValueError("the horizon must be a month or more")
    total = total.dropna().astype(float)
    bills = bills.reindex(total.index).astype(float)
    if bills.isna().any():
        raise ValueError("the bill rate is missing on days the market has a return")
    days = total.index
    key = days.year * 12 + days.month
    ends = days[np.r_[key[1:] != key[:-1], True]]
    final_complete = (days[-1] + pd.offsets.BDay(1)).month != days[-1].month
    excess = np.concatenate([[0.0], np.cumsum(np.log1p(total) - np.log1p(bills))])
    level = np.concatenate([[1.0], np.cumprod(1 + total.to_numpy())])
    at = np.searchsorted(days, ends) + 1            # positions in the arrays above
    n = len(ends)
    y, drawdown = np.full(n, np.nan), np.full(n, np.nan)
    known = pd.Series(pd.NaT, index=ends, dtype="datetime64[ns]")
    for i in range(n - months):
        j = i + months
        if j == n - 1 and not final_complete:
            continue
        y[i] = excess[at[j]] - excess[at[i]]
        path = level[at[i]:at[j] + 1]
        drawdown[i] = float(np.max(1 - path / np.maximum.accumulate(path)))
        known.iloc[i] = ends[j]
    return Origins(ends, pd.Series(y, index=ends), pd.Series(drawdown, index=ends), known,
                   months)


def inputs_at(origins: Origins, inputs: Sequence[RegimeInput]) -> pd.DataFrame:
    """Each of Q1's inputs, raw, as known at each origin's close."""
    return pd.DataFrame({i.name: i.on_days(origins.days)["raw"] for i in inputs},
                        index=origins.days)


def _known_by(origins: Origins, i: int) -> np.ndarray:
    known = origins.known.to_numpy()
    return ~np.isnat(known) & (known <= origins.days[i].to_datetime64())


def _long_run(u: np.ndarray, lags: int) -> float:
    """Σ_{|l| ≤ L} (1 − |l|/(L+1)) Σ_s u_s u_{s−l}: ``ols``'s Newey–West sum."""
    total = float(u @ u)
    for lag in range(1, min(lags, len(u) - 1) + 1):
        total += 2 * (1 - lag / (lags + 1)) * float(u[lag:] @ u[:-lag])
    return total


# -------------------------------------------------------------------- mean

@dataclass(frozen=True)
class _Slope:
    name: str
    sample: np.ndarray       # the origins of its sample
    x_bar: float
    y_bar: float
    sxx: float
    slope: float             # θ̂, unrestricted
    used: bool               # its sign held (or none is expected)
    x_now: float


def _mean_at(y: np.ndarray, X: pd.DataFrame, known: np.ndarray, i: int,
             signs: Mapping[str, int], min_origins: int):
    base = known & np.isfinite(y)
    n0 = int(base.sum())
    if n0 < min_origins:
        return None
    mu_bar = float(y[base].mean())
    slopes = []
    for name in X.columns:
        x = X[name].to_numpy(dtype=float)
        sample = base & np.isfinite(x)
        if int(sample.sum()) < min_origins or not math.isfinite(x[i]):
            continue
        xs, ys = x[sample], y[sample]
        x_bar, y_bar = float(xs.mean()), float(ys.mean())
        sxx = float(((xs - x_bar) ** 2).sum())
        if sxx <= 0:
            continue
        slope = float(((xs - x_bar) * (ys - y_bar)).sum() / sxx)
        sign = signs.get(name)
        slopes.append(_Slope(name, sample, x_bar, y_bar, sxx, slope,
                             sign is None or sign * slope >= 0, float(x[i])))
    return base, n0, mu_bar, slopes


@dataclass(frozen=True)
class MeanForecast:
    """μ̂ at each origin (section 2), with what each input contributed."""

    origins: Origins
    inputs: pd.DataFrame
    signs: Mapping[str, int]
    min_origins: int
    benchmark: pd.Series      # μ̄_t
    unfloored: pd.Series      # μ̂_t before the floor
    forecast: pd.Series       # floored at 0
    slopes: pd.DataFrame      # θ̂_{j,t}, before the sign restriction
    used: pd.DataFrame        # input j's slope entered μ̂_t

    def error(self, day) -> float:
        """The Newey–West standard error of the unfloored μ̂ at ``day``."""
        i = self.origins.days.get_loc(pd.Timestamp(day))
        y = self.origins.y.to_numpy(dtype=float)
        fitted = _mean_at(y, self.inputs, _known_by(self.origins, i), i, self.signs,
                          self.min_origins)
        if fitted is None:
            raise ValueError(f"no forecast on {pd.Timestamp(day):%Y-%m-%d}")
        base, n0, mu_bar, slopes = fitted
        u = np.zeros(len(y))
        u[base] = (y[base] - mu_bar) / n0
        k = 1
        for s in slopes:
            if not s.used:
                continue
            k += 1
            x = self.inputs[s.name].to_numpy(dtype=float)[s.sample]
            e = y[s.sample] - s.y_bar - s.slope * (x - s.x_bar)
            influence = (s.x_now - s.x_bar) * (x - s.x_bar) * e / s.sxx
            u[s.sample] += (influence - s.slope * (x - s.x_bar) / len(x)) / len(slopes)
        variance = n0 / (n0 - k) * _long_run(u[base], min(self.origins.lags, n0 - 1))
        return math.sqrt(variance)


def mean_forecast(origins: Origins, X: pd.DataFrame, signs: Mapping[str, int] = MEAN_SIGNS,
                  min_origins: int = MIN_ORIGINS) -> MeanForecast:
    """The combination forecast at every origin (section 2)."""
    if min_origins < 3:
        raise ValueError("a forecast needs at least three known origins")
    X = X.reindex(origins.days)
    y = origins.y.to_numpy(dtype=float)
    n = len(origins.days)
    benchmark, unfloored = np.full(n, np.nan), np.full(n, np.nan)
    slopes = pd.DataFrame(np.nan, index=origins.days, columns=X.columns)
    used = pd.DataFrame(False, index=origins.days, columns=X.columns)
    for i in range(n):
        fitted = _mean_at(y, X, _known_by(origins, i), i, signs, min_origins)
        if fitted is None:
            continue
        _base, _n0, mu_bar, members = fitted
        benchmark[i] = mu_bar
        tilts = []
        for s in members:
            slopes.iloc[i, X.columns.get_loc(s.name)] = s.slope
            used.iloc[i, X.columns.get_loc(s.name)] = s.used
            tilts.append(s.slope * (s.x_now - s.x_bar) if s.used else 0.0)
        unfloored[i] = mu_bar + (float(np.mean(tilts)) if tilts else 0.0)
    unfloored_s = pd.Series(unfloored, index=origins.days)
    return MeanForecast(origins, X, dict(signs), min_origins,
                        pd.Series(benchmark, index=origins.days), unfloored_s,
                        unfloored_s.clip(lower=0.0), slopes, used)


def r2_out_of_sample(y, forecast, benchmark) -> float:
    """Campbell & Thompson's R²_OS over the origins where all three exist."""
    y, f, b = (np.asarray(v, dtype=float) for v in (y, forecast, benchmark))
    both = np.isfinite(y) & np.isfinite(f) & np.isfinite(b)
    if both.sum() < 2:
        raise ValueError("R²_OS needs at least two origins with both forecasts")
    benchmark_error = float(((y[both] - b[both]) ** 2).sum())
    if benchmark_error == 0:
        raise ValueError("the benchmark is exact: R²_OS is undefined")
    return 1 - float(((y[both] - f[both]) ** 2).sum()) / benchmark_error


# -------------------------------------------------------------------- tail

class Separation(ValueError):
    """The logistic likelihood has its maximum at infinity."""


def _sigmoid(z):
    return 0.5 * (1 + np.tanh(0.5 * np.asarray(z, dtype=float)))


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


@dataclass(frozen=True)
class Logistic:
    intercept: float
    slope: float
    iterations: int

    def probability(self, x):
        return _sigmoid(self.intercept + self.slope * np.asarray(x, dtype=float))


def fit_logistic(events, x=None, max_iterations: int = 100,
                 tolerance: float = 1e-10) -> Logistic:
    """The logistic MLE by IRLS (section 3); intercept only without ``x``."""
    e = np.asarray(events, dtype=float)
    if not len(e) or not np.isin(e, (0.0, 1.0)).all():
        raise ValueError("events must be 0 or 1")
    rate = float(e.mean())
    if rate in (0.0, 1.0):
        raise Separation("every origin is an event, or none is")
    if x is None:
        return Logistic(_logit(rate), 0.0, 0)
    x = np.asarray(x, dtype=float)
    if len(x) != len(e) or not np.isfinite(x).all():
        raise ValueError("x must be finite, one per event")
    if x[e == 0].max() <= x[e == 1].min() or x[e == 1].max() <= x[e == 0].min():
        raise Separation("x separates the events from the rest")
    design = np.column_stack([np.ones_like(x), x])
    b = np.array([_logit(rate), 0.0])
    for iteration in range(1, max_iterations + 1):
        p = _sigmoid(design @ b)
        w = p * (1 - p)
        step = np.linalg.solve(design.T @ (design * w[:, None]), design.T @ (e - p))
        b = b + step
        if np.max(np.abs(step)) < tolerance:
            return Logistic(float(b[0]), float(b[1]), iteration)
    raise ValueError(f"IRLS didn't converge in {max_iterations} iterations")


def episodes(events) -> int:
    """Runs of consecutive events: a crash's origins count once."""
    e = np.asarray(events, dtype=float) == 1
    return int(e[0]) + int(np.sum(e[1:] & ~e[:-1])) if len(e) else 0


def monotone_decreasing(values) -> np.ndarray:
    """The least-squares non-increasing sequence: pool-adjacent-violators."""
    blocks: list[list[float]] = []
    for v in np.asarray(values, dtype=float):
        blocks.append([v, 1.0])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] < blocks[-1][0] / blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    return np.concatenate([np.full(int(c), t / c) for t, c in blocks])


@dataclass(frozen=True)
class _Member:
    name: str
    sample: np.ndarray | None     # None: at the base rate
    model: Logistic | None
    x_now: float
    probability: float


def _tail_at(events: np.ndarray, X: pd.DataFrame, known: np.ndarray, i: int,
             signs: Mapping[str, int], min_origins: int, min_episodes: int):
    base = known & np.isfinite(events)
    n0 = int(base.sum())
    if n0 < min_origins:
        return None
    rate = float(events[base].mean())
    members = []
    for name in X.columns:
        x = X[name].to_numpy(dtype=float)
        if not math.isfinite(x[i]):
            continue
        sample = base & np.isfinite(x)
        model = None
        if int(sample.sum()) >= min_origins and episodes(events[sample]) >= min_episodes:
            try:
                model = fit_logistic(events[sample], x[sample])
            except ValueError:
                model = None
            sign = signs.get(name)
            if model is not None and sign is not None and sign * model.slope < 0:
                model = None                              # the wrong sign: the base rate
        if model is None:
            members.append(_Member(name, None, None, float(x[i]), rate))
        else:
            members.append(_Member(name, sample, model, float(x[i]),
                                   float(model.probability(x[i]))))
    return base, n0, rate, members


@dataclass(frozen=True)
class TailForecast:
    """P(D ≥ d) at each origin (section 3)."""

    origins: Origins
    inputs: pd.DataFrame
    signs: Mapping[str, int]
    thresholds: tuple[float, ...]
    min_origins: int
    min_episodes: int
    base_rate: pd.DataFrame       # Ē_{t,d}
    pooled: pd.DataFrame          # the pool, before the projection
    probability: pd.DataFrame     # made non-increasing in d
    fitted: pd.DataFrame          # inputs with a fitted slope, per (origin, d)

    def interval(self, day, threshold: float, z: float = Z_95) -> tuple[float, float]:
        """The pool's interval on the logit scale, mapped back (section 3),
        around the monotone probability."""
        day = pd.Timestamp(day)
        i = self.origins.days.get_loc(day)
        events = (self.origins.drawdown.to_numpy(dtype=float) >= threshold).astype(float)
        events[~np.isfinite(self.origins.drawdown.to_numpy(dtype=float))] = np.nan
        found = _tail_at(events, self.inputs, _known_by(self.origins, i), i, self.signs,
                         self.min_origins, self.min_episodes)
        if found is None:
            raise ValueError(f"no tail forecast on {day:%Y-%m-%d}")
        base, n0, rate, members = found
        if not members:
            members = [_Member("base rate", None, None, math.nan, rate)]
        u = np.zeros(len(events))
        k = 1
        for m in members:
            if m.model is None:
                u[base] += (events[base] - rate) / n0 / len(members)
                continue
            k += 1
            x = self.inputs[m.name].to_numpy(dtype=float)[m.sample]
            design = np.column_stack([np.ones_like(x), x])
            p = m.model.probability(x)
            information = design.T @ (design * (p * (1 - p))[:, None])
            influence = np.linalg.solve(information, (design * (events[m.sample] - p)[:, None]).T)
            gradient = m.probability * (1 - m.probability) * np.array([1.0, m.x_now])
            u[m.sample] += gradient @ influence / len(members)
        se = math.sqrt(n0 / (n0 - k) * _long_run(u[base], min(self.origins.lags, n0 - 1)))
        centre = float(self.probability.loc[day, threshold])
        if not 0 < centre < 1:
            return centre, centre
        eta, spread = _logit(centre), z * se / (centre * (1 - centre))
        return float(_sigmoid(eta - spread)), float(_sigmoid(eta + spread))


def tail_forecast(origins: Origins, X: pd.DataFrame, signs: Mapping[str, int] = TAIL_SIGNS,
                  thresholds: Sequence[float] = THRESHOLDS, min_origins: int = MIN_ORIGINS,
                  min_episodes: int = MIN_EPISODES) -> TailForecast:
    """The pooled tail probabilities at every origin (section 3)."""
    thresholds = tuple(sorted(float(d) for d in thresholds))
    if not thresholds or thresholds[0] <= 0 or thresholds[-1] >= 1:
        raise ValueError("thresholds are falls between 0 and 1")
    X = X.reindex(origins.days)
    drawdown = origins.drawdown.to_numpy(dtype=float)
    n = len(origins.days)
    base_rate = pd.DataFrame(np.nan, index=origins.days, columns=thresholds)
    pooled = base_rate.copy()
    fitted = pd.DataFrame(0, index=origins.days, columns=thresholds)
    for d in thresholds:
        events = np.where(np.isfinite(drawdown), (drawdown >= d).astype(float), np.nan)
        for i in range(n):
            found = _tail_at(events, X, _known_by(origins, i), i, signs, min_origins,
                             min_episodes)
            if found is None:
                continue
            _base, _n0, rate, members = found
            base_rate.iloc[i, thresholds.index(d)] = rate
            pooled.iloc[i, thresholds.index(d)] = (float(np.mean([m.probability
                                                                  for m in members]))
                                                   if members else rate)
            fitted.iloc[i, thresholds.index(d)] = sum(m.model is not None for m in members)
    probability = pooled.copy()
    for i in np.flatnonzero(pooled.notna().all(axis=1).to_numpy()):
        probability.iloc[i] = monotone_decreasing(pooled.iloc[i].to_numpy())
    return TailForecast(origins, X, dict(signs), thresholds, min_origins, min_episodes,
                        base_rate, pooled, probability, fitted)


def brier(probability, events) -> float:
    """The mean squared error of probability forecasts, over finite pairs."""
    p, e = np.asarray(probability, dtype=float), np.asarray(events, dtype=float)
    both = np.isfinite(p) & np.isfinite(e)
    if not both.any():
        raise ValueError("no forecast has its outcome yet")
    return float(((p[both] - e[both]) ** 2).mean())
