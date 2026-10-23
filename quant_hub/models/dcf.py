r"""Discounted cash flow: the owner's DCF modeler, ported and checked (W4).

Ported from the owner's ``dcfmodeler`` (``engine``, ``assumptions``,
``sensitivity``, ``analysis``, and ``batch``'s seeding). The math was
derived in WORKSTATION_PLAN.md (approved 2026-10-20, D43); this docstring
restates it with the conventions the plan left open, each implemented as an
explicit choice whose default is the owner's original behavior.

Projection
----------
From the latest fiscal year's revenue :math:`\mathrm{Rev}_0`, for projection
years :math:`t = 1, \dots, N` with growth :math:`g_t`, EBIT margin
:math:`m_t`, tax rate :math:`\tau_t`, D&A :math:`d_t` and capex
:math:`c_t` (fractions of revenue), and working capital :math:`n_t`
(a fraction of the revenue *change*):

.. math::

    \mathrm{Rev}_t &= \mathrm{Rev}_{t-1}(1 + g_t)\\
    \mathrm{UFCF}_t &= m_t \mathrm{Rev}_t (1 - \tau_t) + d_t \mathrm{Rev}_t
                       - c_t \mathrm{Rev}_t - n_t(\mathrm{Rev}_t - \mathrm{Rev}_{t-1})
                     = k_t \mathrm{Rev}_t - n_t(\mathrm{Rev}_t - \mathrm{Rev}_{t-1}),

with the **cash margin** :math:`k_t = m_t(1 - \tau_t) + d_t - c_t`. A
negative EBIT earns a tax credit (the owner's convention: tax is
:math:`\tau_t` times EBIT, whatever its sign).

Discounting and the valuation date
----------------------------------
Cash flows arrive at fiscal year ends. Valued :math:`s` years after the
latest fiscal year end (:math:`0 \le s < 1`), each is discounted over the
time still to run:

.. math::  \mathrm{EV} = \sum_{t=1}^{N} \mathrm{UFCF}_t (1+W)^{-(t-s)}
           + \mathrm{TV}\,(1+W)^{-(N-s)} = \mathrm{EV}_{s=0}\,(1+W)^{s}.

:math:`s = 0` is the owner's convention: value as of the fiscal year end.
Comparing that with today's price understates value by the roll-forward
factor: 13 months after the year end at :math:`W = 9\%`, about 9.8%. The
net debt stays the balance sheet's (it is as stale as the filing).
:math:`s \ge 1` is refused: a year-1 flow would already be in the past, and
a newer filing is the remedy. ``years_since`` gives :math:`s` from the
period end and a date.

The terminal value (Gordon growth at :math:`g_T < W`)
-----------------------------------------------------
Two conventions for the flow one year past the horizon:

* **last-year** (the owner's, the default):
  :math:`\mathrm{UFCF}_{N+1} = \mathrm{UFCF}_N (1 + g_T)`. It carries year
  :math:`N`'s working-capital build at growth :math:`g_N`.
* **steady-state**: the year-:math:`N` ratios at growth :math:`g_T`,
  :math:`\mathrm{UFCF}_{N+1} = k_N \mathrm{Rev}_N (1 + g_T) - n_N g_T
  \mathrm{Rev}_N`.

They agree exactly when :math:`g_N = g_T`. Otherwise they differ: at 15%
explicit growth and :math:`g_T = 2.5\%` (the default ratios), the
steady-state flow is 7.9% higher, while the terminal value is 78% of EV.
Then :math:`\mathrm{TV} = \mathrm{UFCF}_{N+1}/(W - g_T)`.

The equity bridge
-----------------
:math:`\text{equity} = \mathrm{EV} - \text{net debt}`, net debt = total debt
less cash and equivalents (marketable securities not netted: the owner's
choice; ``net_marketable_securities`` nets them, and refuses loudly if the
filing lacks them), and value per share = equity / diluted shares. The three
required inputs (latest revenue, diluted shares, net debt) come from the
history or from overrides; a missing one raises ``ValuationError`` with the
source's recorded reason, never a guess.

Gordon identity (a closed-form check)
-------------------------------------
With constant ratios and :math:`g_t = g_T = g`,
:math:`\mathrm{UFCF}_t = \mathrm{Rev}_0 (1+g)^{t-1}\,[k + g(k - n)] =
\mathrm{UFCF}_1 (1+g)^{t-1}`. With :math:`\rho = (1+g)/(1+W)` the explicit
sum is :math:`\mathrm{UFCF}_1 (1 - \rho^N)/(W - g)` and the discounted
terminal value :math:`\mathrm{UFCF}_1 \rho^N/(W - g)` (both conventions
coincide here), so

.. math::  \mathrm{EV} = \frac{\mathrm{UFCF}_1}{W - g} \quad\text{for every } N.

Reverse DCF: every root
-----------------------
The implied growth replaces the growth path by a flat :math:`g` and solves
value per share = price; the implied WACC does the same in :math:`W`.

**Growth.** :math:`\mathrm{UFCF}_t = \mathrm{Rev}_0 (1+g)^{t-1}[k_t + g(k_t
- n_t)]`. If :math:`k_t \ge n_t \ge 0` every year, each factor is
nonnegative and nondecreasing for :math:`g > -1` (the bracket is at least
:math:`n_t`), and so is either terminal flow: value is nondecreasing in
:math:`g` and the root is unique (``growth_monotone``). If :math:`n_t >
k_t`, value can rise and then fall. At :math:`m = 20\%, \tau = 21\%, d = 4\%,
c = 5\%` (:math:`k = 14.8\%`) and :math:`n = 60\%`, it peaks near
:math:`g = 5\%`, and a $18 price has two implied growth rates, −14.9% and
+15.9%; the original bisection found neither, its endpoints not bracketing.

**WACC.** If every :math:`\mathrm{UFCF}_t` and the terminal value are
positive, each discounted term falls in :math:`W` on :math:`(g_T, \infty)`:
the root is unique (``cash_flows_positive``).

So both solvers scan a grid for sign changes and bisect each bracket,
returning every root. Two roots closer than one grid step (0.2 points) can
hide; the monotonicity check says when that cannot happen.

Seeding from history
--------------------
``calibration``: the revenue CAGR over the fiscal years on record, and the
average EBIT margin, capex and D&A shares of revenue, and effective tax rate
(years with positive pretax income only). ``assumptions_from_history``
seeds a trend-continuation base case from them (the owner's batch
screener), with the tax rate clamped to :math:`[0, 0.5]` against one-off
charges. WACC and terminal growth are capital-market inputs no filing holds,
so they stay configuration.

Units
-----
Monetary values in the reporting currency, absolute units; rates are
annual decimals (0.08 is 8%); value per share in currency per share.

What moved elsewhere
--------------------
* The edgartools adapter is replaced by W5's SEC ``companyfacts`` adapter;
  its tests' intent (debt sums its components including commercial paper,
  cash is the face line, prior periods resolve, a gap is a reason) carries
  into W5's tests.
* ``serialize`` (frames, CSV/xlsx) and ``rank_screen`` belong to W6's screen
  (CSV only, D43).
* The CLI and text renderers are not ported: the page renders.

Validation (tests)
------------------
The owner's engine, analysis and seeding tests, ported (the golden value
18.784 holds under the default conventions); the Gordon identity under both
terminal conventions; the steady-state flow against its closed form; the
two-root reverse DCF and the monotonicity condition; WACC uniqueness; the
valuation-date roll-forward :math:`(1+W)^s`; netting marketable securities;
the latest year chosen by fiscal year whatever the list order (the original
picked the oldest when its adapter appended newest first); and rejected
inputs.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date

import numpy as np

from quant_hub.data.fundamentals import FinancialHistory

PathLike = float | Sequence[float]
LAST_YEAR, STEADY_STATE = "last-year", "steady-state"
TERMINAL_METHODS = (LAST_YEAR, STEADY_STATE)
DAYS_PER_YEAR = 365.25                  # the stub between a fiscal year end and a date
GROWTH_RANGE = (-0.20, 0.60)            # the owner's bracket for the implied growth
WACC_RANGE = (0.04, 0.30)               # and for the implied WACC
WACC_FLOOR = 1e-3                       # keep W above g_T: the Gordon denominator > 0
SCAN_STEPS = 400                        # grid for sign changes: 0.2-point steps in growth
_ROOT_TOL = 1e-10


class ValuationError(Exception):
    """A required input is missing; the message lists each with its reason."""


def _expand(value: PathLike, n: int, name: str) -> list[float]:
    """One float per projection year from a scalar or a list of length n."""
    seq = [float(value)] * n if isinstance(value, (int, float)) else [float(x) for x in value]
    if len(seq) != n:
        raise ValueError(f"assumption '{name}' has {len(seq)} entries but projection_years={n}")
    if not all(math.isfinite(x) for x in seq):
        raise ValueError(f"assumption '{name}' must be finite")
    return seq


@dataclass(frozen=True)
class Assumptions:
    """The DCF's inputs (module docstring). Paths take a scalar for every
    year or one value per year. The defaults are the owner's illustrative
    ones, not recommendations."""

    projection_years: int = 5
    revenue_growth: PathLike = 0.05
    ebit_margin: PathLike = 0.20
    tax_rate: PathLike = 0.21
    da_pct_revenue: PathLike = 0.04
    capex_pct_revenue: PathLike = 0.05
    nwc_pct_revenue: PathLike = 0.10      # of the revenue change
    wacc: float = 0.09
    terminal_growth: float = 0.025
    terminal_method: str = LAST_YEAR
    valuation_years: float = 0.0          # s: years since the fiscal year end, [0, 1)
    net_marketable_securities: bool = False
    net_debt_override: float | None = None
    shares_override: float | None = None
    base_revenue_override: float | None = None

    def validate(self) -> None:
        n = self.projection_years
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError("projection_years must be an integer >= 1")
        if not (math.isfinite(self.wacc) and math.isfinite(self.terminal_growth)):
            raise ValueError("wacc and terminal_growth must be finite")
        if self.wacc <= -1:
            raise ValueError("wacc must exceed -100%")
        if self.terminal_growth >= self.wacc:
            raise ValueError(f"terminal_growth ({self.terminal_growth}) must be < wacc "
                             f"({self.wacc}); Gordon terminal value diverges otherwise")
        if self.terminal_method not in TERMINAL_METHODS:
            raise ValueError(f"terminal_method must be one of {TERMINAL_METHODS}")
        if not (math.isfinite(self.valuation_years) and 0 <= self.valuation_years < 1):
            raise ValueError("valuation_years must be in [0, 1): use a newer filing beyond that")
        for name in ("net_debt_override", "shares_override", "base_revenue_override"):
            value = getattr(self, name)
            if value is not None and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.shares_override is not None and self.shares_override <= 0:
            raise ValueError("shares_override must be positive")
        self.expanded()

    def expanded(self) -> dict[str, list[float]]:
        n = self.projection_years
        return {name: _expand(getattr(self, name), n, name) for name in (
            "revenue_growth", "ebit_margin", "tax_rate", "da_pct_revenue", "capex_pct_revenue",
            "nwc_pct_revenue")}


@dataclass(frozen=True)
class ProjectedYear:
    year: int
    revenue: float
    ebit: float
    tax: float
    nopat: float
    da: float
    capex: float
    delta_nwc: float
    ufcf: float
    discount_factor: float
    pv: float


@dataclass(frozen=True)
class ValuationResult:
    enterprise_value: float
    pv_explicit: float
    pv_terminal: float
    terminal_value: float
    terminal_flow: float                  # UFCF_{N+1} under the chosen convention
    net_debt: float
    equity_value: float
    shares: float
    value_per_share: float
    base_revenue: float
    projection: tuple[ProjectedYear, ...]
    assumptions: Assumptions

    @property
    def terminal_share(self) -> float:
        """PV of the terminal value over EV: the share of value past the horizon."""
        return self.pv_terminal / self.enterprise_value if self.enterprise_value else math.nan


def _required(hist: FinancialHistory, a: Assumptions) -> tuple[float, float, float]:
    """Base revenue, shares and net debt from overrides or the history, or a
    ``ValuationError`` naming every gap with the source's reason."""
    problems: list[str] = []
    latest = hist.latest()

    base_rev = a.base_revenue_override
    if base_rev is None:
        if latest is not None and latest.revenue is not None:
            base_rev = latest.revenue
        else:
            problems.append("base_revenue: " + hist.missing.get("revenue", "no latest revenue"))

    shares = a.shares_override
    if shares is None:
        if hist.shares_diluted is None:
            problems.append("shares: " + hist.missing.get("shares_diluted",
                                                          "no diluted share count"))
        elif hist.shares_diluted <= 0:
            problems.append(f"shares: the diluted share count is {hist.shares_diluted:g}")
        else:
            shares = hist.shares_diluted

    net_debt = a.net_debt_override
    if net_debt is None:
        if latest is not None and latest.total_debt is not None:
            net_debt = latest.total_debt - (latest.cash_and_equivalents or 0.0)
            if a.net_marketable_securities:
                if latest.marketable_securities is None:
                    problems.append("marketable_securities: " + hist.missing.get(
                        "marketable_securities", "not in the filing") + " (turn off netting "
                        "or override net debt)")
                else:
                    net_debt -= latest.marketable_securities
        else:
            problems.append("net_debt: " + hist.missing.get("total_debt",
                                                            "no total debt on latest BS"))

    if problems:
        raise ValuationError("cannot value -- missing required inputs:\n  - "
                             + "\n  - ".join(problems)
                             + "\n(supply *_override in Assumptions, or pick a cleaner filing)")
    return float(base_rev), float(shares), float(net_debt)


