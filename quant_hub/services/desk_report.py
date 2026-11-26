r"""The desk as data, for a reader other than the Desk page (A1).

``python -m quant_hub.desk`` prints these as text for you, or as JSON with
``--json`` for an agent. Each function returns plain JSON-ready values
(dicts, lists, strings, finite numbers, None), so an agent reads the
numbers Quant Hub computed and tested instead of computing its own.

Units
-----
Every document carries ``schema`` and ``units``. A field's name says its
unit:

======================  =====================================================
field                   unit
======================  =====================================================
``*_usd``               dollars
``weight``              a fraction of the book's value V (holdings and cash)
``horizon``             trading sessions after the snapshot's ``as_of``
``*.return`` claims     a fraction: the value h sessions on over the value at
                        ``as_of``, minus 1
``*.vol`` claims        realized volatility over the h sessions, annualized,
                        a fraction (0.2 is 20% a year)
``*.fall`` claims       the deepest fall within the h sessions, a positive
                        fraction (0.1 is a 10% fall)
``probability``         0 to 1, of the claim's ``event``
dates                   ISO 8601; sessions are New York's
======================  =====================================================

**What every dollar figure assumes** (``models/comparison.py``): the book
is held unchanged over the horizon and its cash earns nothing, so a
``book.return`` quantile in dollars is V times the return's quantile.

**Scores** (``models/scoring.py``): lower is better (the quantile score,
the Brier score). ``skill`` is :math:`1 - \sum v / \sum b` against the
baseline on the same snapshots, so above 0 beats the no-edge model. ``p``
is two-sided, from non-overlapping claims only, and absent until there are
``MIN_WINDOWS`` of them.

Numbers are rounded to ``DIGITS`` significant figures: cents on a
five-figure book, and far finer than any claim's own error.

``SCHEMA`` goes up when a field changes meaning or goes away; a new field
doesn't change it.

Status
------
``status`` says whether the desk is current, and if not, why:

* **The last daily run** (``data/desk.Run``): when it ran, whether it
  worked. It's a problem when it failed, when none is recorded, or when it
  started before the last session (``last_session``): no run since that
  session closed.
* **The newest snapshot:** how many sessions it is behind the last session
  (a problem when any), and any holding it couldn't price (a problem; an
  option, which the desk doesn't price yet, is a note).
* **Voices that couldn't speak** in the last run: each is a gap in that
  voice's record.
* **The book:** the export it starts from, the history it's rolled forward
  through, and how many sessions ago that was (a note when any: trades
  since aren't in the book until their exports are imported), and any
  holding whose units changed since the snapshot (a note: the next run
  prices it).
* **The history** against the exports: a note when it doesn't reconcile.
  The book's units come from the newest export and the trades after it
  either way; its cash comes from the history only when the cash
  reconciles (``ActivityService.current_holdings``).
* **The backup:** the newest copy and its age. A problem when there's none.
* **The record:** what's resolved, what can't be, when the next claim is
  due.

**The last session** is the newest trading day before today: the newest
close a morning run can price. A snapshot as of today (IBKR after the
close) is current.

**A snapshot is cited by its stored hash** (``SnapshotSummary.sha256``),
the one decisions cite. A snapshot stored before a field was added (the
market, D2b) re-serializes with that field's default, so hashing the
loaded snapshot again would give another number.

Changes
-------
``changes`` sets two snapshots side by side: the book's value, cash, each
holding's units and price, the market, the regime, and every claim both
snapshots' voices made, with its change in its own unit (and in dollars
for ``book.return`` quantiles, at each snapshot's own V).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path

from quant_hub.data.desk import LEVELS, MARKET, Claim, Decision, Run, Snapshot
from quant_hub.data.literature import CanonPaper
from quant_hub.data.trading_calendar import is_trading_day, trading_days
from quant_hub.models import scoring
from quant_hub.models import tail as tail_model
from quant_hub.storage.desk_store import SnapshotSummary

SCHEMA = 1
UNITS = {
    "_usd": "dollars",
    "weight": "a fraction of the book's value V, holdings and cash",
    "horizon": "trading sessions after the snapshot's as_of",
    "return": "a fraction: the value h sessions on over the value at as_of, minus 1",
    "vol": "realized volatility over the h sessions, annualized, a fraction",
    "fall": "the deepest fall within the h sessions, a positive fraction",
    "probability": "0 to 1, of the claim's event",
    "assumes": "the book held unchanged over the horizon, its cash earning nothing",
    "score": "lower is better; skill = 1 - sum(score)/sum(baseline's), above 0 beats "
             "the baseline; p two-sided, non-overlapping claims only",
    "numbers": "rounded to 7 significant figures",
}
DIGITS = 7
# The claims on the book the text tables show, in this order: (target,
# horizon, level or event). The JSON carries every claim.
HEADLINE = (("book.return", 21, 0.05), ("book.return", 21, 0.5), ("book.return", 21, 0.95),
            ("book.return", 63, 0.05), ("book.return", 21, "<= -0.1"),
            ("book.fall", 63, ">= 0.1"), ("book.fall", 63, ">= 0.2"),
            ("book.fall", 252, ">= 0.2"), ("book.vol", 21, 0.5))


def _num(x: float | None) -> float | None:
    return float(x) if x is not None and math.isfinite(x) else None


def _day(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def _rounded(obj):
    """Every float to ``DIGITS`` significant figures, inside dicts and lists."""
    if isinstance(obj, float):
        return float(f"{obj:.{DIGITS}g}") if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _rounded(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_rounded(v) for v in obj]
    return obj


def document(kind: str, now: datetime, **fields) -> dict:
    """The envelope every report shares, its numbers rounded."""
    return {"schema": SCHEMA, "kind": kind, "now": now.replace(microsecond=0).isoformat(),
            "units": UNITS, **_rounded(fields)}


# ------------------------------------------------------------------ pieces
def last_session(today: date) -> date:
    """The newest trading day before ``today`` (module docstring)."""
    day = today - timedelta(days=1)
    while not is_trading_day(day):
        day -= timedelta(days=1)
    return day


def sessions_behind(as_of: date, today: date) -> int:
    """How many sessions after ``as_of`` have closed, up to the last session."""
    last = last_session(today)
    return len(trading_days(as_of + timedelta(days=1), last)) if as_of < last else 0


def ref(summary: SnapshotSummary, snapshot: Snapshot) -> dict:
    """Which snapshot: enough to cite it and check it later, by its stored
    hash (module docstring)."""
    return {"id": summary.id, "as_of": summary.as_of.isoformat(),
            "taken_at": summary.taken_at.isoformat(), "sha256": summary.sha256,
            "source": snapshot.source, "code_version": snapshot.code_version}


def claim(c: Claim, value: float | None = None) -> dict:
    """One claim, its unit named; a ``book.return`` quantile also in dollars
    at the book's value ``value``."""
    subject, _, what = c.target.rpartition(".")
    out = {"key": c.key, "target": c.target, "subject": subject, "what": what,
           "horizon": c.horizon, "unit": UNITS[what]}
    if c.quantiles:
        out["levels"] = list(LEVELS)
        out["quantiles"] = list(c.quantiles)
        if c.target == "book.return" and value:
            out["quantiles_usd"] = [value * q for q in c.quantiles]
    else:
        out["event"] = c.event
        out["probability"] = c.probability
    return out


