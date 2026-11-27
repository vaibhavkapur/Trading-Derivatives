"""Bond coupon cash-flow scheduler and accrued-interest engine."""

from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

from .conventions import Calendar, DayCountConvention, StubType, resolve_calendar
from .daycount import add_months, is_end_of_month, year_fraction
from .errors import ScheduleError, ValidationError
from .models import (
    AccruedInterestResult,
    BondSpec,
    CashFlow,
    CashFlowSchedule,
    stable_id,
)


def _generate_unadjusted_dates(bond: BondSpec) -> Tuple[List[date], List[str]]:
    """Generate unadjusted accrual boundary dates and stub tags per period.

    Returns:
        dates: strictly increasing list [d_0, d_1, ..., d_n] where d_0 == issue_date
               and d_n == maturity_date.
        stub_kinds: list of length n with one of:
               "REGULAR", "SHORT_FIRST", "LONG_FIRST", "SHORT_LAST", "LONG_LAST".
    """
    if bond.frequency == 0:
        return [bond.issue_date, bond.maturity_date], ["ZERO_COUPON"]

    step_months = 12 // bond.frequency
    eom = bond.end_of_month and is_end_of_month(bond.maturity_date)

    # Case 1: Explicit first_coupon_date or penultimate_coupon_date
    if bond.first_coupon_date is not None or bond.penultimate_coupon_date is not None:
        return _generate_explicit_stub_dates(bond, step_months, eom)

    # Case 2: Back-stub generation (roll forward from issue_date)
    if bond.stub_type in (StubType.SHORT_LAST, StubType.LONG_LAST):
        forward_eom = bond.end_of_month and is_end_of_month(bond.issue_date)
        dates = [bond.issue_date]
        k = 1
        while True:
            nxt = add_months(bond.issue_date, k * step_months, end_of_month=forward_eom)
            if nxt >= bond.maturity_date:
                break
            dates.append(nxt)
            k += 1

        if nxt == bond.maturity_date:
            dates.append(bond.maturity_date)
            return dates, ["REGULAR"] * (len(dates) - 1)

        if bond.stub_type == StubType.LONG_LAST and len(dates) >= 2:
            dates.pop()
            dates.append(bond.maturity_date)
            kinds = ["REGULAR"] * (len(dates) - 2) + ["LONG_LAST"]
            return dates, kinds

        dates.append(bond.maturity_date)
        kinds = ["REGULAR"] * (len(dates) - 2) + ["SHORT_LAST"]
        return dates, kinds

    # Case 3: Standard backward generation from maturity_date (NONE, SHORT_FIRST, LONG_FIRST)
    rev_dates = [bond.maturity_date]
    k = 1
    while True:
        prev = add_months(bond.maturity_date, -k * step_months, end_of_month=eom)
        if prev <= bond.issue_date:
            break
        rev_dates.append(prev)
        k += 1

    if prev == bond.issue_date:
        rev_dates.append(bond.issue_date)
        dates = list(reversed(rev_dates))
        return dates, ["REGULAR"] * (len(dates) - 1)

    if bond.stub_type == StubType.NONE:
        raise ScheduleError(
            "Issue and maturity dates do not align to regular coupon periods with stub_type=NONE",
            details={
                "bond_id": bond.bond_id,
                "issue_date": bond.issue_date.isoformat(),
                "maturity_date": bond.maturity_date.isoformat(),
                "frequency": bond.frequency,
            },
        )

    if bond.stub_type == StubType.LONG_FIRST and len(rev_dates) >= 2:
        rev_dates.pop()
        rev_dates.append(bond.issue_date)
        dates = list(reversed(rev_dates))
        kinds = ["LONG_FIRST"] + ["REGULAR"] * (len(dates) - 2)
        return dates, kinds

    rev_dates.append(bond.issue_date)
    dates = list(reversed(rev_dates))
    kinds = ["SHORT_FIRST"] + ["REGULAR"] * (len(dates) - 2)
    return dates, kinds


