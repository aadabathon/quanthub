"""Tests for sync planning (quant_hub.services.sync)."""

from datetime import datetime

from quant_hub.data.ranges import DateRange
from quant_hub.services.sync import SyncStatus, plan_sync


def r(start: str, end: str) -> DateRange:
    return DateRange(datetime.fromisoformat(start), datetime.fromisoformat(end))


REQUEST = r("2020-01-01", "2026-01-01")


class TestClassification:
    def test_full_hit(self):
        plan = plan_sync(REQUEST, [r("2019-01-01", "2026-06-01")])
        assert plan.status is SyncStatus.FULL_HIT
        assert plan.missing == ()

    def test_exact_coverage_is_full_hit(self):
        plan = plan_sync(REQUEST, [REQUEST])
        assert plan.status is SyncStatus.FULL_HIT

    def test_miss_when_store_empty(self):
        plan = plan_sync(REQUEST, [])
        assert plan.status is SyncStatus.MISS
        assert plan.missing == (REQUEST,)

    def test_miss_when_coverage_irrelevant(self):
        plan = plan_sync(REQUEST, [r("2010-01-01", "2012-01-01")])
        assert plan.status is SyncStatus.MISS

    def test_partial_hit_trailing_gap(self):
        # The owner's canonical example: store covers 2020-01-01..2025-06-30.
        plan = plan_sync(REQUEST, [r("2020-01-01", "2025-06-30")])
        assert plan.status is SyncStatus.PARTIAL_HIT
        assert plan.missing == (r("2025-07-01", "2026-01-01"),)

    def test_partial_hit_leading_gap(self):
        plan = plan_sync(REQUEST, [r("2022-01-01", "2026-06-01")])
        assert plan.status is SyncStatus.PARTIAL_HIT
        assert plan.missing == (r("2020-01-01", "2021-12-31"),)

    def test_partial_hit_interior_hole(self):
        plan = plan_sync(
            REQUEST,
            [r("2020-01-01", "2021-12-31"), r("2023-01-01", "2026-06-01")],
        )
        assert plan.status is SyncStatus.PARTIAL_HIT
        assert plan.missing == (r("2022-01-01", "2022-12-31"),)


class TestDescribe:
    def test_full_hit_description(self):
        assert plan_sync(REQUEST, [REQUEST]).describe() == "full-hit"

    def test_partial_description_names_gaps(self):
        text = plan_sync(REQUEST, [r("2020-01-01", "2025-06-30")]).describe()
        assert text == "partial-hit (missing 2025-07-01..2026-01-01)"

    def test_miss_description(self):
        text = plan_sync(REQUEST, []).describe()
        assert text == "miss (missing 2020-01-01..2026-01-01)"