def value(hist: FinancialHistory, assumptions: Assumptions) -> ValuationResult:
    """Value per share by the module docstring's projection and bridge."""
    assumptions.validate()
    base_rev, shares, net_debt = _required(hist, assumptions)
    p = assumptions.expanded()
    wacc, g, s = assumptions.wacc, assumptions.terminal_growth, assumptions.valuation_years
    n = assumptions.projection_years

    projection: list[ProjectedYear] = []
    prev_rev = base_rev
    pv_explicit = 0.0
    for t in range(1, n + 1):
        i = t - 1
        rev = prev_rev * (1 + p["revenue_growth"][i])
        ebit = rev * p["ebit_margin"][i]
        tax = ebit * p["tax_rate"][i]
        nopat = ebit - tax
        da = rev * p["da_pct_revenue"][i]
        capex = rev * p["capex_pct_revenue"][i]
        dnwc = (rev - prev_rev) * p["nwc_pct_revenue"][i]
        ufcf = nopat + da - capex - dnwc
        df = (1 + wacc) ** -(t - s)
        pv_explicit += ufcf * df
        projection.append(ProjectedYear(t, rev, ebit, tax, nopat, da, capex, dnwc, ufcf, df,
                                        ufcf * df))
        prev_rev = rev

    last = projection[-1]
    if assumptions.terminal_method == LAST_YEAR:
        terminal_flow = last.ufcf * (1 + g)
    else:
        k = (p["ebit_margin"][-1] * (1 - p["tax_rate"][-1]) + p["da_pct_revenue"][-1]
             - p["capex_pct_revenue"][-1])
        terminal_flow = k * last.revenue * (1 + g) - p["nwc_pct_revenue"][-1] * g * last.revenue
    terminal_value = terminal_flow / (wacc - g)
    pv_terminal = terminal_value * (1 + wacc) ** -(n - s)
    ev = pv_explicit + pv_terminal
    equity = ev - net_debt
    return ValuationResult(ev, pv_explicit, pv_terminal, terminal_value, terminal_flow,
                           net_debt, equity, shares, equity / shares, base_rev,
                           tuple(projection), assumptions)