def _generate_explicit_stub_dates(
    bond: BondSpec, step_months: int, eom: bool
) -> Tuple[List[date], List[str]]:
    """Build schedule honoring explicit first_coupon_date and/or penultimate_coupon_date."""
    start_regular = bond.first_coupon_date or bond.issue_date
    end_regular = bond.penultimate_coupon_date or bond.maturity_date

    # Build interior regular dates stepping forward from start_regular
    reg_eom = bond.end_of_month and is_end_of_month(start_regular)
    interior = [start_regular]
    k = 1
    while True:
        nxt = add_months(start_regular, k * step_months, end_of_month=reg_eom)
        if nxt > end_regular:
            break
        interior.append(nxt)
        if nxt == end_regular:
            break
        k += 1

    if interior[-1] != end_regular:
        raise ScheduleError(
            "Regular schedule between first_coupon_date and penultimate_coupon_date "
            "does not align on exact coupon multiples",
            details={
                "bond_id": bond.bond_id,
                "start_regular": start_regular.isoformat(),
                "end_regular": end_regular.isoformat(),
                "last_generated": interior[-1].isoformat(),
            },
        )

    dates: List[date] = []
    kinds: List[str] = []

    if bond.first_coupon_date is not None and bond.first_coupon_date != bond.issue_date:
        dates.append(bond.issue_date)
        expected_prev = add_months(bond.first_coupon_date, -step_months, end_of_month=reg_eom)
        if bond.issue_date > expected_prev:
            kinds.append("SHORT_FIRST")
        elif bond.issue_date < expected_prev:
            kinds.append("LONG_FIRST")
        else:
            kinds.append("REGULAR")

    dates.extend(interior)
    kinds.extend(["REGULAR"] * (len(interior) - 1))

    if bond.penultimate_coupon_date is not None and bond.penultimate_coupon_date != bond.maturity_date:
        dates.append(bond.maturity_date)
        expected_next = add_months(
            bond.penultimate_coupon_date, step_months, end_of_month=reg_eom
        )
        if bond.maturity_date < expected_next:
            kinds.append("SHORT_LAST")
        elif bond.maturity_date > expected_next:
            kinds.append("LONG_LAST")
        else:
            kinds.append("REGULAR")

    return dates, kinds


def _icma_stub_year_fraction(
    start_date: date,
    end_date: date,
    frequency: int,
    stub_kind: str,
    end_of_month: bool,
) -> float:
    """Compute exact ACT/ACT ICMA year fraction for regular, short, or long coupon stubs.

    Follows ICMA Rule 251:
    - Regular period: 1 / frequency
    - Short front stub: days(start, end) / (frequency * days(notional_start, end))
    - Short back stub: days(start, end) / (frequency * days(start, notional_end))
    - Long front stub: split backward from end_date into notional regular sub-periods
    - Long back stub: split forward from start_date into notional regular sub-periods
    """
    step_months = 12 // frequency
    if stub_kind == "REGULAR":
        return 1.0 / frequency

    if stub_kind == "SHORT_FIRST":
        ref_start = add_months(end_date, -step_months, end_of_month=end_of_month)
        return year_fraction(
            start_date,
            end_date,
            DayCountConvention.ACT_ACT_ICMA,
            ref_start=ref_start,
            ref_end=end_date,
            frequency=frequency,
        )

    if stub_kind == "SHORT_LAST":
        ref_end = add_months(start_date, step_months, end_of_month=end_of_month)
        return year_fraction(
            start_date,
            end_date,
            DayCountConvention.ACT_ACT_ICMA,
            ref_start=start_date,
            ref_end=ref_end,
            frequency=frequency,
        )

    if stub_kind == "LONG_FIRST":
        # Roll backward from end_date in notional regular periods
        total = 0.0
        cur_end = end_date
        while cur_end > start_date:
            cur_start = add_months(cur_end, -step_months, end_of_month=end_of_month)
            sub_start = max(cur_start, start_date)
            total += year_fraction(
                sub_start,
                cur_end,
                DayCountConvention.ACT_ACT_ICMA,
                ref_start=cur_start,
                ref_end=cur_end,
                frequency=frequency,
            )
            cur_end = cur_start
        return total

    if stub_kind == "LONG_LAST":
        # Roll forward from start_date in notional regular periods
        total = 0.0
        cur_start = start_date
        while cur_start < end_date:
            cur_end = add_months(cur_start, step_months, end_of_month=end_of_month)
            sub_end = min(cur_end, end_date)
            total += year_fraction(
                cur_start,
                sub_end,
                DayCountConvention.ACT_ACT_ICMA,
                ref_start=cur_start,
                ref_end=cur_end,
                frequency=frequency,
            )
            cur_start = cur_end
        return total

    raise ScheduleError(f"Unexpected stub_kind '{stub_kind}'")


