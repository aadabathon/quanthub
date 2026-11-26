"""Take today's desk snapshot from the command line (D1, D96).

    python -m quant_hub.snapshot [--source ibkr|file]

Builds the core from your settings (no window), takes one snapshot of the
book with every voice's claims (``services/desk.py``), stores it, writes
its JSON beside the store (``<store folder>/desk/``), and prints where.
The source defaults to Massive when ``[massive] key`` is set (daily bars
without TWS, D97), else IBKR when it's on in settings (stored bars serve
when TWS isn't running), else imported files.

Meant for a daily scheduled run after the close, so every voice's record
starts accruing whether or not the app is open. Nothing here trades.
"""

from __future__ import annotations

import argparse
import sys

from quant_hub.core import build_core
from quant_hub.services.desk import default_source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quant_hub.snapshot",
                                     description="Take today's desk snapshot.")
    parser.add_argument("--source", help="price source: massive, ibkr or file (default: "
                                         "massive if keyed, else ibkr if on)")
    args = parser.parse_args(argv)
    core = build_core()
    try:
        source = args.source or default_source(bool(core.config.massive_key),
                                               core.config.ibkr_enabled)
        resolved = core.desk.resolve()
        if resolved.stored or resolved.pending:
            print(f"resolved {resolved.stored} outcomes ({resolved.unresolvable} unresolvable); "
                  f"{len(resolved.pending)} pending")
            for line in resolved.pending[:10]:
                print(f"  pending: {line}")
        taken = core.desk.take(source)
        snapshot = taken.snapshot
        claims = sum(len(v.claims) for v in snapshot.voices)
        print(f"snapshot #{taken.id} as of {snapshot.as_of} from {source}: "
              f"{len(snapshot.book.holdings)} holdings, {len(snapshot.voices)} voices, "
              f"{claims} claims, sha256 {snapshot.sha256[:12]}")
        for line in snapshot.book.unpriced:
            print(f"  not priced: {line}")
        for line in taken.silent:
            print(f"  silent: {line}")
        if taken.path is not None:
            print(f"  written to {taken.path}")
        return 0
    except ValueError as exc:
        print(f"no snapshot: {exc}", file=sys.stderr)
        return 1
    finally:
        core.shutdown()


if __name__ == "__main__":
    sys.exit(main())
