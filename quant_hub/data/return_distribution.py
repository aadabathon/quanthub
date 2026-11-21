r"""Return distributions: a historical return histogram, HRH (H1).

**Status: approved 2026-11-10 and built (milestone H1, D84).** The
choices made in review are marked **Review:**. Each was kept as
proposed. What building added is in section 12, "As built".

What it answers, for one security over a window:

- What do its returns look like, and how far are they from a normal?
- Where does the latest move sit among them?

The numbers match Godel Terminal's HRH panel on the same bars (these
formulas reproduce theirs). Two things differ on purpose:
the tests on multi-period horizons (section 5) and the Q-Q band (section
8).

Inputs and units
----------------
- **Daily closes** :math:`S_t` with their dates: a ``PriceHistory``'s
  closes on either basis (D38). Returns are decimal fractions (0.01 = 1%).
- **Frequency** *f*: day, week or month. A week is a calendar week ending
  Friday, and a month a calendar month. Each period is its last trading
  day: that day's close, and that day's date, so a holiday Friday's week
  is dated Thursday. Periods a year: :math:`p_f` = 252, 52 or 12. 252 is
  ``data.stats.TRADING_DAYS_PER_YEAR``.
- **Horizon** :math:`k \ge 1` periods, and **kind**: simple or log.
  **Review (decision 5):** simple by default. It's Godel's default and
  what a trader reads; log stays one click away.
- **Window:** 6 months, YTD, 1, 2 or 5 calendar years, or all.
- **Split-sized gaps:** the dates of D57's named gaps, optional.

1. Returns
----------
With :math:`S_0, \dots, S_N` the period closes and :math:`d_j` their dates:

.. math::  R_j = S_j / S_{j-k} - 1, \qquad x_j = \ln(S_j / S_{j-k}),
           \qquad j = k, \dots, N

These overlap when :math:`k > 1`: each shares :math:`k-1` periods with the
next.

- **The latest move** is :math:`j = N`, the return ending at the last
  stored bar. It's ranked against the window and never in it.
  - **Partial periods:** for weeks and months it's the period to date. It
    is flagged partial when :math:`d_N` isn't the period's last weekday,
    so a holiday Friday also reads "to date". The label is harmless.
  - **Provisional bars:** whether today's bar is still provisional (D18)
    is the page's to say. It knows the clock; this module doesn't.
- **The window** holds the returns whose end date falls in
  :math:`[d_N - L, d_N)`, with :math:`L` the window's calendar length. YTD
  holds those ending on or after 1 January of :math:`d_N`'s year, and All
  holds every return before the latest. A full year of daily bars gives
  about 251 returns, as Godel's 1Y does. For :math:`k > 1` a return's
  start may fall before the window; what counts is where it ends.
- **Split-sized gaps (D57):** a return *spans* a gap when its interval
  :math:`(d_{j-k}, d_j]` contains the gap's date. Spanning returns are
  named in the result.
  **Review (decision 4):** they stay in the sample by default, as D57
  does everywhere: a repair could erase a real crash. ``exclude_gaps``
  drops them, and they're still named.

Below, :math:`x_1, \dots, x_n` are the window's returns of the chosen kind,
and :math:`x_\text{now}` is the latest.

2. The description
------------------
- **n, the mean** :math:`\bar x`, **the median, and**
  :math:`s` (ddof = 1).
- **Annualized vol** :math:`s\sqrt{p_f/k}`. A k-period return is a sum of
  k one-period returns, so its variance is k times one period's under
  i.i.d. That makes the scaling exact for log returns, and an
  approximation for simple ones, which the label says.
- **Skewness and excess kurtosis:** the moment estimators :math:`g_1` and
  :math:`g_2` of ``data.stats.skewness`` and ``excess_kurtosis``, the
  ones Jarque–Bera is built on.
- **The best and worst return** with their end dates.
- **Up and down counts:** :math:`x > 0` and :math:`x < 0`. Unchanged
  returns are counted apart, and shown only when there are any.

3. The normal reference
-----------------------
The reference is :math:`N(\bar x, s^2)`, with :math:`\Phi`, :math:`\varphi`
and :math:`\Phi^{-1}` from ``statistics.NormalDist``.

- **Expected count in a bin** :math:`[a, b)`:
  :math:`n[\Phi((b-\bar x)/s) - \Phi((a-\bar x)/s)]`. The curve drawn is
  its smooth form for bin width :math:`h`:
  :math:`n h \varphi((x - \bar x)/s)/s`.
- **Normal quantile at q:** :math:`\bar x + s\,\Phi^{-1}(q)`, beside the
  empirical quantile. That's numpy's default (Hyndman & Fan's type 7,
  linear between order statistics), at q = 1, 5, 95 and 99%.
- **Share beyond** :math:`\pm m` **sd:** the observed share is
  :math:`\#\{|x_i - \bar x| > m s\}/n`, and the normal's is
  :math:`2(1-\Phi(m))`: 31.73%, 4.550%, 0.270% and 0.00633% for m = 1 to
  4. The ±4 row is added because on SPY the excess sits beyond the 1st
  percentile.

4. Where the latest move sits
-----------------------------
.. math::  z = (x_\text{now} - \bar x) / s

- **Down moves**, :math:`x_\text{now} < \bar x`: the count
  :math:`c = \#\{x_i \le x_\text{now}\}`, and the normal expects
  :math:`n\Phi(z)`.
- **Otherwise:** :math:`c = \#\{x_i \ge x_\text{now}\}`, and the normal
  expects :math:`n(1 - \Phi(z))`.
- **Ties count as "at least this far."** The rank is :math:`c/n`. At
  :math:`c = 0` the move was beyond every return in the window.

For :math:`k > 1` the latest move shares days with the window's last
:math:`k-1` returns. The rank is still a fair description: each return
has the same marginal distribution, so the expected count is unchanged.
Only the tests need independence.

5. The normality tests, on a non-overlapping sample
---------------------------------------------------
**The test sample:** :math:`T = \{x_{N-k}, x_{N-2k}, \dots\}` within the
window, with :math:`n' = |T| \approx \lfloor n/k \rfloor`. For
:math:`k = 1` it's the window itself. It's aligned so that its last return
ends where the latest move begins. With i.i.d. periods, no tested return
shares a period with another, nor with the latest move.

**Why.** Overlapping returns have correlation :math:`(k-h)/k` at lag
:math:`h < k`, and both tests assume independence. I simulated 4,000
years of i.i.d. normal daily returns, so a correct 5% test should reject
5% of them. On the overlapping 251, A-D rejected 22.7% at :math:`k = 5`
and 74.7% at :math:`k = 21`; on the non-overlapping returns, 4.9% and
5.0%. Godel tests the overlapping sample.
**Review (decision 3):** the non-overlapping sample, as here, or Godel's
way with a warning.

**Jarque–Bera:**

.. math::  JB = \frac{n'}{6}\left(g_1^2 + \frac{g_2^2}{4}\right),
           \qquad p = e^{-JB/2}

The p-value is exact for the :math:`\chi^2_2` limit, whose survival
function is :math:`e^{-x/2}`. The limit is slow. On normal samples
(3,000 each, at 5%) JB rejects 0.2% at n' = 8, 1.4% at 12, 4.0% at 51 and
5.1% at 251. That's too timid below about 50, so it's listed, not the
headline.

**Anderson–Darling,** with the mean and variance estimated (Stephens'
case 3). With the sorted test sample
:math:`z_{(i)} = (x_{(i)} - \bar x_T)/s_T`:

.. math::  A^2 = -n' - \frac{1}{n'}\sum_{i=1}^{n'} (2i-1)
               \left[\ln\Phi(z_{(i)}) + \ln\bigl(1 - \Phi(z_{(n'+1-i)})\bigr)\right]

.. math::  A^{*2} = A^2\left(1 + \frac{0.75}{n'} + \frac{2.25}{n'^2}\right)

The second line is Stephens' small-sample modification, which makes one
p-value table serve every n'. The p-value is D'Agostino & Stephens'
(1986) fit, the one R's ``nortest::ad.test`` uses:

.. math::

   p = \begin{cases}
     1 - \exp(-13.436 + 101.14 A^{*2} - 223.73 A^{*4}) & A^{*2} < 0.2 \\
     1 - \exp(-8.318 + 42.796 A^{*2} - 59.938 A^{*4}) & 0.2 \le A^{*2} < 0.34 \\
     \exp(0.9177 - 4.279 A^{*2} - 1.38 A^{*4}) & 0.34 \le A^{*2} < 0.6 \\
     \exp(1.2937 - 5.709 A^{*2} + 0.0186 A^{*4}) & 0.6 \le A^{*2} < 10
   \end{cases}

with :math:`A^{*4} = (A^{*2})^2`.

- **The floor.** At :math:`A^{*2} \ge 10`, p is reported as a bound,
  below :math:`3.8 \times 10^{-24}` (the last line's value at 10). That
  quadratic turns up at 153, past the table it was fitted to, and
  ``nortest`` freezes it the same way.
- **Both logs go through erfc:** :math:`\Phi(z) = \tfrac12
  \mathrm{erfc}(-z/\sqrt2)` and :math:`1 - \Phi(z) = \tfrac12
  \mathrm{erfc}(z/\sqrt2)`. The naive :math:`1 + \mathrm{erf}(z/\sqrt2)`
  rounds to zero below :math:`z \approx -8.26`, a crash day in a calm
  window, and its log is :math:`-\infty`. With erfc, a planted −40 sd
  day gives a finite :math:`A^{*2}` (43.1).

**Calibration and power** (simulated, 3,000 samples each, 5% level):

======  ===========  ==========  ===========  ==========
n'      A-D, normal  JB, normal  A-D, t(3)    JB, t(3)
======  ===========  ==========  ===========  ==========
8       5.2%         0.2%        15.5%        4.1%
12      5.1%         1.4%        21.2%        14.6%
51      4.8%         4.0%        62.7%        67.5%
251     5.2%         5.1%        99.4%        99.6%
======  ===========  ==========  ===========  ==========

So A-D holds its level down to n' = 8 and is the headline verdict.
:math:`\alpha` is 10, 5, 2 or 1%, 5% by default. Godel's "CL" is
:math:`1 - \alpha`. Below ``MIN_TEST_N`` = 8 no test runs, and the result
says why: the p-value fit isn't calibrated there, and ``nortest`` refuses
n < 8 too. Monthly returns over 6 months, for example, give no test.

**Review (decision 6):** Godel's :math:`\chi^2` test is left out. Its
verdict depends on the bins, and A-D asks the same question without them.

The tests assume independence, and even at :math:`k = 1` daily returns
cluster in volatility. So a rejection says the window's *pooled* returns
aren't normal. A calm year mixed with a wild one is fat-tailed even if
each day is normal given its volatility. That is the GARCH Lab's subject,
and a possible H3.

6. Bins
-------
- **Freedman–Diaconis:** :math:`h = 2\,\mathrm{IQR}\,n^{-1/3}`. The
  :math:`n^{-1/3}` rate is the one that minimizes a histogram's
  integrated squared error, and :math:`2\,\mathrm{IQR}` is Freedman &
  Diaconis' (1981) robust stand-in for the unknown constant. The bin count
  :math:`\lceil (\max - \min)/h \rceil` is kept within 10 to 200, for
  legibility. The IQR ignores the tails, so one crash day doesn't widen
  every bin.
  - This is numpy's ``"fd"`` rule, and the counts were checked identical
    at n = 12, 51, 251 and 1,314.
  - An IQR of 0 (a mostly unchanged series) gives the floor of 10.
  - A fixed count (20, 30, 50, 75 or 100) overrides it.
- **Edges** are equally spaced over the window's own range. The page
  widens the drawn range to show the latest move when it lies outside.
- **Counts, not densities,** with section 3's expected count per bin.

7. The Q-Q view
---------------
Plotting positions are :math:`p_i = (i - \tfrac12)/n` and
:math:`z_i = \Phi^{-1}(p_i)`. The points are
:math:`(\bar x + s z_i,\ x_{(i)})`, so normal data lie on :math:`y = x`.

8. The Q-Q band, with the mean and sd fitted
--------------------------------------------
Let :math:`Q(p) = \mu + \sigma z_p` be the true quantile. Two estimators
of it are on the plot:

- **The order statistic** :math:`x_{(i)}`, with asymptotic variance
  :math:`\sigma^2 p(1-p) / (n\,\varphi(z_p)^2)`.
- **The fitted line's** :math:`\bar x + s z_p`. Under normality
  :math:`\bar x` and :math:`s` are independent, with variances
  :math:`\sigma^2/n` and about :math:`\sigma^2/(2n)`, so its variance is
  :math:`\sigma^2(1 + z_p^2/2)/n`.

The second is asymptotically efficient: it's the maximum-likelihood
estimate of :math:`Q(p)`, by invariance, with :math:`s` and the MLE's
:math:`\hat\sigma` equal to first order. An efficient estimator is
asymptotically uncorrelated with its difference from any other
consistent, asymptotically normal one (Hausman 1978). So the variances
subtract:

.. math::  \mathrm{Var}\bigl(x_{(i)} - \bar x - s z_i\bigr) \approx
           \frac{\sigma^2}{n}\left[\frac{p_i(1-p_i)}{\varphi(z_i)^2}
           - 1 - \frac{z_i^2}{2}\right]

The bracket is positive, since an inefficient estimator's variance can't
be below the efficient one's. With :math:`z^\star = \Phi^{-1}(0.975)`,
the pointwise band is

.. math::  \bar x + s z_i \pm z^\star s
           \sqrt{\left[p_i(1-p_i)/\varphi(z_i)^2 - 1 - z_i^2/2\right]/n}

**Checked by simulation** on normal samples (3,000 at n = 51 and 251,
1,000 at 1,314):

- the share of points inside averages 95.5%, 95.2% and 95.5%
- the lowest single position is 94.7%, 94.3% and 93.6%, within sampling
  noise (±0.7 points at 1,000 runs, across 1,314 positions)
- the outer 1% of points is slightly conservative, at 96–98%

The band without the last two terms (``car::qqPlot``'s, which assumes μ
and σ known) is 1.66 times as wide at the median. It holds 99.8% of
points, which would make every sample look normal.

**Review:** for :math:`k > 1` the Q-Q view plots the non-overlapping
sample of section 5, because its band assumes independence. The
histogram keeps the overlapping returns.

It's a guide, not a test: about 5% of points fall outside on normal data.

9. Validation
-------------
Each of these raises ``ValueError``:

- closes that aren't finite and positive
- dates that aren't strictly increasing
- a horizon :math:`k` that isn't an integer of at least 1
- fewer than :math:`k + 1` periods, since there's no latest move then
- a window holding fewer than 2 returns, or with :math:`s = 0`
- :math:`\alpha` outside (0, 0.5)
- a fixed bin count outside 1–1,000
- an unknown frequency, kind or window

A short test sample isn't an error. It's a result saying why no test
ran.

10. The result
--------------
A frozen ``ReturnDistribution``, computed off the GUI thread and only
formatted by the page:

- the settings, n and the window's date range
- the description of section 2
- the latest move: its value, z, side, count, rank, normal expectation,
  and the partial flag
- the tails table to ±4 sd, and the quantile table
- the test sample's size n', and each test's statistic, p, whether p is
  a bound, and the verdict at :math:`\alpha`; or why the tests didn't
  run
- bin edges, counts and expected counts
- the Q-Q points and band (on the sample the view plots)
- the returns that span split-sized gaps, and whether they were excluded

It holds no widget types or colors.

11. Tests (H1)
--------------
Seeded, with binomial tolerances where a rate is checked:

1. **Godel's panel** from its inputs:
   - JB 11.90 with p 0.0026
   - A-D p 0.240 at :math:`A^{*2}` = 0.475, and 0.347 at 0.408
   - z −2.209, where the normal expects 3.41 days
   - the normal quantiles and both annualized vols
2. **A-D by hand** on [−2, −1, 0, 1, 3]: :math:`A^2` = 0.16611,
   :math:`A^{*2}` = 0.20597, p = 0.8708.
3. **Calibration:** 2,000 normal samples each at n' = 8, 51 and 251, and
   at k = 5 through the non-overlapping sample. A-D rejects within 3
   binomial standard errors of 5%.
4. **Power:** t(3) returns at n = 251 are rejected in at least 95% of
   500 seeds.
5. **The Q-Q band:** mean pointwise coverage within [94%, 96.5%] at
   n = 251 over 1,000 seeds.
6. **The floor and erfc:** :math:`A^{*2} \ge 10` gives the bound and
   flags it, and a −40 sd day gives a finite statistic.
7. **Sample sizes:** n and n' for k = 1, 5 and 21. The test sample is
   aligned to the latest move and never shares a period with it.
8. **Resampling:** weekly and monthly, including a holiday Friday and a
   partial last period.
9. **Windows:** :math:`[d_N - L, d_N)`, YTD and All, and the latest move
   is never in its own window.
10. **Ranks:** at ties, at c = 0, and on both sides.
11. **Bins:** they equal numpy's ``"fd"`` within the 10–200 clamp, the
    IQR = 0 floor works, and the expected counts sum to
    :math:`n[\Phi(\max') - \Phi(\min')]`.
12. **Split-sized gaps:** spanning returns are named, and
    ``exclude_gaps`` drops them and still names them.
13. **Validation:** every case in section 9.

12. As built
------------
- **Far tails in A-D.** Beyond :math:`z = 30`, :math:`\ln(1 - \Phi(z))`
  switches from erfc to Mills' ratio, :math:`-z^2/2 - \ln z - \tfrac12
  \ln 2\pi + \ln(1 - z^{-2} + 3z^{-4})`. That's because erfc itself
  underflows to zero near :math:`z = 37.5`. At 30 the two forms agree to
  better than :math:`10^{-6}` in the log. A standardized point can only
  get there with n above about 900, since
  :math:`|z| \le (n-1)/\sqrt n`.
- **The API:**
  - ``period_closes``, ``period_returns`` and ``analyze``
  - the pure pieces the tests check directly: ``jarque_bera``,
    ``jarque_bera_from_moments``, ``anderson_darling``,
    ``ad_p_value``, ``fd_bin_count`` and ``qq_points``
- **Enums and strings:** frequency, kind and window are enums, and
  ``analyze`` also accepts their string values.
- **Split-sized gaps:** ``gap_days`` takes the days of D57's gaps, the
  ``SplitGap.day`` of each in ``PriceHistory.split_gaps``. Whether the
  latest move spans one is on ``LatestMove``.

References
----------
Mandelbrot (1963) and Fama (1965) on fat tails; Anderson & Darling (1952,
1954); Stephens (1974); D'Agostino & Stephens (1986), *Goodness-of-Fit
Techniques*; Jarque & Bera (1980, 1987); Freedman & Diaconis (1981);
Hausman (1978); Hyndman & Fan (1996); Cont (2001) on stylized facts. Each
is verified before it joins the literature canon (D58).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.data.stats import TRADING_DAYS_PER_YEAR, excess_kurtosis, skewness


class Frequency(Enum):
    """How the daily closes are sampled: each period's last trading day."""

    DAY = "day"
    WEEK = "week"      # calendar week ending Friday
    MONTH = "month"    # calendar month


