"""Day-count fraction and date arithmetic utilities."""

from __future__ import annotations

import calendar as pycal
from datetime import date
from typing import Optional

from .conventions import DayCountConvention
from .errors import UnsupportedConventionError, ValidationError


def is_end_of_month(d: date) -> bool:
    """Return True if d is the last calendar day of its month."""
    return d.day == pycal.monthrange(d.year, d.month)[1]


def add_months(d: date, months: int, *, end_of_month: bool = False) -> date:
    """Add (or subtract) an integer number of months, clamping day to month end."""
    total_months = (d.year * 12 + (d.month - 1)) + months
    target_year = total_months // 12
    target_month = (total_months % 12) + 1
    last_day = pycal.monthrange(target_year, target_month)[1]
    if end_of_month:
        target_day = last_day
    else:
        target_day = min(d.day, last_day)
    return date(target_year, target_month, target_day)


def year_fraction(
    start_date: date,
    end_date: date,
    convention: DayCountConvention,
    *,
    ref_start: Optional[date] = None,
    ref_end: Optional[date] = None,
    frequency: Optional[int] = None,
) -> float:
    """Compute the accrual year fraction between start_date and end_date.

    Conventions:
    - ACT/360: actual days / 360
    - ACT/365F: actual days / 365
    - 30/360_US (Bond Basis / NASD): standard US 30/360 with Feb EOM handling
    - 30E/360 (Eurobond Basis): d1 = min(d1, 30), d2 = min(d2, 30)
    - ACT/ACT_ICMA: actual days / (frequency * days_in_reference_period)
      When ref_start/ref_end and frequency are provided (coupon accruals/stubs),
      uses exact ICMA period denominator; when omitted (e.g. standalone curve
      tenor time), falls back to ACT/365.25-free exact per-year ACT/ACT ISDA split.
    """
    if end_date < start_date:
        raise ValidationError(
            "end_date must be on or after start_date for year_fraction",
            details={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "convention": convention.value,
            },
        )
    if start_date == end_date:
        return 0.0

    actual_days = (end_date - start_date).days

    if convention == DayCountConvention.ACT_360:
        return actual_days / 360.0

    if convention == DayCountConvention.ACT_365F:
        return actual_days / 365.0

    if convention == DayCountConvention.THIRTY_360_US:
        d1, m1, y1 = start_date.day, start_date.month, start_date.year
        d2, m2, y2 = end_date.day, end_date.month, end_date.year

        is_feb_eom_1 = m1 == 2 and is_end_of_month(start_date)
        is_feb_eom_2 = m2 == 2 and is_end_of_month(end_date)

        if is_feb_eom_1 and is_feb_eom_2:
            d2 = 30
        if is_feb_eom_1 or d1 == 31:
            d1 = 30
        if d2 == 31 and d1 == 30:
            d2 = 30

        days_360 = 360 * (y2 - y1) + 30 * (m2 - m1) + (d2 - d1)
        return days_360 / 360.0

    if convention == DayCountConvention.THIRTY_E_360:
        d1 = min(start_date.day, 30)
        d2 = min(end_date.day, 30)
        days_360 = (
            360 * (end_date.year - start_date.year)
            + 30 * (end_date.month - start_date.month)
            + (d2 - d1)
        )
        return days_360 / 360.0

    if convention == DayCountConvention.ACT_ACT_ICMA:
        if ref_start is not None and ref_end is not None and frequency is not None:
            if frequency <= 0:
                raise ValidationError(
                    "frequency must be positive for ACT/ACT_ICMA",
                    details={"frequency": frequency},
                )
            ref_days = (ref_end - ref_start).days
            if ref_days <= 0:
                raise ValidationError(
                    "ref_end must be strictly after ref_start for ACT/ACT_ICMA",
                    details={
                        "ref_start": ref_start.isoformat(),
                        "ref_end": ref_end.isoformat(),
                    },
                )
            return actual_days / (frequency * ref_days)
        # Fallback for curve/time-to-maturity queries without coupon period context:
        # exact per-calendar-year split (ISDA ACT/ACT).
        return _act_act_isda(start_date, end_date)

    raise UnsupportedConventionError(
        f"Unsupported day-count convention: {convention}",
        details={"convention": str(convention)},
    )


def _act_act_isda(start_date: date, end_date: date) -> float:
    """Exact ACT/ACT ISDA split across calendar years."""
    if start_date.year == end_date.year:
        denom = 366.0 if pycal.isleap(start_date.year) else 365.0
        return (end_date - start_date).days / denom

    total = 0.0
    # First partial year
    end_of_first_year = date(start_date.year + 1, 1, 1)
    denom_first = 366.0 if pycal.isleap(start_date.year) else 365.0
    total += (end_of_first_year - start_date).days / denom_first

    # Whole intermediate years
    total += float(end_date.year - start_date.year - 1)

    # Final partial year
    start_of_last_year = date(end_date.year, 1, 1)
    denom_last = 366.0 if pycal.isleap(end_date.year) else 365.0
    total += (end_date - start_of_last_year).days / denom_last
    return total
