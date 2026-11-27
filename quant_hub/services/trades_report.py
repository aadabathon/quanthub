r"""Your trades as data, for a reader other than the Trades page (A4).

``python -m quant_hub.trades`` prints these as text, or as JSON with
``--json`` for an agent, in the same envelope as the desk's reports
(``services/desk_report.py``: ``schema``, ``kind``, ``now``, ``units``,
numbers to 7 significant figures). The numbers are the Trades page's,
computed by the models it uses:

* **Imports** (TR1, D90): each activity file's rows and what was new (a
  transaction is stored once, however often its file comes back); each
  positions export as one snapshot, refused before anything is stored when
  the same file was imported before.
* **Round trips** (TR3, ``models/lots.py``): flat to flat per account and
  symbol, FIFO, realized P&L from the cash; with the trades since a date.
* **Timing** (TR4, ``models/timing.py``): the P&L split per piece, the
  window rank of each decision's day against luck, markouts after it, MFE,
  MAE and capture.
* **Returns as held** (TR6, ``models/held.py``): time- and money-weighted,
  beside the same deposits put in the market.

Nothing here trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from quant_hub.data.activity import Kind, Transaction
from quant_hub.services.desk_report import document

UNITS = {
    "_usd": "dollars",
    "return": "a fraction (0.05 is 5%); _annual a fraction a year",
    "better_days": "W: of the 2h closes in the window h sessions either side of a "
                   "decision's day, how many were better for it (lower for a buy, higher "
                   "for a sell; a tie counts 1/2). Luck averages h; 0 is the best day",
    "window_p": "one-sided, P(S <= s) under no timing skill for S the sum of W: small "
                "when the days chosen were good",
    "markout": "M(k): the move k sessions after the decision, its way, net of beta times "
               "the market. For an exit, negative means it rose after you sold",
    "markout_p": "the exact two-sided sign test on how many markouts were positive",
    "mfe": "from the entry day's close: the best (mfe) and worst (mae) the trade "
           "offered, fractions; capture is the share of the mfe it kept",
    "twr": "time-weighted return over the window; mwr, money-weighted, is a year",
    "carried": "names without closes, carried at their last trade price; carried_share_end "
               "is their share of the book's gross value on the last day",
    "numbers": "rounded to 7 significant figures",
}
TRADED = (Kind.BUY, Kind.SELL, Kind.SHORT, Kind.COVER)


def _day(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def imported_activity(files: Sequence, now: datetime) -> dict:
    """``ActivityService.import_activity``'s ``FileImported`` list as data."""
    rows = [{"file": f.summary.name, "seen_before": f.seen_before, "rows": f.summary.rows,
             "added": f.summary.added, "first_date": _day(f.summary.first_date),
             "last_date": _day(f.summary.last_date),
             "imported_at": f.summary.imported_at.isoformat(),
             "unrecognized": list(f.unrecognized)} for f in files]
    return document("imported", now, glossary=UNITS, what="activity", files=rows,
                    added=sum(r["added"] for r in rows))


def imported_positions(snapshot_id: int | None, snapshot, refused: str,
                       now: datetime) -> dict:
    """One positions import: the snapshot stored, or why nothing was."""
    return document("imported", now, glossary=UNITS, what="positions",
                    snapshot_id=snapshot_id, file=snapshot.name, layout=snapshot.source,
                    as_of=snapshot.as_of.isoformat(), positions=len(snapshot.positions),
                    accounts=list(snapshot.accounts), stored=snapshot_id is not None,
                    refused=refused or None)


def _trade(t: Transaction) -> dict:
    return {"date": t.trade_date.isoformat(), "account": t.account, "kind": t.kind.value,
            "symbol": t.symbol, "units": t.quantity, "price_usd": t.price,
            "amount_usd": t.amount, "costs_usd": t.commission + t.fees,
            "option": t.option is not None}