def run(r: Run) -> dict:
    return {"started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat(),
            "ok": r.ok, "source": r.source, "snapshot_id": r.snapshot_id,
            "resolved": r.resolved, "pending": r.pending, "backup": r.backup or None,
            "problems": list(r.problems), "notes": list(r.notes)}


def book(snapshot: Snapshot, bands: Mapping[str, Sequence[float]] | None = None) -> dict:
    """The snapshot's book: what's held, at what price, as what share of V;
    β from the tail voice when it spoke; ``bands`` the baseline's 21-session
    dollar quantiles per holding."""
    b = snapshot.book
    value = b.value
    weights = b.weights()
    tail = next((v for v in snapshot.voices if v.voice == tail_model.NAME), None)
    beta = tail.parameters.get("beta") if tail is not None else None
    holdings = []
    for h in b.holdings:
        row = {"symbol": h.symbol, "units": h.units, "price_usd": h.price,
               "value_usd": h.value, "weight": _num(weights.get(h.symbol))}
        if bands and h.symbol in bands:
            row["band_21_usd"] = list(bands[h.symbol])
        holdings.append(row)
    return {"as_of": b.as_of.isoformat(), "units_as_of": _day(b.units_as_of),
            "value_usd": value, "cash_usd": b.cash,
            "cash_weight": _num(b.cash / value) if value else None,
            "holdings": holdings, "unpriced": list(b.unpriced),
            "market": {"symbol": b.market or MARKET, "price_usd": b.market_price},
            "beta": beta, "beta_se": tail.parameters.get("beta_se") if tail else None,
            "beta_from": tail_model.NAME if beta is not None else None}


