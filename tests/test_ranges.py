"""Tests for closed date-interval arithmetic (quant_hub.data.ranges)."""

from datetime import datetime

import pytest

from quant_hub.data.ranges import DateRange, merge_ranges, subtract_ranges, weekdays_missed


def r(start: str, end: str) -> DateRange:
    return DateRange(datetime.fromisoformat(start), datetime.fromisoformat(end))


class TestDateRange:
    def test_endpoints_floored_to_midnight(self):
        rng = DateRange(datetime(2024, 1, 2, 15, 30), datetime(2024, 1, 5, 9, 1))
        assert rng.start == datetime(2024, 1, 2)
        assert rng.end == datetime(2024, 1, 5)

    def test_single_day_range_is_legal(self):
        rng = r("2024-01-02", "2024-01-02")
        assert rng.start == rng.end

    def test_inverted_range_rejected(self):
        with pytest.raises(ValueError, match="after"):
            r("2024-01-05", "2024-01-02")

    def test_overlaps_and_contains(self):
        assert r("2024-01-01", "2024-01-10").overlaps(r("2024-01-10", "2024-01-20"))
        assert not r("2024-01-01", "2024-01-09").overlaps(r("2024-01-10", "2024-01-20"))
        assert r("2024-01-01", "2024-01-31").contains(r("2024-01-05", "2024-01-06"))
        assert not r("2024-01-05", "2024-01-06").contains(r("2024-01-01", "2024-01-31"))


class TestMerge:
    def test_disjoint_stay_separate(self):
        merged = merge_ranges([r("2024-01-01", "2024-01-05"), r("2024-01-10", "2024-01-15")])
        assert merged == [r("2024-01-01", "2024-01-05"), r("2024-01-10", "2024-01-15")]

    def test_overlapping_merge(self):
        merged = merge_ranges([r("2024-01-01", "2024-01-10"), r("2024-01-05", "2024-01-20")])
        assert merged == [r("2024-01-01", "2024-01-20")]

    def test_day_adjacent_merge(self):
        merged = merge_ranges([r("2024-01-01", "2024-01-05"), r("2024-01-06", "2024-01-10")])
        assert merged == [r("2024-01-01", "2024-01-10")]

    def test_contained_range_absorbed(self):
        merged = merge_ranges([r("2024-01-01", "2024-01-31"), r("2024-01-10", "2024-01-15")])
        assert merged == [r("2024-01-01", "2024-01-31")]

    def test_unsorted_input(self):
        merged = merge_ranges([r("2024-02-01", "2024-02-05"), r("2024-01-01", "2024-01-05")])
        assert merged[0].start == datetime(2024, 1, 1)

    def test_empty(self):
        assert merge_ranges([]) == []


class TestSubtract:
    TARGET = r("2020-01-01", "2020-12-31")

    def test_nothing_covered(self):
        assert subtract_ranges(self.TARGET, []) == [self.TARGET]

    def test_fully_covered(self):
        assert subtract_ranges(self.TARGET, [r("2019-01-01", "2021-01-01")]) == []

    def test_exactly_covered(self):
        assert subtract_ranges(self.TARGET, [self.TARGET]) == []

    def test_trailing_gap(self):
        missing = subtract_ranges(self.TARGET, [r("2019-06-01", "2020-06-30")])
        assert missing == [r("2020-07-01", "2020-12-31")]

    def test_leading_gap(self):
        missing = subtract_ranges(self.TARGET, [r("2020-03-01", "2021-06-30")])
        assert missing == [r("2020-01-01", "2020-02-29")]

    def test_interior_hole(self):
        missing = subtract_ranges(
            self.TARGET,
            [r("2020-01-01", "2020-03-31"), r("2020-06-01", "2020-12-31")],
        )
        assert missing == [r("2020-04-01", "2020-05-31")]

    def test_multiple_gaps(self):
        missing = subtract_ranges(
            self.TARGET,
            [r("2020-02-01", "2020-02-29"), r("2020-06-01", "2020-06-30")],
        )
        assert missing == [
            r("2020-01-01", "2020-01-31"),
            r("2020-03-01", "2020-05-31"),
            r("2020-07-01", "2020-12-31"),
        ]

    def test_irrelevant_coverage_ignored(self):
        missing = subtract_ranges(self.TARGET, [r("2015-01-01", "2015-12-31")])
        assert missing == [self.TARGET]

    def test_single_day_target_covered(self):
        day = r("2020-06-15", "2020-06-15")
        assert subtract_ranges(day, [r("2020-06-01", "2020-06-30")]) == []
        assert subtract_ranges(day, [r("2020-06-16", "2020-06-30")]) == [day]


class TestWeekdaysMissed:
    """V0: how stale a stored series is, counted in weekday sessions."""

    def test_fridays_bar_read_on_the_weekend_has_missed_nothing(self):
        friday = datetime(2026, 10, 23)
        assert weekdays_missed(friday, datetime(2026, 10, 24, 9)) == 0   # Saturday
        assert weekdays_missed(friday, datetime(2026, 10, 25, 9)) == 0   # Sunday
        assert weekdays_missed(friday, datetime(2026, 10, 26, 9)) == 0   # Monday: provisional

    def test_completed_weekdays_count(self):
        friday = datetime(2026, 10, 23)
        assert weekdays_missed(friday, datetime(2026, 10, 27, 9)) == 1   # Monday done
        assert weekdays_missed(friday, datetime(2026, 11, 2, 9)) == 5    # a whole week

    def test_a_series_through_yesterday_or_today_is_current(self):
        assert weekdays_missed(datetime(2026, 10, 21), datetime(2026, 10, 22, 16)) == 0
        assert weekdays_missed(datetime(2026, 10, 22), datetime(2026, 10, 22, 16)) == 0


class TestIntersection:
    def test_overlap_and_disjoint(self):
        a = DateRange(datetime(2024, 1, 1), datetime(2024, 1, 31))
        b = DateRange(datetime(2024, 1, 15), datetime(2024, 2, 15))
        assert a.intersection(b) == DateRange(datetime(2024, 1, 15), datetime(2024, 1, 31))
        assert a.intersection(DateRange(datetime(2024, 3, 1), datetime(2024, 3, 2))) is None
