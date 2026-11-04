"""NYSE trading days by rule (D63), against the exchange's published holidays.
(Checked once against every date in Ken French's daily file, 1998-2026: an
exact match, 7,230 days. That data isn't committed, so this file uses the
published lists.)"""
from datetime import date

import pytest

from quant_hub.data.trading_calendar import easter, holidays, is_trading_day, trading_days


def days(year, *month_days):
    return {date(year, m, d) for m, d in month_days}


PUBLISHED = {
    2021: days(2021, (1, 1), (1, 18), (2, 15), (4, 2), (5, 31), (7, 5), (9, 6), (11, 25), (12, 24)),
    2022: days(2022, (1, 17), (2, 21), (4, 15), (5, 30), (6, 20), (7, 4), (9, 5), (11, 24), (12, 26)),
    2024: days(2024, (1, 1), (1, 15), (2, 19), (3, 29), (5, 27), (6, 19), (7, 4), (9, 2), (11, 28),
               (12, 25)),
    2025: days(2025, (1, 1), (1, 9), (1, 20), (2, 17), (4, 18), (5, 26), (6, 19), (7, 4), (9, 1),
               (11, 27), (12, 25)),
    2026: days(2026, (1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7), (11, 26),
               (12, 25)),
}


@pytest.mark.parametrize("year", sorted(PUBLISHED))
def test_the_rules_reproduce_the_published_holidays(year):
    assert holidays(year) == PUBLISHED[year]


@pytest.mark.parametrize("year, count", [(2021, 252), (2022, 251), (2024, 252), (2025, 250)])
def test_trading_days_per_year(year, count):
    assert len(trading_days(date(year, 1, 1), date(year, 12, 31))) == count


def test_a_saturday_new_year_isnt_observed_and_a_sunday_one_moves_to_monday():
    assert is_trading_day(date(2021, 12, 31))        # Jan 1, 2022 was a Saturday
    assert date(2023, 1, 2) in holidays(2023)        # Jan 1, 2023 was a Sunday


def test_juneteenth_starts_in_2022_and_special_closures_are_kept():
    assert is_trading_day(date(2021, 6, 18))
    assert not any(is_trading_day(date(2001, 9, d)) for d in (11, 12, 13, 14))
    assert not is_trading_day(date(2012, 10, 29)) and not is_trading_day(date(2018, 12, 5))


@pytest.mark.parametrize("year, sunday", [(2000, date(2000, 4, 23)), (2024, date(2024, 3, 31)),
                                          (2038, date(2038, 4, 25)), (2285, date(2285, 3, 22))])
def test_easter_including_its_latest_and_earliest_dates(year, sunday):
    assert easter(year) == sunday


def test_weekends_endpoints_and_unmodeled_years():
    assert trading_days(date(2026, 11, 6), date(2026, 11, 9)) == [date(2026, 11, 6), date(2026, 11, 9)]
    assert not is_trading_day(date(2026, 11, 7))
    with pytest.raises(ValueError, match="from 1998"):
        holidays(1997)