def paper(p: CanonPaper) -> dict:
    return {"id": p.id, "title": p.title, "authors": list(p.authors), "year": p.year,
            "venue": p.venue, "doi": p.doi or None, "url": p.url or None}


def regime(label: str | None, snapshot: Snapshot,
           cutoffs: tuple[float, float] | None) -> dict:
    """The market's regime at the snapshot: its 63-session volatility
    (annualized) against the century's terciles."""
    base = next((v for v in snapshot.voices if v.voice == scoring.BASELINE), None)
    sigma = base.parameters.get("market_sigma_daily") if base is not None else None
    return {"label": label,
            "market_vol": _num(float(sigma) * math.sqrt(252)) if sigma is not None else None,
            "calm_below": cutoffs[0] if cutoffs else None,
            "stormy_from": cutoffs[1] if cutoffs else None}


def _headline_value(claims: Iterable[Claim], target: str, horizon: int, which,
                    value: float) -> float | None:
    """One headline cell: a quantile (dollars for ``book.return``) or a
    probability; None when the voice made no such claim."""
    for c in claims:
        if c.target != target or c.horizon != horizon:
            continue
        if isinstance(which, str):
            if c.event == which:
                return c.probability
        elif c.quantiles:
            q = c.quantiles[LEVELS.index(which)]
            return value * q if target == "book.return" else q
    return None


def headline(snapshot: Snapshot) -> list[dict]:
    """The text tables' rows: each ``HEADLINE`` claim, every voice's value."""
    value = snapshot.book.value
    rows = []
    for target, horizon, which in HEADLINE:
        values = {v.voice: _headline_value(v.claims, target, horizon, which, value)
                  for v in snapshot.voices}
        if any(x is not None for x in values.values()):
            rows.append({"target": target, "horizon": horizon,
                         "level": which if not isinstance(which, str) else None,
                         "event": which if isinstance(which, str) else None,
                         "usd": target == "book.return" and not isinstance(which, str),
                         "values": values})
    return rows


# ------------------------------------------------------------------ view
def view(v, now: datetime) -> dict:
    """``DeskService.view``'s ``DeskView`` as data: the Desk page."""
    snap = v.snapshot
    comparison = v.comparison
    value = snap.book.value
    proposed = {p.voice: p for p in comparison.proposals} if comparison else {}
    voices = []
    for out in snap.voices:
        p = proposed.get(out.voice)
        proposal = None
        if out.proposal is not None:
            proposal = {"hold": out.proposal.hold, "note": out.proposal.note,
                        "weights": dict(out.proposal.weights)}
            if p is not None:
                proposal.update({
                    "trades": [{"symbol": t.symbol, "usd": t.dollars, "units": t.units}
                               for t in p.trades],
                    "to_cash_usd": p.to_cash, "scale": p.scale})
        voices.append({"voice": out.voice, "version": out.version, "why": out.why,
                       "parameters": dict(out.parameters),
                       "claims": [claim(c, value) for c in out.claims],
                       "proposal": proposal,
                       "papers": [paper(x) for x in v.papers.get(out.voice, ())]})
    agreement, effects = [], []
    if comparison is not None:
        for a in comparison.across:
            low, high = a.low, a.high
            agreement.append({"target": a.target, "horizon": a.horizon, "kind": a.kind,
                              "unit": "dollars" if a.kind == "dollars" else
                              UNITS["probability"] if a.kind == "probability" else
                              UNITS[a.kind],
                              "level": a.level, "event": a.event or None,
                              "values": dict(a.values),
                              "low": {"voice": low[0], "value": low[1]},
                              "high": {"voice": high[0], "value": high[1]},
                              "apart": a.apart, "against_baseline": a.against()})
        effects = [{"proposal": e.proposal, "law": e.law, "horizon": e.horizon,
                    "levels": list(LEVELS), "held_usd": list(e.held),
                    "proposed_usd": list(e.proposed), "change_usd": list(e.change)}
                   for e in comparison.effects]
    cutoffs = v.record.cutoffs if v.record is not None else None
    summary = next(s for s in v.snapshots if s.id == v.id)
    return document("view", now, snapshot=ref(summary, snap),
                    regime=regime(v.regime, snap, cutoffs),
                    book=book(snap, v.holdings), voices=voices, agreement=agreement,
                    effects=effects, headline=headline(snap), problem=v.problem or None,
                    stored=[{"id": s.id, "as_of": s.as_of.isoformat()}
                            for s in v.snapshots[:10]])


