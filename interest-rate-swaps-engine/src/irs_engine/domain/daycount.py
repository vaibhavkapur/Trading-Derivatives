"""ISDA day-count fraction calculations."""

from __future__ import annotations

import calendar as py_calendar
from datetime import date

from irs_engine.domain.enums import DayCountConvention
from irs_engine.domain.errors import UnsupportedConventionError, ValidationError


def year_fraction(
    start_date: date,
    end_date: date,
    convention: DayCountConvention,
    *,
    allow_negative: bool = False,
) -> float:
    """Compute accrual year fraction between start_date and end_date."""
    if end_date == start_date:
        return 0.0
    if end_date < start_date:
        if not allow_negative:
            raise ValidationError(
                "end_date must be on or after start_date for day-count fraction.",
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "convention": convention.value,
                },
            )
        return -year_fraction(end_date, start_date, convention, allow_negative=False)

    if convention == DayCountConvention.ACT_360:
        return (end_date - start_date).days / 360.0

    if convention == DayCountConvention.ACT_365F:
        return (end_date - start_date).days / 365.0

    if convention == DayCountConvention.THIRTY_360_US:
        d1 = min(start_date.day, 30)
        d2 = 30 if (end_date.day == 31 and d1 == 30) else end_date.day
        days = (
            (end_date.year - start_date.year) * 360
            + (end_date.month - start_date.month) * 30
            + (d2 - d1)
        )
        return days / 360.0

    if convention == DayCountConvention.THIRTY_E_360:
        d1 = min(start_date.day, 30)
        d2 = min(end_date.day, 30)
        days = (
            (end_date.year - start_date.year) * 360
            + (end_date.month - start_date.month) * 30
            + (d2 - d1)
        )
        return days / 360.0

    if convention == DayCountConvention.ACT_ACT_ISDA:
        return _act_act_isda(start_date, end_date)

    raise UnsupportedConventionError(
        f"Unsupported day-count convention: {convention}",
        details={"convention": str(convention)},
    )


def _act_act_isda(start_date: date, end_date: date) -> float:
    """ISDA Actual/Actual day-count fraction splitting across leap/non-leap years."""
    if start_date.year == end_date.year:
        denom = 366.0 if py_calendar.isleap(start_date.year) else 365.0
        return (end_date - start_date).days / denom

    total = 0.0
    # First partial year: [start_date, Jan 1 of start_date.year + 1)
    first_year_end = date(start_date.year + 1, 1, 1)
    denom_first = 366.0 if py_calendar.isleap(start_date.year) else 365.0
    total += (first_year_end - start_date).days / denom_first

    # Full intervening years
    total += float(end_date.year - start_date.year - 1)

    # Last partial year: [Jan 1 of end_date.year, end_date)
    last_year_start = date(end_date.year, 1, 1)
    denom_last = 366.0 if py_calendar.isleap(end_date.year) else 365.0
    total += (end_date - last_year_start).days / denom_last
    return total
