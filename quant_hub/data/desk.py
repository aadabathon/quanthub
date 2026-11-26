r"""The desk's record: what the models said, when, about what (D1, D96).

A **snapshot** is everything the desk's models said about one book on one
day, frozen. It's written so that a person or an LLM can read it, and so
that every number in it can later be checked against what happened. That
makes it objective in four ways:

* **Point in time.** ``as_of`` is the last day of data any model saw. A
  snapshot taken today about yesterday's close says so; nothing after
  ``as_of`` can leak in. When the units held are from a later day than the
  last prices (trades since the newest close), ``units_as_of`` says so.
* **Frozen.** A snapshot is serialized to canonical JSON and identified by
  its SHA-256. The store only appends. A decision cites the hash of the
  snapshot it was made on, so what the desk said can't be revised after
  the fact.
* **Falsifiable.** A model's view is a set of **claims**: forecasts of a
  named quantity over a named number of trading sessions, each resolving
  to one number on a known day. Prose ("why") explains a claim but is
  never scored.
* **Comparable.** Every claim on a target is scored the same way, and
  every model is scored against the same **baseline**, the no-edge model
  (``models/baseline.py``). A model is worth reading only where it beats
  the baseline.

Claims
------
A claim is about a **target** over a **horizon** h, in trading sessions
after ``as_of``:

=====================  =====================================================
target                 resolves to
=====================  =====================================================
``book.return``        the snapshot's book, units unchanged, cash at zero:
                       its value h sessions on, over its value at ``as_of``,
                       minus 1
``book.vol``           that book's realized volatility over the h sessions:
                       :math:`\sqrt{252 \cdot \tfrac1h \sum r_t^2}`, daily
                       log returns
``book.fall``          that book's deepest fall within the h sessions (D2b):
                       :math:`\max_{0 \le u \le v \le h} (1 - V_v / V_u)`,
                       with :math:`V_0` its value at ``as_of``, so a fall
                       from today's own level counts
``SYM.return``         the symbol's adjusted close h sessions on, over its
                       close at ``as_of``, minus 1
``SYM.vol``            the symbol's realized volatility, as for the book
``SYM.fall``           the symbol's deepest fall, as for the book, on its
                       adjusted closes
=====================  =====================================================

The book is the snapshot's own, held unchanged, so a claim about it
doesn't depend on later trades.

**The market** (D2b) is ``MARKET``, SPY: every snapshot prices it, held
or not, and records its close beside the book, so every voice can make
the same claims about it. It's the one target all voices share with the
voices that forecast the market itself, and β ties the book to it.

A claim takes one of two forms, each made for a proper scoring rule. A
proper rule is one whose expected score is best when the forecast is the
forecaster's true belief, so it can't be gamed by hedging:

* **Quantiles** at the levels in ``LEVELS``, nondecreasing. Scored by the
  pinball (quantile) loss, :math:`\sum_\tau (\mathbb 1\{y < q_\tau\} -
  \tau)(q_\tau - y)`.
* **A probability** of an event, a threshold on the target (``<= -0.10``,
  ``> 0.05``). Scored by the Brier score :math:`(p - \mathbb 1\{E\})^2`
  and the log score.

``models/scoring.py`` scores them once their day has passed (D4).

Decisions
---------
A **decision** is what you (or an LLM) decided on a snapshot: the trades,
the reasoning, and what you **expect**. The expectations are claims too,
in the same forms, so a decision is scored like a model, and your judgment
gets a record beside the models'.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from quant_hub.data.trading_calendar import trading_days

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
MARKET = "SPY"                     # the market every snapshot prices (module docstring)
TARGETS = ("return", "vol", "fall")
_EVENT_OPS = ("<=", ">=", "<", ">")


@dataclass(frozen=True)
class Claim:
    """A forecast of ``target`` over ``horizon`` sessions (module docstring):
    quantiles at ``LEVELS``, or the probability of ``event``."""

    target: str
    horizon: int
    quantiles: tuple[float, ...] = ()
    event: str = ""
    probability: float | None = None

    def __post_init__(self) -> None:
        if not self.target or "." not in self.target:
            raise ValueError(f"a claim's target is SYMBOL.return, book.vol and the like, "
                             f"not {self.target!r}")
        if self.target.rsplit(".", 1)[1] not in TARGETS:
            raise ValueError(f"{self.target}: only .return, .vol and .fall resolve")
        if self.horizon < 1:
            raise ValueError(f"{self.target}: the horizon is at least one session")
        has_quantiles, has_probability = bool(self.quantiles), self.probability is not None
        if has_quantiles == has_probability:
            raise ValueError(f"{self.target}: a claim is quantiles or a probability, one of them")
        if has_quantiles:
            if len(self.quantiles) != len(LEVELS) or not all(map(math.isfinite, self.quantiles)):
                raise ValueError(f"{self.target}: {len(LEVELS)} finite quantiles, at {LEVELS}")
            if any(b < a for a, b in zip(self.quantiles, self.quantiles[1:])):
                raise ValueError(f"{self.target}: quantiles can't decrease")
            if self.event:
                raise ValueError(f"{self.target}: quantiles have no event")
        else:
            if not 0.0 <= self.probability <= 1.0:
                raise ValueError(f"{self.target}: a probability is between 0 and 1")
            parse_event(self.event)

    @property
    def kind(self) -> str:
        return "quantiles" if self.quantiles else "probability"

    @property
    def key(self) -> str:
        """Unique within a voice: ``book.return@21`` or ``book.return@21 <= -0.1``."""
        base = f"{self.target}@{self.horizon}"
        return f"{base} {self.event}" if self.event else base


def parse_event(event: str) -> tuple[str, float]:
    """``"<= -0.10"`` → ``("<=", -0.1)``; a ValueError names what's wrong."""
    text = event.strip()
    op = next((o for o in _EVENT_OPS if text.startswith(o)), None)
    if op is None:
        raise ValueError(f"an event is one of {', '.join(_EVENT_OPS)} and a number, "
                         f"not {event!r}")
    try:
        return op, float(text[len(op):])
    except ValueError:
        raise ValueError(f"an event is one of {', '.join(_EVENT_OPS)} and a number, "
                         f"not {event!r}") from None


