r"""Regressions of daily returns: α, β and honest errors (W2).

**Status: derivation for owner review (milestone W2).** The implementation
follows once this is approved; nothing below is code yet.

What it answers, for each holding and for the portfolio:
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
