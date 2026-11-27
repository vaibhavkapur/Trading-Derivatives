"""Pure dual-curve vanilla fixed-floating interest rate swap pricer."""

from __future__ import annotations

from datetime import date
from typing import Any

from irs_engine.diagnostics import compute_valuation_id
from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.curves import YieldCurve
from irs_engine.domain.enums import (
    CurveRole,
    FixingFallbackPolicy,
    FixingSource,
    SwapDirection,
    ValuationDateFixingPolicy,
)
from irs_engine.domain.errors import CurveError, MissingFixingError, ValidationError
from irs_engine.domain.models import (
    CashflowRow,
    CurveSnapshot,
    FixingHistory,
    SwapTrade,
    SwapValuationResult,
    ValuationConfig,
)
from irs_engine.domain.schedule import generate_swap_schedules


def _validate_inputs(
    trade: SwapTrade,
    discount_curve: YieldCurve,
    forward_curve: YieldCurve,
) -> date:
    """Enforce explicit dual-curve separation, currency, and valuation-date contracts."""
    if discount_curve.snapshot.role != CurveRole.DISCOUNT:
        raise CurveError(
            f"discount_curve must have role DISCOUNT, got {discount_curve.snapshot.role.value}.",
            details={"curve_id": discount_curve.snapshot.curve_id, "role": discount_curve.snapshot.role.value},
        )
    if forward_curve.snapshot.role != CurveRole.FORWARD:
        raise CurveError(
            f"forward_curve must have role FORWARD, got {forward_curve.snapshot.role.value}.",
            details={"curve_id": forward_curve.snapshot.curve_id, "role": forward_curve.snapshot.role.value},
        )
    if discount_curve.snapshot.currency != trade.currency:
        raise CurveError(
            f"Discount curve currency '{discount_curve.snapshot.currency}' does not match trade currency '{trade.currency}'.",
            details={
                "trade_currency": trade.currency,
                "discount_curve_currency": discount_curve.snapshot.currency,
            },
        )
    if forward_curve.snapshot.currency != trade.currency:
        raise CurveError(
            f"Forward curve currency '{forward_curve.snapshot.currency}' does not match trade currency '{trade.currency}'.",
            details={
                "trade_currency": trade.currency,
                "forward_curve_currency": forward_curve.snapshot.currency,
            },
        )
    if discount_curve.snapshot.valuation_date != forward_curve.snapshot.valuation_date:
        raise CurveError(
            "Discount curve and forward curve must share the same valuation_date.",
            details={
                "discount_valuation_date": discount_curve.snapshot.valuation_date.isoformat(),
                "forward_valuation_date": forward_curve.snapshot.valuation_date.isoformat(),
            },
        )
    if (
        forward_curve.snapshot.index_name is not None
        and forward_curve.snapshot.index_name != trade.floating_leg.index_name
    ):
        raise ValidationError(
            f"Forward curve index '{forward_curve.snapshot.index_name}' does not match floating leg index '{trade.floating_leg.index_name}'.",
            details={
                "forward_curve_index": forward_curve.snapshot.index_name,
                "floating_leg_index": trade.floating_leg.index_name,
            },
        )
    return discount_curve.snapshot.valuation_date


