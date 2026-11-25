"""How well each trade was timed (TR4): the exact nulls, the split, the measures."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant_hub.data.activity import Kind, Transaction
from quant_hub.models import timing as T
from quant_hub.models.lots import Piece

DAYS = pd.bdate_range("2025-06-02", periods=200)


def bars(closes, highs=None, lows=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"close": closes,
                         "high": closes if highs is None else np.asarray(highs, float),
                         "low": closes if lows is None else np.asarray(lows, float)},
                        index=DAYS[:len(closes)])


def day(i: int) -> date:
    return DAYS[i].date()


class TestNulls:
    def test_the_arcsine_law_and_the_window_law(self):
        assert T.arcsine_pmf(10).sum() == pytest.approx(1.0)
        law = T.window_null(10)
        assert len(law) == 21 and law.sum() == pytest.approx(1.0)
        assert law[:4].sum() == pytest.approx(0.1350, abs=5e-4)       # a best-15% day by luck
        assert law[10] == law.max()                                    # the middle, most likely

    def test_the_window_law_against_simulation(self):
        rng = np.random.default_rng(1)
        h, n = 5, 200_000
        x = rng.standard_t(3, (n, 2 * h))
        left = -np.cumsum(x[:, :h][:, ::-1], axis=1)
        right = np.cumsum(x[:, h:], axis=1)
        w = (left < 0).sum(axis=1) + (right < 0).sum(axis=1)
        simulated = np.bincount(w, minlength=2 * h + 1) / n
        assert np.abs(simulated - T.window_null(h)).max() < 0.004

    def test_the_aggregate_p(self):
        single = T.window_null(5)
        assert T.window_p(5, [0.0]) == pytest.approx(single[0])
        assert T.window_p(5, [0.0, 0.0]) == pytest.approx(single[0] ** 2)
        assert T.window_p(5, [5.0] * 4) > 0.4                          # middling days: no evidence
        assert T.window_p(5, [0.5]) == pytest.approx(single[:2].sum())  # a half rounds up
        assert T.window_p(5, []) is None

    def test_the_sign_test(self):
        assert T.sign_test(5, 10) == pytest.approx(1.0)
        assert T.sign_test(0, 10) == pytest.approx(2 / 1024)
        assert T.sign_test(9, 10) == pytest.approx(22 / 1024)
        assert T.sign_test(0, 0) is None


def test_beta_recovers_a_known_slope():
    rng = np.random.default_rng(2)
    market = 100 * np.cumprod(1 + rng.normal(0, 0.01, 300))
    stock_r = 1.5 * np.diff(market) / market[:-1] + rng.normal(0, 0.002, 299)
    stock = 50 * np.concatenate([[1.0], np.cumprod(1 + stock_r)])
    index = pd.bdate_range("2025-01-02", periods=300)
    b = T.beta(pd.Series(stock, index), pd.Series(market, index), index[-1].date())
    assert b == pytest.approx(1.5, abs=0.05)
    assert T.beta(pd.Series(stock[:30], index[:30]), pd.Series(market[:30], index[:30]),
                  index[29].date()) is None                         # too few sessions


def piece(side="long", units=10.0, opened=2, closed=8, open_fill=100.0, close_fill=110.0,
          open_cash=-100.5, close_cash=109.5, option=False) -> Piece:
    return Piece("Brokerage", "AAA", side, units, day(opened), day(closed), open_cash,
                 close_cash, open_fill, close_fill, option)


class TestPieces:
    CLOSES = [100, 101, 101, 104, 103, 107, 112, 109, 111, 110] + [110] * 190

    def test_the_split_is_exact(self):
        # A year of history before the entry, so β can be estimated.
        rng = np.random.default_rng(9)
        market = 400 * np.cumprod(1 + rng.normal(0, 0.01, 200))
        market_r = np.diff(np.concatenate([[400.0], market])) / np.concatenate([[400.0],
                                                                                  market[:-1]])
        closes = 100 * np.cumprod(1 + 1.2 * market_r + rng.normal(0, 0.005, 200))
        p = piece(opened=150, closed=160, open_fill=closes[150] - 0.3,
                  close_fill=closes[160] + 0.2, open_cash=-closes[150] + 0.1,
                  close_cash=closes[160] - 0.1)
        row = T.piece_timing(p, bars(closes), pd.Series(market, index=DAYS))
        total = row.entry_fill + row.exit_fill + row.market + row.stock
        assert total == pytest.approx(10 * (p.close_fill - p.open_fill))
        assert row.entry_fill == pytest.approx(10 * 0.3)               # filled below the close
        assert row.exit_fill == pytest.approx(10 * 0.2)                # sold above the close
        assert row.market is not None and row.costs == pytest.approx(p.pnl - total)

    def test_a_short_is_mirrored(self):
        row = T.piece_timing(piece(side="short", open_fill=102.0, close_fill=110.0,
                                   open_cash=102.0, close_cash=-110.0), bars(self.CLOSES), None)
        assert row.entry_fill == pytest.approx(-10 * (101 - 102))        # shorted above the close
        assert row.market is None and row.stock is None                  # no benchmark, no split
        assert row.exit_fill == pytest.approx(-10 * (110 - 111))

    def test_mfe_and_mae_start_at_the_entry_close(self):
        highs = [c + 1 for c in self.CLOSES]
        lows = [c - 1 for c in self.CLOSES]
        row = T.piece_timing(piece(), bars(self.CLOSES, highs, lows), None)
        assert row.mfe == pytest.approx(113 / 101 - 1)                   # day 6's high
        assert row.mae == pytest.approx(102 / 101 - 1)                   # never below the entry
        assert row.capture == pytest.approx((111 / 101 - 1) / (113 / 101 - 1))

    @pytest.mark.parametrize("make, reason", [
        (lambda: (piece(option=True), "full"), "an option"),
        (lambda: (piece(), None), "no bars"),
        (lambda: (piece(closed=40), "short"), "entry or exit day"),
        (lambda: (piece(open_fill=50.0), "full"), "another price scale"),
    ])
    def test_what_cant_be_measured_says_why(self, make, reason):
        p, have = make()
        frame = None if have is None else bars(self.CLOSES[:20] if have == "short"
                                               else self.CLOSES)
        row = T.piece_timing(p, frame, None)
        assert reason in row.reason and row.entry_fill is None


def trade(i, units, price, symbol="AAA", kind=Kind.BUY):
    q = units if kind in (Kind.BUY, Kind.COVER) else -units
    return Transaction(day(i), kind, -q * price, account="B", symbol=symbol, quantity=q,
                       price=price)


class TestDecisions:
    def test_a_days_fills_merge_at_their_average_price(self):
        ds = T.decisions_from([trade(3, 10, 10.0), trade(3, 30, 11.0),
                               trade(3, 5, 12.0, kind=Kind.SELL),
                               trade(4, 1, 9.0, symbol="BBB")])
        merged = next(d for d in ds if d.symbol == "AAA" and d.entry)
        assert (merged.units, merged.fill) == (40, pytest.approx(10.75))
        exit_ = next(d for d in ds if d.symbol == "AAA" and not d.entry)
        assert (exit_.side, exit_.units) == (-1, 5)
        assert len(ds) == 3

    def test_buying_the_windows_low_is_zero_better_days(self):
        closes = [10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10, 11, 12] + [12] * 187
        d = T.decision_timing(T.decisions_from([trade(5, 1, 5.0)])[0], bars(closes), None)
        assert d.better[5] == 0.0
        assert d.dollars[5] > 0                                          # beat a random day
        assert d.markouts[1] == pytest.approx(6 / 5 - 1)                 # it rose after

    def test_selling_the_windows_low_is_every_day_better(self):
        closes = [10, 9, 8, 7, 6, 5, 6, 7, 8, 9, 10] + [10] * 189
        d = T.decision_timing(T.decisions_from([trade(5, 1, 5.0, kind=Kind.SELL)])[0],
                              bars(closes), None)
        assert d.better[5] == 10.0 and d.markouts[1] < 0                 # it rose after the sale

    def test_too_close_to_the_edge_has_no_window(self):
        d = T.decision_timing(T.decisions_from([trade(2, 1, 100.0)])[0], bars([100] * 200), None)
        assert 5 not in d.better and "fewer than 5" in d.reason


def test_the_report_tests_entries_and_exits_apart():
    closes = list(np.linspace(100, 130, 200))
    history = [trade(10, 1, closes[10]), trade(30, 1, closes[30], kind=Kind.SELL)]
    report = T.timing([], history, {"AAA": bars(closes)}, None)
    assert report.windows[(True, 5)].n == 1 and report.windows[(False, 5)].n == 1
    assert report.windows[(True, 5)].total == 5.0                    # a steady rise: buy mid-window
    assert report.markouts[(True, 1)].positive == 1