def build_schedule(bond: BondSpec, calendar: Optional[Calendar] = None) -> CashFlowSchedule:
    """Build the full deterministic cash-flow schedule for a bond."""
    cal = calendar or resolve_calendar(bond.calendar_name)
    unadj_dates, stub_kinds = _generate_unadjusted_dates(bond)

    cash_flows: List[CashFlow] = []
    eom = bond.end_of_month and is_end_of_month(bond.maturity_date)

    for idx in range(len(unadj_dates) - 1):
        raw_start = unadj_dates[idx]
        raw_end = unadj_dates[idx + 1]
        kind = stub_kinds[idx]

        accrual_start = (
            cal.adjust(raw_start, bond.business_day_convention)
            if bond.adjust_accrual_dates
            else raw_start
        )
        accrual_end = (
            cal.adjust(raw_end, bond.business_day_convention)
            if bond.adjust_accrual_dates
            else raw_end
        )
        payment_date = cal.adjust(raw_end, bond.business_day_convention)

        if accrual_end <= accrual_start:
            raise ScheduleError(
                "Adjusted accrual_end is not after accrual_start",
                details={
                    "bond_id": bond.bond_id,
                    "accrual_start": accrual_start.isoformat(),
                    "accrual_end": accrual_end.isoformat(),
                },
            )

        if bond.frequency == 0:
            alpha = year_fraction(accrual_start, accrual_end, bond.day_count)
            coupon_amt = 0.0
            is_stub = False
        else:
            if bond.day_count == DayCountConvention.ACT_ACT_ICMA:
                alpha = _icma_stub_year_fraction(
                    accrual_start, accrual_end, bond.frequency, kind, eom
                )
            else:
                alpha = year_fraction(accrual_start, accrual_end, bond.day_count)
            coupon_amt = bond.notional * bond.coupon_rate * alpha
            is_stub = kind != "REGULAR"

        principal_amt = bond.notional if idx == len(unadj_dates) - 2 else 0.0

        cash_flows.append(
            CashFlow(
                index=idx + 1,
                accrual_start=accrual_start,
                accrual_end=accrual_end,
                payment_date=payment_date,
                accrual_factor=alpha,
                coupon_amount=coupon_amt,
                principal_amount=principal_amt,
                is_stub=is_stub,
                stub_kind=kind,
            )
        )

    total_coupons = sum(cf.coupon_amount for cf in cash_flows)
    total_principal = sum(cf.principal_amount for cf in cash_flows)
    total_cash_flows = sum(cf.total_amount for cf in cash_flows)

    if abs((total_coupons + total_principal) - total_cash_flows) > 1e-9:
        raise ScheduleError(
            "Internal cash-flow reconciliation invariant failed",
            details={
                "bond_id": bond.bond_id,
                "total_coupons": total_coupons,
                "total_principal": total_principal,
                "total_cash_flows": total_cash_flows,
            },
        )

    sched_id = stable_id(
        "sched",
        {
            "bond": bond.to_dict(),
            "calendar": cal.name,
            "num_flows": len(cash_flows),
        },
    )

    return CashFlowSchedule(
        bond_id=bond.bond_id,
        currency=bond.currency,
        cash_flows=tuple(cash_flows),
        total_coupons=total_coupons,
        total_principal=total_principal,
        total_cash_flows=total_cash_flows,
        schedule_id=sched_id,
    )