class ReturnKind(Enum):
    """Simple :math:`S_j/S_{j-k} - 1` or log :math:`\\ln(S_j/S_{j-k})`."""

    SIMPLE = "simple"
    LOG = "log"


class Window(Enum):
    """Which returns, by end date, form the sample (section 1)."""

    MONTHS_6 = "6M"
    YTD = "YTD"
    YEARS_1 = "1Y"
    YEARS_2 = "2Y"
    YEARS_5 = "5Y"
    ALL = "All"


#: Periods a year, for annualizing (section 2).
PERIODS_PER_YEAR: dict[Frequency, int] = {
    Frequency.DAY: TRADING_DAYS_PER_YEAR,
    Frequency.WEEK: 52,
    Frequency.MONTH: 12,
}

#: Calendar lengths of the fixed windows; YTD and All are handled apart.
_WINDOW_LENGTH: dict[Window, pd.DateOffset] = {
    Window.MONTHS_6: pd.DateOffset(months=6),
    Window.YEARS_1: pd.DateOffset(years=1),
    Window.YEARS_2: pd.DateOffset(years=2),
    Window.YEARS_5: pd.DateOffset(years=5),
}

#: Fewest non-overlapping returns the tests run on (section 5).
MIN_TEST_N = 8

#: Where D'Agostino & Stephens' last quadratic stops being used (section 5).
AD_FLOOR_STATISTIC = 10.0