def trips(round_trips: Sequence, transactions: Sequence[Transaction], since: date | None,
          now: datetime) -> dict:
    """Round trips (oldest first), only those open or touched since ``since``
    when given, and the trades made since then."""
    def touched(rt) -> bool:
        return (since is None or rt.closed is None or rt.closed >= since
                or rt.opened >= since)
    rows = [{"account": rt.account, "symbol": rt.symbol, "side": rt.side,
             "opened": rt.opened.isoformat(), "closed": _day(rt.closed),
             "open": rt.closed is None, "invested_usd": rt.invested,
             "realized_usd": rt.realized, "return": rt.return_on_invested,
             "income_usd": rt.income, "peak_units": rt.peak_units,
             "open_units": rt.open_units, "pieces": len(rt.pieces), "option": rt.option}
            for rt in round_trips if touched(rt)]
    trades = [_trade(t) for t in transactions if t.kind in TRADED
              and (since is None or t.trade_date >= since)]
    return document("trips", now, glossary=UNITS, since=_day(since), trips=rows,
                    trades=trades)


def timing(report, missing: dict[str, str], now: datetime) -> dict:
    """``ActivityService.timing``'s ``TimingReport`` as data."""
    def side(entry: bool) -> str:
        return "entries" if entry else "exits"

    windows = [{"of": side(entry), "h": h, "n": w.n, "better_days": w.total,
                "luck": w.n * h, "window_p": w.p, "vs_random_day_usd": w.dollars}
               for (entry, h), w in sorted(report.windows.items())]
    markouts = [{"of": side(entry), "k": k, "n": m.n, "mean": m.mean,
                 "positive": m.positive, "markout_p": m.p, "usd": m.dollars}
                for (entry, k), m in sorted(report.markouts.items())]
    decisions = [{"symbol": d.symbol, "day": d.day.isoformat(),
                  "side": "buy" if d.side > 0 else "sell", "entry": d.entry, "units": d.units,
                  "fill_usd": d.fill, "better_days": {str(h): w for h, w in d.better.items()},
                  "vs_random_day_usd": {str(h): x for h, x in d.dollars.items()},
                  "markouts": {str(k): m for k, m in d.markouts.items()},
                  "reason": d.reason or None} for d in report.decisions]
    pieces = [{"symbol": p.piece.symbol, "account": p.piece.account, "side": p.piece.side,
               "units": p.piece.units, "opened": p.piece.opened.isoformat(),
               "closed": p.piece.closed.isoformat(), "pnl_usd": p.piece.pnl,
               "market_usd": p.market, "stock_usd": p.stock, "entry_fill_usd": p.entry_fill,
               "exit_fill_usd": p.exit_fill, "costs_usd": p.costs, "mfe": p.mfe, "mae": p.mae,
               "capture": p.capture, "reason": p.reason or None} for p in report.pieces]
    return document("timing", now, glossary=UNITS, windows=windows, markouts=markouts,
                    decisions=decisions, pieces=pieces, not_priced=dict(missing))


def held(report, missing: dict[str, str], now: datetime) -> dict:
    """``ActivityService.held``'s ``HeldReport`` as data: the book's value
    path summarized, its returns, and the market's on the same deposits."""
    r, m = report.returns, report.market
    value = report.value
    return document(
        "held", now, glossary=UNITS, start=_day(report.start), end=_day(report.end),
        value_start_usd=float(value.iloc[0]) if len(value) else None,
        value_end_usd=float(value.iloc[-1]) if len(value) else None,
        deposits_usd=report.deposits, withdrawals_usd=report.withdrawals,
        twr=r.twr if r else None, twr_annual=r.twr_annual if r else None,
        mwr_annual=r.mwr if r else None, mwr_note=(r.mwr_note or None) if r else None,
        stopped=(r.stopped or None) if r else None,
        market=({"symbol": m.symbol, "value_end_usd": m.value_end, "twr": m.twr,
                 "mwr_annual": m.mwr, "mwr_note": m.mwr_note or None} if m else None),
        carried_at_cost=list(report.carried_names),
        carried_share_end=float(report.carried.iloc[-1]) if len(report.carried) else None,
        reason=report.reason or None, not_priced=dict(missing))
