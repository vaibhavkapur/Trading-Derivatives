"""Unit tests for holiday calendars, business day adjustments, and ISDA day counts."""

from __future__ import annotations

from datetime import date

import pytest

from irs_engine.domain.calendars import BusinessCalendar, CalendarRegistry, add_months
from irs_engine.domain.daycount import year_fraction
from irs_engine.domain.enums import BusinessDayConvention, DayCountConvention
from irs_engine.domain.errors import CalendarError, ValidationError


def test_day_count_conventions_standard_periods() -> None:
    d1 = date(2026, 1, 15)
    d2 = date(2026, 7, 15)  # 181 actual days, 6 months
    assert year_fraction(d1, d2, DayCountConvention.ACT_360) == pytest.approx(181.0 / 360.0)
    assert year_fraction(d1, d2, DayCountConvention.ACT_365F) == pytest.approx(181.0 / 365.0)
    assert year_fraction(d1, d2, DayCountConvention.THIRTY_360_US) == pytest.approx(0.5)
    assert year_fraction(d1, d2, DayCountConvention.THIRTY_E_360) == pytest.approx(0.5)
    assert year_fraction(d1, d2, DayCountConvention.ACT_ACT_ISDA) == pytest.approx(181.0 / 365.0)


def test_thirty_360_us_vs_eurobond_31st_day() -> None:
    # Jan 15 -> Jan 31: US keeps d2=31 since d1=15 != 30; Eurobond caps d2=30
    d1 = date(2026, 1, 15)
    d2 = date(2026, 1, 31)
    assert year_fraction(d1, d2, DayCountConvention.THIRTY_360_US) == pytest.approx(16.0 / 360.0)
    assert year_fraction(d1, d2, DayCountConvention.THIRTY_E_360) == pytest.approx(15.0 / 360.0)


def test_act_act_isda_across_leap_year() -> None:
    # 2027 (non-leap) to 2028 (leap)
    d1 = date(2027, 7, 1)
    d2 = date(2028, 7, 1)
    # 2027 remaining days: Jul 1 -> Jan 1 2028 = 184 days / 365
    # 2028 elapsed days: Jan 1 -> Jul 1 2028 = 182 days / 366 (includes Feb 29)
    expected = 184.0 / 365.0 + 182.0 / 366.0
    assert year_fraction(d1, d2, DayCountConvention.ACT_ACT_ISDA) == pytest.approx(expected)


def test_year_fraction_rejects_reversed_dates_by_default() -> None:
    with pytest.raises(ValidationError):
        year_fraction(date(2026, 5, 1), date(2026, 1, 1), DayCountConvention.ACT_365F)
    assert year_fraction(
        date(2026, 5, 1), date(2026, 1, 1), DayCountConvention.ACT_365F, allow_negative=True
    ) < 0.0


def test_business_day_adjustments_and_month_roll(calendar_registry: CalendarRegistry) -> None:
    usd = calendar_registry.get("USD_NY")
    # 2026-01-31 is Saturday -> FOLLOWING is Feb 2 (Monday), MODIFIED_FOLLOWING rolls back to Jan 30 (Friday)
    sat_eom = date(2026, 1, 31)
    assert usd.adjust(sat_eom, BusinessDayConvention.UNADJUSTED) == sat_eom
    assert usd.adjust(sat_eom, BusinessDayConvention.FOLLOWING) == date(2026, 2, 2)
    assert usd.adjust(sat_eom, BusinessDayConvention.MODIFIED_FOLLOWING) == date(2026, 1, 30)
    assert usd.adjust(sat_eom, BusinessDayConvention.PRECEDING) == date(2026, 1, 30)

    # 2026-07-03 is US Independence Day observed holiday (Friday) -> FOLLOWING is Mon Jul 6
    assert not usd.is_business_day(date(2026, 7, 3))
    assert usd.adjust(date(2026, 7, 3), BusinessDayConvention.FOLLOWING) == date(2026, 7, 6)


def test_add_business_days_fixing_and_payment_lags(calendar_registry: CalendarRegistry) -> None:
    usd = calendar_registry.get("USD_NY")
    # 2026-01-05 is Monday; 2 business days before skips Jan 1 (holiday) & weekend -> Dec 31, 2025
    # Jan 2 (Fri) is -1 bd, Jan 1 (Thu holiday), Dec 31 (Wed) is -2 bd
    assert usd.add_business_days(date(2026, 1, 5), -2) == date(2025, 12, 31)
    assert usd.add_business_days(date(2026, 1, 2), 2) == date(2026, 1, 6)


def test_add_months_eom_and_unknown_calendar() -> None:
    assert add_months(date(2027, 2, 28), 12, end_of_month=True) == date(2028, 2, 29)
    assert add_months(date(2027, 2, 28), 6, end_of_month=True) == date(2027, 8, 31)

    reg = CalendarRegistry()
    with pytest.raises(CalendarError):
        reg.get("NON_EXISTENT_CAL")
    cal = BusinessCalendar("EMPTY", frozenset(), frozenset())
    assert cal.is_business_day(date(2026, 1, 3))