def years_since(period_end: str | date, on: date) -> float:
    """s for ``valuation_years``: years from a fiscal year end to ``on``."""
    end = date.fromisoformat(period_end) if isinstance(period_end, str) else period_end
    return (on - end).days / DAYS_PER_YEAR


# ------------------------------------------------------------------ sensitivity
@dataclass(frozen=True)
class SensitivityTable:
    """Value per share over a WACC x terminal-growth grid; ``grid[i, j]`` at
    ``waccs[i]``, ``growths[j]``, NaN where g >= WACC."""

    waccs: np.ndarray
    growths: np.ndarray
    grid: np.ndarray


def sensitivity(hist: FinancialHistory, base: Assumptions, waccs: Sequence[float],
                growths: Sequence[float]) -> SensitivityTable:
    """The spread across plausible (W, g): a point estimate alone is false
    precision. Missing data raises once, rather than blanking every cell."""
    grid = np.full((len(waccs), len(growths)), np.nan)
    for i, w in enumerate(waccs):
        for j, g in enumerate(growths):
            try:
                grid[i, j] = value(hist, replace(base, wacc=w, terminal_growth=g)).value_per_share
            except ValueError:
                continue                  # e.g. g >= wacc
    return SensitivityTable(np.asarray(waccs, dtype=float), np.asarray(growths, dtype=float),
                            grid)


