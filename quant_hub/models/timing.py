r"""How well each trade was timed, against luck (TR4, D100).

The lots (``models/lots.py``) say what each trade made. This says how much
of that was the entry, the exit, the stock and the market, and whether the
days chosen were better than chance would give. Every measure uses daily
bars on the traded basis (split-adjusted) and the fills the history
quotes; nothing is intraday.

Notation: a piece of m units opened on day e at fill :math:`p_e` and closed
on day x at :math:`p_x`; :math:`C_t, H_t, L_t` the symbol's close, high
and low; σ = +1 for a long piece, −1 for a short; :math:`R^M_{e\to x}` the
benchmark's return; β the symbol's beta on the benchmark from up to 252
sessions before e (at least ``MIN_BETA_SESSIONS``; without them the
market and stock terms are left out, not guessed).

(a) Where each dollar came from
-------------------------------
An exact split of each piece, with no residual:

.. math::  \sigma m (p_x - p_e) = \underbrace{\sigma m (C_x - C_e)}_{\text{holding}}
           + \underbrace{\sigma m (C_e - p_e)}_{\text{entry fill}}
           + \underbrace{\sigma m (p_x - C_x)}_{\text{exit fill}}

and the holding term splits into the market, :math:`\sigma m C_e \beta
R^M_{e\to x}`, and the stock, what's left. The piece's realized P&L from
its cash (fees inside) minus :math:`\sigma m (p_x - p_e)` is its **costs**.
A fill more than ``SCALE_LIMIT`` from its day's close is on another price
scale (a split the bars adjusted for and the fill didn't), and the piece
is left out of (a), named.

(b) Which day you chose: the window rank
----------------------------------------
For a **decision** on day d (a day's fills of one symbol on one side merged
at their volume-weighted price), count the
**better days** W among the h closes on each side: closes below
:math:`C_d` for a buy, above it for a sell; a tie counts ½. W = 0 means the
best close in the window.

With no timing skill and a symmetric random walk, each side of the window
is a walk from :math:`C_d`, independent of the other, and the number of its
h points below the start has the discrete arcsine law (Sparre Andersen):

.. math::  P(K = k) = \binom{2k}{k}\binom{2h-2k}{h-k}\Big/4^{h}

so W's null law :math:`\pi_h` is that law convolved with itself. It is
exact, not uniform: at h = 10 a best-15% day (W ≤ 3) has probability
0.135. Over n decisions, :math:`S = \sum W_j` has the n-fold convolution
as its null, and the one-sided p is :math:`P(S \le s)`, small when the days
were good, with s rounded up when ties made it a half (conservative).
Entries and exits are tested apart. Decisions on different names the same
day aren't independent; the p is stated with that caveat. **In dollars:**
:math:`D = \sigma m (\bar C_{\text{window}} - C_d)`, what the day earned
against a random day in the window, with a null mean of zero.

(c) What happened after: markouts
---------------------------------
.. math::  M(k) = \sigma \frac{C_{d+k} - p}{p} - \sigma \beta R^M_{d\to d+k},
           \qquad k \in \{1, 5, 20\}

positive when the stock then moved the decision's way, beyond the market
(the raw move when β can't be estimated).
For an exit, a negative markout means it rose after you sold. Its null
mean is zero; the test is the exact two-sided sign test on the count of
positive markouts, entries and exits apart. Markouts more than k sessions
apart overlap less; those closer don't, which the count ignores.

(d) What the trade offered: MFE, MAE, capture
---------------------------------------------
For a long piece, from the entry day's **close** (the fill's time of day
is unknown, so a high before the fill isn't credited) over the sessions
after e through x:

.. math::  \text{MFE} = \max_t \frac{H_t}{C_e} - 1, \quad
           \text{MAE} = \min_t \frac{L_t}{C_e} - 1, \quad
           \text{capture} = \frac{C_x / C_e - 1}{\text{MFE}}\ (\text{MFE} > 0)

Shorts mirrored. Descriptive only: every trade has an MFE in hindsight.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from quant_hub.data.activity import Kind, Transaction
from quant_hub.models.lots import Piece

WINDOWS = (5, 10)
MARKOUTS = (1, 5, 20)
BETA_SESSIONS = 252
MIN_BETA_SESSIONS = 60
SCALE_LIMIT = 0.25
_ENTRIES = {Kind.BUY: 1, Kind.SHORT: -1}
_EXITS = {Kind.SELL: -1, Kind.COVER: 1}


@dataclass(frozen=True)
class PieceTiming:
    """(a) and (d) for one piece; ``reason`` says why a measure is missing."""

    piece: Piece
    entry_fill: float | None = None       # dollars
    exit_fill: float | None = None
    market: float | None = None
    stock: float | None = None
    costs: float | None = None
    mfe: float | None = None
    mae: float | None = None
    capture: float | None = None
    reason: str = ""


@dataclass(frozen=True)
class Decision:
    """One day's fills of one symbol on one side, merged (module docstring)."""

    symbol: str
    day: date
    side: int                             # +1 buys, −1 sells
    entry: bool                           # opens or adds (buy, short) vs closes (sell, cover)
    units: float
    fill: float                           # volume-weighted price
    better: dict[int, float] = field(default_factory=dict)      # h -> W
    dollars: dict[int, float] = field(default_factory=dict)     # h -> D
    markouts: dict[int, float] = field(default_factory=dict)    # k -> M(k)
    reason: str = ""


