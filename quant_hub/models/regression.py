r"""Regressions of daily returns: α, β and honest errors (W2).

The derivation below was reviewed before the code (2026-10-21), as K1's
was. What it answers, for each holding and for the portfolio:
- How much benchmark (and factor) exposure, β, and how sure are we?
- Is there return beyond it, α, and is it distinguishable from zero?
- Are the streams orthogonal, i.e. are the residuals uncorrelated?

These are REGIME.md's beta band, "max beta of a stream to the core" and
"max correlation between streams".

Data and units
--------------
Daily simple returns of adjusted closes (D38), on the days every series
traded: ``portfolio.simple_returns``, the risk model's own input. Simple
rather than log returns, because dollar P&L is linear in them, so a β is a
hedge ratio in dollars.

Returns are in excess of cash at :math:`r/252` a day (the Portfolio page's
*Rate %*, simple daily). Let :math:`R_{i,t}` be a holding's return and
:math:`F_{k,t}` the factors' (the benchmark first; optionally other ETFs in
the store). Then

.. math::  y_t = R_{i,t} - r/252, \qquad f_{k,t} = F_{k,t} - r/252.

The portfolio's series is the replayed P&L of today's holdings divided by
equity (``RiskReport.pnl / equity``), options repriced in full. It's the
series the page's replay chart already shows.

Model and estimator
-------------------
With :math:`x_t = (1, f_{1,t}, \dots, f_{k,t})^\top` and :math:`p = k + 1`
coefficients,

.. math::  y_t = x_t^\top b + e_t, \qquad b = (\alpha, \beta_1, \dots, \beta_k)^\top,

the OLS estimate is :math:`\hat b = (X^\top X)^{-1} X^\top y`. It's computed
by least squares (``numpy.linalg.lstsq``, QR-based), never by inverting
:math:`X^\top X` explicitly. A rank-deficient :math:`X`, e.g. a factor
listed twice, is an error. The condition number is reported, since nearly
collinear factors inflate errors without biasing anything.

- Residuals: :math:`\hat e = y - X\hat b`.
- Residual variance: :math:`s^2 = \hat e^\top \hat e / (T - p)`.
- Fit: :math:`R^2 = 1 - \hat e^\top \hat e / \sum_t (y_t - \bar y)^2`, plus
  adjusted :math:`R^2`.

With one factor, :math:`\hat\beta = \widehat{\mathrm{Cov}}(y, f) /
\widehat{\mathrm{Var}}(f)` and :math:`\hat\alpha = \bar y - \hat\beta \bar f`.
That β is identical to the Portfolio page's beta tile, which regresses raw
rather than excess returns: a constant :math:`r/252` shifts both series, so
it moves only the intercept. The raw intercept is
:math:`\alpha + (1 - \beta)\,r/252`.

**Units:**
- α is a daily excess return, annualized as :math:`252\hat\alpha` (arithmetic;
  W3's growth view handles compounding).
- Its standard error scales the same way.
- Residual vol is :math:`s\sqrt{252}`.

**Why α is nearly unknowable.** :math:`\mathrm{SE}(\hat\alpha) \approx
s/\sqrt T` for daily data, so annualized it's
:math:`\sigma_{e,\text{ann}} / \sqrt{\text{years}}`. Example: a stock with
30% residual vol, over two years, has :math:`\mathrm{SE}(252\hat\alpha)
\approx 21\%` a year. The page shows α with its error first, as it shows
Kelly (D39).

Standard errors
---------------
:math:`\hat b` is unbiased if :math:`E[e_t \mid X] = 0`. Its covariance is
:math:`(X^\top X)^{-1} X^\top \Omega X (X^\top X)^{-1}`,
:math:`\Omega = \mathrm{Cov}(e)`. Three estimates of it, each right under
weaker assumptions than the last:

1. **Classical**, for i.i.d. errors: :math:`\hat V_{\mathrm{cl}} = s^2 (X^\top X)^{-1}`.
2. **White (HC1)**, robust to heteroskedasticity:

   .. math::  \hat V_{\mathrm{W}} = \tfrac{T}{T-p}\,(X^\top X)^{-1}\,\hat\Gamma_0\,(X^\top X)^{-1},
              \qquad \hat\Gamma_0 = \sum_t \hat e_t^2\, x_t x_t^\top .

3. **Newey–West**, also robust to autocorrelation up to lag :math:`L`.
   With :math:`g_t = \hat e_t x_t` and
   :math:`\hat\Gamma_\ell = \sum_{t > \ell} g_t g_{t-\ell}^\top`:

   .. math::  \hat S = \hat\Gamma_0 + \sum_{\ell=1}^{L} \Bigl(1 - \tfrac{\ell}{L+1}\Bigr)
              \bigl(\hat\Gamma_\ell + \hat\Gamma_\ell^\top\bigr), \qquad
              \hat V_{\mathrm{NW}} = \tfrac{T}{T-p}\,(X^\top X)^{-1}\hat S(X^\top X)^{-1}.

   :math:`L = 0` gives White.

**Newey–West is the default.** Classical is shown beside it, so the gap
itself is information.

**Why the Bartlett weights keep** :math:`\hat S` **positive semidefinite.**
Pad :math:`g_t = 0` outside :math:`1..T`, and let :math:`G_s =
\sum_{j=0}^{L} g_{s-j}` be the sum of :math:`L + 1` consecutive terms.
Expanding :math:`\sum_s G_s G_s^\top`, the pairs :math:`(j, j')` with
:math:`j' - j = \ell` number :math:`L + 1 - |\ell|`. So

.. math::  \sum_s G_s G_s^\top = \sum_{|\ell| \le L} (L + 1 - |\ell|)\,\hat\Gamma_\ell
           = (L + 1)\,\hat S

(:math:`\hat\Gamma_{-\ell} = \hat\Gamma_\ell^\top`). That's a sum of outer
products, hence PSD: no standard error can come out imaginary. Truncating
the lags with equal weights instead has no such guarantee.

**The lag.** :math:`L = \lfloor 4 (T/100)^{2/9} \rfloor` (Newey and West,
1994, the usual plug-in rate): 4 for one year, 5 for two, 7 for five. The
page shows it. Daily equity returns are close to serially uncorrelated, so
Newey–West usually lands near White. It earns its place on thinly traded
names, whose stale prices autocorrelate.

When the classical error misleads
---------------------------------
Two closed forms show when the classical error misleads, and the tests
check both.

**Volatility clustering, for β.** With one mean-zero factor, the
asymptotic variance is :math:`\mathrm{Var}(\hat\beta) =
E[f^2 e^2] / (T\, E[f^2]^2)`. The classical estimate converges to
:math:`E[e^2] / (T\, E[f^2])`. Their ratio is

.. math::  \rho = \frac{E[f^2 e^2]}{E[f^2]\,E[e^2]},

so a nominal 95% classical interval covers :math:`2\Phi(1.96/\sqrt\rho) - 1`.

- When factor and residual share a variance process :math:`h_t` (calm and
  turbulent days, the Markov Lab's regimes), :math:`\rho = E[h^2]/E[h]^2 >
  1`.
- Example: 20% of days at nine times the variance gives :math:`\rho = 2.52`
  and coverage 78%. Simulated: 79% classical; 95% White and Newey–West
  (3,000 runs, T = 504).
- GARCH errors are not used for this check. Their :math:`E[h^2]` is driven
  by rare extremes a two-year sample seldom contains: at persistence 0.97
  the formula said 84%, and simulation gave 90%.

**Autocorrelated residuals, for α.** If :math:`e_t` is AR(1) with
coefficient :math:`\phi` and the factor is i.i.d., the long-run variance of
:math:`\bar e` exceeds the i.i.d. one by :math:`(1+\phi)/(1-\phi)`. White's
interval for α then covers :math:`2\Phi\bigl(1.96\sqrt{(1-\phi)/(1+\phi)}\bigr) - 1`.
- At :math:`\phi = 0.3`, that's 85%. Simulated: 85% White, 93% Newey–West.
- Newey–West's shortfall is its known finite-sample bias: the Bartlett
  weights underweight the lags.
- β's error is unaffected, because :math:`E[f_t f_{t-\ell}] = 0`.

Stale prices: the Dimson sum beta (optional)
--------------------------------------------
A thinly traded stock (an OTC listing, a small cap) reacts to the market
partly a day late. Then :math:`y_t = \beta_0 f_t + \beta_1 f_{t-1} + e_t`,
and a plain regression finds only :math:`\beta_0`.

The fix (Dimson, 1979): add :math:`q` lags of the benchmark and report the
sum :math:`\beta_\Sigma = \sum_{j=0}^{q} \beta_j`, with
:math:`\mathrm{SE} = \sqrt{\mathbf 1^\top \hat V_{\beta} \mathbf 1}` over
the lag block. Simulated with :math:`\beta = 0.6 + 0.4` lagged: plain
:math:`\hat\beta = 0.61`, sum :math:`0.61 + 0.40 = 1.00`.

The default is :math:`q = 0`. The page offers 1 and marks holdings whose
lag coefficient is significant.

Several factors
---------------
Replacing the factors by an invertible recombination, :math:`X \to XA`,
changes nothing that's fitted:
- fitted values, residuals and :math:`R^2` are unchanged
- :math:`\hat b \to A^{-1}\hat b`
- every :math:`\hat V \to A^{-1}\hat V A^{-\top}`, since each estimator is
  a sandwich in :math:`X`

So correlated ETF factors can be entered as spreads (QQQ − SPY: a growth
tilt; IWM − SPY: a size tilt). Each coefficient then reads as a tilt on top
of the market, and the condition number drops, without changing the fit.

Rolling β
---------
The regression over the :math:`W` days ending at :math:`t` uses only data
through :math:`t`, so the series is causal: what was knowable that day.
- Each window's Newey–West error uses the lag rule at :math:`W`. The chart
  draws :math:`\hat\beta_t \pm 1.96\,\mathrm{SE}`.
- :math:`W \ge 60` days, the risk model's ``MIN_DAYS``: below about three
  months the estimates are guesses.
- The default is 126 days (half a year).

Orthogonal streams: residual correlation
----------------------------------------
"Orthogonal" is a claim about residuals: once the factors are taken out,
are two holdings' remaining returns uncorrelated? The page shows the
residual correlation matrix.

Under independence, each correlation's sampling error is about
:math:`1/\sqrt T`: :math:`|\hat\rho| < 0.087` is indistinguishable from
zero over two years. Intervals use Fisher's :math:`z`:
:math:`\tanh\bigl(\operatorname{atanh}\hat\rho \pm 1.96/\sqrt{T-3}\bigr)`.
That assumes i.i.d. bivariate normal pairs, so under volatility clustering
it is labelled approximate. The raw correlation of a stream with the core,
:math:`\beta\,\sigma_f/\sigma_y`, is shown beside it.

The portfolio row
-----------------
OLS is linear in :math:`y` with :math:`X` fixed, so for stocks and ETFs
the portfolio's coefficients are the weighted holdings':
:math:`\hat b_p = \sum_i w_i \hat b_i`, exactly. Options break this,
because the replay reprices them in full. So the portfolio row is
regressed on its own series, and its errors come from that series. Errors
don't add across holdings: their residuals covary.

API sketch
----------
- ``ols(y, X, names, lags=None) -> Regression``: coefficients; classical,
  White and Newey–West covariances; :math:`R^2`, adjusted :math:`R^2`;
  residual sd; :math:`T`; :math:`L`; the condition number.
- ``regress_returns(returns, factors, rate, dimson_lags=0) -> dict[str, Regression]``:
  one regression per column, on common days, in excess of cash.
- ``rolling_regression(y, X, window) -> pd.DataFrame``: β and its
  Newey–West error, indexed by window end.
- ``residual_correlation(residuals) -> (corr, lower, upper)``: Fisher
  :math:`z` bounds.

Limitations
-----------
- Linear and constant within a window. β moves with regimes, which is why
  the rolling view exists.
- The errors are asymptotic: Newey–West undercovers a little in finite
  samples (93% at :math:`\phi = 0.3`, T = 504).
- Fisher intervals are approximate under volatility clustering.
- Factors are whatever ETFs the store holds. A factor-return CSV (e.g. Ken
  French's library, once its terms are checked, D34) is a later option.

Validation plan (tests)
-----------------------
1. **Hand cases.** Coefficients equal the normal equations' and
   ``numpy.polyfit``'s. With one factor, :math:`\hat\beta =
   \widehat{\mathrm{Cov}}/\widehat{\mathrm{Var}}`, matching the Portfolio
   tile, with the raw intercept shifted by :math:`(1-\beta)r/252`.
2. **Invariance.** Under :math:`X \to XA`, fitted values and :math:`R^2` are
   unchanged, and :math:`\hat b` and all three :math:`\hat V` transform by
   :math:`A^{-1}` (checked to 1e-12).
3. **Bartlett.** :math:`\hat S` equals the block-sum form to rounding, and
   is PSD on random inputs. :math:`L = 0` equals White. On i.i.d. data the
   three errors agree within sampling error.
4. **Coverage against the closed forms** (seeded, 3,000 runs, binomial
   tolerance):
   - i.i.d.: all three cover 95%
   - two variance regimes (20% at 9×): classical covers
     :math:`2\Phi(1.96/\sqrt\rho) - 1` = 78.4% (±2.3 points), and White and
     Newey–West 95%
   - AR(1) residuals at :math:`\phi = 0.3`: White's α interval covers 85%,
     Newey–West within [92%, 97.5%]
5. **Dimson.** With a planted lagged response, the plain β is biased by the
   lag's share, and the sum beta recovers the total within its error.
6. **Rolling.** Each window equals a full regression on that slice.
   Perturbing data after :math:`t` leaves :math:`\hat\beta_t` unchanged.
7. **Residual correlation.** Under independence, the Fisher intervals cover
   95%.
8. **Portfolio identity.** With stocks only, :math:`\hat b_p = \sum_i w_i
   \hat b_i` to 1e-12.
9. **Rejections.** A rank-deficient :math:`X`, :math:`T \le p`,
   :math:`L \ge T`, non-finite values, a window under ``MIN_DAYS``.

The page (W2's second step, after this module)
----------------------------------------------
A Regressions view on the Portfolio page, on the same holdings, lookback,
benchmark and rate:
- per-holding table: α a year ± SE, β ± SE, :math:`R^2`, residual vol,
  :math:`T`; the portfolio as the last row
- a rolling-β chart for the selected holding, with its band
- the residual correlation heatmap; it needs a diverging ramp in
  ``theme.py``, a new component token
- optional extra factors, entered as tickers or spreads
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
import pandas as pd

from quant_hub.models.portfolio import MIN_DAYS, TRADING_DAYS

CLASSICAL, WHITE, NEWEY_WEST = "classical", "white", "newey-west"
METHODS = (CLASSICAL, WHITE, NEWEY_WEST)
ALPHA = "alpha"
ROLLING_WINDOW = 126
"""Default rolling window: half a year of sessions (module docstring)."""

Z95 = NormalDist().inv_cdf(0.975)
"""The two-sided 95% normal quantile, 1.96: every interval on the page."""


@dataclass(frozen=True)
class Regression:
    """One OLS fit (module docstring). Coefficients and errors are daily."""

    names: tuple[str, ...]
    coef: np.ndarray
    covariance: dict[str, np.ndarray]     # by method: classical, white, newey-west
    lags: int                             # Newey–West L
    observations: int                     # T
    r2: float
    r2_adjusted: float
    residual_sd: float                    # s, daily
    condition: float                      # of X
    residuals: pd.Series

    def standard_errors(self, method: str = NEWEY_WEST) -> np.ndarray:
        if method not in self.covariance:
            raise ValueError(f"method must be one of {METHODS}, not {method!r}")
        return np.sqrt(np.clip(np.diag(self.covariance[method]), 0.0, None))

    def value(self, name: str) -> float:
        return float(self.coef[self._index(name)])

    def error(self, name: str, method: str = NEWEY_WEST) -> float:
        return float(self.standard_errors(method)[self._index(name)])

    def total(self, names: tuple[str, ...], method: str = NEWEY_WEST) -> tuple[float, float]:
        """A sum of coefficients and its error, sqrt(1' V 1) over them: the
        Dimson sum beta (module docstring)."""
        idx = [self._index(n) for n in names]
        block = self.covariance[method][np.ix_(idx, idx)]
        return float(self.coef[idx].sum()), math.sqrt(max(float(block.sum()), 0.0))

    @property
    def alpha_annual(self) -> float:
        return TRADING_DAYS * self.value(ALPHA)

    def alpha_error_annual(self, method: str = NEWEY_WEST) -> float:
        return TRADING_DAYS * self.error(ALPHA, method)

    @property
    def residual_vol_annual(self) -> float:
        return self.residual_sd * math.sqrt(TRADING_DAYS)

    def _index(self, name: str) -> int:
        try:
            return self.names.index(name)
        except ValueError:
            raise KeyError(f"no coefficient {name!r}; have {self.names}") from None


def newey_west_lags(observations: int) -> int:
    """L = floor(4 (T/100)^(2/9)): Newey and West (1994)'s plug-in rate."""
    if observations < 1:
        raise ValueError("observations must be positive")
    return int(math.floor(4.0 * (observations / 100.0) ** (2.0 / 9.0)))


def ols(y, X, names: tuple[str, ...] | None = None, lags: int | None = None,
        index: pd.Index | None = None) -> Regression:
    """OLS of ``y`` on the columns of ``X`` with all three covariances
    (module docstring). ``X`` carries its own constant column; ``lags``
    defaults to the plug-in rate."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if y.ndim != 1 or X.ndim != 2 or X.shape[0] != len(y):
        raise ValueError("y must be a vector and X a matrix with one row per observation")
    T, p = X.shape
    if T <= p:
        raise ValueError(f"{T} observations can't fit {p} coefficients")
    if not (np.isfinite(y).all() and np.isfinite(X).all()):
        raise ValueError("y and X must be finite")
    names = tuple(names) if names is not None else tuple(f"x{j}" for j in range(p))
    if len(names) != p:
        raise ValueError(f"{len(names)} names for {p} columns")
    L = newey_west_lags(T) if lags is None else int(lags)
    if not 0 <= L < T:
        raise ValueError(f"Newey–West lags must be in [0, {T}), not {L}")

    coef, _ssr, rank, singular = np.linalg.lstsq(X, y, rcond=None)
    if rank < p:
        raise ValueError(f"the regressors are collinear (rank {rank} of {p}): a factor is "
                         "repeated, or constant beside the intercept")
    residuals = y - X @ coef
    ssr = float(residuals @ residuals)
    s2 = ssr / (T - p)

    # (X'X)^-1 from X = QR without forming X'X: R^-1 R^-T.
    r_inverse = np.linalg.inv(np.linalg.qr(X, mode="r"))
    bread = r_inverse @ r_inverse.T
    g = X * residuals[:, None]
    gamma0 = g.T @ g
    S = gamma0.copy()
    for lag in range(1, L + 1):
        gamma = g[lag:].T @ g[:-lag]
        S += (1.0 - lag / (L + 1.0)) * (gamma + gamma.T)
    small_sample = T / (T - p)
    covariance = {
        CLASSICAL: s2 * bread,
        WHITE: small_sample * bread @ gamma0 @ bread,
        NEWEY_WEST: small_sample * bread @ S @ bread,
    }
    centered = y - y.mean()
    tss = float(centered @ centered)
    r2 = 1.0 - ssr / tss if tss > 0 else float("nan")
    r2_adjusted = 1.0 - (1.0 - r2) * (T - 1) / (T - p)
    return Regression(names, coef, covariance, L, T, r2, r2_adjusted, math.sqrt(s2),
                      float(singular[0] / singular[-1]),
                      pd.Series(residuals, index=index if index is not None else pd.RangeIndex(T)))


def design(factors: pd.DataFrame, dimson_lags: int = 0) -> pd.DataFrame:
    """X with its constant first, then the factors, then lags of the first
    factor (the benchmark) for the Dimson sum beta. The first ``dimson_lags``
    rows, which lack lagged values, are dropped."""
    if dimson_lags < 0:
        raise ValueError("dimson_lags must not be negative")
    if factors.shape[1] == 0:
        raise ValueError("at least one factor is needed")
    columns = {ALPHA: pd.Series(1.0, index=factors.index)}
    columns.update({str(name): factors[name] for name in factors.columns})
    benchmark = factors.columns[0]
    for j in range(1, dimson_lags + 1):
        columns[lag_name(benchmark, j)] = factors[benchmark].shift(j)
    return pd.DataFrame(columns).iloc[dimson_lags:]


def lag_name(factor: str, lag: int) -> str:
    return f"{factor} (t-{lag})"


def regress_returns(returns: pd.DataFrame, factors: pd.DataFrame, rate: float, *,
                    dimson_lags: int = 0, lags: int | None = None) -> dict[str, Regression]:
    """Each column of ``returns`` on ``factors``, both daily simple returns,
    in excess of cash at ``rate``/252 a day, on their common days."""
    if not math.isfinite(rate):
        raise ValueError("rate must be finite")
    common = returns.index.intersection(factors.index)
    returns, factors = returns.loc[common], factors.loc[common]
    if returns.isna().any().any() or factors.isna().any().any():
        raise ValueError("returns and factors must be complete on their common days")
    cash = rate / TRADING_DAYS
    X = design(factors - cash, dimson_lags)
    if len(X) < MIN_DAYS:
        raise ValueError(f"{len(X)} common days; a regression needs {MIN_DAYS}")
    names = tuple(X.columns)
    return {str(column): ols(returns.loc[X.index, column] - cash, X.to_numpy(), names, lags,
                             index=X.index)
            for column in returns.columns}


def rolling_regression(y: pd.Series, X: pd.DataFrame, window: int = ROLLING_WINDOW,
                       total: tuple[str, ...] = ()) -> pd.DataFrame:
    """Each coefficient over the ``window`` days ending at each date, with
    its Newey–West error (``<name> se``): causal, as the module docstring says.
    With ``total``, also the sum of those coefficients (``total``, ``total se``),
    e.g. the Dimson sum beta."""
    if window < MIN_DAYS:
        raise ValueError(f"a rolling window needs at least {MIN_DAYS} days, not {window}")
    if len(y) != len(X) or not y.index.equals(X.index):
        raise ValueError("y and X must share their index")
    if len(y) < window:
        raise ValueError(f"{len(y)} days are fewer than the {window}-day window")
    names = tuple(X.columns)
    values, targets = X.to_numpy(dtype=float), y.to_numpy(dtype=float)
    lags = newey_west_lags(window)
    rows = []
    for end in range(window, len(y) + 1):
        fit = ols(targets[end - window:end], values[end - window:end], names, lags)
        rows.append([*fit.coef, *fit.standard_errors(NEWEY_WEST),
                     *(fit.total(total) if total else ())])
    columns = [*names, *(f"{name} se" for name in names), *(("total", "total se") if total else ())]
    return pd.DataFrame(rows, index=y.index[window - 1:], columns=columns)


def residual_correlation(residuals: pd.DataFrame
                         ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """The residuals' correlations with Fisher-z 95% bounds (approximate under
    volatility clustering; module docstring). A residual with no variance
    has no correlation: NaN."""
    T = len(residuals)
    if T <= 3:
        raise ValueError("Fisher intervals need more than three observations")
    corr = residuals.corr()
    half = Z95 / math.sqrt(T - 3)
    z = np.arctanh(corr.clip(-1 + 1e-15, 1 - 1e-15))
    lower, upper = np.tanh(z - half), np.tanh(z + half)
    for i in range(len(corr)):
        lower.iat[i, i] = upper.iat[i, i] = corr.iat[i, i]     # exactly 1 (or NaN)
    return corr, lower, upper
