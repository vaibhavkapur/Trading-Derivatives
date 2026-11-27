"""Explicit discount and projection (forward) yield curve engine with bumping."""

from __future__ import annotations

import bisect
import math
from datetime import date

from irs_engine.domain.daycount import year_fraction
from irs_engine.domain.enums import Compounding, DayCountConvention, InterpolationMethod
from irs_engine.domain.errors import CurveError
from irs_engine.domain.models import CurveNode, CurveSnapshot


def _solve_natural_cubic_spline(xs: list[float], ys: list[float]) -> list[float]:
    """Compute second derivatives M_i for a natural cubic spline via Thomas algorithm."""
    n = len(xs)
    if n <= 2:
        return [0.0] * n

    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    m_inner = n - 2

    # Tridiagonal system A * M_inner = rhs
    a = [0.0] * m_inner  # sub-diagonal
    b = [0.0] * m_inner  # main diagonal
    c = [0.0] * m_inner  # super-diagonal
    rhs = [0.0] * m_inner

    for i in range(m_inner):
        hi = h[i]
        hi1 = h[i + 1]
        a[i] = hi
        b[i] = 2.0 * (hi + hi1)
        c[i] = hi1
        rhs[i] = 6.0 * ((ys[i + 2] - ys[i + 1]) / hi1 - (ys[i + 1] - ys[i]) / hi)

    # Forward elimination
    for i in range(1, m_inner):
        factor = a[i] / b[i - 1]
        b[i] -= factor * c[i - 1]
        rhs[i] -= factor * rhs[i - 1]

    # Back substitution
    sol = [0.0] * m_inner
    sol[-1] = rhs[-1] / b[-1]
    for i in range(m_inner - 2, -1, -1):
        sol[i] = (rhs[i] - c[i] * sol[i + 1]) / b[i]

    return [0.0] + sol + [0.0]


