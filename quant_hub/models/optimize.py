r"""Derivative-free minimization shared by the model fitters.

Nelder--Mead simplex method
---------------------------
To minimize :math:`f:\mathbb{R}^n \to \mathbb{R}` without gradients, keep a
simplex of :math:`n+1` vertices ordered so that
:math:`f(x_0) \le \dots \le f(x_n)`.  Let :math:`\bar{x}` be the centroid of
the best :math:`n` vertices.  Each iteration tries to replace the worst
vertex :math:`x_n` by a point on the ray from :math:`x_n` through
:math:`\bar{x}`:

.. math::

   x_r = \bar{x} + (\bar{x} - x_n)                  \quad\text{(reflection)}

   x_e = \bar{x} + 2(x_r - \bar{x})                 \quad\text{(expansion)}

   x_c = \bar{x} + \tfrac{1}{2}(x_n - \bar{x})      \quad\text{(inside contraction)}

* if :math:`f(x_0) \le f(x_r) < f(x_{n-1})`, accept :math:`x_r`;
* if :math:`f(x_r) < f(x_0)`, accept the better of :math:`x_e` and :math:`x_r`;
* otherwise accept :math:`x_c` if :math:`f(x_c) < f(x_n)`;
* otherwise shrink every vertex halfway toward :math:`x_0`.

The coefficients (1, 2, 1/2, 1/2) are the standard Nelder--Mead choices.

The initial simplex is ``start`` plus one vertex per axis,
:math:`x_i = \text{start} + \delta e_i`.  Those :math:`n` edge vectors are
linearly independent, so the simplex spans :math:`\mathbb{R}^n`.  Every move
above is an affine combination of existing vertices, so a simplex that
starts degenerate (lower-dimensional) *stays* degenerate and can only search
that subspace.  The axis-aligned start is what lets the method explore every
coordinate.

Convergence is declared when the simplex is small in both senses: every
vertex lies within ``x_tolerance`` (max-norm) of the best one, and the
spread of objective values is below ``f_tolerance``.

Assumptions and scope
---------------------
Deterministic and NumPy-only.  This exists so that we don't add SciPy just
for one optimizer.  It is a local method with no convergence guarantee on
non-convex objectives, so callers that care use several starting points.
Callers own reparameterization: constraints are handled by optimizing over
unconstrained transformed coordinates, and an infeasible point should
evaluate to ``inf``.  Tolerances are in the caller's coordinates, so callers
should scale their parameters to order one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

Objective = Callable[[np.ndarray], float]


@dataclass(frozen=True)
class MinimizeResult:
    """Best point found, its objective value, and how the search ended."""

    x: np.ndarray
    value: float
    converged: bool
    iterations: int


def nelder_mead(
    objective: Objective,
    start: np.ndarray,
    max_iterations: int,
    initial_step: float = 0.35,
    x_tolerance: float = 1e-6,
    f_tolerance: float = 1e-8,
) -> MinimizeResult:
    """Minimize ``objective`` from ``start`` with the Nelder--Mead method.

    ``initial_step`` is the edge length :math:`\\delta` of the axis-aligned
    starting simplex, in the same (ideally order-one) coordinates as
    ``start``.
    """
    start = np.asarray(start, dtype=float)
    if start.ndim != 1 or len(start) == 0:
        raise ValueError("start must be a non-empty one-dimensional array")
    if not np.isfinite(start).all():
        raise ValueError("start must contain only finite values")
    if max_iterations < 1:
        raise ValueError(f"max_iterations must be >= 1, got {max_iterations}")
    if initial_step <= 0 or x_tolerance <= 0 or f_tolerance <= 0:
        raise ValueError("initial_step and tolerances must be positive")

    dimension = len(start)
    simplex = np.tile(start, (dimension + 1, 1))
    simplex[1:] += initial_step * np.eye(dimension)
    values = np.array([objective(point) for point in simplex])

    for iteration in range(1, max_iterations + 1):
        order = np.argsort(values)
        simplex, values = simplex[order], values[order]
        if (
            np.max(np.abs(simplex[1:] - simplex[0])) < x_tolerance
            and np.ptp(values) < f_tolerance
        ):
            return MinimizeResult(simplex[0], float(values[0]), True, iteration)

        centroid = simplex[:-1].mean(axis=0)
        reflected = centroid + (centroid - simplex[-1])
        reflected_value = objective(reflected)
        if values[0] <= reflected_value < values[-2]:
            simplex[-1], values[-1] = reflected, reflected_value
            continue
        if reflected_value < values[0]:
            expanded = centroid + 2.0 * (reflected - centroid)
            expanded_value = objective(expanded)
            simplex[-1], values[-1] = (
                (expanded, expanded_value) if expanded_value < reflected_value
                else (reflected, reflected_value)
            )
            continue
        contracted = centroid + 0.5 * (simplex[-1] - centroid)
        contracted_value = objective(contracted)
        if contracted_value < values[-1]:
            simplex[-1], values[-1] = contracted, contracted_value
            continue
        simplex[1:] = simplex[0] + 0.5 * (simplex[1:] - simplex[0])
        values[1:] = [objective(point) for point in simplex[1:]]

    best = int(np.argmin(values))
    return MinimizeResult(simplex[best], float(values[best]), False, max_iterations)