# ------------------------------------------------------------- analysis outputs
@dataclass(frozen=True)
class ForwardMultiples:
    """Year-1 multiples implied by the model's EV and equity; None when the
    denominator is zero."""

    ev_revenue: float | None
    ev_ebit: float | None
    fcf_yield_ev: float | None
    fcf_yield_equity: float | None
    price_nopat: float | None             # model equity over year-1 NOPAT


def multiples(result: ValuationResult) -> ForwardMultiples:
    y1 = result.projection[0]
    ev, eq = result.enterprise_value, result.equity_value

    def ratio(num: float, den: float) -> float | None:
        return num / den if den else None
    return ForwardMultiples(ratio(ev, y1.revenue), ratio(ev, y1.ebit), ratio(y1.ufcf, ev),
                            ratio(y1.ufcf, eq), ratio(eq, y1.nopat))


@dataclass(frozen=True)
class MarketComparison:
    price: float
    value_per_share: float
    upside: float                         # value / price - 1
    margin_of_safety: float               # 1 - price / value; > 0: below intrinsic
    market_cap: float
    model_equity: float


def market_comparison(result: ValuationResult, price: float) -> MarketComparison:
    if not (math.isfinite(price) and price > 0):
        raise ValueError("price must be positive")
    v = result.value_per_share
    return MarketComparison(price, v, v / price - 1, 1 - price / v if v else math.nan,
                            price * result.shares, result.equity_value)