# ------------------------------------------------------------------ changes
def changes(old_summary: SnapshotSummary, old: Snapshot, new_summary: SnapshotSummary,
            new: Snapshot, regimes: tuple[str | None, str | None], now: datetime) -> dict:
    """Two snapshots side by side (module docstring)."""
    ob, nb = old.book, new.book
    o_hold = {h.symbol: h for h in ob.holdings}
    n_hold = {h.symbol: h for h in nb.holdings}
    holdings = []
    for symbol in sorted(set(o_hold) | set(n_hold)):
        a, b = o_hold.get(symbol), n_hold.get(symbol)
        price = [a.price if a else None, b.price if b else None]
        holdings.append({
            "symbol": symbol, "units": [a.units if a else None, b.units if b else None],
            "traded": abs((a.units if a else 0.0) - (b.units if b else 0.0)) > 1e-9,
            "price_usd": price, "value_usd": [a.value if a else None, b.value if b else None],
            "price_change": _num(price[1] / price[0] - 1) if a and b and price[0] else None})
    market = None
    if ob.market_price and nb.market_price:
        market = {"symbol": nb.market or MARKET, "price_usd": [ob.market_price, nb.market_price],
                  "change": nb.market_price / ob.market_price - 1}
    o_voices = {v.voice: v for v in old.voices}
    n_voices = {v.voice: v for v in new.voices}
    rows = []
    for name, nv in n_voices.items():
        ov = o_voices.get(name)
        if ov is None:
            continue
        o_claims = {c.key: c for c in ov.claims}
        for c in nv.claims:
            before = o_claims.get(c.key)
            if before is None or before.kind != c.kind:
                continue
            base = {"voice": name, "key": c.key, "target": c.target, "horizon": c.horizon,
                    "unit": UNITS[c.target.rpartition(".")[2]]}
            if c.quantiles:
                for level, q0, q1 in zip(LEVELS, before.quantiles, c.quantiles):
                    row = {**base, "level": level, "old": q0, "new": q1, "change": q1 - q0}
                    if c.target == "book.return":
                        row.update({"old_usd": ob.value * q0, "new_usd": nb.value * q1,
                                    "change_usd": nb.value * q1 - ob.value * q0})
                    rows.append(row)
            else:
                rows.append({**base, "event": c.event, "unit": UNITS["probability"],
                             "old": before.probability, "new": c.probability,
                             "change": c.probability - before.probability})
    rows.sort(key=lambda r: (not r["target"].startswith("book."), r["target"], r["horizon"],
                             r.get("level") or 0.0, r.get("event") or "", r["voice"]))
    sessions = (len(trading_days(ob.as_of + timedelta(days=1), nb.as_of))
                if nb.as_of > ob.as_of else 0)
    return document(
        "changes", now, old=ref(old_summary, old), new=ref(new_summary, new),
        sessions=sessions,
        book={"value_usd": [ob.value, nb.value], "change_usd": nb.value - ob.value,
              "cash_usd": [ob.cash, nb.cash]},
        holdings=holdings, market=market, regime=list(regimes),
        voices={"gone": sorted(set(o_voices) - set(n_voices)),
                "new": sorted(set(n_voices) - set(o_voices)),
                "versions": {n: [o_voices[n].version, v.version] for n, v in n_voices.items()
                             if n in o_voices and o_voices[n].version != v.version}},
        claims=rows, headline=[headline(old), headline(new)])


