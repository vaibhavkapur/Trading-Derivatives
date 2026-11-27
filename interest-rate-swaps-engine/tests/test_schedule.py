"""Unit tests for payment and reset schedule generation including stubs and EOM."""

from __future__ import annotations

from datetime import date

import pytest

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import BusinessDayConvention, DayCountConvention, Frequency, StubType
from irs_engine.domain.errors import ScheduleError
from irs_engine.domain.schedule import generate_leg_schedule


def test_regular_quarterly_schedule_with_fixing_and_payment_lag(
    calendar_registry: CalendarRegistry,
) -> None:
    usd = calendar_registry.get("USD_NY")
    sched = generate_leg_schedule(
        date(2026, 1, 5),
        date(2027, 1, 5),
        Frequency.QUARTERLY,
        DayCountConvention.ACT_360,
        BusinessDayConvention.MODIFIED_FOLLOWING,
        usd,
        StubType.NONE,
        leg_type="FLOATING",
        payment_lag_days=2,
        fixing_lag_days=2,
    )
    assert len(sched.periods) == 4
    assert all(not p.is_stub for p in sched.periods)
    # Check contiguity of adjusted accrual periods and lags
    for i in range(len(sched.periods) - 1):
        assert sched.periods[i].accrual_end == sched.periods[i + 1].accrual_start
    for p in sched.periods:
        assert p.fixing_date is not None and p.fixing_date < p.accrual_start
        assert p.payment_date > p.accrual_end


def test_front_short_and_front_long_stubs(calendar_registry: CalendarRegistry) -> None:
    cal = calendar_registry.get("NONE")
    # 2Y + 2M tenor with semi-annual frequency
    short_s = generate_leg_schedule(
        date(2026, 1, 15),
        date(2028, 3, 15),
        Frequency.SEMI_ANNUAL,
        DayCountConvention.THIRTY_360_US,
        BusinessDayConvention.UNADJUSTED,
        cal,
        StubType.FRONT_SHORT,
        leg_type="FIXED",
    )
    assert len(short_s.periods) == 5
    assert short_s.periods[0].is_stub
    assert short_s.periods[0].stub_kind == "FRONT_SHORT"
    assert short_s.periods[0].accrual_start == date(2026, 1, 15)
    assert short_s.periods[0].accrual_end == date(2026, 3, 15)

    long_s = generate_leg_schedule(
        date(2026, 1, 15),
        date(2028, 3, 15),
        Frequency.SEMI_ANNUAL,
        DayCountConvention.THIRTY_360_US,
        BusinessDayConvention.UNADJUSTED,
        cal,
        StubType.FRONT_LONG,
        leg_type="FIXED",
    )
    assert len(long_s.periods) == 4
    assert long_s.periods[0].is_stub
    assert long_s.periods[0].stub_kind == "FRONT_LONG"
    assert long_s.periods[0].accrual_start == date(2026, 1, 15)
    assert long_s.periods[0].accrual_end == date(2026, 9, 15)


def test_back_short_and_back_long_stubs(calendar_registry: CalendarRegistry) -> None:
    cal = calendar_registry.get("NONE")
    short_b = generate_leg_schedule(
        date(2026, 1, 15),
        date(2028, 3, 15),
        Frequency.SEMI_ANNUAL,
        DayCountConvention.THIRTY_360_US,
        BusinessDayConvention.UNADJUSTED,
        cal,
        StubType.BACK_SHORT,
        leg_type="FIXED",
    )
    assert len(short_b.periods) == 5
    assert short_b.periods[-1].is_stub
    assert short_b.periods[-1].stub_kind == "BACK_SHORT"
    assert short_b.periods[-1].accrual_start == date(2028, 1, 15)
    assert short_b.periods[-1].accrual_end == date(2028, 3, 15)

    long_b = generate_leg_schedule(
        date(2026, 1, 15),
        date(2028, 3, 15),
        Frequency.SEMI_ANNUAL,
        DayCountConvention.THIRTY_360_US,
        BusinessDayConvention.UNADJUSTED,
        cal,
        StubType.BACK_LONG,
        leg_type="FIXED",
    )
    assert len(long_b.periods) == 4
    assert long_b.periods[-1].is_stub
    assert long_b.periods[-1].stub_kind == "BACK_LONG"
    assert long_b.periods[-1].accrual_start == date(2027, 7, 15)
    assert long_b.periods[-1].accrual_end == date(2028, 3, 15)


def test_stub_none_raises_when_unaligned(calendar_registry: CalendarRegistry) -> None:
    cal = calendar_registry.get("NONE")
    with pytest.raises(ScheduleError):
        generate_leg_schedule(
            date(2026, 1, 15),
            date(2028, 3, 15),
            Frequency.SEMI_ANNUAL,
            DayCountConvention.THIRTY_360_US,
            BusinessDayConvention.UNADJUSTED,
            cal,
            StubType.NONE,
            leg_type="FIXED",
        )


def test_eom_roll_preserves_leap_feb_29(calendar_registry: CalendarRegistry) -> None:
    cal = calendar_registry.get("NONE")
    sched = generate_leg_schedule(
        date(2027, 2, 28),
        date(2029, 2, 28),
        Frequency.SEMI_ANNUAL,
        DayCountConvention.ACT_ACT_ISDA,
        BusinessDayConvention.UNADJUSTED,
        cal,
        StubType.FRONT_SHORT,
        leg_type="FIXED",
        end_of_month=True,
    )
    ends = [p.accrual_end for p in sched.periods]
    assert ends == [
        date(2027, 8, 31),
        date(2028, 2, 29),
        date(2028, 8, 31),
        date(2029, 2, 28),
    ]
