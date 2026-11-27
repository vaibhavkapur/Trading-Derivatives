"""Par rate, parallel PV01, convexity, and key-rate (KR01) sensitivity engine."""

from __future__ import annotations

from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.curves import YieldCurve
from irs_engine.domain.enums import CurveRole, FiniteDifferenceScheme
from irs_engine.domain.models import (
    CurveSnapshot,
    FixingHistory,
    KeyRateSensitivity,
    SensitivityResult,
    SwapTrade,
    ValuationConfig,
)
from irs_engine.domain.pricer import price_swap


def _bump_derivative(
    npv_base: float,
    npv_up: float,
    npv_down: float | None,
    bump_bps: float,
    scheme: FiniteDifferenceScheme,
) -> float:
    """First derivative in currency units per +1 bp."""
    if scheme == FiniteDifferenceScheme.CENTRAL:
        assert npv_down is not None
        return (npv_up - npv_down) / (2.0 * bump_bps)
    return (npv_up - npv_base) / bump_bps


def compute_sensitivities(
    trade: SwapTrade,
    discount_curve: YieldCurve | CurveSnapshot,
    forward_curve: YieldCurve | CurveSnapshot,
    *,
    fixing_history: FixingHistory | None = None,
    calendar_registry: CalendarRegistry | None = None,
    config: ValuationConfig | None = None,
    bump_bps: float | None = None,
    scheme: FiniteDifferenceScheme | None = None,
) -> SensitivityResult:
    """Compute parallel PV01 (discount, forward, combined), convexity, and pillar KR01s.

    Reconciles:
      - sum(discount_kr01) vs discount_pv01
      - sum(forward_kr01)  vs forward_pv01
      - sum(discount_kr01) + sum(forward_kr01) vs parallel_pv01
    """
    disc = discount_curve if isinstance(discount_curve, YieldCurve) else YieldCurve(discount_curve)
    fwd = forward_curve if isinstance(forward_curve, YieldCurve) else YieldCurve(forward_curve)
    fixings = fixing_history or FixingHistory()
    calendars = calendar_registry or CalendarRegistry.default()
    cfg = config or ValuationConfig()

    h = bump_bps if bump_bps is not None else cfg.default_bump_bps
    fd_scheme = scheme if scheme is not None else cfg.finite_difference_scheme

    base_res = price_swap(
        trade,
        disc,
        fwd,
        fixing_history=fixings,
        calendar_registry=calendars,
        config=cfg,
    )
    base_npv = base_res.npv

    # 1. Parallel discount PV01
    disc_up = price_swap(
        trade, disc.bumped_parallel(h), fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
    ).npv
    disc_dn = (
        price_swap(
            trade, disc.bumped_parallel(-h), fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
        ).npv
        if fd_scheme == FiniteDifferenceScheme.CENTRAL
        else None
    )
    discount_pv01 = _bump_derivative(base_npv, disc_up, disc_dn, h, fd_scheme)

    # 2. Parallel forward PV01
    fwd_up = price_swap(
        trade, disc, fwd.bumped_parallel(h), fixing_history=fixings, calendar_registry=calendars, config=cfg
    ).npv
    fwd_dn = (
        price_swap(
            trade, disc, fwd.bumped_parallel(-h), fixing_history=fixings, calendar_registry=calendars, config=cfg
        ).npv
        if fd_scheme == FiniteDifferenceScheme.CENTRAL
        else None
    )
    forward_pv01 = _bump_derivative(base_npv, fwd_up, fwd_dn, h, fd_scheme)

    # 3. Combined parallel PV01 & convexity (both curves shifted simultaneously)
    both_up = price_swap(
        trade,
        disc.bumped_parallel(h),
        fwd.bumped_parallel(h),
        fixing_history=fixings,
        calendar_registry=calendars,
        config=cfg,
    ).npv
    both_dn = price_swap(
        trade,
        disc.bumped_parallel(-h),
        fwd.bumped_parallel(-h),
        fixing_history=fixings,
        calendar_registry=calendars,
        config=cfg,
    ).npv
    parallel_pv01 = _bump_derivative(
        base_npv,
        both_up,
        both_dn if fd_scheme == FiniteDifferenceScheme.CENTRAL else None,
        h,
        fd_scheme,
    )
    parallel_convexity = (both_up - 2.0 * base_npv + both_dn) / (h**2)

    # 4. Key-rate sensitivities on discount curve
    disc_kr_list: list[KeyRateSensitivity] = []
    for idx, node in enumerate(disc.snapshot.nodes):
        up_v = price_swap(
            trade,
            disc.bumped_pillar(idx, h),
            fwd,
            fixing_history=fixings,
            calendar_registry=calendars,
            config=cfg,
        ).npv
        dn_v = (
            price_swap(
                trade,
                disc.bumped_pillar(idx, -h),
                fwd,
                fixing_history=fixings,
                calendar_registry=calendars,
                config=cfg,
            ).npv
            if fd_scheme == FiniteDifferenceScheme.CENTRAL
            else None
        )
        kr_val = _bump_derivative(base_npv, up_v, dn_v, h, fd_scheme)
        disc_kr_list.append(
            KeyRateSensitivity(
                curve_role=CurveRole.DISCOUNT,
                curve_id=disc.snapshot.curve_id,
                tenor_years=node.tenor_years,
                label=node.label or f"{node.tenor_years}Y",
                kr01=kr_val,
            )
        )

    # 5. Key-rate sensitivities on forward curve
    fwd_kr_list: list[KeyRateSensitivity] = []
    for idx, node in enumerate(fwd.snapshot.nodes):
        up_v = price_swap(
            trade,
            disc,
            fwd.bumped_pillar(idx, h),
            fixing_history=fixings,
            calendar_registry=calendars,
            config=cfg,
        ).npv
        dn_v = (
            price_swap(
                trade,
                disc,
                fwd.bumped_pillar(idx, -h),
                fixing_history=fixings,
                calendar_registry=calendars,
                config=cfg,
            ).npv
            if fd_scheme == FiniteDifferenceScheme.CENTRAL
            else None
        )
        kr_val = _bump_derivative(base_npv, up_v, dn_v, h, fd_scheme)
        fwd_kr_list.append(
            KeyRateSensitivity(
                curve_role=CurveRole.FORWARD,
                curve_id=fwd.snapshot.curve_id,
                tenor_years=node.tenor_years,
                label=node.label or f"{node.tenor_years}Y",
                kr01=kr_val,
            )
        )

    disc_kr_sum = sum(k.kr01 for k in disc_kr_list)
    fwd_kr_sum = sum(k.kr01 for k in fwd_kr_list)
    total_kr_sum = disc_kr_sum + fwd_kr_sum

    disc_diff = disc_kr_sum - discount_pv01
    fwd_diff = fwd_kr_sum - forward_pv01
    total_diff = total_kr_sum - parallel_pv01
    denom = max(abs(parallel_pv01), 1e-12)
    rel_diff = abs(total_diff) / denom

    within_tol = (
        abs(total_diff) <= cfg.kr01_reconciliation_abs_tolerance
        or rel_diff <= cfg.kr01_reconciliation_rel_tolerance
    )

    return SensitivityResult(
        valuation_id=base_res.valuation_id,
        trade_id=trade.trade_id,
        valuation_date=base_res.valuation_date,
        currency=trade.currency,
        direction=trade.direction,
        bump_bps=h,
        scheme=fd_scheme,
        base_npv=base_npv,
        par_rate=base_res.par_rate,
        discount_pv01=discount_pv01,
        forward_pv01=forward_pv01,
        parallel_pv01=parallel_pv01,
        parallel_convexity=parallel_convexity,
        discount_kr01=tuple(disc_kr_list),
        forward_kr01=tuple(fwd_kr_list),
        discount_kr01_sum=disc_kr_sum,
        forward_kr01_sum=fwd_kr_sum,
        total_kr01_sum=total_kr_sum,
        discount_reconciliation_diff=disc_diff,
        forward_reconciliation_diff=fwd_diff,
        total_reconciliation_diff=total_diff,
        total_reconciliation_rel_diff=rel_diff,
        reconciliation_within_tolerance=within_tol,
    )