def price_swap(
    trade: SwapTrade,
    discount_curve: YieldCurve | CurveSnapshot,
    forward_curve: YieldCurve | CurveSnapshot,
    *,
    fixing_history: FixingHistory | None = None,
    calendar_registry: CalendarRegistry | None = None,
    config: ValuationConfig | None = None,
) -> SwapValuationResult:
    """Price a vanilla fixed-floating swap with explicit forward and discount curves.

    Sign convention:
      - PAYER:    NPV = PV(floating) - PV(fixed)
      - RECEIVER: NPV = PV(fixed) - PV(floating)
    """
    disc = discount_curve if isinstance(discount_curve, YieldCurve) else YieldCurve(discount_curve)
    fwd = forward_curve if isinstance(forward_curve, YieldCurve) else YieldCurve(forward_curve)
    fixings = fixing_history or FixingHistory()
    calendars = calendar_registry or CalendarRegistry.default()
    cfg = config or ValuationConfig()

    val_date = _validate_inputs(trade, disc, fwd)

    fixed_cal = calendars.get(trade.fixed_leg.calendar)
    float_cal = calendars.get(trade.floating_leg.calendar)
    fixed_sched, float_sched = generate_swap_schedules(trade, fixed_cal, float_cal)

    # --- Fixed Leg ---
    fixed_rows: list[CashflowRow] = []
    fixed_leg_pv = 0.0
    annuity = 0.0
    fixed_settled_count = 0
    fixed_active_count = 0

    for period in fixed_sched.periods:
        if period.payment_date <= val_date:
            fixed_settled_count += 1
            fixed_rows.append(
                CashflowRow(
                    period_index=period.period_index,
                    leg_type="FIXED",
                    accrual_start=period.accrual_start,
                    accrual_end=period.accrual_end,
                    payment_date=period.payment_date,
                    fixing_date=None,
                    year_fraction=period.year_fraction,
                    rate=trade.fixed_leg.fixed_rate,
                    spread=0.0,
                    all_in_rate=trade.fixed_leg.fixed_rate,
                    cashflow_amount=trade.notional * trade.fixed_leg.fixed_rate * period.year_fraction,
                    discount_factor=0.0,
                    present_value=0.0,
                    status="SETTLED",
                    fixing_source=FixingSource.SETTLED,
                    is_stub=period.is_stub,
                )
            )
            continue

        fixed_active_count += 1
        df = disc.discount_factor_on_date(period.payment_date)
        cf = trade.notional * trade.fixed_leg.fixed_rate * period.year_fraction
        pv = cf * df
        fixed_leg_pv += pv
        annuity += period.year_fraction * df

        fixed_rows.append(
            CashflowRow(
                period_index=period.period_index,
                leg_type="FIXED",
                accrual_start=period.accrual_start,
                accrual_end=period.accrual_end,
                payment_date=period.payment_date,
                fixing_date=None,
                year_fraction=period.year_fraction,
                rate=trade.fixed_leg.fixed_rate,
                spread=0.0,
                all_in_rate=trade.fixed_leg.fixed_rate,
                cashflow_amount=cf,
                discount_factor=df,
                present_value=pv,
                status="ACTIVE",
                fixing_source=None,
                is_stub=period.is_stub,
            )
        )

    # --- Floating Leg ---
    float_rows: list[CashflowRow] = []
    floating_leg_pv = 0.0
    float_annuity = 0.0
    float_settled_count = 0
    historical_fixings_used = 0
    projected_fixings_used = 0
    fallback_fixings_used = 0

    for period in float_sched.periods:
        if period.payment_date <= val_date:
            float_settled_count += 1
            known = (
                fixings.lookup(trade.floating_leg.index_name, period.fixing_date)
                if period.fixing_date is not None
                else None
            )
            r_val = known if known is not None else 0.0
            all_in = r_val + trade.floating_leg.spread
            float_rows.append(
                CashflowRow(
                    period_index=period.period_index,
                    leg_type="FLOATING",
                    accrual_start=period.accrual_start,
                    accrual_end=period.accrual_end,
                    payment_date=period.payment_date,
                    fixing_date=period.fixing_date,
                    year_fraction=period.year_fraction,
                    rate=r_val,
                    spread=trade.floating_leg.spread,
                    all_in_rate=all_in,
                    cashflow_amount=trade.notional * all_in * period.year_fraction,
                    discount_factor=0.0,
                    present_value=0.0,
                    status="SETTLED",
                    fixing_source=FixingSource.SETTLED,
                    is_stub=period.is_stub,
                )
            )
            continue

        assert period.fixing_date is not None
        fixing_rate, fixing_src = _resolve_floating_rate(
            index_name=trade.floating_leg.index_name,
            fixing_date=period.fixing_date,
            accrual_start=period.accrual_start,
            accrual_end=period.accrual_end,
            day_count=trade.floating_leg.day_count,
            val_date=val_date,
            forward_curve=fwd,
            fixing_history=fixings,
            config=cfg,
        )

        if fixing_src == FixingSource.HISTORICAL:
            historical_fixings_used += 1
        elif fixing_src == FixingSource.PROJECTED:
            projected_fixings_used += 1
        elif fixing_src == FixingSource.FALLBACK_PROJECTED:
            fallback_fixings_used += 1

        df = disc.discount_factor_on_date(period.payment_date)
        all_in = fixing_rate + trade.floating_leg.spread
        cf = trade.notional * all_in * period.year_fraction
        pv = cf * df
        floating_leg_pv += pv
        float_annuity += period.year_fraction * df

        float_rows.append(
            CashflowRow(
                period_index=period.period_index,
                leg_type="FLOATING",
                accrual_start=period.accrual_start,
                accrual_end=period.accrual_end,
                payment_date=period.payment_date,
                fixing_date=period.fixing_date,
                year_fraction=period.year_fraction,
                rate=fixing_rate,
                spread=trade.floating_leg.spread,
                all_in_rate=all_in,
                cashflow_amount=cf,
                discount_factor=df,
                present_value=pv,
                status="ACTIVE",
                fixing_source=fixing_src,
                is_stub=period.is_stub,
            )
        )

    # Signed NPV according to direction
    if trade.direction == SwapDirection.PAYER:
        npv = floating_leg_pv - fixed_leg_pv
    else:
        npv = fixed_leg_pv - floating_leg_pv

    # Par fixed rate & par floating spread
    par_rate = floating_leg_pv / (trade.notional * annuity) if annuity > 0.0 else 0.0
    fixed_dv01 = trade.notional * annuity * 1e-4

    # Par spread: spread s* such that PV_float(s*) == PV_fixed
    # PV_float(s) = PV_float_base + N * s * float_annuity
    pv_float_ex_spread = floating_leg_pv - trade.notional * trade.floating_leg.spread * float_annuity
    par_spread = (
        (fixed_leg_pv - pv_float_ex_spread) / (trade.notional * float_annuity)
        if float_annuity > 0.0
        else 0.0
    )

    valuation_id = compute_valuation_id(
        trade=trade,
        discount_snapshot=disc.snapshot,
        forward_snapshot=fwd.snapshot,
        fixing_history=fixings,
        config=cfg,
    )

    diagnostics: dict[str, Any] = {
        "fixed_periods_total": len(fixed_rows),
        "fixed_periods_active": fixed_active_count,
        "fixed_periods_settled": fixed_settled_count,
        "floating_periods_total": len(float_rows),
        "floating_periods_active": len(float_rows) - float_settled_count,
        "floating_periods_settled": float_settled_count,
        "historical_fixings_used": historical_fixings_used,
        "projected_fixings_used": projected_fixings_used,
        "fallback_fixings_used": fallback_fixings_used,
        "dual_curve_distinct": disc.snapshot.curve_id != fwd.snapshot.curve_id,
    }

    return SwapValuationResult(
        valuation_id=valuation_id,
        trade_id=trade.trade_id,
        valuation_date=val_date,
        currency=trade.currency,
        direction=trade.direction,
        notional=trade.notional,
        discount_curve_id=disc.snapshot.curve_id,
        forward_curve_id=fwd.snapshot.curve_id,
        fixed_leg_pv=fixed_leg_pv,
        floating_leg_pv=floating_leg_pv,
        npv=npv,
        annuity=annuity,
        fixed_dv01=fixed_dv01,
        par_rate=par_rate,
        par_spread=par_spread,
        fixed_cashflows=tuple(fixed_rows),
        floating_cashflows=tuple(float_rows),
        diagnostics=diagnostics,
    )