# ------------------------------------------------------------------ record
def _test(t) -> dict | None:
    if t is None:
        return None
    return {"windows": t.n, "mean_difference": t.mean_difference, "t": t.t, "p": t.p,
            "reason": t.reason or None}


def _series(s) -> dict:
    return {"author": s.voice, "target": s.target, "horizon": s.horizon, "form": s.form,
            "regime": s.regime, "n": s.n, "score": s.score, "paired": s.paired,
            "baseline_score": s.baseline, "skill": s.skill, "test": _test(s.test),
            "versions": list(s.versions)}


def record(r, now: datetime, scored: bool = False) -> dict:
    """``DeskService.record``'s ``DeskRecord`` as data: every author's
    record (voices, you, an agent), overall and by regime, and calibration;
    with ``scored``, every scored claim too."""
    out = document(
        "record", now, resolved=r.resolved, unresolvable=r.unresolvable,
        next_due=_day(r.next_due),
        regimes={"calm_below": r.cutoffs[0], "stormy_from": r.cutoffs[1]} if r.cutoffs else None,
        series=[_series(s) for s in r.series],
        by_regime={k: [_series(s) for s in v] for k, v in r.by_regime.items()},
        calibration=[{"author": c.voice, "target": c.target, "horizon": c.horizon,
                      "form": c.form, "n": c.n, "windows": c.windows,
                      "coverage": (dict(zip((str(x) for x in LEVELS), c.coverage))
                                   if c.coverage is not None else None),
                      "claimed": c.claimed, "happened": c.happened} for c in r.calibration])
    if scored:
        out["scored"] = [{"snapshot_id": s.snapshot_id, "as_of": s.as_of.isoformat(),
                          "author": s.voice, "version": s.version, "claim": claim(s.claim),
                          "outcome": s.y, "score": s.score, "log_score": s.log_score,
                          "regime": s.regime} for s in r.scored]
    return out


def decisions(rows: Sequence[tuple[Decision, SnapshotSummary | None]], now: datetime) -> dict:
    """Recorded decisions, newest first, each with the snapshot it cites."""
    from quant_hub.data.desk import resolves_on
    out = []
    for d, s in rows:
        claims = []
        for c in d.expects:
            row = claim(c)
            row["resolves_on"] = resolves_on(s.as_of, c.horizon).isoformat() if s else None
            claims.append(row)
        out.append({"made_at": d.made_at.isoformat(), "author": d.author,
                    "snapshot": ({"id": s.id, "as_of": s.as_of.isoformat(), "sha256": s.sha256}
                                 if s else None),
                    "trades": [{"symbol": sym, "units": u} for sym, u in d.trades],
                    "why": d.rationale, "claims": claims})
    return document("decisions", now, decisions=out)


