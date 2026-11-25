"""Massive's daily bars (D97): the client, the dividend adjustment, the cache."""

import io
import json
import urllib.error
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from quant_hub.data.sources import SourceOfflineError
from quant_hub.data.types import PriceBasis
from quant_hub.providers.massive import (
    Bar,
    Dividend,
    MassiveClient,
    MassiveError,
    MassiveUnreachable,
)
from quant_hub.services.cached_source import CachedSource
from quant_hub.services.massive_source import MassiveSource, dividend_adjusted
from quant_hub.storage.bar_store import BarStore

KEY = "k" * 32


def ms(day: str) -> int:
    """Midnight in New York, as Massive stamps a daily bar (05:00 UTC in winter)."""
    return int(datetime.fromisoformat(f"{day}T05:00:00").replace(tzinfo=UTC).timestamp() * 1000)


def bar_rows(days_closes, scale=1.0):
    return [{"t": ms(d), "o": c * scale, "h": c * scale, "l": c * scale, "c": c * scale,
             "v": 1000, "n": 10} for d, c in days_closes]


class Server:
    """Answers each request from a list of (status, body) and records it."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []

    def __call__(self, request, timeout):
        self.requests.append(request)
        status, body = self.replies.pop(0)
        if status == "down":
            raise urllib.error.URLError("no route to host")
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(b"{}"))
        return io.BytesIO(json.dumps(body).encode())


def client(server, sleeps=None):
    clock = iter(range(0, 1000, 1))
    return MassiveClient(KEY, opener=server, sleep=(sleeps.append if sleeps is not None
                                                    else lambda s: None),
                         clock=lambda: float(next(clock)))


class TestClient:
    def test_bars_are_read_by_session_and_the_key_stays_out_of_urls(self):
        server = Server((200, {"status": "OK", "results": bar_rows([("2026-11-19", 10.0),
                                                                    ("2026-11-20", 11.0)])}))
        bars = client(server).bars("AAA", date(2026, 11, 19), date(2026, 11, 20))
        assert [(b.day, b.close) for b in bars] == [(date(2026, 11, 19), 10.0),
                                                    (date(2026, 11, 20), 11.0)]
        request = server.requests[0]
        assert "/v2/aggs/ticker/AAA/range/1/day/2026-11-19/2026-11-20" in request.full_url
        assert "adjusted=true" in request.full_url and KEY not in request.full_url
        assert request.get_header("Authorization") == f"Bearer {KEY}"

    def test_a_summer_session_keeps_its_date(self):
        row = {"t": int(datetime(2026, 7, 2, 4, tzinfo=UTC).timestamp() * 1000),
               "o": 1, "h": 1, "l": 1, "c": 1, "v": 0}
        server = Server((200, {"status": "OK", "results": [row]}))
        (bar,) = client(server).bars("AAA", date(2026, 7, 2), date(2026, 7, 2))
        assert bar.day == date(2026, 7, 2)

    def test_pages_are_followed_and_requests_paced(self):
        sleeps = []
        page2 = "https://api.massive.com/v2/aggs/ticker/AAA/range/1/day/x/y?cursor=abc"
        server = Server((200, {"status": "OK", "results": bar_rows([("2026-11-19", 10.0)]),
                               "next_url": page2}),
                        (200, {"status": "OK", "results": bar_rows([("2026-11-20", 11.0)])}))
        bars = client(server, sleeps).bars("AAA", date(2026, 11, 1), date(2026, 11, 20))
        assert len(bars) == 2 and server.requests[1].full_url == page2
        assert sleeps == [pytest.approx(11.5)]          # 12.5 s apart, 1 s already gone

    def test_a_next_page_elsewhere_is_refused(self):
        server = Server((200, {"status": "OK", "results": [], "next_url": "https://evil.test/x"}))
        with pytest.raises(MassiveError, match="outside"):
            client(server).bars("AAA", date(2026, 11, 1), date(2026, 11, 20))

    @pytest.mark.parametrize("status, message", [(401, "check \\[massive\\] key"),
                                                 (429, "rate limit"), (500, "HTTP 500")])
    def test_a_refusal_says_what_to_do_without_the_key(self, status, message):
        with pytest.raises(MassiveError, match=message) as caught:
            client(Server((status, None))).bars("AAA", date(2026, 11, 1), date(2026, 11, 2))
        assert KEY not in str(caught.value)

    def test_no_answer_is_unreachable(self):
        with pytest.raises(MassiveUnreachable):
            client(Server(("down", None))).bars("AAA", date(2026, 11, 1), date(2026, 11, 2))

    def test_a_reply_that_isnt_ok_is_refused(self):
        with pytest.raises(MassiveError, match="status 'ERROR'"):
            client(Server((200, {"status": "ERROR"}))).bars("AAA", date(2026, 11, 1),
                                                            date(2026, 11, 2))

    def test_dividends(self):
        server = Server((200, {"status": "OK", "results": [
            {"ex_dividend_date": "2026-11-20", "cash_amount": 1.0,
             "split_adjusted_cash_amount": 0.5, "ticker": "AAA"}]}))
        (d,) = client(server).dividends("AAA", date(2026, 1, 1), date(2026, 11, 24))
        assert d == Dividend(date(2026, 11, 20), 0.5)
        url = server.requests[0].full_url
        assert "/stocks/v1/dividends?ticker=AAA" in url and "ex_dividend_date.lte=2026-11-24" in url

    def test_a_key_that_cant_be_one_is_refused(self):
        with pytest.raises(MassiveError, match=r"\[massive\] key"):
            MassiveClient("short")


def bars(days_closes):
    return [Bar(date.fromisoformat(d), c, c, c, c, 100.0) for d, c in days_closes]


class TestDividendAdjustment:
    def test_a_dividend_scales_every_earlier_price(self):
        traded = bars([("2026-11-18", 100.0), ("2026-11-19", 100.0), ("2026-11-20", 99.0)])
        adjusted = dividend_adjusted(traded, [Dividend(date(2026, 11, 20), 1.0)])
        f = 1 - 1.0 / 100.0
        assert [b.close for b in adjusted] == [pytest.approx(100 * f), pytest.approx(100 * f),
                                               99.0]
        # The return across the ex-date carries the dividend: (99 + 1) / 100 - 1 = 0.
        assert adjusted[2].close / adjusted[1].close - 1 == pytest.approx(0.0)

    def test_a_split_after_a_dividend_keeps_the_factor_on_one_scale(self):
        # $1.00 paid before a later 2:1 split: on today's scale, $0.50 on a $50 close.
        traded = bars([("2026-11-17", 50.0), ("2026-11-18", 49.5), ("2026-11-19", 49.5)])
        adjusted = dividend_adjusted(traded, [Dividend(date(2026, 11, 18), 0.5)])
        assert adjusted[0].close == pytest.approx(50.0 * 0.99)      # 1 − 0.50 / 50, as 1 − 1/100
        assert adjusted[1].close == pytest.approx(49.5)

    def test_a_dividend_before_the_data_is_skipped_and_a_bad_one_refused(self):
        traded = bars([("2026-11-18", 100.0), ("2026-11-19", 100.0)])
        assert dividend_adjusted(traded, [Dividend(date(2026, 11, 18), 1.0)]) == traded
        with pytest.raises(MassiveError, match="not below"):
            dividend_adjusted(traded, [Dividend(date(2026, 11, 19), 150.0)])

    def test_a_dividend_not_yet_ex_changes_nothing(self):
        # Declared for after the last bar: today's prices stay on today's scale.
        traded = bars([("2026-11-23", 100.0), ("2026-11-24", 101.0)])
        assert dividend_adjusted(traded, [Dividend(date(2026, 12, 2), 1.0)]) == traded


class FakeClient:
    def __init__(self, unreachable=False):
        self.unreachable, self.calls = unreachable, []

    def bars(self, ticker, start, end):
        self.calls.append(("bars", start, end))
        if self.unreachable:
            raise MassiveUnreachable("couldn't reach Massive: offline")
        return [Bar(d.date(), 10.0, 10.0, 10.0, 10.0, 5.0) for d in pd.bdate_range(start, end)]

    def dividends(self, ticker, since, until):
        self.calls.append(("dividends", since, until))
        return [Dividend(date(2026, 11, 18), 0.2)]


class TestSource:
    def test_traded_is_one_request_and_adjusted_two(self):
        fake = FakeClient()
        source = MassiveSource(fake, today=lambda: date(2026, 11, 24))
        traded = source.fetch_bars("aaa", datetime(2026, 11, 16), datetime(2026, 11, 20))
        assert len(fake.calls) == 1 and traded["close"].iloc[0] == 10.0
        adjusted = source.fetch_bars("AAA", datetime(2026, 11, 16), datetime(2026, 11, 20),
                                     basis=PriceBasis.ADJUSTED)
        assert [c[0] for c in fake.calls[1:]] == ["bars", "dividends"]
        assert fake.calls[1][2] == date(2026, 11, 24)            # through today
        assert list(adjusted.index.date) == [date(2026, 11, d) for d in range(16, 21)]
        f = 1 - 0.2 / 10.0                                       # cash over the close before
        assert adjusted["close"].iloc[0] == pytest.approx(10.0 * f)
        assert adjusted["close"].iloc[-1] == pytest.approx(10.0)

    def test_unreachable_is_offline_so_the_store_can_answer(self):
        source = MassiveSource(FakeClient(unreachable=True))
        with pytest.raises(SourceOfflineError):
            source.fetch_bars("AAA", datetime(2026, 11, 16), datetime(2026, 11, 20))

    def test_cached_like_ibkr(self, tmp_path):
        store = BarStore(tmp_path / "s.db")
        cached = CachedSource(MassiveSource(FakeClient(), today=lambda: date(2026, 11, 24)),
                              store)
        history = cached.get_history("AAA", datetime(2026, 11, 2), datetime(2026, 11, 20))
        assert history.source == "massive" and len(history.frame) == 15
        store.close()

    def test_no_live_quote(self):
        from quant_hub.broker.types import NoMarketDataError
        with pytest.raises(NoMarketDataError, match="end of day"):
            MassiveSource(FakeClient()).get_quote("AAA")


def test_the_key_setting_adds_the_source(tmp_path):
    from quant_hub.config import AppConfig, load_config
    from quant_hub.core import build_core
    settings = tmp_path / "settings.toml"
    settings.write_text(f'[massive]\nkey = "{KEY}"\n', encoding="utf-8")
    config, problems = load_config(settings)
    assert problems == [] and config.massive_key == KEY
    settings.write_text('[massive]\nkey = "x"\n', encoding="utf-8")
    assert any("Massive API key" in p for p in load_config(settings)[1])
    core = build_core(AppConfig(db_path=tmp_path / "s.db", ibkr_enabled=False, massive_key=KEY))
    try:
        assert "massive" in core.market_service.source_names
    finally:
        core.shutdown()
    core = build_core(AppConfig(db_path=tmp_path / "t.db", ibkr_enabled=False))
    try:
        assert "massive" not in core.market_service.source_names
    finally:
        core.shutdown()
