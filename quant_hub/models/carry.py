r"""Implied carry: each expiry's forward and discount factor from put–call parity.

Milestone C1b (D33, decision 3). The derivation below was reviewed before
the code.

Why
---
A chain needs each expiry's forward :math:`F(T)` (to centre the smile at
:math:`k = \ln(K/F)`) and discount factor :math:`D(T)` (to price). A flat
rate and dividend yield get both wrong for real underlyings: SPY pays
discrete quarterly dividends, index carry varies by expiry, and the delayed
spot is a last-trade print whose time doesn't match the option quotes
(C0). Parity reads :math:`F` and :math:`D` off the quotes themselves.

Parity
------
For European options on the same underlying, strike :math:`K` and expiry
:math:`T`, no arbitrage alone gives

.. math::  C - P = D\,(F - K).

No volatility model is involved. Long the call and short the put is a
forward contract struck at :math:`K`, worth :math:`D(F - K)` today. Every
carry effect (rates, discrete dividends, borrow) lives inside :math:`F`
and :math:`D`.

Estimating F and D
------------------
At one expiry, take the strikes :math:`K_1, \dots, K_n` near the money where
both the call and the put are quoted two-sided (bid > 0), and let
:math:`y_i = \tilde C_i - \tilde P_i` be the difference of the mids. Then

.. math::  y_i = a + b K_i + \varepsilon_i, \qquad a = DF, \quad b = -D.

Each mid is off by up to its half-spread, so the error scale of
:math:`y_i` is set by :math:`h_{C,i}^2 + h_{P,i}^2`. That's the same
convention as the SVI weights in ``vol_surface``, where the half-spread is
the unit of quote uncertainty. Weighted least squares with
:math:`w_i = 1/(h_{C,i}^2 + h_{P,i}^2)`, using weighted means
:math:`\bar K = \sum w_i K_i / \sum w_i` and
:math:`\bar y = \sum w_i y_i / \sum w_i`, gives

.. math::

    \hat b = \frac{\sum w_i (K_i - \bar K)(y_i - \bar y)}{\sum w_i (K_i - \bar K)^2},
    \qquad \hat D = -\hat b,
    \qquad \hat F = \bar K + \frac{\bar y}{\hat D}.

(:math:`\hat a = \bar y - \hat b\bar K`, and :math:`\hat F = \hat a/\hat D`
rearranges to the form above. Read it as the average strike, plus the
average call-minus-put converted to a forward distance.)

Uncertainty
-----------
With residual scale :math:`\hat s^2 = \sum w_i e_i^2/(n-2)`, the centred
regressor makes :math:`\bar y` and :math:`\hat b` uncorrelated, and

.. math::

    \mathrm{SE}(\hat D) = \frac{\hat s}{\sqrt{\sum w_i (K_i - \bar K)^2}},
    \qquad
    \mathrm{SE}(\hat F) \approx \frac{1}{\hat D}\sqrt{\frac{\hat s^2}{\sum w_i}
        + (\hat F - \bar K)^2\,\mathrm{SE}(\hat D)^2}

by the delta method on :math:`\hat F = \bar K + \bar y/\hat D`. Near the
money :math:`\hat F - \bar K` is small, so **F is well determined even when
D is not.**

D is usually not well determined over short horizons. The rate
:math:`r_T = -\ln D/T` has :math:`\mathrm{SE}(r_T) \approx \mathrm{SE}(\hat D)/(\hat D T)`.
With strikes spanning a few percent, :math:`\mathrm{SE}(\hat D) \sim 10^{-3}`,
which is about ±5% a year at :math:`T` = 7 days. Pricing barely cares,
since :math:`D` scales a price linearly, but a 7-day "rate" from parity is
noise.

The rule, per expiry
--------------------
1. With :math:`n \ge 3` pairs, regress. Keep :math:`(\hat F, \hat D)` when
   :math:`r_T` lies in a plausible band (:math:`-2\%` to :math:`15\%`) and
   :math:`\mathrm{SE}(r_T) \le 1\%`.
2. Otherwise, with :math:`n \ge 1`, fix :math:`D = e^{-rT}` at the chain's
   flat rate and estimate only :math:`F`. That's the one-parameter
   weighted least squares :math:`\hat F = \bar K + \bar y/D`, with
   :math:`\mathrm{SE}(\hat F) = \hat s/(D\sqrt{\sum w_i})` and
   :math:`\hat s^2 = \sum w_i e_i^2/(n-1)`. A single pair leaves no
   residual, so there :math:`\hat s = 1`: the half-spreads themselves are
   the uncertainty.
3. With no pairs, the expiry keeps the flat carry.

The fit records which branch applied, :math:`n`, and the standard errors,
so the page can show them.

Into the chain
--------------
The estimates become ``data.options.ExpiryCarry(F, D)``. The chain then
reports :math:`r_T = -\ln D/T` and :math:`q_T = r_T - \ln(F/S)/T`, which
reproduce :math:`F` and :math:`D` exactly. In Black–Scholes–Merton the spot
then cancels:
:math:`S e^{-q_T T} = F D` makes the price :math:`D[F N(d_1) - K N(d_2)]`,
Black-76 on the forward. **The stale delayed spot no longer affects any
implied vol.** It still appears in the displayed spot; only moneyness and
pricing move to :math:`F`.

Limits
------
* **American options (SPY).** Parity holds only as bounds, and
  early-exercise premia bias :math:`y` slightly; near the money and short
  dated, the bias is small. SPX options are European, so their parity is
  exact. This is documented, not corrected.
* **Only two-sided pairs count.** A no-bid quote's mid isn't a price.

Validation plan (tests)
-----------------------
1. Noise-free synthetic European chains (truth :math:`F = S e^{(r-q)T}`,
   :math:`D = e^{-rT}`, known exactly) recover :math:`F, D` to ~1e-10.
2. Noisy chains: across many draws, :math:`\pm t\,\mathrm{SE}` covers
   the true :math:`F` (and :math:`D`) about 95% of the time. Here :math:`t`
   is the Student-t quantile with ``dof`` degrees of freedom: :math:`n-2`
   for the regression, :math:`n-1` for the fixed rate. The scale is
   estimated from a handful of residuals, so :math:`\pm 1.96` would cover
   only about 88% at :math:`n` = 5 (measured).
3. Short expiries take branch 2 and still recover :math:`F`.
4. A chain whose flat dividend yield is wrong: implied vols with implied
   carry match the true surface much more closely than with the flat
   carry.
5. Unpaired strikes, no-bid quotes and single pairs follow the stated
   branches; invalid inputs are rejected.

Live check: SPY and SPX chains, comparing implied :math:`r_T`/:math:`q_T`
with known rates and dividend yields, and the χ²/pt of the SVI fit with and
without implied carry. Before any carry, SPY's χ²/pt was 5–76.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from quant_hub.data.options import ExpiryCarry, OptionChain
from quant_hub.models.vol_surface import MIN_HALF_SPREAD

RATE_BAND = (-0.02, 0.15)
"""Plausible continuously-compounded rates for the regression branch."""

RATE_SE_MAX = 0.01
"""Largest standard error of :math:`r_T` the regression branch accepts."""

MIN_REGRESSION_PAIRS = 3
"""Fewest pairs for the two-parameter regression (one residual dof)."""

MAX_PAIRS = 7
"""Pairs used per expiry: the two-sided strikes nearest the flat forward."""

REGRESSION, FIXED_RATE, NONE = "regression", "fixed rate", "none"


@dataclass(frozen=True)
class ExpiryCarryFit:
    """One expiry's estimate (module docstring, "The rule").

    ``method`` is ``"regression"``, ``"fixed rate"`` or ``"none"``.
    ``discount_se`` and ``rate_se`` are None unless the regression was kept;
    every estimate is None for ``"none"``. ``dof`` is the residual degrees
    of freedom behind the standard errors (Student-t intervals; 0 for a
    single pair, whose uncertainty is the half-spreads themselves).
    """

    dte: float
    method: str
    pairs: int
    forward: float | None = None
    forward_se: float | None = None
    discount: float | None = None
    discount_se: float | None = None
    rate: float | None = None
    rate_se: float | None = None
    dof: int = 0


@dataclass(frozen=True)
class CarryFit:
    """The chain with each fitted expiry's ``ExpiryCarry``, and the fits."""

    chain: OptionChain
    expiries: tuple[ExpiryCarryFit, ...]