def resolves_on(as_of: date, horizon: int) -> date:
    """The day a claim over ``horizon`` sessions resolves: the horizon-th
    NYSE session after ``as_of``."""
    days = trading_days(as_of + timedelta(days=1), as_of + timedelta(days=2 * horizon + 14))
    return days[horizon - 1]


def event_happened(event: str, value: float) -> bool:
    op, threshold = parse_event(event)
    return {"<=": value <= threshold, ">=": value >= threshold,
            "<": value < threshold, ">": value > threshold}[op]


@dataclass(frozen=True)
class Holding:
    symbol: str
    units: float
    price: float                   # the close at as_of

    @property
    def value(self) -> float:
        return self.units * self.price


@dataclass(frozen=True)
class Book:
    """What the claims are about: priced holdings and cash at ``as_of``.
    Anything held but not priced (an option, a symbol without bars) is
    named in ``unpriced`` and left out, never guessed. ``market`` and
    ``market_price`` are the market's symbol and its close at ``as_of``
    (module docstring), or "" and None when it couldn't be priced."""

    as_of: date
    holdings: tuple[Holding, ...]
    cash: float = 0.0
    unpriced: tuple[str, ...] = ()
    units_as_of: date | None = None   # the day the units are from, when not as_of
    market: str = ""
    market_price: float | None = None

    @property
    def value(self) -> float:
        return sum(h.value for h in self.holdings) + self.cash

    def weights(self) -> dict[str, float]:
        total = self.value
        return {h.symbol: h.value / total for h in self.holdings} if total else {}


