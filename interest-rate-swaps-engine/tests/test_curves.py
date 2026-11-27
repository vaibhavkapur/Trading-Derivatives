"""Unit tests for YieldCurve interpolation, compounding, forward rates, and bumping."""

from __future__ import annotations

import math
from datetime import date

import pytest

from irs_engine.domain.curves import YieldCurve
from irs_engine.domain.enums import (
    Compounding,
    CurveRole,
    DayCountConvention,
    InterpolationMethod,
)
from irs_engine.domain.errors import CurveError
from irs_engine.domain.models import CurveNode, CurveSnapshot


def _sample_snapshot(
    interp: InterpolationMethod = InterpolationMethod.LINEAR_ZERO,
    comp: Compounding = Compounding.CONTINUOUS,
) -> CurveSnapshot:
    return CurveSnapshot(
        curve_id="USD-TEST",
        role=CurveRole.DISCOUNT,
        currency="USD",
        valuation_date=date(2026, 1, 5),
        day_count=DayCountConvention.ACT_365F,
        compounding=comp,
        interpolation=interp,
        nodes=(
            CurveNode(0.5, 0.040, "6M"),
            CurveNode(1.0, 0.042, "1Y"),
            CurveNode(2.0, 0.045, "2Y"),
            CurveNode(5.0, 0.050, "5Y"),
        ),
    )


def test_linear_zero_interpolation_and_extrapolation() -> None:
    curve = YieldCurve(_sample_snapshot(InterpolationMethod.LINEAR_ZERO))
    assert curve.zero_rate(0.1) == pytest.approx(0.040)
    assert curve.zero_rate(1.5) == pytest.approx(0.0435)
    assert curve.zero_rate(10.0) == pytest.approx(0.050)
    assert curve.discount_factor(0.0) == pytest.approx(1.0)
    assert curve.discount_factor(1.5) == pytest.approx(math.exp(-0.0435 * 1.5))


def test_log_linear_df_and_cubic_spline_reproduce_nodes() -> None:
    for interp in (InterpolationMethod.LOG_LINEAR_DF, InterpolationMethod.CUBIC_SPLINE_ZERO):
        snap = _sample_snapshot(interp=interp)
        curve = YieldCurve(snap)
        for node in snap.nodes:
            assert curve.zero_rate(node.tenor_years) == pytest.approx(node.zero_rate, abs=1e-12)
            assert curve.discount_factor(node.tenor_years) == pytest.approx(
                math.exp(-node.zero_rate * node.tenor_years), abs=1e-12
            )


def test_compounding_modes() -> None:
    c_ann = YieldCurve(_sample_snapshot(comp=Compounding.ANNUAL))
    assert c_ann.discount_factor(2.0) == pytest.approx((1.0 + 0.045) ** -2.0)

    c_simp = YieldCurve(_sample_snapshot(comp=Compounding.SIMPLE))
    assert c_simp.discount_factor(2.0) == pytest.approx(1.0 / (1.0 + 0.045 * 2.0))


def test_forward_rate_flat_curve_exact() -> None:
    snap = CurveSnapshot(
        curve_id="USD-FLAT",
        role=CurveRole.FORWARD,
        currency="USD",
        valuation_date=date(2026, 1, 5),
        nodes=(CurveNode(1.0, 0.05, "1Y"), CurveNode(5.0, 0.05, "5Y")),
    )
    curve = YieldCurve(snap)
    fwd = curve.forward_rate(date(2026, 1, 5), date(2027, 1, 5), DayCountConvention.ACT_365F)
    assert fwd == pytest.approx(math.exp(0.05) - 1.0, abs=1e-12)


def test_bumping_parallel_pillar_and_vector() -> None:
    curve = YieldCurve(_sample_snapshot())
    bumped_par = curve.bumped_parallel(10.0)
    for orig, mod in zip(curve.snapshot.nodes, bumped_par.snapshot.nodes):
        assert mod.zero_rate == pytest.approx(orig.zero_rate + 0.001)

    bumped_p1 = curve.bumped_pillar(1, 5.0)
    assert bumped_p1.snapshot.nodes[0].zero_rate == pytest.approx(0.040)
    assert bumped_p1.snapshot.nodes[1].zero_rate == pytest.approx(0.0425)

    with pytest.raises(CurveError):
        curve.bumped_pillar(99, 1.0)
    with pytest.raises(CurveError):
        curve.bumped_vector([1.0, 2.0])