def growth_monotone(base: Assumptions) -> bool:
    """k_t >= n_t >= 0 every year: value is then nondecreasing in a flat
    growth rate, and the implied growth is unique (module docstring)."""
    p = base.expanded()
    for m, tax, d, c, n in zip(p["ebit_margin"], p["tax_rate"], p["da_pct_revenue"],
                               p["capex_pct_revenue"], p["nwc_pct_revenue"]):
        if not (m * (1 - tax) + d - c >= n >= 0):
            return False
    return True


def cash_flows_positive(result: ValuationResult) -> bool:
    """Every UFCF and the terminal value positive: the implied WACC is then unique."""
    return all(y.ufcf > 0 for y in result.projection) and result.terminal_value > 0


def _roots(f: Callable[[float], float], lo: float, hi: float, steps: int) -> tuple[float, ...]:
    """Every root of f on [lo, hi]: sign changes on a grid, each bisected.
    Points where f is undefined (a ValueError) break the scan, not the call."""
    if not (lo < hi) or steps < 2:
        raise ValueError("need lo < hi and at least two steps")

    def safe(x: float) -> float:
        try:
            return float(f(x))
        except (ValueError, ZeroDivisionError):
            return math.nan
    xs = np.linspace(lo, hi, steps + 1)
    ys = [safe(x) for x in xs]
    roots: list[float] = []
    for x0, x1, y0, y1 in zip(xs[:-1], xs[1:], ys[:-1], ys[1:]):
        if not (math.isfinite(y0) and math.isfinite(y1)):
            continue
        if y0 == 0:
            roots.append(float(x0))
        elif y0 * y1 < 0:
            a, b, fa = float(x0), float(x1), y0
            for _ in range(200):
                mid = 0.5 * (a + b)
                fm = safe(mid)
                if fm == 0 or (b - a) < _ROOT_TOL:
                    break
                if (fm > 0) == (fa > 0):
                    a, fa = mid, fm
                else:
                    b = mid
            roots.append(0.5 * (a + b))
    if ys and math.isfinite(ys[-1]) and ys[-1] == 0:
        roots.append(float(xs[-1]))
    unique: list[float] = []
    for r in sorted(roots):
        if not unique or r - unique[-1] > 1e-8:
            unique.append(r)
    return tuple(unique)


