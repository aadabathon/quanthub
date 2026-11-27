"""Your trades from the command line: for you, or for an agent (A4).

    python -m quant_hub.trades import-activity FILE... [--json]
    python -m quant_hub.trades import-positions FILE... [--json]
    python -m quant_hub.trades trips   [--since YYYY-MM-DD] [--json]
    python -m quant_hub.trades timing  [--source S] [--json]
    python -m quant_hub.trades held    [--source S] [--json]

* ``import-activity``: your broker's transaction history (Fidelity's
  activity download), as the Trades page imports it: each transaction
  stored once, however often its file comes back.
* ``import-positions``: a positions export, as the Portfolio page imports
  it: the files given are one day's snapshot (an account each). A file
  imported before is refused, and nothing is stored.
* ``trips``: round trips, FIFO, with realized P&L; ``--since`` keeps those
  open or touched since then and lists the trades made since.
* ``timing``: how well each trade was timed, against luck (TR4).
* ``held``: returns as you held them, beside the same deposits in SPY
  (TR6).

``timing`` and ``held`` read daily bars through yesterday from ``--source``
(default: Massive when keyed, else IBKR when on, else imported files);
the store already holds most of them. Text is for reading; ``--json`` is
for an agent (``services/trades_report.py``). The imports are the only
writes here, and they only append. Nothing here trades.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from quant_hub.core import build_core
from quant_hub.services import trades_report as report
from quant_hub.services.desk import default_source
from quant_hub.services.portfolio import DuplicateImportError


def _usd(x: float | None, signed: bool = False) -> str:
    if x is None:
        return "-"
    sign = "-" if x < 0 else ("+" if signed else "")
    return f"{sign}${abs(x):,.0f}"


def _pct(x: float | None, signed: bool = True) -> str:
    return "-" if x is None else (f"{x:+.1%}" if signed else f"{x:.1%}")


def text_imported(doc: dict) -> str:
    if doc["what"] == "positions":
        if not doc["stored"]:
            return f"not stored: {doc['refused']}"
        return (f"positions snapshot #{doc['snapshot_id']}: {doc['file']} ({doc['layout']}), "
                f"as of {doc['as_of'][:10]}, {doc['positions']} positions")
    lines = [f"{f['file']}: {f['added']} new of {f['rows']} rows, {f['first_date']} to "
             f"{f['last_date']}" + (" (seen before)" if f["seen_before"] else "")
             for f in doc["files"]]
    return "\n".join(lines + [f"{doc['added']} transactions added"])


def text_trips(doc: dict) -> str:
    lines = []
    if doc["since"]:
        lines.append(f"trades since {doc['since']}:")
        lines += [f"  {t['date']} {t['kind']:<6}{t['symbol']:<8}{t['units']:>10,.2f} @ "
                  + (f"{t['price_usd']:,.2f}" if t["price_usd"] is not None else "-")
                  + f"  {_usd(t['amount_usd'], True)}" for t in doc["trades"]] or ["  none"]
    lines.append("round trips" + (f" open or touched since {doc['since']}:" if doc["since"]
                                  else ":"))
    for t in doc["trips"]:
        if t["open"] and not t["pieces"]:          # nothing sold yet: nothing realized
            lines.append(f"  {t['symbol']:<8}{t['side']:<6}opened {t['opened']}, open: "
                         f"{_usd(t['invested_usd'])} in, {t['open_units']:,.2f} units")
            continue
        state = "open" if t["open"] else f"closed {t['closed']}"
        lines.append(f"  {t['symbol']:<8}{t['side']:<6}opened {t['opened']}, {state}: "
                     f"{_usd(t['realized_usd'], True)} realized on {_usd(t['invested_usd'])}"
                     + (f" ({_pct(t['return'])})" if t["return"] is not None else ""))
    return "\n".join(lines)


def text_timing(doc: dict) -> str:
    lines = ["the day you chose, against luck (better days W; luck averages h per decision):"]
    for w in doc["windows"]:
        lines.append(f"  {w['of']:<8} +-{w['h']:<3} {w['n']:>3} decisions: W {w['better_days']:.1f}"
                     f" of luck {w['luck']}, p {w['window_p']:.2f}" if w["window_p"] is not None
                     else f"  {w['of']:<8} +-{w['h']:<3} {w['n']:>3} decisions: too few")
    lines.append("what happened after, net of the market (markouts):")
    for m in doc["markouts"]:
        lines.append(f"  {m['of']:<8} {m['k']:>2} sessions: mean {_pct(m['mean'])}, "
                     f"{m['positive']}/{m['n']} positive"
                     + (f", p {m['markout_p']:.2f}" if m["markout_p"] is not None else ""))
    if doc["not_priced"]:
        lines.append(f"  not priced: {', '.join(sorted(doc['not_priced']))}")
    return "\n".join(lines)


def text_held(doc: dict) -> str:
    if doc["reason"]:
        return f"no returns as held: {doc['reason']}"
    m = doc["market"]
    lines = [f"as held, {doc['start']} to {doc['end']}: time-weighted {_pct(doc['twr'])} "
             f"({_pct(doc['twr_annual'])} a year), money-weighted "
             f"{_pct(doc['mwr_annual'])} a year",
             f"  deposits {_usd(doc['deposits_usd'])}, withdrawals "
             f"{_usd(doc['withdrawals_usd'])}"]
    if m:
        lines.append(f"  the same deposits in {m['symbol']}: {_pct(m['twr'])}, money-weighted "
                     f"{_pct(m['mwr_annual'])} a year")
    if doc["carried_at_cost"]:
        lines.append(f"  carried at a last trade price: {', '.join(doc['carried_at_cost'])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quant_hub.trades",
                                     description="Your trades: import, trips, timing, held.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("import-activity", "import a transaction history download"),
                       ("import-positions", "import one day's positions export(s)"),
                       ("trips", "round trips and recent trades"),
                       ("timing", "how well each trade was timed, against luck"),
                       ("held", "returns as you held them")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("--json", action="store_true", help="JSON for an agent")
        if name.startswith("import"):
            sub.add_argument("files", nargs="+", help="the CSV file(s)")
        if name == "trips":
            sub.add_argument("--since", type=date.fromisoformat, help="YYYY-MM-DD")
        if name in ("timing", "held"):
            sub.add_argument("--source", help="price source: massive, ibkr or file")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    now = datetime.now()
    core = build_core()
    try:
        activity = core.activity
        source = getattr(args, "source", None) or default_source(
            bool(core.config.massive_key), core.config.ibkr_enabled)
        if args.command == "import-activity":
            doc = report.imported_activity(activity.import_activity(args.files), now)
            render = text_imported
        elif args.command == "import-positions":
            try:
                snapshot_id, snapshot = core.portfolio.import_positions(args.files)
                doc = report.imported_positions(snapshot_id, snapshot, "", now)
            except DuplicateImportError as exc:
                doc = report.imported_positions(None, exc.snapshot, str(exc), now)
            render = text_imported
        elif args.command == "trips":
            doc = report.trips(activity.round_trips(), activity.transactions(), args.since, now)
            render = text_trips
        elif args.command == "timing":
            doc = report.timing(*activity.timing(core.market_service, source), now)
            render = text_timing
        else:
            doc = report.held(*activity.held(core.market_service, source), now)
            render = text_held
        print(json.dumps(doc, indent=1, allow_nan=False) if args.json else render(doc))
        return 0
    except (ValueError, OSError) as exc:
        print(f"nothing done: {exc}", file=sys.stderr)
        return 1
    finally:
        core.shutdown()


if __name__ == "__main__":
    sys.exit(main())