# ------------------------------------------------------------------ status
def status(desk, activity, backup_folder: Path | None, now: datetime) -> dict:
    """Whether the desk is current, and if not, why (module docstring).
    ``desk`` is the ``DeskService``, ``activity`` the ``ActivityService``
    (None without one)."""
    problems: list[str] = []
    notes: list[str] = []
    today = now.date()
    last = last_session(today)

    runs = desk.runs(1)
    last_run = runs[0] if runs else None
    if last_run is None:
        problems.append("no daily run recorded: schedule python -m quant_hub.snapshot")
    else:
        if not last_run.ok:
            problems.append(f"the last daily run ({last_run.started_at:%Y-%m-%d %H:%M}) failed: "
                            + "; ".join(last_run.problems))
        if last_run.started_at.date() < last:
            problems.append(f"no daily run since {last_run.started_at:%Y-%m-%d %H:%M}, before "
                            f"the {last} session")
        silent = [n for n in last_run.notes if n.startswith("silent: ")
                  and not n.startswith("silent: nothing new")]
        problems += [f"a voice couldn't speak: {n.removeprefix('silent: ')}" for n in silent]
        if last_run.pending:
            notes.append(f"{last_run.pending} claims due but not yet resolvable")

    summaries = desk.summaries()
    snap_out, snapshot = None, None
    if not summaries:
        problems.append("no snapshot yet")
    else:
        newest = summaries[0]
        snapshot = desk.snapshot(newest.id)
        behind = sessions_behind(snapshot.as_of, today)
        if behind:
            problems.append(f"snapshot #{newest.id} is as of {snapshot.as_of}, {behind} "
                            f"session{'s' if behind > 1 else ''} behind the last ({last})")
        for u in snapshot.book.unpriced:      # options aren't priced yet, by design: a note
            (notes if u.endswith("(an option)") else problems).append(
                f"not priced in #{newest.id}: {u}")
        snap_out = {**ref(newest, snapshot), "sessions_behind": behind,
                    "holdings": len(snapshot.book.holdings), "claims": newest.claims,
                    "voices": [v.voice for v in snapshot.voices],
                    "unpriced": list(snapshot.book.unpriced)}

    book_out = history_out = None
    if activity is not None:
        try:
            held = activity.current_holdings()
        except ValueError as exc:
            problems.append(f"no book: {exc}")
        else:
            changed = []
            if snapshot is not None:
                priced = {h.symbol: h.units for h in snapshot.book.holdings}
                left_out = {u.split(" ")[0] for u in snapshot.book.unpriced}
                for symbol in sorted(set(priced) | set(held.units)):
                    if symbol in held.options or symbol in left_out:
                        continue
                    before, units = priced.get(symbol, 0.0), held.units.get(symbol, 0.0)
                    if abs(units - before) > 1e-9:
                        changed.append({"symbol": symbol, "snapshot_units": before,
                                        "units": units})
                if changed:
                    notes.append(f"the book has changed since snapshot #{summaries[0].id} "
                                 f"({', '.join(c['symbol'] for c in changed)}); the next run "
                                 "prices it")
            age = sessions_behind(held.through, today)
            if age:
                notes.append(f"the book's units are as of {held.through}, {age} session"
                             f"{'s' if age > 1 else ''} ago: trades since aren't in it until "
                             "their exports are imported")
            book_out = {"export": held.base, "export_day": held.base_day.isoformat(),
                        "history_through": held.through.isoformat(),
                        "sessions_since_history": age, "cash_from": held.cash_from,
                        "changed_since_snapshot": changed}
        try:
            rec = activity.reconcile()
        except ValueError:
            rec = None
        if rec is not None and rec.last_day is not None:
            history_out = {"first_day": _day(rec.first_day), "last_day": _day(rec.last_day),
                           "reconciled": rec.reconciled, "cash_reconciled": rec.cash_reconciled}
            if not rec.reconciled:
                notes.append("the history doesn't reconcile with the exports (the Trades page "
                             "says where); the book's units are the newest export's, rolled "
                             "forward")

    backup_out = None
    if backup_folder is not None:
        copies = sorted(Path(backup_folder).glob("*.db")) if Path(backup_folder).is_dir() else []
        if not copies:
            problems.append(f"no backup in {backup_folder}")
        else:
            newest_copy = copies[-1]
            made = datetime.fromtimestamp(newest_copy.stat().st_mtime).replace(microsecond=0)
            backup_out = {"folder": str(backup_folder), "newest": str(newest_copy),
                          "made_at": made.isoformat(),
                          "age_hours": round((now - made).total_seconds() / 3600, 1),
                          "copies": len(copies)}

    rec = desk.record()
    record_out = {"resolved": rec.resolved, "unresolvable": rec.unresolvable,
                  "scored": len(rec.scored), "next_due": _day(rec.next_due)}
    return document("status", now, ok=not problems, problems=problems, notes=notes,
                    last_session=last.isoformat(),
                    run=run(last_run) if last_run else None, snapshot=snap_out, book=book_out,
                    history=history_out, backup=backup_out, record=record_out)