def compute_accrued_interest(
    bond: BondSpec,
    valuation_date: date,
    schedule: Optional[CashFlowSchedule] = None,
    calendar: Optional[Calendar] = None,
) -> AccruedInterestResult:
    """Compute auditable accrued interest at valuation_date.

    Rules:
    - Valuation before issue_date or after maturity_date raises ValidationError.
    - Zero-coupon bonds always have 0.0 accrued interest.
    - Valuation on issue_date, any coupon accrual_start, or maturity_date has 0.0 accrued.
    - Within an active accrual period [accrual_start, accrual_end), accrued interest uses
      the bond's exact day-count convention (including ICMA notional period splitting for stubs).
    """
    if valuation_date < bond.issue_date:
        raise ValidationError(
            "valuation_date cannot be before bond issue_date",
            details={
                "bond_id": bond.bond_id,
                "valuation_date": valuation_date.isoformat(),
                "issue_date": bond.issue_date.isoformat(),
            },
        )
    if valuation_date > bond.maturity_date:
        raise ValidationError(
            "valuation_date cannot be after bond maturity_date",
            details={
                "bond_id": bond.bond_id,
                "valuation_date": valuation_date.isoformat(),
                "maturity_date": bond.maturity_date.isoformat(),
            },
        )

    if bond.is_zero_coupon or valuation_date == bond.maturity_date:
        return AccruedInterestResult(
            valuation_date=valuation_date,
            accrual_start=None,
            accrual_end=None,
            accrued_days=0,
            period_days=0,
            accrued_fraction=0.0,
            accrued_interest=0.0,
            accrued_pct_of_par=0.0,
            is_on_coupon_date=(valuation_date == bond.issue_date or valuation_date == bond.maturity_date),
        )

    sched = schedule or build_schedule(bond, calendar=calendar)

    active_cf: Optional[CashFlow] = None
    for cf in sched.cash_flows:
        if cf.accrual_start <= valuation_date < cf.accrual_end:
            active_cf = cf
            break

    if active_cf is None:
        return AccruedInterestResult(
            valuation_date=valuation_date,
            accrual_start=None,
            accrual_end=None,
            accrued_days=0,
            period_days=0,
            accrued_fraction=0.0,
            accrued_interest=0.0,
            accrued_pct_of_par=0.0,
            is_on_coupon_date=True,
        )

    accrued_days = (valuation_date - active_cf.accrual_start).days
    period_days = (active_cf.accrual_end - active_cf.accrual_start).days

    if accrued_days == 0:
        return AccruedInterestResult(
            valuation_date=valuation_date,
            accrual_start=active_cf.accrual_start,
            accrual_end=active_cf.accrual_end,
            accrued_days=0,
            period_days=period_days,
            accrued_fraction=0.0,
            accrued_interest=0.0,
            accrued_pct_of_par=0.0,
            is_on_coupon_date=True,
        )

    eom = bond.end_of_month and is_end_of_month(bond.maturity_date)
    if bond.day_count == DayCountConvention.ACT_ACT_ICMA:
        if active_cf.stub_kind == "REGULAR":
            accrued_frac = year_fraction(
                active_cf.accrual_start,
                valuation_date,
                DayCountConvention.ACT_ACT_ICMA,
                ref_start=active_cf.accrual_start,
                ref_end=active_cf.accrual_end,
                frequency=bond.frequency,
            )
        elif active_cf.stub_kind in ("SHORT_FIRST", "LONG_FIRST"):
            # ICMA accrued fraction for front stub = full stub fraction minus remaining
            full_frac = active_cf.accrual_factor
            step_months = 12 // bond.frequency
            # Roll backward from accrual_end to valuation_date
            rem_frac = 0.0
            cur_end = active_cf.accrual_end
            while cur_end > valuation_date:
                cur_start = add_months(cur_end, -step_months, end_of_month=eom)
                sub_start = max(cur_start, valuation_date)
                rem_frac += year_fraction(
                    sub_start,
                    cur_end,
                    DayCountConvention.ACT_ACT_ICMA,
                    ref_start=cur_start,
                    ref_end=cur_end,
                    frequency=bond.frequency,
                )
                cur_end = cur_start
            accrued_frac = max(0.0, full_frac - rem_frac)
        else:
            # SHORT_LAST / LONG_LAST: roll forward from accrual_start to valuation_date
            step_months = 12 // bond.frequency
            accrued_frac = 0.0
            cur_start = active_cf.accrual_start
            while cur_start < valuation_date:
                cur_end = add_months(cur_start, step_months, end_of_month=eom)
                sub_end = min(cur_end, valuation_date)
                accrued_frac += year_fraction(
                    cur_start,
                    sub_end,
                    DayCountConvention.ACT_ACT_ICMA,
                    ref_start=cur_start,
                    ref_end=cur_end,
                    frequency=bond.frequency,
                )
                cur_start = cur_end
    else:
        accrued_frac = year_fraction(active_cf.accrual_start, valuation_date, bond.day_count)

    accrued_interest = bond.notional * bond.coupon_rate * accrued_frac
    return AccruedInterestResult(
        valuation_date=valuation_date,
        accrual_start=active_cf.accrual_start,
        accrual_end=active_cf.accrual_end,
        accrued_days=accrued_days,
        period_days=period_days,
        accrued_fraction=accrued_frac,
        accrued_interest=accrued_interest,
        accrued_pct_of_par=(accrued_interest / bond.notional) * 100.0,
        is_on_coupon_date=False,
    )