#: The p-value bound reported at and above it: the last quadratic at 10.
AD_FLOOR_P = math.exp(1.2937 - 5.709 * AD_FLOOR_STATISTIC + 0.0186 * AD_FLOOR_STATISTIC**2)

#: Freedman–Diaconis' bin count is kept within this range (section 6).
BINS_MIN, BINS_MAX = 10, 200

#: Largest fixed bin count accepted (section 9).
FIXED_BINS_MAX = 1000

#: The tails table: shares beyond ±m sd (section 3).
TAIL_SDS: tuple[int, ...] = (1, 2, 3, 4)

#: The quantile table's levels (section 3).
QUANTILE_LEVELS: tuple[float, ...] = (0.01, 0.05, 0.95, 0.99)

DEFAULT_ALPHA = 0.05

AD_NAME = "Anderson–Darling"
JB_NAME = "Jarque–Bera"

_STANDARD_NORMAL = NormalDist()
_SQRT2 = math.sqrt(2.0)
_HALF_LOG_2PI = 0.5 * math.log(2.0 * math.pi)

#: z beyond which ln(1 − Φ(z)) uses Mills' ratio instead of erfc (section 12).
_MILLS_FROM = 30.0

#: The pointwise band's quantile, Φ⁻¹(0.975) (section 8).
Z_STAR = _STANDARD_NORMAL.inv_cdf(0.975)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalityTest:
    """One test's outcome. ``p_is_bound``: ``p_value`` is an upper bound
    (A-D at or above its floor, section 5)."""

    name: str
    statistic: float
    p_value: float
    p_is_bound: bool
    rejects: bool