@dataclass(frozen=True)
class WindowTest:
    h: int
    n: int
    total: float                          # S
    p: float | None                       # P(S <= s) under no skill
    dollars: float


@dataclass(frozen=True)
class MarkoutTest:
    k: int
    n: int
    mean: float | None
    positive: int
    p: float | None                       # exact two-sided sign test
    dollars: float


@dataclass(frozen=True)
class TimingReport:
    pieces: tuple[PieceTiming, ...]
    decisions: tuple[Decision, ...]
    windows: dict[tuple[bool, int], WindowTest]      # (entry?, h)
    markouts: dict[tuple[bool, int], MarkoutTest]    # (entry?, k)

    def totals(self) -> dict[str, float]:
        """The P&L split summed over the pieces that have it."""
        out: dict[str, float] = defaultdict(float)
        for p in self.pieces:
            for name in ("entry_fill", "exit_fill", "market", "stock", "costs"):
                value = getattr(p, name)
                if value is not None:
                    out[name] += value
        return dict(out)


# ---------------------------------------------------------------- the nulls

def arcsine_pmf(h: int) -> np.ndarray:
    """The discrete arcsine law: points below the start among h steps."""
    return np.array([math.comb(2 * k, k) * math.comb(2 * h - 2 * k, h - k)
                     for k in range(h + 1)], dtype=float) / 4.0 ** h


def window_null(h: int) -> np.ndarray:
    """π_h: the null law of W, better days among 2h (module docstring)."""
    p = arcsine_pmf(h)
    return np.convolve(p, p)


def window_p(h: int, ws: Sequence[float]) -> float | None:
    """P(S ≤ s) for n decisions under no skill; s rounded up when it's a half."""
    if not ws:
        return None
    law = np.array([1.0])
    single = window_null(h)
    for _ in ws:
        law = np.convolve(law, single)
    s = math.ceil(sum(ws) - 1e-9)
    return float(min(1.0, law[: s + 1].sum()))


def sign_test(positive: int, n: int) -> float | None:
    """Exact two-sided binomial(n, ½) p for ``positive`` successes."""
    if n == 0:
        return None
    probs = [math.comb(n, i) / 2.0 ** n for i in range(n + 1)]
    observed = probs[positive]
    return float(min(1.0, sum(q for q in probs if q <= observed * (1 + 1e-12))))


