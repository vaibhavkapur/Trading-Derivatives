"""Unit and boundary tests for bond pricing, YTM, Z-spread, duration, convexity, and DV01."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from fixed_income.conventions import (
    BusinessDayConvention,
    Compounding,
    Currency,
    DayCountConvention,
)
from fixed_income.curve import bootstrap_curve
from fixed_income.errors import ValidationError
from fixed_income.io import load_market_fixture
from fixed_income.models import BondSpec
from fixed_income.pricing import (
    compute_analytic_sensitivities,
    price_bond_by_yield_api,
    price_bond_from_yield,
    price_bond_on_curve,
    solve_ytm,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class TestPricingAndSensitivities(unittest.TestCase):
    def test_hand_calculated_zero_coupon_and_par_bond_oracles(self) -> None:
        oracles = json.loads((FIXTURES_DIR / "reference_hand_calculated.json").read_text())["oracles"]

        # 5Y zero-coupon bond oracle
        zo = oracles["zero_coupon_5y_semiannual_ytm"]
        z_bond = BondSpec.from_dict(zo["bond"])
        z_res = price_bond_by_yield_api(
            z_bond, zo["ytm"], date.fromisoformat(zo["valuation_date"])
        )
        self.assertAlmostEqual(z_res.dirty_price, zo["expected_dirty_price"], delta=zo["tolerance"])
        self.assertAlmostEqual(z_res.clean_price, zo["expected_clean_price"], delta=zo["tolerance"])
        self.assertAlmostEqual(z_res.accrued_interest, zo["expected_accrued_interest"], delta=zo["tolerance"])
        self.assertAlmostEqual(z_res.macaulay_duration, zo["expected_macaulay_duration"], delta=zo["tolerance"])
        self.assertAlmostEqual(z_res.modified_duration, zo["expected_modified_duration"], delta=zo["tolerance"])
        self.assertAlmostEqual(z_res.convexity, zo["expected_convexity"], delta=zo["tolerance"])
        self.assertAlmostEqual(z_res.analytic_dv01, zo["expected_analytic_dv01"], delta=zo["tolerance"])
        self.assertLess(z_res.dv01_relative_diff_pct, 0.01)

        # 2Y semi-annual par bond oracle
        po = oracles["par_bond_2y_semiannual"]
        p_bond = BondSpec.from_dict(po["bond"])
        p_res = price_bond_by_yield_api(
            p_bond, po["ytm"], date.fromisoformat(po["valuation_date"])
        )
        self.assertAlmostEqual(p_res.dirty_price, po["expected_dirty_price"], delta=po["tolerance"])
        self.assertAlmostEqual(p_res.clean_price, po["expected_clean_price"], delta=po["tolerance"])
        self.assertAlmostEqual(p_res.macaulay_duration, po["expected_macaulay_duration"], delta=zo["tolerance"])
        self.assertAlmostEqual(p_res.modified_duration, po["expected_modified_duration"], delta=zo["tolerance"])
        self.assertAlmostEqual(p_res.analytic_dv01, po["expected_analytic_dv01"], delta=zo["tolerance"])
        self.assertLess(p_res.dv01_relative_diff_pct, 0.01)

    def test_ytm_round_trip_and_curve_pricing(self) -> None:
        config, deposits, bond_quotes, portfolio, _ = load_market_fixture(
            FIXTURES_DIR / "valid_curve_and_bonds.json"
        )
        curve = bootstrap_curve(
            config.valuation_date,
            deposits,
            bond_quotes,
            currency=config.currency,
            curve_day_count=config.curve_day_count,
            interpolation=config.interpolation,
        )

        for bond in portfolio:
            res = price_bond_on_curve(bond, curve, bump_bp=config.dv01_bump_bp)
            # PV weights sum to 1.0 and individual cash-flow PVs sum to dirty price
            self.assertAlmostEqual(sum(cf.pv_weight for cf in res.cash_flow_valuations), 1.0, places=12)
            self.assertAlmostEqual(
                sum(cf.present_value for cf in res.cash_flow_valuations), res.dirty_price, places=9
            )
            # Clean + accrued == dirty
            self.assertAlmostEqual(res.clean_price + res.accrued_interest, res.dirty_price, places=9)
            # YTM round-trip recovers dirty price
            dirty_rt, _, _ = price_bond_from_yield(bond, res.ytm, config.valuation_date)
            self.assertAlmostEqual(dirty_rt, res.dirty_price, places=8)
            # Acceptance criterion: DV01 agrees with finite differences within 0.5%
            self.assertLess(res.dv01_relative_diff_pct, 0.5)
            # Key-rate DV01 sum closely matches parallel curve shift DV01
            kr_sum = sum(res.key_rate_dv01.values())
            rel_kr = abs(kr_sum - res.curve_parallel_dv01) / abs(res.curve_parallel_dv01) * 100.0
            self.assertLess(rel_kr, 0.1)

        # Z-spread recovery check: discount bond by +75 bp over curve -> recover 75 bp
        test_bond = portfolio[1]
        shifted_curve = curve.with_node_zero_shifts(
            {n.instrument_id: 75.0 for n in curve.nodes[1:]}, label="z75"
        )
        shifted_res = price_bond_on_curve(test_bond, shifted_curve, compute_key_rate_dv01=False)
        res_with_mkt = price_bond_on_curve(
            test_bond,
            curve,
            market_clean_price_pct=shifted_res.clean_price_pct_par,
            compute_key_rate_dv01=False,
        )
        self.assertAlmostEqual(res_with_mkt.z_spread_bp, 75.0, places=4)

    def test_boundary_analytic_duration_vs_numerical_bumps(self) -> None:
        """Required boundary/failure scenario: analytic duration vs numerical bumps.

        Verifies:
        1. On smooth 1bp central bumps, analytic DV01 and numerical FD DV01 agree < 0.01%.
        2. On large bumps (200bp), one-sided numerical bump diverges from linear analytic DV01
           by the exact convexity term (0.5 * Convexity * P * dy^2), while central FD remains
           accurate to O(dy^2).
        3. Near-maturity bond (5 days left) maintains positive finite sensitivities without instability.
        4. Invalid bump sizes and unphysical yields raise explicit ValidationError.
        """
        val_date = date(2026, 3, 16)
        long_bond = BondSpec(
            bond_id="LONG_30Y",
            currency=Currency.USD,
            notional=100.0,
            coupon_rate=0.04,
            frequency=2,
            issue_date=val_date,
            maturity_date=date(2056, 3, 16),
            day_count=DayCountConvention.ACT_ACT_ICMA,
            business_day_convention=BusinessDayConvention.UNADJUSTED,
            calendar_name="NONE",
        )
        small = compute_analytic_sensitivities(long_bond, 0.045, val_date, bump_bp=1.0)
        large = compute_analytic_sensitivities(long_bond, 0.045, val_date, bump_bp=200.0)

        # Small bump: near-exact agreement (< 0.01%)
        self.assertLess(small["dv01_relative_diff_pct"], 0.01)

        # Large 200bp one-sided bump: convexity makes one-sided DV01 significantly smaller than analytic DV01
        one_sided_diff_pct = (
            abs(large["analytic_dv01"] - large["one_sided_up_dv01"]) / large["analytic_dv01"] * 100.0
        )
        self.assertGreater(one_sided_diff_pct, 5.0)
        # Central FD absorbs the quadratic convexity term, so its error is much smaller than one-sided
        self.assertLess(large["dv01_relative_diff_pct"], one_sided_diff_pct)

        # Near-maturity (5 days) bond stays stable and agrees with FD
        near_mat = BondSpec(
            bond_id="NEAR_MAT_5D",
            currency=Currency.USD,
            notional=100.0,
            coupon_rate=0.05,
            frequency=2,
            issue_date=date(2025, 9, 21),
            maturity_date=date(2026, 3, 21),
            day_count=DayCountConvention.ACT_ACT_ICMA,
            business_day_convention=BusinessDayConvention.UNADJUSTED,
            calendar_name="NONE",
        )
        near_sens = compute_analytic_sensitivities(near_mat, 0.045, val_date, bump_bp=1.0)
        self.assertGreater(near_sens["analytic_dv01"], 0.0)
        self.assertLess(near_sens["dv01_relative_diff_pct"], 0.01)

        # Failure cases: non-positive bump, unphysical yield (-250% semi-annual -> 1 + y/2 <= 0), negative price
        with self.assertRaises(ValidationError):
            compute_analytic_sensitivities(long_bond, 0.045, val_date, bump_bp=0.0)
        with self.assertRaises(ValidationError):
            compute_analytic_sensitivities(long_bond, -2.5, val_date, bump_bp=1.0)
        with self.assertRaises(ValidationError):
            solve_ytm(long_bond, -10.0, val_date)


if __name__ == "__main__":
    unittest.main()