@dataclass(frozen=True)
class Proposal:
    """What a voice would hold instead: target weights of the book's value
    (cash is what's left). ``hold`` means no change."""

    hold: bool = True
    weights: Mapping[str, float] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class VoiceOutput:
    """One model's say: its claims (scored), its proposal, why (not
    scored), and the parameters it ran with."""

    voice: str
    version: str
    claims: tuple[Claim, ...]
    proposal: Proposal | None = None
    why: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        keys = [c.key for c in self.claims]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{self.voice}: each claim once")


@dataclass(frozen=True)
class Snapshot:
    taken_at: datetime
    as_of: date
    source: str                    # where the prices came from
    code_version: str
    book: Book
    voices: tuple[VoiceOutput, ...]

    def to_json(self) -> str:
        """Canonical JSON: sorted keys, no spaces, so one snapshot has one hash."""
        return json.dumps(_plain(self), sort_keys=True, separators=(",", ":"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    @staticmethod
    def from_json(text: str) -> Snapshot:
        d = json.loads(text)
        book = d["book"]
        return Snapshot(
            datetime.fromisoformat(d["taken_at"]), date.fromisoformat(d["as_of"]), d["source"],
            d["code_version"],
            Book(date.fromisoformat(book["as_of"]),
                 tuple(Holding(**h) for h in book["holdings"]), book["cash"],
                 tuple(book["unpriced"]),
                 date.fromisoformat(book["units_as_of"]) if book.get("units_as_of") else None,
                 book.get("market", ""), book.get("market_price")),
            tuple(_voice(v) for v in d["voices"]))


@dataclass(frozen=True)
class Outcome:
    """What happened to one subject (``book``, the market, a symbol) of one
    snapshot over one horizon (D4, ``models/scoring.py``): its return,
    realized volatility and deepest fall, or, when ``status`` is
    ``"unresolvable"``, why it can't be known."""

    snapshot_id: int
    subject: str
    horizon: int
    status: str                    # "resolved" or "unresolvable"
    ret: float | None
    vol: float | None
    fall: float | None
    reason: str
    source: str
    resolved_at: datetime

    def value(self, what: str) -> float | None:
        """The resolved number for a target's kind: return, vol or fall."""
        return {"return": self.ret, "vol": self.vol, "fall": self.fall}[what]


@dataclass(frozen=True)
class Decision:
    """What was decided on a snapshot (module docstring)."""

    made_at: datetime
    snapshot_sha: str
    author: str                    # "owner", "llm", or a name
    trades: tuple[tuple[str, float], ...]   # (symbol, units): + buys, − sells; () holds
    rationale: str
    expects: tuple[Claim, ...] = ()

    def __post_init__(self) -> None:
        if len(self.snapshot_sha) != 64:
            raise ValueError("a decision cites its snapshot's SHA-256")
        if not self.author:
            raise ValueError("a decision has an author")

    def to_json(self) -> str:
        return json.dumps(_plain(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def from_json(text: str) -> Decision:
        d = json.loads(text)
        return Decision(datetime.fromisoformat(d["made_at"]), d["snapshot_sha"], d["author"],
                        tuple((s, float(u)) for s, u in d["trades"]), d["rationale"],
                        tuple(_claim(c) for c in d["expects"]))


def _plain(obj: Any) -> Any:
    """Dataclasses, tuples, mappings and dates as JSON-ready values."""
    if hasattr(obj, "__dataclass_fields__"):
        return {name: _plain(getattr(obj, name)) for name in obj.__dataclass_fields__}
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Mapping):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, Sequence) and not isinstance(obj, str):
        return [_plain(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError("a snapshot holds finite numbers only")
    return obj


def _claim(d: dict) -> Claim:
    return Claim(d["target"], d["horizon"], tuple(d["quantiles"]), d["event"], d["probability"])


def _voice(d: dict) -> VoiceOutput:
    p = d["proposal"]
    proposal = Proposal(p["hold"], dict(p["weights"]), p["note"]) if p is not None else None
    return VoiceOutput(d["voice"], d["version"], tuple(_claim(c) for c in d["claims"]),
                       proposal, d["why"], dict(d["parameters"]))
