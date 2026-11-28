"""Record a decision on the desk from the command line (D108).

    python -m quant_hub.decide --claim "MSFT.return@21 q -12%,-4%,1%,6%,15%"
                               [--claim ...] [--trade "MSFT +5"] [--why "..."]
                               [--author me] [--snapshot ID]
    python -m quant_hub.decide --json decision.json
    python -m quant_hub.decide --list [N] [--json]

A decision cites a stored snapshot (the newest unless ``--snapshot`` names
one), says what was traded and why, and states at least one **claim** of
what it expects, written as ``data/desk.parse_claim`` reads it. The daily
run resolves its claims and scores them like a voice's, under its
author's name (``models/scoring.py``), so your judgment, or an LLM's, gets
a record beside the models'.

``--json`` takes ``{"author", "why", "trades": [["MSFT", 5]], "claims":
[...], "snapshot": id}``, each claim a line of text or ``{"target",
"horizon", "quantiles"}`` / ``{"target", "horizon", "event",
"probability"}``: the form an agent writes. ``--list --json`` lists them
in the same form, with each claim's resolve day (``services/desk_report.py``).
Nothing here trades.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from quant_hub.console import safe_console
from quant_hub.core import build_core
from quant_hub.data.desk import Claim, parse_claim, parse_trades, resolves_on
from quant_hub.services import desk_report


def _claim(item) -> Claim:
    if isinstance(item, str):
        return parse_claim(item)
    if isinstance(item, dict):
        return Claim(str(item["target"]), int(item["horizon"]),
                     tuple(float(q) for q in item.get("quantiles", ())),
                     str(item.get("event", "")),
                     None if item.get("probability") is None else float(item["probability"]))
    raise ValueError(f"a claim is text or an object, not {item!r}")


def _from_json(path: str) -> dict:
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    trades = tuple((str(s).upper(), float(u)) for s, u in body.get("trades", ()))
    return {"author": str(body.get("author", "llm")), "rationale": str(body.get("why", "")),
            "trades": trades, "expects": tuple(_claim(c) for c in body.get("claims", ())),
            "snapshot_id": body.get("snapshot")}


def main(argv: list[str] | None = None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(prog="python -m quant_hub.decide",
                                     description="Record a decision on the desk.")
    parser.add_argument("--claim", action="append", default=[],
                        help='what you expect, e.g. "book.fall@63 >= 10%% p 25%%"; repeatable')
    parser.add_argument("--trade", action="append", default=[],
                        help='what you traded, e.g. "MSFT +5"; repeatable')
    parser.add_argument("--why", default="", help="your reasoning, in a sentence or two")
    parser.add_argument("--author", default="me", help='who decided (default "me"; an agent: "llm")')
    parser.add_argument("--snapshot", type=int, help="the snapshot's number (default: the newest)")
    parser.add_argument("--json", nargs="?", const="", metavar="FILE",
                        help="read the decision from a JSON file instead; with --list, "
                             "list them as JSON")
    parser.add_argument("--list", nargs="?", const=10, type=int, metavar="N",
                        help="list the newest N decisions instead (default 10)")
    args = parser.parse_args(argv)
    core = build_core()
    try:
        if args.list is not None:
            rows = core.desk.decisions()[:args.list]
            if args.json is not None:
                print(json.dumps(desk_report.decisions(rows, datetime.now()), indent=1,
                                 allow_nan=False))
                return 0
            for decision, summary in rows:
                cited = f"#{summary.id} as of {summary.as_of}" if summary else "a missing snapshot"
                print(f"{decision.made_at:%Y-%m-%d %H:%M} {decision.author}: "
                      f"{len(decision.expects)} claims on {cited}"
                      + (f" | {decision.rationale}" if decision.rationale else ""))
            return 0
        if args.json == "":
            raise ValueError("--json names the decision's file (or lists, with --list)")
        if args.json:
            fields = _from_json(args.json)
        else:
            fields = {"author": args.author, "rationale": args.why,
                      "trades": tuple(t for text in args.trade for t in parse_trades(text)),
                      "expects": tuple(parse_claim(c) for c in args.claim),
                      "snapshot_id": args.snapshot}
        decision_id, decision, summary = core.desk.decide(**fields)
        print(f"decision #{decision_id} by {decision.author} on snapshot #{summary.id} "
              f"(as of {summary.as_of}, {summary.sha256[:12]}): {len(decision.expects)} claims")
        for claim in decision.expects:
            print(f"  {claim.key}, resolves {resolves_on(summary.as_of, claim.horizon)}")
        return 0
    except (ValueError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(f"not recorded: {exc}", file=sys.stderr)
        return 1
    finally:
        core.shutdown()


if __name__ == "__main__":
    sys.exit(main())