class YieldCurve:
    """Immutable analytical yield curve built from a CurveSnapshot."""

    def __init__(self, snapshot: CurveSnapshot) -> None:
        self.snapshot = snapshot
        self._tenors: list[float] = [n.tenor_years for n in snapshot.nodes]
        self._rates: list[float] = [n.zero_rate for n in snapshot.nodes]
        self._log_dfs: list[float] = [
            math.log(self._df_from_zero(t, z)) for t, z in zip(self._tenors, self._rates)
        ]
        self._spline_m: list[float] = (
            _solve_natural_cubic_spline(self._tenors, self._rates)
            if snapshot.interpolation == InterpolationMethod.CUBIC_SPLINE_ZERO
            else []
        )

    def _df_from_zero(self, t: float, z: float) -> float:
        if t <= 0.0:
            return 1.0
        comp = self.snapshot.compounding
        if comp == Compounding.CONTINUOUS:
            return math.exp(-z * t)
        if comp == Compounding.ANNUAL:
            base = 1.0 + z
            if base <= 0.0:
                raise CurveError(
                    "Annual compounded zero rate <= -100% yields non-positive discount factor.",
                    details={"curve_id": self.snapshot.curve_id, "t": t, "z": z},
                )
            return base ** (-t)
        if comp == Compounding.SIMPLE:
            denom = 1.0 + z * t
            if denom <= 0.0:
                raise CurveError(
                    "Simple compounded zero rate yields non-positive discount factor.",
                    details={"curve_id": self.snapshot.curve_id, "t": t, "z": z},
                )
            return 1.0 / denom
        raise CurveError(f"Unsupported compounding: {comp}")

    def _zero_from_df(self, t: float, df: float) -> float:
        if t <= 0.0:
            return self._rates[0]
        if df <= 0.0:
            raise CurveError(
                "Discount factor must be strictly positive.",
                details={"curve_id": self.snapshot.curve_id, "t": t, "df": df},
            )
        comp = self.snapshot.compounding
        if comp == Compounding.CONTINUOUS:
            return -math.log(df) / t
        if comp == Compounding.ANNUAL:
            return df ** (-1.0 / t) - 1.0
        if comp == Compounding.SIMPLE:
            return (1.0 / df - 1.0) / t
        raise CurveError(f"Unsupported compounding: {comp}")

    def time_from_valuation(self, target_date: date) -> float:
        """Compute year fraction from valuation_date to target_date using curve day count."""
        return year_fraction(
            self.snapshot.valuation_date,
            target_date,
            self.snapshot.day_count,
            allow_negative=True,
        )

    def zero_rate(self, t: float) -> float:
        """Interpolate zero rate at year fraction t (flat extrapolation outside pillar range)."""
        if t <= 0.0:
            return self._rates[0]
        if len(self._tenors) == 1:
            return self._rates[0]

        if self.snapshot.interpolation == InterpolationMethod.LOG_LINEAR_DF:
            df = self.discount_factor(t)
            return self._zero_from_df(t, df)

        if t <= self._tenors[0]:
            return self._rates[0]
        if t >= self._tenors[-1]:
            return self._rates[-1]

        idx = bisect.bisect_right(self._tenors, t) - 1
        t0, t1 = self._tenors[idx], self._tenors[idx + 1]
        z0, z1 = self._rates[idx], self._rates[idx + 1]

        if self.snapshot.interpolation == InterpolationMethod.LINEAR_ZERO:
            w = (t - t0) / (t1 - t0)
            return z0 + w * (z1 - z0)

        if self.snapshot.interpolation == InterpolationMethod.CUBIC_SPLINE_ZERO:
            h = t1 - t0
            m0, m1 = self._spline_m[idx], self._spline_m[idx + 1]
            a = (t1 - t) / h
            b = (t - t0) / h
            return (
                a * z0
                + b * z1
                + ((a**3 - a) * m0 + (b**3 - b) * m1) * (h**2) / 6.0
            )

        raise CurveError(f"Unsupported interpolation method: {self.snapshot.interpolation}")

    def discount_factor(self, t: float) -> float:
        """Evaluate discount factor DF(0, t) for t >= 0."""
        if t <= 0.0:
            return 1.0

        if self.snapshot.interpolation == InterpolationMethod.LOG_LINEAR_DF:
            # Anchor (t=0, ln DF = 0)
            if t <= self._tenors[0]:
                log_df = (t / self._tenors[0]) * self._log_dfs[0]
                return math.exp(log_df)
            if t >= self._tenors[-1]:
                # Flat zero rate extrapolation past last pillar
                return self._df_from_zero(t, self._rates[-1])
            idx = bisect.bisect_right(self._tenors, t) - 1
            t0, t1 = self._tenors[idx], self._tenors[idx + 1]
            l0, l1 = self._log_dfs[idx], self._log_dfs[idx + 1]
            w = (t - t0) / (t1 - t0)
            return math.exp(l0 + w * (l1 - l0))

        z = self.zero_rate(t)
        return self._df_from_zero(t, z)

    def discount_factor_on_date(self, target_date: date) -> float:
        """Evaluate discount factor from curve valuation_date to target_date."""
        t = self.time_from_valuation(target_date)
        if t <= 0.0:
            return 1.0
        return self.discount_factor(t)

    def forward_rate(
        self,
        start_date: date,
        end_date: date,
        day_count: DayCountConvention,
    ) -> float:
        """Simply-compounded forward rate over [start_date, end_date] on this curve."""
        tau = year_fraction(start_date, end_date, day_count)
        if tau <= 0.0:
            raise CurveError(
                "Forward accrual period must have positive year fraction.",
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
        t_start = max(0.0, self.time_from_valuation(start_date))
        t_end = self.time_from_valuation(end_date)
        if t_end <= 0.0:
            raise CurveError(
                "Cannot project forward rate for a period ending on or before valuation_date.",
                details={
                    "valuation_date": self.snapshot.valuation_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
        df_start = self.discount_factor(t_start)
        df_end = self.discount_factor(t_end)
        return (df_start / df_end - 1.0) / tau

    def bumped_parallel(self, shift_bps: float) -> YieldCurve:
        """Return a new YieldCurve with all zero-rate pillars shifted by shift_bps."""
        shift = shift_bps * 1e-4
        new_nodes = tuple(
            CurveNode(tenor_years=n.tenor_years, zero_rate=n.zero_rate + shift, label=n.label)
            for n in self.snapshot.nodes
        )
        return YieldCurve(
            CurveSnapshot(
                curve_id=f"{self.snapshot.curve_id}__par_{shift_bps:+.4f}bp",
                role=self.snapshot.role,
                currency=self.snapshot.currency,
                valuation_date=self.snapshot.valuation_date,
                nodes=new_nodes,
                day_count=self.snapshot.day_count,
                compounding=self.snapshot.compounding,
                interpolation=self.snapshot.interpolation,
                index_name=self.snapshot.index_name,
                schema_version=self.snapshot.schema_version,
            )
        )

    def bumped_pillar(self, pillar_index: int, shift_bps: float) -> YieldCurve:
        """Return a new YieldCurve with a single pillar shifted by shift_bps."""
        if pillar_index < 0 or pillar_index >= len(self.snapshot.nodes):
            raise CurveError(
                f"pillar_index {pillar_index} out of bounds for curve {self.snapshot.curve_id}.",
                details={"pillar_index": pillar_index, "num_pillars": len(self.snapshot.nodes)},
            )
        shift = shift_bps * 1e-4
        new_nodes = tuple(
            CurveNode(
                tenor_years=n.tenor_years,
                zero_rate=n.zero_rate + (shift if i == pillar_index else 0.0),
                label=n.label,
            )
            for i, n in enumerate(self.snapshot.nodes)
        )
        pillar_lbl = self.snapshot.nodes[pillar_index].label or f"{self.snapshot.nodes[pillar_index].tenor_years}Y"
        return YieldCurve(
            CurveSnapshot(
                curve_id=f"{self.snapshot.curve_id}__kr_{pillar_lbl}_{shift_bps:+.4f}bp",
                role=self.snapshot.role,
                currency=self.snapshot.currency,
                valuation_date=self.snapshot.valuation_date,
                nodes=new_nodes,
                day_count=self.snapshot.day_count,
                compounding=self.snapshot.compounding,
                interpolation=self.snapshot.interpolation,
                index_name=self.snapshot.index_name,
                schema_version=self.snapshot.schema_version,
            )
        )

    def bumped_vector(self, shifts_bps: list[float], suffix: str = "shock") -> YieldCurve:
        """Return a new YieldCurve with per-pillar shocks in basis points."""
        if len(shifts_bps) != len(self.snapshot.nodes):
            raise CurveError(
                "Shock vector length must match number of curve nodes.",
                details={"shock_len": len(shifts_bps), "node_len": len(self.snapshot.nodes)},
            )
        new_nodes = tuple(
            CurveNode(
                tenor_years=n.tenor_years,
                zero_rate=n.zero_rate + s * 1e-4,
                label=n.label,
            )
            for n, s in zip(self.snapshot.nodes, shifts_bps)
        )
        return YieldCurve(
            CurveSnapshot(
                curve_id=f"{self.snapshot.curve_id}__{suffix}",
                role=self.snapshot.role,
                currency=self.snapshot.currency,
                valuation_date=self.snapshot.valuation_date,
                nodes=new_nodes,
                day_count=self.snapshot.day_count,
                compounding=self.snapshot.compounding,
                interpolation=self.snapshot.interpolation,
                index_name=self.snapshot.index_name,
                schema_version=self.snapshot.schema_version,
            )
        )