def implied_carry(chain: OptionChain, max_pairs: int = MAX_PAIRS) -> CarryFit:
    """Estimate every expiry's forward and discount factor from parity and
    return the chain with them as its carry (module docstring)."""
    if max_pairs < 1:
        raise ValueError("max_pairs must be >= 1")
    fits = []
    carry = dict(chain.carry)
    for dte in chain.expiries:
        strikes, y, weights = parity_pairs(chain, dte, max_pairs)
        flat_rate = -math.log(chain.discount(dte)) / (dte / 365.0) if dte in chain.carry \
            else chain.rate
        fit = fit_expiry_carry(dte, strikes, y, weights, flat_rate)
        fits.append(fit)
        if fit.method != NONE:
            carry[dte] = ExpiryCarry(forward=fit.forward, discount=fit.discount)
    return CarryFit(chain=replace(chain, carry=carry), expiries=tuple(fits))


def parity_pairs(chain: OptionChain, dte: float, max_pairs: int = MAX_PAIRS
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Strikes quoted two-sided (bid > 0) as both a call and a put at
    ``dte``, the ``max_pairs`` nearest the chain's flat forward: their
    strikes, :math:`y = \tilde C - \tilde P`, and weights
    :math:`1/(h_C^2 + h_P^2)` (half-spreads floored at half a tick)."""
    calls, puts = {}, {}
    for q in chain.quotes:
        if q.dte == dte and q.bid > 0:
            (calls if q.option_type == "call" else puts)[q.strike] = q
    common = sorted(set(calls) & set(puts))
    guess = chain.forward(dte)
    chosen = sorted(sorted(common, key=lambda k: (abs(k - guess), k))[:max_pairs])
    strikes = np.array(chosen, dtype=float)
    y = np.array([calls[k].mid - puts[k].mid for k in chosen])
    half_c = np.array([max(calls[k].half_spread, MIN_HALF_SPREAD) for k in chosen])
    half_p = np.array([max(puts[k].half_spread, MIN_HALF_SPREAD) for k in chosen])
    return strikes, y, 1.0 / (half_c**2 + half_p**2)


def fit_expiry_carry(dte: float, strikes: np.ndarray, y: np.ndarray, weights: np.ndarray,
                     flat_rate: float) -> ExpiryCarryFit:
    """Apply the rule to one expiry's pairs (module docstring, "The rule")."""
    strikes, y, weights = (np.asarray(a, dtype=float) for a in (strikes, y, weights))
    if not (strikes.shape == y.shape == weights.shape and strikes.ndim == 1):
        raise ValueError("strikes, y and weights must be matching 1-D arrays")
    if not (np.isfinite(strikes).all() and np.isfinite(y).all() and np.isfinite(weights).all()):
        raise ValueError("pairs must be finite")
    if np.any(strikes <= 0) or np.any(weights <= 0):
        raise ValueError("strikes and weights must be positive")
    if not (math.isfinite(dte) and dte > 0 and math.isfinite(flat_rate)):
        raise ValueError("dte must be positive and the flat rate finite")
    n, years = len(strikes), dte / 365.0
    if n == 0:
        return ExpiryCarryFit(dte, NONE, 0)

    total = weights.sum()
    k_bar = float(weights @ strikes / total)
    y_bar = float(weights @ y / total)

    if n >= MIN_REGRESSION_PAIRS:
        sxx = float(weights @ (strikes - k_bar) ** 2)
        slope = float(weights @ ((strikes - k_bar) * (y - y_bar)) / sxx)
        discount = -slope
        if discount > 0:
            residuals = y - (y_bar + slope * (strikes - k_bar))
            scale = math.sqrt(float(weights @ residuals**2) / (n - 2))
            discount_se = scale / math.sqrt(sxx)
            forward = k_bar + y_bar / discount
            forward_se = math.sqrt(scale**2 / total
                                   + (forward - k_bar) ** 2 * discount_se**2) / discount
            rate = -math.log(discount) / years
            rate_se = discount_se / (discount * years)
            if forward > 0 and RATE_BAND[0] <= rate <= RATE_BAND[1] and rate_se <= RATE_SE_MAX:
                return ExpiryCarryFit(dte, REGRESSION, n, forward, forward_se, discount,
                                      discount_se, rate, rate_se, dof=n - 2)

    # Fixed rate: D from the flat rate, F alone by one-parameter WLS.
    discount = math.exp(-flat_rate * years)
    forward = k_bar + y_bar / discount
    if not forward > 0:
        return ExpiryCarryFit(dte, NONE, n)
    if n >= 2:
        residuals = y - discount * (forward - strikes)
        scale = math.sqrt(float(weights @ residuals**2) / (n - 1))
    else:
        scale = 1.0   # one pair: the half-spreads themselves are the uncertainty
    return ExpiryCarryFit(dte, FIXED_RATE, n, forward, scale / (discount * math.sqrt(total)),
                          discount, rate=flat_rate, dof=n - 1)
