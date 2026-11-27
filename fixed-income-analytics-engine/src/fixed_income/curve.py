"""Yield curve representation, bootstrapping, and scenario transformations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .conventions import (
    Compounding,
    Currency,
    DayCountConvention,
    InterpolationMethod,
    resolve_calendar,
)
from .daycount import year_fraction
from .errors import CalibrationError, UnsupportedConventionError, ValidationError
from .models import (
    BondQuote,
    CalibrationResidual,
    CurveNode,
    DepositQuote,
    ScenarioSpec,
    stable_id,
)
from .schedule import build_schedule, compute_accrued_interest
from .solvers import brentq


@dataclass(frozen=True)
class YieldCurve:
    """Immutable, auditable discount-factor and zero-rate term structure."""

    curve_id: str
    valuation_date: date
    currency: Currency
    day_count: DayCountConvention
    interpolation: InterpolationMethod
    nodes: Tuple[CurveNode, ...]
    residuals: Tuple[CalibrationResidual, ...] = ()
    allow_negative_zero_rates: bool = False

    def __post_init__(self) -> None:
        if len(self.nodes) < 2:
            raise ValidationError(
                "YieldCurve requires at least 2 nodes (anchor at t=0 plus >=1 maturity node)",
                details={"num_nodes": len(self.nodes)},
            )
        if abs(self.nodes[0].time_years) > 1e-12 or abs(self.nodes[0].discount_factor - 1.0) > 1e-12:
            raise ValidationError(
                "First YieldCurve node must be the t=0 anchor with DF=1.0",
                details={"first_node": self.nodes[0].to_dict()},
            )
        prev_t = self.nodes[0].time_years
        for node in self.nodes[1:]:
            if node.time_years <= prev_t:
                raise ValidationError(
                    "YieldCurve node times must be strictly increasing",
                    details={"prev_t": prev_t, "node": node.to_dict()},
                )
            if node.discount_factor <= 0.0 or not math.isfinite(node.discount_factor):
                raise ValidationError(
                    "YieldCurve discount factors must be finite and strictly positive",
                    details={"node": node.to_dict()},
                )
            if not self.allow_negative_zero_rates and node.discount_factor > 1.0 + 1e-12:
                raise CalibrationError(
                    "Negative zero rate detected (DF > 1.0) while allow_negative_zero_rates=False",
                    details={"node": node.to_dict()},
                )
            prev_t = node.time_years

    def time_from_valuation(self, target_date: date) -> float:
        """Compute curve time in years from valuation_date to target_date."""
        return year_fraction(self.valuation_date, target_date, self.day_count)

    def discount_factor(self, t: float) -> float:
        """Evaluate discount factor DF(t) for t >= 0."""
        if not math.isfinite(t) or t < 0.0:
            raise ValidationError(
                "Curve time t must be finite and non-negative",
                details={"t": t},
            )
        if t == 0.0:
            return 1.0

        times = [n.time_years for n in self.nodes]
        dfs = [n.discount_factor for n in self.nodes]
        zeros = [n.zero_rate_continuous for n in self.nodes]

        if self.interpolation == InterpolationMethod.LOG_LINEAR_DF:
            if t >= times[-1]:
                # Flat forward rate extrapolation from the last segment
                t_prev, t_last = times[-2], times[-1]
                fwd = -(math.log(dfs[-1]) - math.log(dfs[-2])) / (t_last - t_prev)
                return dfs[-1] * math.exp(-fwd * (t - t_last))

            # Locate bracketing interval [t_i, t_{i+1}]
            for idx in range(len(times) - 1):
                t0, t1 = times[idx], times[idx + 1]
                if t0 <= t <= t1:
                    w = (t - t0) / (t1 - t0)
                    ln_df = (1.0 - w) * math.log(dfs[idx]) + w * math.log(dfs[idx + 1])
                    return math.exp(ln_df)

        elif self.interpolation == InterpolationMethod.LINEAR_ZERO:
            if t <= times[1]:
                z = zeros[1]
                return math.exp(-z * t)
            if t >= times[-1]:
                z = zeros[-1]
                return math.exp(-z * t)
            for idx in range(1, len(times) - 1):
                t0, t1 = times[idx], times[idx + 1]
                if t0 <= t <= t1:
                    w = (t - t0) / (t1 - t0)
                    z = (1.0 - w) * zeros[idx] + w * zeros[idx + 1]
                    return math.exp(-z * t)
        else:
            raise UnsupportedConventionError(
                f"Unsupported interpolation method: {self.interpolation}",
                details={"interpolation": str(self.interpolation)},
            )

        raise CalibrationError("Failed to bracket curve time t", details={"t": t})

    def discount_factor_on(self, target_date: date) -> float:
        """Evaluate discount factor DF(target_date)."""
        return self.discount_factor(self.time_from_valuation(target_date))

    def zero_rate(
        self,
        t: float,
        compounding: Compounding = Compounding.CONTINUOUS,
    ) -> float:
        """Evaluate zero rate for maturity t > 0 under the requested compounding."""
        if t <= 0.0:
            raise ValidationError("zero_rate requires strictly positive t", details={"t": t})
        df = self.discount_factor(t)
        if compounding == Compounding.CONTINUOUS:
            return -math.log(df) / t
        if compounding == Compounding.SIMPLE:
            return (1.0 / df - 1.0) / t
        m = compounding.periods_per_year
        return m * (df ** (-1.0 / (m * t)) - 1.0)

    def forward_rate(
        self,
        t1: float,
        t2: float,
        compounding: Compounding = Compounding.CONTINUOUS,
    ) -> float:
        """Evaluate implied forward rate between t1 and t2 (0 <= t1 < t2)."""
        if t1 < 0.0 or t2 <= t1:
            raise ValidationError(
                "forward_rate requires 0 <= t1 < t2",
                details={"t1": t1, "t2": t2},
            )
        df1 = self.discount_factor(t1)
        df2 = self.discount_factor(t2)
        dt = t2 - t1
        ratio = df1 / df2
        if compounding == Compounding.CONTINUOUS:
            return math.log(ratio) / dt
        if compounding == Compounding.SIMPLE:
            return (ratio - 1.0) / dt
        m = compounding.periods_per_year
        return m * (ratio ** (1.0 / (m * dt)) - 1.0)

    def with_node_zero_shifts(
        self,
        shifts_bp: Dict[str, float],
        *,
        label: str = "shifted",
    ) -> "YieldCurve":
        """Create a new YieldCurve with continuous zero-rate shifts (in bp) at named nodes."""
        new_nodes: List[CurveNode] = [self.nodes[0]]
        for node in self.nodes[1:]:
            dz = shifts_bp.get(node.instrument_id, 0.0) * 1e-4
            new_z = node.zero_rate_continuous + dz
            new_df = math.exp(-new_z * node.time_years)
            new_nodes.append(
                CurveNode(
                    instrument_id=node.instrument_id,
                    instrument_type=node.instrument_type,
                    maturity_date=node.maturity_date,
                    time_years=node.time_years,
                    discount_factor=new_df,
                    zero_rate_continuous=new_z,
                )
            )
        new_id = stable_id(
            "curve",
            {
                "base_curve_id": self.curve_id,
                "label": label,
                "shifts_bp": shifts_bp,
            },
        )
        return YieldCurve(
            curve_id=new_id,
            valuation_date=self.valuation_date,
            currency=self.currency,
            day_count=self.day_count,
            interpolation=self.interpolation,
            nodes=tuple(new_nodes),
            residuals=(),
            allow_negative_zero_rates=True,
        )

    def apply_scenario(self, scenario: ScenarioSpec) -> "YieldCurve":
        """Apply a named curve shift scenario (PARALLEL, STEEPENER, FLATTENER, BUTTERFLY, NODE_SHIFTS)."""
        shifts: Dict[str, float] = {}
        non_anchor = self.nodes[1:]
        if not non_anchor:
            raise ValidationError("Cannot shift curve without maturity nodes")

        t_min = non_anchor[0].time_years
        t_max = non_anchor[-1].time_years
        span = max(t_max - t_min, 1e-9)

        if scenario.shift_type == "PARALLEL":
            for node in non_anchor:
                shifts[node.instrument_id] = scenario.magnitude_bp

        elif scenario.shift_type in ("STEEPENER", "FLATTENER"):
            # Linear twist pivoted around pivot_years:
            # STEEPENER: -magnitude/2 at short end, +magnitude/2 at long end
            # FLATTENER: +magnitude/2 at short end, -magnitude/2 at long end
            sign = 1.0 if scenario.shift_type == "STEEPENER" else -1.0
            pivot = scenario.pivot_years
            half_span = max(max(pivot - t_min, t_max - pivot), 1e-9)
            for node in non_anchor:
                norm = (node.time_years - pivot) / half_span
                shifts[node.instrument_id] = sign * 0.5 * scenario.magnitude_bp * norm

        elif scenario.shift_type == "BUTTERFLY":
            # Belly up (+magnitude), wings down (-magnitude) around pivot_years
            pivot = scenario.pivot_years
            for node in non_anchor:
                dist = abs(node.time_years - pivot) / (0.5 * span)
                weight = 1.0 - 2.0 * min(dist, 1.0)
                shifts[node.instrument_id] = scenario.magnitude_bp * weight

        elif scenario.shift_type == "NODE_SHIFTS":
            valid_ids = {n.instrument_id for n in non_anchor}
            unknown = [k for k in scenario.node_shifts_bp if k not in valid_ids]
            if unknown:
                raise ValidationError(
                    f"Unknown curve node IDs in scenario '{scenario.scenario_id}': {unknown}",
                    details={"unknown_nodes": unknown, "valid_nodes": sorted(valid_ids)},
                )
            shifts = dict(scenario.node_shifts_bp)

        else:
            raise UnsupportedConventionError(
                f"Unsupported scenario shift_type '{scenario.shift_type}'",
                details={
                    "shift_type": scenario.shift_type,
                    "supported": ["PARALLEL", "STEEPENER", "FLATTENER", "BUTTERFLY", "NODE_SHIFTS"],
                },
            )

        return self.with_node_zero_shifts(shifts, label=scenario.scenario_id)

    def max_calibration_error_bp(self) -> float:
        if not self.residuals:
            return 0.0
        return max(r.error_bp for r in self.residuals)

    def to_dict(self) -> Dict[str, object]:
        return {
            "curve_id": self.curve_id,
            "valuation_date": self.valuation_date.isoformat(),
            "currency": self.currency.value,
            "day_count": self.day_count.value,
            "interpolation": self.interpolation.value,
            "allow_negative_zero_rates": self.allow_negative_zero_rates,
            "max_calibration_error_bp": self.max_calibration_error_bp(),
            "nodes": [n.to_dict() for n in self.nodes],
            "residuals": [r.to_dict() for r in self.residuals],
        }


def _bond_dirty_price_from_ytm_simple_helper(
    bond_quote: BondQuote,
    valuation_date: date,
) -> float:
    """Resolve target dirty price in currency units from a BondQuote."""
    from .pricing import price_bond_from_yield

    bond = bond_quote.bond
    sched = build_schedule(bond)
    accrued = compute_accrued_interest(bond, valuation_date, schedule=sched)
    scale = bond.notional / 100.0

    if bond_quote.quote_type == "DIRTY_PRICE":
        return bond_quote.quote_value * scale
    if bond_quote.quote_type == "CLEAN_PRICE":
        return bond_quote.quote_value * scale + accrued.accrued_interest
    if bond_quote.quote_type == "YTM":
        dirty, _clean, _acc = price_bond_from_yield(
            bond, bond_quote.quote_value, valuation_date, schedule=sched
        )
        return dirty
    raise ValidationError(f"Unsupported quote_type '{bond_quote.quote_type}'")


def bootstrap_curve(
    valuation_date: date,
    deposits: Sequence[DepositQuote],
    bond_quotes: Sequence[BondQuote],
    *,
    currency: Currency = Currency.USD,
    curve_day_count: DayCountConvention = DayCountConvention.ACT_365F,
    interpolation: InterpolationMethod = InterpolationMethod.LOG_LINEAR_DF,
    solver_tolerance: float = 1e-12,
    solver_max_iterations: int = 200,
    repricing_tolerance_bp: float = 1.0,
    allow_negative_zero_rates: bool = False,
) -> YieldCurve:
    """Bootstrap an auditable discount-factor yield curve from deposits and bonds.

    Algorithm:
    1. Validate currency consistency, non-empty calibration set, and strictly
       increasing maturity dates.
    2. Anchor the curve at (t=0, DF=1.0).
    3. Sort all deposits and bonds by effective maturity date and bootstrap
       sequentially:
       - Deposit: DF(T_end) = DF(T_start) / (1 + r * alpha_dep)
       - Bond: If all intermediate cash flows lie on or before the last calibrated
         node, compute DF(T_end) in closed form. Otherwise (sparse nodes with
         intermediate coupons between T_prev and T_end), solve for z(T_end) via
         Brent's method so the interpolated curve reprices the bond exactly.
    4. Reprice every calibration instrument against the final curve and verify
       residuals are within repricing_tolerance_bp.
    """
    if not deposits and not bond_quotes:
        raise ValidationError("At least one deposit or bond quote is required to bootstrap a curve")

    # Build unified sorted list of calibration items
    items: List[Tuple[date, str, object]] = []
    for dep in deposits:
        if dep.currency != currency:
            raise ValidationError(
                f"Deposit '{dep.instrument_id}' currency {dep.currency.value} "
                f"does not match curve currency {currency.value}",
                details={"instrument_id": dep.instrument_id},
            )
        if dep.start_date < valuation_date:
            raise ValidationError(
                f"Deposit '{dep.instrument_id}' start_date is before valuation_date",
                details={
                    "instrument_id": dep.instrument_id,
                    "start_date": dep.start_date.isoformat(),
                    "valuation_date": valuation_date.isoformat(),
                },
            )
        cal = resolve_calendar(dep.calendar_name)
        adj_mat = cal.adjust(dep.maturity_date, dep.business_day_convention)
        if adj_mat <= valuation_date:
            raise ValidationError(
                f"Deposit '{dep.instrument_id}' adjusted maturity must be after valuation_date",
                details={"instrument_id": dep.instrument_id, "maturity_date": adj_mat.isoformat()},
            )
        items.append((adj_mat, "DEPOSIT", (dep, adj_mat)))

    for bq in bond_quotes:
        if bq.bond.currency != currency:
            raise ValidationError(
                f"Bond '{bq.bond.bond_id}' currency {bq.bond.currency.value} "
                f"does not match curve currency {currency.value}",
                details={"bond_id": bq.bond.bond_id},
            )
        sched = build_schedule(bq.bond)
        future_cfs = sched.future_cash_flows(valuation_date)
        if not future_cfs:
            raise ValidationError(
                f"Calibration bond '{bq.bond.bond_id}' has no future cash flows after valuation_date",
                details={"bond_id": bq.bond.bond_id, "valuation_date": valuation_date.isoformat()},
            )
        last_pay = future_cfs[-1].payment_date
        items.append((last_pay, "BOND", (bq, sched, future_cfs, last_pay)))

    items.sort(key=lambda x: x[0])

    # Check strictly increasing maturities
    seen_dates: Dict[date, str] = {}
    for mat_date, kind, payload in items:
        inst_id = payload[0].instrument_id if kind == "DEPOSIT" else payload[0].bond.bond_id
        if mat_date in seen_dates:
            raise CalibrationError(
                f"Duplicate calibration maturity date {mat_date.isoformat()} "
                f"for '{seen_dates[mat_date]}' and '{inst_id}'",
                details={
                    "maturity_date": mat_date.isoformat(),
                    "first_instrument": seen_dates[mat_date],
                    "second_instrument": inst_id,
                },
            )
        seen_dates[mat_date] = inst_id

    nodes: List[CurveNode] = [
        CurveNode(
            instrument_id="ANCHOR_0",
            instrument_type="ANCHOR",
            maturity_date=valuation_date,
            time_years=0.0,
            discount_factor=1.0,
            zero_rate_continuous=0.0,
        )
    ]

    iteration_counts: Dict[str, int] = {}

    for mat_date, kind, payload in items:
        t_mat = year_fraction(valuation_date, mat_date, curve_day_count)
        if t_mat <= nodes[-1].time_years:
            raise CalibrationError(
                "Non-monotonic curve time encountered during bootstrapping",
                details={"t_mat": t_mat, "prev_t": nodes[-1].time_years},
            )

        if kind == "DEPOSIT":
            dep, adj_mat = payload
            cal = resolve_calendar(dep.calendar_name)
            adj_start = cal.adjust(dep.start_date, dep.business_day_convention)
            alpha_dep = year_fraction(adj_start, adj_mat, dep.day_count)
            if alpha_dep <= 0.0:
                raise CalibrationError(
                    f"Deposit '{dep.instrument_id}' has non-positive accrual factor",
                    details={"instrument_id": dep.instrument_id, "alpha": alpha_dep},
                )

            if adj_start == valuation_date:
                df_start = 1.0
            else:
                t_start = year_fraction(valuation_date, adj_start, curve_day_count)
                if len(nodes) < 2 or t_start > nodes[-1].time_years:
                    raise CalibrationError(
                        f"Forward deposit '{dep.instrument_id}' start date exceeds calibrated curve",
                        details={"instrument_id": dep.instrument_id},
                    )
                temp_curve = YieldCurve(
                    curve_id="tmp",
                    valuation_date=valuation_date,
                    currency=currency,
                    day_count=curve_day_count,
                    interpolation=interpolation,
                    nodes=tuple(nodes),
                    allow_negative_zero_rates=allow_negative_zero_rates,
                )
                df_start = temp_curve.discount_factor(t_start)

            denom = 1.0 + dep.rate * alpha_dep
            if denom <= 0.0:
                raise CalibrationError(
                    f"Deposit '{dep.instrument_id}' produces non-positive denominator",
                    details={"instrument_id": dep.instrument_id, "denom": denom},
                )
            df_mat = df_start / denom
            z_mat = -math.log(df_mat) / t_mat
            nodes.append(
                CurveNode(
                    instrument_id=dep.instrument_id,
                    instrument_type="DEPOSIT",
                    maturity_date=adj_mat,
                    time_years=t_mat,
                    discount_factor=df_mat,
                    zero_rate_continuous=z_mat,
                )
            )
            iteration_counts[dep.instrument_id] = 1

        else:
            bq, sched, future_cfs, last_pay = payload
            target_dirty = _bond_dirty_price_from_ytm_simple_helper(bq, valuation_date)
            t_prev = nodes[-1].time_years

            intermediate_after_prev = [
                cf
                for cf in future_cfs[:-1]
                if year_fraction(valuation_date, cf.payment_date, curve_day_count) > t_prev + 1e-12
            ]

            if not intermediate_after_prev:
                # Exact closed-form bootstrap step
                known_pv = 0.0
                if future_cfs[:-1]:
                    temp_curve = YieldCurve(
                        curve_id="tmp",
                        valuation_date=valuation_date,
                        currency=currency,
                        day_count=curve_day_count,
                        interpolation=interpolation,
                        nodes=tuple(nodes),
                        allow_negative_zero_rates=allow_negative_zero_rates,
                    )
                    for cf in future_cfs[:-1]:
                        t_cf = year_fraction(valuation_date, cf.payment_date, curve_day_count)
                        known_pv += cf.total_amount * temp_curve.discount_factor(t_cf)

                final_cf = future_cfs[-1].total_amount
                rem_pv = target_dirty - known_pv
                if rem_pv <= 0.0:
                    raise CalibrationError(
                        f"Bond '{bq.bond.bond_id}' implies non-positive residual PV ({rem_pv:.6f})",
                        details={
                            "bond_id": bq.bond.bond_id,
                            "target_dirty": target_dirty,
                            "known_pv": known_pv,
                        },
                    )
                df_mat = rem_pv / final_cf
                z_mat = -math.log(df_mat) / t_mat
                iters = 1
            else:
                # Sparse node: solve continuous zero rate z_mat via Brent so interpolated curve reprices exactly
                cf_times = [
                    (year_fraction(valuation_date, cf.payment_date, curve_day_count), cf.total_amount)
                    for cf in future_cfs
                ]

                def objective(z_candidate: float) -> float:
                    df_cand = math.exp(-z_candidate * t_mat)
                    cand_node = CurveNode(
                        instrument_id=bq.bond.bond_id,
                        instrument_type="BOND",
                        maturity_date=last_pay,
                        time_years=t_mat,
                        discount_factor=df_cand,
                        zero_rate_continuous=z_candidate,
                    )
                    cand_curve = YieldCurve(
                        curve_id="tmp",
                        valuation_date=valuation_date,
                        currency=currency,
                        day_count=curve_day_count,
                        interpolation=interpolation,
                        nodes=tuple(nodes + [cand_node]),
                        allow_negative_zero_rates=True,
                    )
                    pv = sum(amt * cand_curve.discount_factor(t_cf) for t_cf, amt in cf_times)
                    return pv - target_dirty

                low_z = -0.10 if allow_negative_zero_rates else -0.05
                high_z = 0.60
                z_mat, iters = brentq(
                    objective,
                    low_z,
                    high_z,
                    tol=solver_tolerance,
                    max_iter=solver_max_iterations,
                    label=f"bootstrap:{bq.bond.bond_id}",
                )
                df_mat = math.exp(-z_mat * t_mat)

            if not allow_negative_zero_rates and z_mat < -1e-10:
                raise CalibrationError(
                    f"Bond '{bq.bond.bond_id}' bootstrapped negative zero rate ({z_mat:.6f})",
                    details={"bond_id": bq.bond.bond_id, "zero_rate": z_mat, "df": df_mat},
                )

            nodes.append(
                CurveNode(
                    instrument_id=bq.bond.bond_id,
                    instrument_type="BOND",
                    maturity_date=last_pay,
                    time_years=t_mat,
                    discount_factor=df_mat,
                    zero_rate_continuous=z_mat,
                )
            )
            iteration_counts[bq.bond.bond_id] = iters

    # Construct final curve and run full repricing audit across all calibration instruments
    curve_id = stable_id(
        "curve",
        {
            "valuation_date": valuation_date.isoformat(),
            "currency": currency.value,
            "day_count": curve_day_count.value,
            "interpolation": interpolation.value,
            "nodes": [(n.instrument_id, n.maturity_date.isoformat(), round(n.discount_factor, 12)) for n in nodes],
        },
    )
    provisional_curve = YieldCurve(
        curve_id=curve_id,
        valuation_date=valuation_date,
        currency=currency,
        day_count=curve_day_count,
        interpolation=interpolation,
        nodes=tuple(nodes),
        allow_negative_zero_rates=allow_negative_zero_rates,
    )

    residuals: List[CalibrationResidual] = []
    for mat_date, kind, payload in items:
        if kind == "DEPOSIT":
            dep, adj_mat = payload
            cal = resolve_calendar(dep.calendar_name)
            adj_start = cal.adjust(dep.start_date, dep.business_day_convention)
            alpha_dep = year_fraction(adj_start, adj_mat, dep.day_count)
            df_start = provisional_curve.discount_factor_on(adj_start)
            df_end = provisional_curve.discount_factor_on(adj_mat)
            implied_rate = ((df_start / df_end) - 1.0) / alpha_dep
            abs_err = abs(implied_rate - dep.rate)
            err_bp = abs_err * 10_000.0
            residuals.append(
                CalibrationResidual(
                    instrument_id=dep.instrument_id,
                    instrument_type="DEPOSIT",
                    maturity_date=adj_mat,
                    target_value=dep.rate,
                    repriced_value=implied_rate,
                    abs_error=abs_err,
                    error_bp=err_bp,
                    convention=f"SIMPLE_{dep.day_count.value}_RATE_BP",
                    iterations=iteration_counts[dep.instrument_id],
                )
            )
        else:
            bq, sched, future_cfs, last_pay = payload
            target_dirty = _bond_dirty_price_from_ytm_simple_helper(bq, valuation_date)
            repriced_dirty = sum(
                cf.total_amount * provisional_curve.discount_factor_on(cf.payment_date)
                for cf in future_cfs
            )
            scale = bq.bond.notional / 100.0
            target_pct = target_dirty / scale
            repriced_pct = repriced_dirty / scale
            abs_err_pct = abs(repriced_pct - target_pct)
            # 1 bp of par = 0.01% of par -> multiply % of par diff by 100
            err_bp = abs_err_pct * 100.0
            residuals.append(
                CalibrationResidual(
                    instrument_id=bq.bond.bond_id,
                    instrument_type="BOND",
                    maturity_date=last_pay,
                    target_value=target_pct,
                    repriced_value=repriced_pct,
                    abs_error=abs_err_pct,
                    error_bp=err_bp,
                    convention=f"{bq.quote_type}_DIRTY_PCT_PAR_BP",
                    iterations=iteration_counts[bq.bond.bond_id],
                )
            )

    max_err_bp = max(r.error_bp for r in residuals)
    if max_err_bp > repricing_tolerance_bp:
        raise CalibrationError(
            f"Calibration repricing error {max_err_bp:.6f} bp exceeds tolerance {repricing_tolerance_bp} bp",
            details={
                "max_error_bp": max_err_bp,
                "tolerance_bp": repricing_tolerance_bp,
                "residuals": [r.to_dict() for r in residuals],
            },
        )

    return YieldCurve(
        curve_id=curve_id,
        valuation_date=valuation_date,
        currency=currency,
        day_count=curve_day_count,
        interpolation=interpolation,
        nodes=tuple(nodes),
        residuals=tuple(residuals),
        allow_negative_zero_rates=allow_negative_zero_rates,
    )