@dataclass(frozen=True)
class LatestMove:
    """Where the latest move sits (section 4). ``count`` is c, the window's
    returns at least this far on the move's side; ``rank`` is c/n."""

    value: float
    start: pd.Timestamp
    end: pd.Timestamp
    z: float
    down: bool
    count: int
    rank: float
    normal_expected: float
    partial: bool
    spans_gap: bool


@dataclass(frozen=True)
class TailRow:
    """Returns beyond ±``sds`` sd of the mean, observed and normal."""

    sds: int
    observed: int
    observed_share: float
    normal_share: float
    normal_expected: float


@dataclass(frozen=True)
class QuantileRow:
    """The empirical quantile at ``level`` beside the normal's."""

    level: float
    observed: float
    normal: float


@dataclass(frozen=True, eq=False)
class QQ:
    """Q-Q points and the fitted-parameter 95% band (sections 7–8), in
    return units; all four arrays are aligned and sorted."""

    theoretical: np.ndarray
    observed: np.ndarray
    lower: np.ndarray
    upper: np.ndarray

    @property
    def n(self) -> int:
        return len(self.observed)


@dataclass(frozen=True, eq=False)
class ReturnDistribution:
    """Everything HRH shows for one security, one consistent state
    (section 10). Computed off the GUI thread; the page only formats it."""

    frequency: Frequency
    horizon: int
    kind: ReturnKind
    window: Window
    alpha: float
    returns: pd.Series            # the window's sample, indexed by end date
    mean: float
    median: float
    sd: float
    annualized_vol: float
    skewness: float
    excess_kurtosis: float
    best: tuple[pd.Timestamp, float]
    worst: tuple[pd.Timestamp, float]
    up: int
    down: int
    unchanged: int
    latest: LatestMove
    tails: tuple[TailRow, ...]
    quantiles: tuple[QuantileRow, ...]
    test_returns: pd.Series       # the non-overlapping test sample (section 5)
    tests: tuple[NormalityTest, ...]
    tests_skipped: str            # why no test ran; "" when they did
    bin_edges: np.ndarray
    bin_counts: np.ndarray
    bin_expected: np.ndarray
    qq: QQ
    gap_returns: tuple[pd.Timestamp, ...]
    gaps_excluded: bool

    @property
    def n(self) -> int:
        return len(self.returns)

    @property
    def test_n(self) -> int:
        return len(self.test_returns)

    @property
    def start(self) -> pd.Timestamp:
        return self.returns.index[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.returns.index[-1]

    @property
    def verdict(self) -> NormalityTest | None:
        """The headline test, Anderson–Darling; None when no test ran."""
        return self.tests[0] if self.tests else None


# --------------------------------------------------------------------------
# Returns (section 1)
# --------------------------------------------------------------------------

def period_closes(closes: pd.Series,
                  frequency: Frequency | str = Frequency.DAY) -> tuple[pd.Series, bool]:
    """Each period's last trading day, with its close and its own date.

    Returns the sampled closes and whether the last period is partial: its
    last bar isn't the period's last weekday (always False for days).
    """
    frequency = Frequency(frequency)
    closes = _validated_closes(closes)
    if frequency is Frequency.DAY:
        return closes, False
    periods = closes.index.to_period("W-FRI" if frequency is Frequency.WEEK else "M")
    last_of_period = np.append(np.asarray(periods[1:] != periods[:-1]), True)
    end = closes.index[-1].normalize()
    return closes[last_of_period], end != _last_weekday(end, frequency)


def period_returns(closes: pd.Series, horizon: int = 1,
                   kind: ReturnKind | str = ReturnKind.SIMPLE) -> pd.Series:
    """Overlapping k-period returns, indexed by end date (section 1)."""
    horizon = _validated_horizon(horizon)
    kind = ReturnKind(kind)
    values = closes.to_numpy(dtype=float)
    if len(values) < horizon + 1:
        raise ValueError(f"need at least {horizon + 1} periods for a {horizon}-period "
                         f"return, got {len(values)}")
    ratio = values[horizon:] / values[:-horizon]
    returns = ratio - 1.0 if kind is ReturnKind.SIMPLE else np.log(ratio)
    return pd.Series(returns, index=closes.index[horizon:])


def _last_weekday(day: pd.Timestamp, frequency: Frequency) -> pd.Timestamp:
    """The last weekday of ``day``'s week (its Friday) or month."""
    if frequency is Frequency.WEEK:
        return day + pd.Timedelta(days=(4 - day.weekday()) % 7)
    month_end = day + pd.offsets.MonthEnd(0)
    return month_end - pd.Timedelta(days=max(0, month_end.weekday() - 4))


def _validated_closes(closes: pd.Series) -> pd.Series:
    if not isinstance(closes, pd.Series):
        raise ValueError("closes must be a pandas Series")
    if not isinstance(closes.index, pd.DatetimeIndex):
        raise ValueError("closes must have a DatetimeIndex")
    if closes.index.has_duplicates or not closes.index.is_monotonic_increasing:
        raise ValueError("closes' dates must be strictly increasing")
    values = closes.to_numpy(dtype=float)
    if not np.all(np.isfinite(values)) or np.any(values <= 0):
        raise ValueError("closes must be finite and positive")
    return closes.astype(float)


def _validated_horizon(horizon: int) -> int:
    if isinstance(horizon, bool) or not isinstance(horizon, (int, np.integer)) or horizon < 1:
        raise ValueError(f"horizon must be an integer >= 1, got {horizon!r}")
    return int(horizon)


# --------------------------------------------------------------------------
# The normal (sections 3 and 5)
# --------------------------------------------------------------------------

def _cdf(z: float) -> float:
    """Φ(z) through erfc, accurate far into the lower tail."""
    return 0.5 * math.erfc(-z / _SQRT2)


def _sf(z: float) -> float:
    """1 − Φ(z) through erfc, accurate far into the upper tail."""
    return 0.5 * math.erfc(z / _SQRT2)


def _log_sf(z: float) -> float:
    """ln(1 − Φ(z)): erfc, then Mills' ratio beyond ``_MILLS_FROM``."""
    if z < _MILLS_FROM:
        return math.log(_sf(z))
    inv2 = 1.0 / (z * z)
    return -0.5 * z * z - math.log(z) - _HALF_LOG_2PI + math.log1p(-inv2 + 3.0 * inv2 * inv2)


# --------------------------------------------------------------------------
# Tests (section 5)
# --------------------------------------------------------------------------

def jarque_bera_from_moments(n: int, g1: float, g2: float) -> tuple[float, float]:
    """JB = (n/6)(g₁² + g₂²/4) and its χ²₂ p-value, e^(−JB/2)."""
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if not (math.isfinite(g1) and math.isfinite(g2)):
        raise ValueError("g1 and g2 must be finite")
    statistic = n / 6.0 * (g1 * g1 + g2 * g2 / 4.0)
    return statistic, math.exp(-statistic / 2.0)


def jarque_bera(x: Sequence[float] | np.ndarray) -> tuple[float, float]:
    """Jarque–Bera on a sample, with ``data.stats``' moment estimators."""
    values = _validated_sample(x)
    series = pd.Series(values)
    return jarque_bera_from_moments(len(values), skewness(series), excess_kurtosis(series))


def ad_p_value(a_star: float) -> tuple[float, bool]:
    """D'Agostino & Stephens' p-value for A*², and whether it's the floor's
    bound (at A*² ≥ ``AD_FLOOR_STATISTIC``)."""
    if not math.isfinite(a_star) or a_star < 0:
        raise ValueError(f"A*2 must be finite and >= 0, got {a_star}")
    a, a2 = a_star, a_star * a_star
    if a >= AD_FLOOR_STATISTIC:
        return AD_FLOOR_P, True
    if a >= 0.6:
        p = math.exp(1.2937 - 5.709 * a + 0.0186 * a2)
    elif a >= 0.34:
        p = math.exp(0.9177 - 4.279 * a - 1.38 * a2)
    elif a >= 0.2:
        p = 1.0 - math.exp(-8.318 + 42.796 * a - 59.938 * a2)
    else:
        p = 1.0 - math.exp(-13.436 + 101.14 * a - 223.73 * a2)
    return min(1.0, max(0.0, p)), False


def anderson_darling(x: Sequence[float] | np.ndarray) -> tuple[float, float, bool]:
    """Anderson–Darling with mean and variance estimated (Stephens' case 3).

    Returns ``(A*², p, p_is_bound)``. The sample-size policy (``MIN_TEST_N``)
    is ``analyze``'s; this needs only two distinct values.
    """
    values = np.sort(_validated_sample(x))
    n = len(values)
    z = (values - values.mean()) / values.std(ddof=1)
    log_cdf = np.array([_log_sf(-v) for v in z])          # ln Φ(z_(i))
    log_sf = np.array([_log_sf(v) for v in z[::-1]])      # ln(1 − Φ(z_(n+1−i)))
    weights = 2.0 * np.arange(1, n + 1) - 1.0
    a2 = -n - float(np.sum(weights * (log_cdf + log_sf))) / n
    a_star = max(0.0, a2) * (1.0 + 0.75 / n + 2.25 / n**2)
    p, bound = ad_p_value(a_star)
    return a_star, p, bound


def _validated_sample(x: Sequence[float] | np.ndarray) -> np.ndarray:
    values = np.asarray(x, dtype=float).ravel()
    if len(values) < 2:
        raise ValueError(f"need at least 2 values, got {len(values)}")
    if not np.all(np.isfinite(values)):
        raise ValueError("values must be finite")
    if not values.std(ddof=1) > 0:
        raise ValueError("values must not all be equal")
    return values


# --------------------------------------------------------------------------
# Bins and the Q-Q view (sections 6–8)
# --------------------------------------------------------------------------

def fd_bin_count(x: Sequence[float] | np.ndarray) -> int:
    """Freedman–Diaconis' bin count, kept within ``BINS_MIN``–``BINS_MAX``."""
    values = np.asarray(x, dtype=float)
    if len(values) < 1 or not np.all(np.isfinite(values)):
        raise ValueError("need at least one finite value")
    q75, q25 = np.percentile(values, [75, 25])
    span = float(values.max() - values.min())
    iqr = float(q75 - q25)
    if iqr <= 0 or span <= 0:
        return BINS_MIN
    width = 2.0 * iqr * len(values) ** (-1.0 / 3.0)
    return int(min(BINS_MAX, max(BINS_MIN, math.ceil(span / width))))


def qq_points(x: Sequence[float] | np.ndarray) -> QQ:
    """Q-Q points against N(x̄, s²) and the fitted-parameter band (section 8)."""
    values = np.sort(_validated_sample(x))
    n = len(values)
    mean, sd = float(values.mean()), float(values.std(ddof=1))
    p = (np.arange(1, n + 1) - 0.5) / n
    z = np.array([_STANDARD_NORMAL.inv_cdf(v) for v in p])
    density = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)
    variance = np.maximum(p * (1.0 - p) / density**2 - 1.0 - z * z / 2.0, 0.0)
    theoretical = mean + sd * z
    half = Z_STAR * sd * np.sqrt(variance / n)
    return QQ(theoretical=theoretical, observed=values,
              lower=theoretical - half, upper=theoretical + half)


