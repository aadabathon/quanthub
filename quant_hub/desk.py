"""Read the desk from the command line: for you, or for an agent (A1).

    python -m quant_hub.desk status   [--json]
    python -m quant_hub.desk view     [--snapshot N] [--json]
    python -m quant_hub.desk changes  [--since N | --since YYYY-MM-DD] [--json]
    python -m quant_hub.desk record   [--scored] [--json]
    python -m quant_hub.desk scorecard [--json]
    python -m quant_hub.desk whatif   --trade "SYM +units" [--trade "SYM -$500" ...]
                                      [--snapshot N] [--json]

* ``status``: whether the desk is current: the last daily run, the newest
  snapshot's age, the book's export and history, the backup, the record.
  It exits 1 when anything is wrong, and says what first.
* ``view``: the Desk page as data: the book, the regime, every voice's
  claims in fractions and dollars, where they agree and disagree, each
  proposal, each voice's why and papers.
* ``changes``: the newest snapshot against an earlier one (the one before,
  or the newest on or before a date; a week back gives the week).
* ``record``: every author's record, the voices', yours and an agent's,
  overall and by regime; ``--scored`` adds every scored claim.
* ``scorecard``: you and an agent, each against the baseline and head to
  head with every voice and each other, on the claims you share, overall
  and by regime (``models/scoring.py`` section 8).
* ``whatif``: the trades you're weighing, at the snapshot's closes, and
  every voice's claims on the book after them beside the book as held, in
  dollars (``models/weighing.py``). Nothing is stored.

Text is for reading; ``--json`` is for an agent, every unit named
(``services/desk_report.py``). Everything here only reads: nothing is
stored, and nothing trades. Decisions are recorded with ``python -m
quant_hub.decide``.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from quant_hub.core import build_core
from quant_hub.models.weighing import parse_move
from quant_hub.services import desk_report as report
from quant_hub.storage.desk_store import DeskStoreError


# ------------------------------------------------------------------ text
def _usd(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "-"
    sign = "-" if x < 0 else ("+" if signed else "")
    return f"{sign}${abs(x):,.0f}"


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "-"
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def _row_label(row: dict) -> str:
    what = row["target"].removeprefix("book.")
    if row["event"] is not None:
        return f"{what}@{row['horizon']} {row['event']}"
    return f"{what}@{row['horizon']} {row['level']:.0%}"


def _cell(row: dict, x: float | None, change: float | None = None) -> str:
    if x is None:
        return "-"
    if row["usd"]:
        text = _usd(x)
        return text + (f" ({_usd(change, True)})" if change is not None else "")
    if row["event"] is not None:
        return f"p {x:.2f}" + (f" ({change:+.2f})" if change is not None else "")
    return _pct(x) + (f" ({_pct(change, True)})" if change is not None else "")


def _table(rows: list[dict], voices: list[str], before: list[dict] | None = None) -> list[str]:
    width = 20 if before else 12
    lines = ["  " + "claims on the book".ljust(22) + "".join(v.rjust(width) for v in voices)]
    old = {(_row_label(r)): r for r in before} if before else {}
    for row in rows:
        prior = old.get(_row_label(row))
        cells = []
        for v in voices:
            x = row["values"].get(v)
            was = prior["values"].get(v) if prior else None
            change = x - was if before and x is not None and was is not None else None
            cells.append(_cell(row, x, change).rjust(width))
        lines.append("  " + _row_label(row).ljust(22) + "".join(cells))
    return lines


def text_status(doc: dict) -> str:
    problems = doc["problems"]
    lines = [f"desk status at {doc['now'][:16].replace('T', ' ')} (last session "
             f"{doc['last_session']}): "
             + (f"{len(problems)} problem{'s' if len(problems) != 1 else ''}"
                if problems else "current")]
    lines += [f"  ! {p}" for p in problems] + [f"  - {n}" for n in doc["notes"]]
    if doc["run"]:
        r = doc["run"]
        lines.append(f"run       {r['started_at'][:16].replace('T', ' ')} to "
                     f"{r['finished_at'][11:16]}: {'worked' if r['ok'] else 'FAILED'}; "
                     + (f"snapshot #{r['snapshot_id']}" if r["snapshot_id"] else "no snapshot")
                     + f", {r['resolved']} resolved, "
                     f"{r['pending']} pending" + ("" if r["backup"] else ", no backup"))
    if doc["snapshot"]:
        s = doc["snapshot"]
        lines.append(f"snapshot  #{s['id']} as of {s['as_of']} from {s['source']}: "
                     f"{s['holdings']} holdings, {len(s['voices'])} voices, {s['claims']} "
                     f"claims ({s['sha256'][:12]})")
    if doc["book"]:
        b = doc["book"]
        lines.append(f"book      {b['export']} of {b['export_day']}, history to its last "
                     f"trade {b['history_through']}, imported {b['history_imported'] or '-'}; "
                     f"current to {b['current_to']} ({b['sessions_since']} sessions ago), "
                     f"cash from the {b['cash_from']}")
    if doc["history"]:
        h = doc["history"]
        lines.append(f"history   {h['first_day']} to {h['last_day']}: "
                     f"{'reconciled' if h['reconciled'] else 'NOT reconciled'}, cash "
                     f"{'reconciled' if h['cash_reconciled'] else 'not reconciled'}")
    if doc["backup"]:
        k = doc["backup"]
        lines.append(f"backup    {Path(k['newest']).name}, {k['age_hours']} hours old, "
                     f"{k['copies']} cop{'ies' if k['copies'] != 1 else 'y'}")
    rec = doc["record"]
    lines.append(f"record    {rec['resolved']} resolved, {rec['unresolvable']} unresolvable, "
                 f"{rec['scored']} claims scored" +
                 (f"; next due {rec['next_due']}" if rec["next_due"] else ""))
    return "\n".join(lines)


def text_view(doc: dict) -> str:
    s, b, g = doc["snapshot"], doc["book"], doc["regime"]
    lines = [f"snapshot #{s['id']} as of {s['as_of']} ({s['source']}, {s['sha256'][:12]})"
             + (f"; regime {g['label']}" if g["label"] else "")
             + (f" (market vol {_pct(g['market_vol'])})" if g["market_vol"] else "")]
    lines.append(f"book {_usd(b['value_usd'])}: {len(b['holdings'])} holdings, cash "
                 f"{_usd(b['cash_usd'])} ({_pct(b['cash_weight'])})"
                 + (f", beta {b['beta']:.2f}" if b["beta"] is not None else ""))
    for h in sorted(b["holdings"], key=lambda h: -abs(h["value_usd"])):
        lines.append(f"  {h['symbol']:<8}{h['units']:>12,.2f} x {h['price_usd']:>10,.2f} = "
                     f"{_usd(h['value_usd']):>10}  {_pct(h['weight']):>6}")
    lines += [f"  not priced: {u}" for u in b["unpriced"]]
    voices = [v["voice"] for v in doc["voices"]]
    lines += _table(doc["headline"], voices)
    for v in doc["voices"]:
        p = v["proposal"]
        if p and not p["hold"]:
            lines.append(f"  {v['voice']} proposes: {p['note']}"
                         + (f" ({_usd(p.get('to_cash_usd'), True)} to cash)"
                            if p.get("to_cash_usd") is not None else ""))
    if doc["problem"]:
        lines.append(f"  not in dollars: {doc['problem']}")
    return "\n".join(lines)


def text_changes(doc: dict) -> str:
    o, n, b = doc["old"], doc["new"], doc["book"]
    lines = [f"#{o['id']} (as of {o['as_of']}) -> #{n['id']} (as of {n['as_of']}), "
             f"{doc['sessions']} session{'s' if doc['sessions'] != 1 else ''}",
             f"book      {_usd(b['value_usd'][0])} -> {_usd(b['value_usd'][1])} "
             f"({_usd(b['change_usd'], True)}); cash {_usd(b['cash_usd'][0])} -> "
             f"{_usd(b['cash_usd'][1])}"]
    if doc["market"]:
        m = doc["market"]
        lines.append(f"market    {m['symbol']} {m['price_usd'][0]:,.2f} -> "
                     f"{m['price_usd'][1]:,.2f} ({_pct(m['change'], True)})")
    if doc["regime"][0] or doc["regime"][1]:
        lines.append(f"regime    {doc['regime'][0] or '-'} -> {doc['regime'][1] or '-'}")
    for h in doc["holdings"]:
        units = h["units"]
        traded = (f"{units[0] or 0:,.2f} -> {units[1] or 0:,.2f} units" if h["traded"]
                  else f"{units[1]:,.2f} units")
        price = h["price_usd"]
        moved = (f", {price[0]:,.2f} -> {price[1]:,.2f} ({_pct(h['price_change'], True)})"
                 if h["price_change"] is not None else "")
        lines.append(f"  {h['symbol']:<8}{traded}{moved}")
    voices = list(doc["headline"][1][0]["values"]) if doc["headline"][1] else []
    lines += _table(doc["headline"][1], voices, doc["headline"][0])
    gone, new = doc["voices"]["gone"], doc["voices"]["new"]
    if gone or new:
        lines.append(f"  voices: {', '.join(new) or 'none'} new; {', '.join(gone) or 'none'} gone")
    return "\n".join(lines)


def text_record(doc: dict) -> str:
    regimes = doc["regimes"]
    lines = [f"record: {doc['resolved']} resolved, {doc['unresolvable']} unresolvable"
             + (f"; next due {doc['next_due']}" if doc["next_due"] else "")
             + (f"; regimes: calm below {_pct(regimes['calm_below'])}, stormy from "
                f"{_pct(regimes['stormy_from'])}" if regimes else "")]
    if not doc["series"]:
        lines.append("  nothing scored yet")
        return "\n".join(lines)
    lines.append(f"  {'author':<11}{'claim':<24}{'form':<11}{'n':>4}{'score':>9}"
                 f"{'baseline':>10}{'skill':>8}{'p':>7}")
    for s in doc["series"]:
        test = s["test"] or {}
        lines.append(f"  {s['author']:<11}{s['target'] + '@' + str(s['horizon']):<24}"
                     f"{s['form']:<11}{s['n']:>4}{s['score']:>9.4f}"
                     + (f"{s['baseline_score']:>10.4f}" if s["baseline_score"] is not None
                        else f"{'-':>10}")
                     + (f"{s['skill']:>+8.1%}" if s["skill"] is not None else f"{'-':>8}")
                     + (f"{test['p']:>7.2f}" if test.get("p") is not None else f"{'-':>7}"))
    return "\n".join(lines)


def text_scorecard(doc: dict) -> str:
    lines = [f"scorecard: {doc['resolved']} resolved, {doc['unresolvable']} unresolvable"
             + (f"; next due {doc['next_due']}" if doc["next_due"] else "")]
    if not doc["judges"]:
        lines.append("  no decision scored yet: yours and the agent's claims appear here "
                     "once they resolve")
        return "\n".join(lines)

    def p(test: dict | None) -> str:
        return f"p {test['p']:.2f}" if test and test.get("p") is not None else "p -"
    for judge in doc["judges"]:
        lines.append(f"{judge}:")
        for s in (s for s in doc["against_baseline"] if s["author"] == judge):
            skill = f"{s['skill']:+.1%}" if s["skill"] is not None else "-"
            lines.append(f"  {s['target']}@{s['horizon']} {s['form']}: vs baseline {skill} "
                         f"on {s['paired']} ({p(s['test'])})")
            for d in (d for d in doc["head_to_head"] if d["author"] == judge
                      and (d["target"], d["horizon"], d["form"])
                      == (s["target"], s["horizon"], s["form"])
                      and d["against"] != "baseline"):
                dskill = f"{d['skill']:+.1%}" if d["skill"] is not None else "-"
                lines.append(f"      vs {d['against']:<10} {dskill} on {d['paired']}, "
                             f"wins {d['wins']}/{d['paired']} ({p(d['test'])})")
    return "\n".join(lines)


def text_whatif(doc: dict) -> str:
    s, b, a = doc["snapshot"], doc["before"], doc["after"]
    lines = [f"what-if on snapshot #{s['id']} as of {s['as_of']} ({s['sha256'][:12]}), "
             "at that day's closes"]
    for t in doc["trades"]:
        lines.append(f"  {'buy' if t['units'] > 0 else 'sell'} {abs(t['units']):,.2f} "
                     f"{t['symbol']} @ {t['price_usd']:,.2f} = {_usd(t['usd'], True)}")
    beta = (f"; beta {b['beta']:.2f} -> {a['beta']:.2f}"
            if b["beta"] is not None and a["beta"] is not None else "")
    lines.append(f"book {_usd(a['value_usd'])} either way; cash {_usd(b['cash_usd'])} -> "
                 f"{_usd(a['cash_usd'])} ({_pct(b['cash_weight'])} -> "
                 f"{_pct(a['cash_weight'])}){beta}")
    if doc["shorts"]:
        lines.append(f"  ! short after: {', '.join(doc['shorts'])}")
    if doc["margin"]:
        lines.append("  ! cash below zero: a loan, and its interest isn't in the claims")
    voices = list(doc["headline"][1][0]["values"]) if doc["headline"][1] else []
    lines += _table(doc["headline"][1], voices, doc["headline"][0])
    lines.append("  (after, and the change from the book as held)")
    if not doc["reproduces"]:
        lines.append(f"  the book as held, re-run, differs from #{s['id']}'s claims for "
                     f"{', '.join(doc['differs'])}: data or a version has moved since")
    lines += [f"  silent on the book after: {v}" for v in doc["silent"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ main
def _since(desk, since: str | None, newest_id: int) -> int:
    """The earlier snapshot's id: the one before the newest, a number, or the
    newest on or before a date."""
    summaries = desk.summaries()
    if since is None:
        if len(summaries) < 2:
            raise ValueError("only one snapshot is stored: nothing to compare yet")
        return summaries[1].id
    if since.isdigit():
        return int(since)
    day = date.fromisoformat(since)
    earlier = [s for s in summaries if s.as_of <= day and s.id != newest_id]
    if not earlier:
        raise ValueError(f"no snapshot as of {day} or before")
    return earlier[0].id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quant_hub.desk",
                                     description="Read the desk: status, view, changes, record.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("status", "whether the desk is current"),
                       ("view", "a snapshot, as the Desk page shows it"),
                       ("changes", "the newest snapshot against an earlier one"),
                       ("record", "every author's record"),
                       ("scorecard", "you and the agent against every voice"),
                       ("whatif", "the voices on the book after trades you're weighing")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--json", action="store_true", help="JSON for an agent")
        if name in ("view", "whatif"):
            sub.add_argument("--snapshot", type=int, help="its number (default: the newest)")
        if name == "whatif":
            sub.add_argument("--trade", action="append", required=True,
                             help='a trade, e.g. "MSFT +5" or "MSFT -$500"; repeatable')
        if name == "changes":
            sub.add_argument("--since", help="a snapshot's number, or a date (YYYY-MM-DD)")
        if name == "record":
            sub.add_argument("--scored", action="store_true", help="every scored claim too")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    now = datetime.now()
    core = build_core()
    try:
        desk = core.desk
        if args.command == "status":
            config = core.config
            folder = (config.backup_folder or Path(config.db_path).parent / "backups" / "daily")
            doc = report.status(desk, getattr(core, "activity", None), Path(folder), now)
            render = text_status
        elif args.command == "view":
            view = desk.view(args.snapshot)
            if view is None:
                raise ValueError("no snapshot yet: run python -m quant_hub.snapshot")
            doc, render = report.view(view, now), text_view
        elif args.command == "changes":
            summaries = {s.id: s for s in desk.summaries()}
            if not summaries:
                raise ValueError("no snapshot yet: run python -m quant_hub.snapshot")
            newest = max(summaries)
            old_id = _since(desk, args.since, newest)
            if old_id not in summaries:
                raise ValueError(f"no snapshot #{old_id}")
            old, new = desk.snapshot(old_id), desk.snapshot(newest)
            doc = report.changes(summaries[old_id], old, summaries[newest], new,
                                 (desk.regime(old), desk.regime(new)), now)
            render = text_changes
        elif args.command == "scorecard":
            doc, render = report.scorecard(desk.scorecard(), now), text_scorecard
        elif args.command == "whatif":
            moves = [parse_move(t) for t in args.trade]
            doc, render = report.whatif(desk.whatif(moves, args.snapshot), now), text_whatif
        else:
            doc, render = report.record(desk.record(), now, args.scored), text_record
        print(json.dumps(doc, indent=1, allow_nan=False) if args.json else render(doc))
        return 0 if args.command != "status" or doc["ok"] else 1
    except (ValueError, DeskStoreError) as exc:
        print(f"nothing to show: {exc}", file=sys.stderr)
        return 1
    finally:
        core.shutdown()


if __name__ == "__main__":
    sys.exit(main())
