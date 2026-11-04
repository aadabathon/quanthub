r"""How a core and the rest of the book add up (ALLOCATION A1).

The β a core leaves room for
============================

Mathematical description
------------------------
The book holds names :math:`i` at weights :math:`w_i = x_i / E` (exposure
over equity, options counted by delta) and the rest in cash. Each name's
β to the benchmark is its sample covariance with the benchmark over the
benchmark's variance, :math:`\beta_i = \widehat{\mathrm{Cov}}(r_i, r_m) /
\widehat{\mathrm{Var}}(r_m)`, over the risk model's lookback.

The book's β is linear in the weights:

.. math::  \beta = \sum_i w_i \beta_i

Two facts make that exact rather than approximate. The OLS slope on one
regressor is linear in the dependent series, and cash earns the same rate
every day, so it has no covariance with the market and adds nothing. This
is the same β that W2's regression of the portfolio on the benchmark
estimates, over the same days.

Split the names into the core :math:`C` and the rest :math:`N`:

.. math::  \beta = B_N + w_C\,\beta_C, \qquad
           B_N = \sum_{i \in N} w_i \beta_i, \qquad
           w_C = \sum_{i \in C} w_i, \qquad
           \beta_C = \frac{1}{w_C} \sum_{i \in C} w_i \beta_i

:math:`B_N` is the β the rest of the book already supplies, and
:math:`\beta_C` is the core's own β (1 when the core is the benchmark).
Hold the rest fixed, and let the core grow or shrink as one unit, funded
from or returned to cash. The book's β is then :math:`\beta^*` at

.. math::  w_C^* = \frac{\beta^* - B_N}{\beta_C}, \qquad \beta_C > 0

so a β band :math:`[L, U]` holds for core weights between
:math:`(L - B_N)/\beta_C` and :math:`(U - B_N)/\beta_C`.

This is how a pick's β is "shorted out" without a short. Holding less
core cancels the market exposure the pick brings. An IRA can't short,
and this needs no margin. It's Treynor and Black's (1973) split of a
book into the market and an active portfolio, where the market's weight
sets the whole book's β. Their sizing of the active names, by α over
residual variance, is BUDGET_PLAN's, folded into ALLOCATION A3.

What it is not
--------------
* **Shown, never applied.** The review reports :math:`w_C^*`. Nothing
  here trades.
* **Leverage is shown, not assumed away.** A :math:`w_C^*` above
  :math:`1 - w_N` needs leverage, and one below zero means the rest of the
  book alone is past that side of the band. Both are reported as they
  are. ``CoreNetting.reachable`` gives the core weights the band allows
  without leverage, the band's range intersected with
  :math:`[0, 1 - w_N]`, or None when that's empty.
* **The βs are estimates.** Each carries sampling error, and a stock's β
  tends to rise in a crash. ALLOCATION A2's crash stress is the check on
  that.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CoreNetting:
    """The module docstring's decomposition of today's book."""

    core: tuple[str, ...]               # the core's names, sorted
    core_weight: float                  # w_C
    core_beta: float                    # β_C
    rest_weight: float                  # w_N
    rest_beta: float                    # B_N, the β the rest supplies
    band: tuple[float | None, float | None] | None    # the Beta band, when set
    core_for_band: tuple[float | None, float | None] | None   # w_C* at each side

    @property
    def beta(self) -> float:
        """The book's β, B_N + w_C β_C."""
        return self.rest_beta + self.core_weight * self.core_beta

    @property
    def room(self) -> float:
        """The most the core can be without leverage, 1 − w_N."""
        return 1.0 - self.rest_weight

    def reachable(self) -> tuple[float, float] | None:
        """The core weights that keep β in the band without leverage: the
        band's range intersected with [0, 1 − w_N]. None without a band, or
        when no such weight exists."""
        if self.core_for_band is None:
            return None
        low, high = self.core_for_band
        lowest = max(0.0, low if low is not None else 0.0)
        highest = min(self.room, high if high is not None else self.room)
        return (lowest, highest) if lowest <= highest else None


def core_netting(weights: Mapping[str, float], betas: Mapping[str, float],
                 core: Collection[str],
                 band: tuple[float | None, float | None] | None = None) -> CoreNetting:
    """Split the book's β into the core's and the rest's (module docstring).

    ``weights`` and ``betas`` are by name, over the same names. ``core``
    names the core's holdings; the names it lists that aren't held are
    ignored. ``band`` is section 4's Beta band, (lower, upper), either side
    None. Raises ``ValueError`` when no core is held, when its weight or β
    isn't positive, or for a non-finite input.
    """
    if set(weights) != set(betas):
        raise ValueError("weights and betas must cover the same names")
    for name in weights:
        if not (math.isfinite(weights[name]) and math.isfinite(betas[name])):
            raise ValueError(f"{name}: weights and betas must be finite")
    held = sorted(set(core) & set(weights))
    if not held:
        raise ValueError("no core holding is in the book")
    w_core = sum(weights[n] for n in held)
    if w_core <= 0:
        raise ValueError("the core's weight isn't positive")
    b_core = sum(weights[n] * betas[n] for n in held) / w_core
    if b_core <= 0:
        raise ValueError(f"the core's β ({b_core:.2f}) isn't positive, so it can't net the rest")
    rest = [n for n in weights if n not in held]
    w_rest = sum(weights[n] for n in rest)
    b_rest = sum(weights[n] * betas[n] for n in rest)
    for_band = None
    if band is not None:
        for side in band:
            if side is not None and not math.isfinite(side):
                raise ValueError("the band's sides must be finite")
        for_band = tuple(None if side is None else (side - b_rest) / b_core for side in band)
    return CoreNetting(tuple(held), w_core, b_core, w_rest, b_rest, band, for_band)