# ------------------------------------------------------------ the measures

def beta(closes: pd.Series, market: pd.Series, before: date) -> float | None:
    """OLS slope of the symbol's daily returns on the benchmark's, over up
    to ``BETA_SESSIONS`` sessions before ``before``."""
    pair = pd.concat([closes, market], axis=1, join="inner").dropna()
    pair = pair.loc[pair.index < pd.Timestamp(before)].iloc[-(BETA_SESSIONS + 1):]
    r = pair.pct_change().dropna()
    if len(r) < MIN_BETA_SESSIONS:
        return None
    x, y = r.iloc[:, 1].to_numpy(), r.iloc[:, 0].to_numpy()
    var = float(np.var(x, ddof=1))
    return float(np.cov(x, y, ddof=1)[0, 1] / var) if var > 0 else None


def _at(frame: pd.DataFrame, day: date):
    ts = pd.Timestamp(day)
    return frame.loc[ts] if ts in frame.index else None


def piece_timing(piece: Piece, bars: pd.DataFrame | None,
                 market: pd.Series | None) -> PieceTiming:
    """(a) and (d) for one piece (module docstring)."""
    if piece.option:
        return PieceTiming(piece, reason="an option: no option bars")
    if bars is None:
        return PieceTiming(piece, reason="no bars for the symbol")
    e, x = _at(bars, piece.opened), _at(bars, piece.closed)
    if e is None or x is None:
        return PieceTiming(piece, reason="no bar on the entry or exit day")
    if piece.open_fill is None or piece.close_fill is None:
        return PieceTiming(piece, reason="no quoted fill")
    s = 1.0 if piece.side == "long" else -1.0
    ce, cx = float(e["close"]), float(x["close"])
    if abs(piece.open_fill / ce - 1) > SCALE_LIMIT or abs(piece.close_fill / cx - 1) > SCALE_LIMIT:
        return PieceTiming(piece, reason="fills on another price scale than the bars (a split?)")
    m = piece.units
    entry_fill = s * m * (ce - piece.open_fill)
    exit_fill = s * m * (piece.close_fill - cx)
    holding = s * m * (cx - ce)
    market_term = stock_term = None
    if market is not None:
        b = beta(bars["close"], market, piece.opened)
        me, mx = _at(market.to_frame("close"), piece.opened), _at(market.to_frame("close"),
                                                                  piece.closed)
        if b is not None and me is not None and mx is not None:
            market_term = s * m * ce * b * (float(mx["close"]) / float(me["close"]) - 1)
            stock_term = holding - market_term
    pnl = piece.pnl
    costs = None if pnl is None else pnl - s * m * (piece.close_fill - piece.open_fill)
    window = bars.loc[(bars.index > pd.Timestamp(piece.opened))
                      & (bars.index <= pd.Timestamp(piece.closed))]
    mfe = mae = capture = None
    if len(window):
        hi, lo = float(window["high"].max()), float(window["low"].min())
        if s > 0:
            mfe, mae, realized = hi / ce - 1, lo / ce - 1, cx / ce - 1
        else:
            mfe, mae, realized = 1 - lo / ce, 1 - hi / ce, 1 - cx / ce
        capture = realized / mfe if mfe > 0 else None
    if market_term is None:
        stock_term = None
    return PieceTiming(piece, entry_fill, exit_fill, market_term, stock_term, costs,
                       mfe, mae, capture)