def _resolve_floating_rate(
    *,
    index_name: str,
    fixing_date: date,
    accrual_start: date,
    accrual_end: date,
    day_count: Any,
    val_date: date,
    forward_curve: YieldCurve,
    fixing_history: FixingHistory,
    config: ValuationConfig,
) -> tuple[float, FixingSource]:
    """Determine known historical fixing vs projected forward rate with explicit error handling."""
    known = fixing_history.lookup(index_name, fixing_date)

    if fixing_date < val_date:
        if known is not None:
            return known, FixingSource.HISTORICAL
        if config.missing_fixing_policy == FixingFallbackPolicy.PROJECT_FALLBACK:
            proj = forward_curve.forward_rate(accrual_start, accrual_end, day_count)
            return proj, FixingSource.FALLBACK_PROJECTED
        raise MissingFixingError(
            f"Missing required historical fixing for index '{index_name}' on {fixing_date.isoformat()} "
            f"(before valuation_date {val_date.isoformat()}).",
            details={
                "index_name": index_name,
                "fixing_date": fixing_date.isoformat(),
                "valuation_date": val_date.isoformat(),
                "policy": config.missing_fixing_policy.value,
            },
        )

    if fixing_date == val_date:
        if known is not None:
            return known, FixingSource.HISTORICAL
        if config.valuation_date_fixing_policy == ValuationDateFixingPolicy.REQUIRE_HISTORICAL:
            raise MissingFixingError(
                f"Missing valuation-date fixing for index '{index_name}' on {fixing_date.isoformat()} "
                f"under policy REQUIRE_HISTORICAL.",
                details={
                    "index_name": index_name,
                    "fixing_date": fixing_date.isoformat(),
                    "valuation_date": val_date.isoformat(),
                    "policy": config.valuation_date_fixing_policy.value,
                },
            )
        proj = forward_curve.forward_rate(accrual_start, accrual_end, day_count)
        return proj, FixingSource.PROJECTED

    # Future fixing_date > val_date -> project from forward curve
    proj = forward_curve.forward_rate(accrual_start, accrual_end, day_count)
    return proj, FixingSource.PROJECTED