_EMPTY_QQ = QQ(theoretical=np.empty(0), observed=np.empty(0),
               lower=np.empty(0), upper=np.empty(0))


# --------------------------------------------------------------------------
# The analysis
# --------------------------------------------------------------------------

def analyze(
    closes: pd.Series,
    *,
    frequency: Frequency | str = Frequency.DAY,
    horizon: int = 1,
    kind: ReturnKind | str = ReturnKind.SIMPLE,
    window: Window | str = Window.YEARS_1,
    alpha: float = DEFAULT_ALPHA,
    bins: int | None = None,
    gap_days: Sequence = (),
    exclude_gaps: bool = False,
) -> ReturnDistribution:
    """The return distribution of ``closes`` (daily, dated), sections 1–10."""
    frequency, kind, window = Frequency(frequency), ReturnKind(kind), Window(window)
    horizon = _validated_horizon(horizon)
    if not (isinstance(alpha, (int, float)) and 0.0 < alpha < 0.5):
        raise ValueError(f"alpha must be in (0, 0.5), got {alpha!r}")
    if bins is not None and (isinstance(bins, bool) or not isinstance(bins, (int, np.integer))
                             or not 1 <= bins <= FIXED_BINS_MAX):
        raise ValueError(f"bins must be None or an integer in 1..{FIXED_BINS_MAX}, got {bins!r}")

    sampled, partial = period_closes(closes, frequency)
    if len(sampled) < horizon + 1:
        raise ValueError(f"{len(sampled)} {frequency.value} closes can't make a latest "
                         f"{horizon}-period return")
    all_returns = period_returns(sampled, horizon, kind)
    starts = sampled.index[:-horizon]
    ends = all_returns.index

    spans = np.zeros(len(all_returns), dtype=bool)
    for day in sorted({pd.Timestamp(d).normalize() for d in gap_days}):
        spans |= (starts.normalize() < day) & (ends.normalize() >= day)

    latest_end, latest_value = ends[-1], float(all_returns.iloc[-1])
    earlier = ends[:-1]
    if window is Window.ALL:
        in_window = np.ones(len(earlier), dtype=bool)
    elif window is Window.YTD:
        in_window = np.asarray(earlier >= pd.Timestamp(latest_end.year, 1, 1))
    else:
        in_window = np.asarray(earlier >= latest_end - _WINDOW_LENGTH[window])
    named = in_window & spans[:-1]
    keep = in_window & ~named if exclude_gaps else in_window

    sample = all_returns.iloc[:-1][keep]
    if len(sample) < 2:
        raise ValueError(f"the {window.value} window holds {len(sample)} returns; need 2")
    x = sample.to_numpy(dtype=float)
    n = len(x)
    mean, sd = float(x.mean()), float(x.std(ddof=1))
    if not sd > 0:
        raise ValueError("the window's returns are all equal")

    # Section 4: the latest move.
    z = (latest_value - mean) / sd
    down = latest_value < mean
    count = int(np.sum(x <= latest_value)) if down else int(np.sum(x >= latest_value))
    latest = LatestMove(
        value=latest_value, start=starts[-1], end=latest_end, z=z, down=down, count=count,
        rank=count / n, normal_expected=n * (_cdf(z) if down else _sf(z)),
        partial=partial, spans_gap=bool(spans[-1]))

    # Section 3: tails and quantiles.
    deviations = np.abs(x - mean)
    tails = tuple(
        TailRow(sds=m, observed=int(np.sum(deviations > m * sd)),
                observed_share=float(np.mean(deviations > m * sd)),
                normal_share=2.0 * _sf(m), normal_expected=n * 2.0 * _sf(m))
        for m in TAIL_SDS)
    quantiles = tuple(
        QuantileRow(level=q, observed=float(np.quantile(x, q)),
                    normal=mean + sd * _STANDARD_NORMAL.inv_cdf(q))
        for q in QUANTILE_LEVELS)

    # Section 5: the non-overlapping test sample, aligned to the latest move.
    last = len(all_returns) - 1
    positions = np.arange(last - horizon, -1, -horizon)[::-1]
    test_returns = all_returns.iloc[positions[keep[positions]]]
    tests: tuple[NormalityTest, ...] = ()
    if len(test_returns) < MIN_TEST_N:
        skipped = (f"{len(test_returns)} non-overlapping returns; the tests need at "
                   f"least {MIN_TEST_N}")
    elif not test_returns.std(ddof=1) > 0:
        skipped = "the non-overlapping returns are all equal"
    else:
        skipped = ""
        a_star, p_ad, bound = anderson_darling(test_returns.to_numpy())
        jb, p_jb = jarque_bera(test_returns.to_numpy())
        tests = (NormalityTest(AD_NAME, a_star, p_ad, bound, p_ad < alpha),
                 NormalityTest(JB_NAME, jb, p_jb, False, p_jb < alpha))

    # Section 6: bins, counts and the normal's expected counts.
    count_bins = bins if bins is not None else fd_bin_count(x)
    edges = np.linspace(float(x.min()), float(x.max()), int(count_bins) + 1)
    counts = np.histogram(x, bins=edges)[0]
    cdf_at_edges = np.array([_cdf((e - mean) / sd) for e in edges])
    expected = n * np.diff(cdf_at_edges)

    # Sections 7–8: the Q-Q view, on the test sample for k > 1.
    qq_sample = test_returns.to_numpy() if horizon > 1 else x
    if len(qq_sample) >= 2 and np.std(qq_sample) > 0:
        qq = qq_points(qq_sample)
    else:
        qq = _EMPTY_QQ

    up, down_count = int(np.sum(x > 0)), int(np.sum(x < 0))
    return ReturnDistribution(
        frequency=frequency, horizon=horizon, kind=kind, window=window, alpha=float(alpha),
        returns=sample, mean=mean, median=float(np.median(x)), sd=sd,
        annualized_vol=sd * math.sqrt(PERIODS_PER_YEAR[frequency] / horizon),
        skewness=skewness(sample), excess_kurtosis=excess_kurtosis(sample),
        best=(sample.idxmax(), float(sample.max())), worst=(sample.idxmin(), float(sample.min())),
        up=up, down=down_count, unchanged=n - up - down_count,
        latest=latest, tails=tails, quantiles=quantiles,
        test_returns=test_returns, tests=tests, tests_skipped=skipped,
        bin_edges=edges, bin_counts=counts, bin_expected=expected, qq=qq,
        gap_returns=tuple(earlier[named]), gaps_excluded=bool(exclude_gaps),
    )
