"""Accounts from the broker: positions and balances, as a snapshot (E1).

``AccountService.snapshot`` reads what the IBKR login can see: its
accounts, its positions and each account's balances. It only reads (D52:
orders live elsewhere, and not yet at all). It turns them into the
broker-agnostic ``PositionSnapshot`` that the Portfolio page and the review
already read (``data/positions.py``):

* **Stocks and ETFs** (IBKR's ``STK``) become ``Holding.EQUITY``: shares,
  with a cost basis of IBKR's average cost per share times the shares. They
  carry no price or value. The portfolio service values a holding without
  them at the price source's last close, as it does any import without
  prices.
* **Options** (``OPT``) become ``Holding.OPTION`` with their contract.
  IBKR's average cost is per contract, multiplier included, so the cost
  basis is that times the contracts. Only the US equity multiplier, 100,
  is modelled (``OPTION_MULTIPLIER``). Others are left out and named.
* **Cash** is each account's ``TotalCashValue`` from the account summary,
  in the account's base currency.
* **Everything else is left out and named,** with its account: futures,
  forex, bonds, funds, and any position whose currency isn't USD (the risk
  model prices in dollars). So is cash in a base currency other than USD.
* **A position of zero** (closed today) is dropped. It holds nothing.

The snapshot is dated today: session dates are naive (D19). It's named
``IBKR positions``, and ``PortfolioService.keep`` keeps it as it keeps a
file import. The page, the review and the history then all see it the
same way.

Blocking: run on the task worker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from quant_hub.broker.base import Broker
from quant_hub.broker.types import AccountSummary, BrokerPosition
from quant_hub.data.positions import (
    OPTION_MULTIPLIER,
    Holding,
    OptionContract,
    Position,
    PositionSnapshot,
)

SOURCE = "IBKR"
CASH_SYMBOL = "CASH"
_MODELLED_CURRENCY = "USD"


@dataclass(frozen=True)
class BrokerSnapshot:
    """What the broker showed: the snapshot, what was left out of it and
    why, and each account's balances as the broker states them."""

    snapshot: PositionSnapshot
    left_out: tuple[str, ...]
    summaries: tuple[AccountSummary, ...]

    @property
    def net_liquidation(self) -> float | None:
        """The accounts' total net liquidation, when every account sent one."""
        values = [s.net_liquidation for s in self.summaries]
        return sum(values) if values and all(v is not None for v in values) else None


def broker_snapshot(accounts: Sequence[str], positions: Sequence[BrokerPosition],
                    summaries: Sequence[AccountSummary], now: datetime) -> BrokerSnapshot:
    """The module docstring's mapping, from what the broker sent."""
    kept: list[Position] = []
    left_out: list[str] = []
    for p in positions:
        where = f"{p.symbol} ({p.sec_type}, {p.account})"
        if p.quantity == 0:
            continue
        if p.currency != _MODELLED_CURRENCY:
            left_out.append(f"{where}: priced in {p.currency}, and the risk model uses dollars")
            continue
        if p.sec_type == "STK":
            kept.append(Position(p.symbol, Holding.EQUITY, p.quantity, p.account,
                                 cost_basis=p.average_cost * p.quantity))
        elif p.sec_type == "OPT":
            if p.multiplier != OPTION_MULTIPLIER:
                left_out.append(f"{where}: multiplier {p.multiplier:g}, and only "
                                f"{OPTION_MULTIPLIER:g} is modelled")
                continue
            contract = OptionContract(p.symbol, "call" if p.right == "C" else "put",
                                      float(p.strike), p.expiry)
            label = (f"{p.symbol} {p.expiry:%Y-%m-%d} {p.strike:g} "
                     f"{'C' if p.right == 'C' else 'P'}")
            kept.append(Position(label, Holding.OPTION, p.quantity, p.account,
                                 cost_basis=p.average_cost * p.quantity, option=contract))
        else:
            left_out.append(f"{where}: this kind of position isn't modelled")
    for summary in summaries:
        if summary.total_cash is None:
            continue
        if summary.currency and summary.currency != _MODELLED_CURRENCY:
            left_out.append(f"cash in {summary.account}: in {summary.currency}, and the "
                            "risk model uses dollars")
            continue
        kept.append(Position(CASH_SYMBOL, Holding.CASH, summary.total_cash, summary.account,
                             value=summary.total_cash))
    if not kept:
        seen = ", ".join(accounts) or "no accounts"
        raise ValueError(f"IBKR shows no positions or cash to model ({seen})"
                         + (f"; left out: {'; '.join(left_out)}" if left_out else ""))
    order = {account: i for i, account in enumerate(accounts)}
    kept.sort(key=lambda p: (order.get(p.account, len(order)), p.kind is Holding.CASH))
    snapshot = PositionSnapshot(tuple(kept), datetime(now.year, now.month, now.day), SOURCE,
                                "IBKR positions")
    return BrokerSnapshot(snapshot, tuple(left_out), tuple(summaries))


class AccountService:
    """Reads the broker's accounts, read-only (module docstring)."""

    def __init__(self, broker: Broker) -> None:
        self._broker = broker

    def snapshot(self, now: datetime | None = None) -> BrokerSnapshot:
        accounts = self._broker.get_managed_accounts()
        positions = self._broker.get_positions()
        summaries = self._broker.get_account_summary()
        return broker_snapshot(accounts, positions, summaries, now or datetime.now())