def implied_growth(hist: FinancialHistory, base: Assumptions, price: float,
                   lo: float = GROWTH_RANGE[0], hi: float = GROWTH_RANGE[1],
                   steps: int = SCAN_STEPS) -> tuple[float, ...]:
    """Every flat revenue growth that makes value per share equal ``price``
    (others fixed): one root when ``growth_monotone``; empty if none in range."""
    return _roots(lambda x: value(hist, replace(base, revenue_growth=x)).value_per_share - price,
                  lo, hi, steps)


def implied_wacc(hist: FinancialHistory, base: Assumptions, price: float,
                 lo: float = WACC_RANGE[0], hi: float = WACC_RANGE[1],
                 steps: int = SCAN_STEPS) -> tuple[float, ...]:
    """Every WACC that makes value per share equal ``price`` (others fixed),
    kept above terminal growth: one root when ``cash_flows_positive``."""
    lo = max(lo, base.terminal_growth + WACC_FLOOR)
    return _roots(lambda w: value(hist, replace(base, wacc=w)).value_per_share - price,
                  lo, hi, steps)


# --------------------------------------------------------- seeding from history
@dataclass(frozen=True)
class Calibration:
    """Trailing averages from the filings, to seed assumptions (module
    docstring); None where the history can't say."""

    revenue_cagr: float | None
    ebit_margin: float | None
    capex_pct_revenue: float | None
    da_pct_revenue: float | None
    tax_rate: float | None


def calibration(hist: FinancialHistory) -> Calibration:
    years = [y for y in hist.ordered() if y.revenue]
    if len(years) < 2:
        return Calibration(None, None, None, None, None)
    first, last = years[0], years[-1]
    span = last.fiscal_year - first.fiscal_year or len(years) - 1
    cagr = None
    if first.revenue > 0 and last.revenue > 0:          # a CAGR through a sign change is meaningless
        cagr = (last.revenue / first.revenue) ** (1 / span) - 1

    def average(values: list[float | None]) -> float | None:
        kept = [v for v in values if v is not None]
        return sum(kept) / len(kept) if kept else None
    return Calibration(
        cagr,
        average([y.operating_income / y.revenue for y in years if y.operating_income]),
        average([y.capex / y.revenue for y in years if y.capex]),
        average([y.depreciation_amortization / y.revenue for y in years
                 if y.depreciation_amortization]),
        average([y.effective_tax_rate() for y in years]))


@dataclass(frozen=True)
class BatchConfig:
    """The owner's screen settings: capital-market inputs and fallbacks for
    calibration fields a filing lacks (illustrative defaults)."""

    wacc: float = 0.09
    terminal_growth: float = 0.025
    projection_years: int = 5
    nwc_pct_revenue: float = 0.0
    default_growth: float = 0.03
    default_margin: float = 0.15
    default_tax: float = 0.21
    default_capex_pct: float = 0.05
    default_da_pct: float = 0.04
    tax_clamp: float = 0.50               # cap a seeded tax rate (one-off charges)


def assumptions_from_history(hist: FinancialHistory,
                             cfg: BatchConfig = BatchConfig()) -> Assumptions:
    """A trend-continuation base case from the filer's own averages, falling
    back to ``cfg`` where a field didn't resolve (module docstring)."""
    c = calibration(hist)

    def pick(v: float | None, default: float) -> float:
        return v if v is not None and math.isfinite(v) else default
    tax = min(max(pick(c.tax_rate, cfg.default_tax), 0.0), cfg.tax_clamp)
    return Assumptions(
        projection_years=cfg.projection_years,
        revenue_growth=pick(c.revenue_cagr, cfg.default_growth),
        ebit_margin=pick(c.ebit_margin, cfg.default_margin),
        tax_rate=tax,
        capex_pct_revenue=pick(c.capex_pct_revenue, cfg.default_capex_pct),
        da_pct_revenue=pick(c.da_pct_revenue, cfg.default_da_pct),
        nwc_pct_revenue=cfg.nwc_pct_revenue,
        wacc=cfg.wacc,
        terminal_growth=cfg.terminal_growth)
