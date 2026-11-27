"""Payment and reset schedule generation with front/back short/long stubs and EOM rules."""

from __future__ import annotations

from datetime import date

from irs_engine.domain.calendars import BusinessCalendar, add_months, is_end_of_month
from irs_engine.domain.daycount import year_fraction
from irs_engine.domain.enums import BusinessDayConvention, DayCountConvention, Frequency, StubType
from irs_engine.domain.errors import ScheduleError
from irs_engine.domain.models import FixedLegSpec, FloatingLegSpec, LegSchedule, SchedulePeriod, SwapTrade


def generate_unadjusted_dates(
    effective_date: date,
    maturity_date: date,
    frequency: Frequency,
    stub_type: StubType,
    *,
    end_of_month: bool = False,
) -> tuple[list[date], int | None, str]:
    """Generate unadjusted schedule boundary dates and identify any stub period index.

    Returns:
        (dates, stub_period_index, stub_kind)
    """
    if maturity_date <= effective_date:
        raise ScheduleError(
            "maturity_date must be strictly after effective_date.",
            details={
                "effective_date": effective_date.isoformat(),
                "maturity_date": maturity_date.isoformat(),
            },
        )

    step = frequency.months

    if stub_type in (StubType.NONE, StubType.FRONT_SHORT, StubType.FRONT_LONG):
        # Roll backward from maturity_date
        use_eom = end_of_month and is_end_of_month(maturity_date)
        backward_dates: list[date] = [maturity_date]
        k = 1
        while True:
            cand = add_months(maturity_date, -step * k, end_of_month=use_eom)
            if cand <= effective_date:
                break
            backward_dates.append(cand)
            k += 1

        # Check exact alignment: the step that crossed/landed on effective_date
        exact_landing = add_months(maturity_date, -step * k, end_of_month=use_eom)
        is_exact = exact_landing == effective_date

        dates = [effective_date] + list(reversed(backward_dates))
        if is_exact:
            return dates, None, "REGULAR"

        if stub_type == StubType.NONE:
            raise ScheduleError(
                "Effective and maturity dates do not align with frequency and stub_type is NONE.",
                details={
                    "effective_date": effective_date.isoformat(),
                    "maturity_date": maturity_date.isoformat(),
                    "frequency": frequency.value,
                },
            )

        if stub_type == StubType.FRONT_LONG:
            if len(dates) >= 3:
                # Merge the short front stub with the first full period
                merged = [dates[0]] + dates[2:]
                return merged, 0, "FRONT_LONG"
            # Single period shorter than one full tenor cannot be lengthened
            return dates, 0, "FRONT_SHORT"

        return dates, 0, "FRONT_SHORT"

    # Forward roll for BACK_SHORT and BACK_LONG
    use_eom = end_of_month and is_end_of_month(effective_date)
    forward_dates: list[date] = [effective_date]
    k = 1
    while True:
        cand = add_months(effective_date, step * k, end_of_month=use_eom)
        if cand >= maturity_date:
            break
        forward_dates.append(cand)
        k += 1

    exact_landing = add_months(effective_date, step * k, end_of_month=use_eom)
    is_exact = exact_landing == maturity_date

    dates = forward_dates + [maturity_date]
    if is_exact:
        return dates, None, "REGULAR"

    if stub_type == StubType.BACK_LONG:
        if len(dates) >= 3:
            merged = dates[:-2] + [dates[-1]]
            return merged, len(merged) - 2, "BACK_LONG"
        return dates, len(dates) - 2, "BACK_SHORT"

    return dates, len(dates) - 2, "BACK_SHORT"


def generate_leg_schedule(
    effective_date: date,
    maturity_date: date,
    frequency: Frequency,
    day_count: DayCountConvention,
    business_day_convention: BusinessDayConvention,
    calendar: BusinessCalendar,
    stub_type: StubType,
    *,
    leg_type: str,
    end_of_month: bool = False,
    payment_lag_days: int = 0,
    fixing_lag_days: int | None = None,
) -> LegSchedule:
    """Build adjusted accrual, payment, and (optional) reset/fixing schedule for a leg."""
    unadj_dates, stub_idx, stub_kind = generate_unadjusted_dates(
        effective_date,
        maturity_date,
        frequency,
        stub_type,
        end_of_month=end_of_month,
    )

    adj_dates = [calendar.adjust(d, business_day_convention) for d in unadj_dates]

    periods: list[SchedulePeriod] = []
    for idx in range(len(unadj_dates) - 1):
        u_start = unadj_dates[idx]
        u_end = unadj_dates[idx + 1]
        a_start = adj_dates[idx]
        a_end = adj_dates[idx + 1]

        if a_end <= a_start:
            raise ScheduleError(
                f"Adjusted accrual_end ({a_end}) is not after accrual_start ({a_start}) at period {idx}.",
                details={
                    "period_index": idx,
                    "accrual_start": a_start.isoformat(),
                    "accrual_end": a_end.isoformat(),
                },
            )

        payment_date = calendar.add_business_days(a_end, payment_lag_days)
        tau = year_fraction(a_start, a_end, day_count)
        fixing_date = (
            calendar.add_business_days(a_start, -fixing_lag_days)
            if fixing_lag_days is not None
            else None
        )

        is_stub = stub_idx is not None and idx == stub_idx
        p_stub_kind = stub_kind if is_stub else "REGULAR"

        periods.append(
            SchedulePeriod(
                period_index=idx,
                unadjusted_start=u_start,
                unadjusted_end=u_end,
                accrual_start=a_start,
                accrual_end=a_end,
                payment_date=payment_date,
                year_fraction=tau,
                is_stub=is_stub,
                stub_kind=p_stub_kind,
                fixing_date=fixing_date,
            )
        )

    return LegSchedule(leg_type=leg_type, periods=tuple(periods))


def generate_swap_schedules(
    trade: SwapTrade,
    fixed_calendar: BusinessCalendar,
    floating_calendar: BusinessCalendar,
) -> tuple[LegSchedule, LegSchedule]:
    """Generate both fixed and floating schedules for a vanilla swap."""
    fl: FixedLegSpec = trade.fixed_leg
    flt: FloatingLegSpec = trade.floating_leg

    fixed_sched = generate_leg_schedule(
        trade.effective_date,
        trade.maturity_date,
        fl.frequency,
        fl.day_count,
        fl.business_day_convention,
        fixed_calendar,
        fl.stub_type,
        leg_type="FIXED",
        end_of_month=fl.end_of_month,
        payment_lag_days=fl.payment_lag_days,
        fixing_lag_days=None,
    )

    float_sched = generate_leg_schedule(
        trade.effective_date,
        trade.maturity_date,
        flt.frequency,
        flt.day_count,
        flt.business_day_convention,
        floating_calendar,
        flt.stub_type,
        leg_type="FLOATING",
        end_of_month=flt.end_of_month,
        payment_lag_days=flt.payment_lag_days,
        fixing_lag_days=flt.fixing_lag_days,
    )

    return fixed_sched, float_sched