def decisions_from(transactions: Iterable[Transaction],
                   options: frozenset[str] = frozenset()) -> list[Decision]:
    """Each day's fills of one symbol on one side, merged at their
    volume-weighted price (module docstring)."""
    groups: dict[tuple[str, date, int, bool], list[Transaction]] = defaultdict(list)
    for t in transactions:
        if t.option is not None or t.symbol in options or t.price is None:
            continue
        if t.kind in _ENTRIES:
            groups[(t.symbol, t.trade_date, _ENTRIES[t.kind], True)].append(t)
        elif t.kind in _EXITS:
            groups[(t.symbol, t.trade_date, _EXITS[t.kind], False)].append(t)
    out = []
    for (symbol, day, side, entry), fills in sorted(groups.items()):
        units = sum(abs(t.quantity) for t in fills)
        price = sum(abs(t.quantity) * t.price for t in fills) / units
        out.append(Decision(symbol, day, side, entry, units, price))
    return out


def decision_timing(d: Decision, bars: pd.DataFrame | None,
                    market: pd.Series | None) -> Decision:
    """(b) and (c) for one decision (module docstring)."""
    if bars is None:
        return _with(d, reason="no bars for the symbol")
    closes = bars["close"]
    ts = pd.Timestamp(d.day)
    if ts not in closes.index:
        return _with(d, reason="no bar on the day")
    i = closes.index.get_loc(ts)
    c = float(closes.iloc[i])
    if abs(d.fill / c - 1) > SCALE_LIMIT:
        return _with(d, reason="fill on another price scale than the bars (a split?)")
    better, dollars, markouts = {}, {}, {}
    for h in WINDOWS:
        if i - h < 0 or i + h >= len(closes):
            continue
        around = np.concatenate([closes.iloc[i - h:i].to_numpy(),
                                 closes.iloc[i + 1:i + h + 1].to_numpy()])
        worse_side = around < c if d.side > 0 else around > c
        better[h] = float(worse_side.sum() + 0.5 * (around == c).sum())
        dollars[h] = d.side * d.units * (float(around.mean()) - c)
    b = beta(closes, market, d.day) if market is not None else None
    for k in MARKOUTS:
        if i + k >= len(closes):
            continue
        moved = d.side * (float(closes.iloc[i + k]) - d.fill) / d.fill
        if market is not None and b is not None:
            mk = market.reindex(closes.index)
            m0, m1 = mk.iloc[i], mk.iloc[i + k]
            if pd.notna(m0) and pd.notna(m1):
                markouts[k] = moved - d.side * b * (float(m1) / float(m0) - 1)
        else:
            markouts[k] = moved
    reason = "" if better else f"fewer than {min(WINDOWS)} sessions on a side"
    return _with(d, better, dollars, markouts, reason)


def _with(d: Decision, better=None, dollars=None, markouts=None, reason="") -> Decision:
    return Decision(d.symbol, d.day, d.side, d.entry, d.units, d.fill, better or {},
                    dollars or {}, markouts or {}, reason)


def timing(pieces: Iterable[Piece], transactions: Iterable[Transaction],
           bars: Mapping[str, pd.DataFrame], market: pd.Series | None) -> TimingReport:
    """Every measure, and the tests over them (module docstring)."""
    piece_rows = tuple(piece_timing(p, bars.get(p.symbol), market) for p in pieces)
    decisions = tuple(decision_timing(d, bars.get(d.symbol), market)
                      for d in decisions_from(transactions))
    windows, markouts = {}, {}
    for entry in (True, False):
        group = [d for d in decisions if d.entry == entry]
        for h in WINDOWS:
            ws = [d.better[h] for d in group if h in d.better]
            windows[(entry, h)] = WindowTest(h, len(ws), float(sum(ws)), window_p(h, ws),
                                             float(sum(d.dollars[h] for d in group
                                                       if h in d.dollars)))
        for k in MARKOUTS:
            ms = [(d.markouts[k], d.units * d.fill) for d in group if k in d.markouts]
            positive = sum(1 for m, _ in ms if m > 0)
            markouts[(entry, k)] = MarkoutTest(
                k, len(ms), float(np.mean([m for m, _ in ms])) if ms else None, positive,
                sign_test(positive, len(ms)), float(sum(m * v for m, v in ms)))
    return TimingReport(piece_rows, decisions, windows, markouts)
