"""Unit tests for yield curve bootstrapping, interpolation, and calibration residuals."""

from __future__ import annotations

import json
import math
import unittest
from datetime import date
from pathlib import Path

from fixed_income.conventions import (
    BusinessDayConvention,
    Compounding,
    Currency,
    DayCountConvention,
    InterpolationMethod,
)
from fixed_income.curve import bootstrap_curve
from fixed_income.errors import CalibrationError, ValidationError
from fixed_income.io import load_market_fixture
from fixed_income.models import BondQuote, BondSpec, DepositQuote

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class TestCurveBootstrapping(unittest.TestCase):
    def test_deposit_oracle_matches_closed_form(self) -> None:
        oracles = json.loads((FIXTURES_DIR / "reference_hand_calculated.json").read_text())["oracles"]
        dep_oracle = oracles["deposit_3m_act360"]
        val_date = date.fromisoformat(dep_oracle["valuation_date"])
        dep = DepositQuote.from_dict(dep_oracle["deposit"])

        curve = bootstrap_curve(
            valuation_date=val_date,
            deposits=[dep],
            bond_quotes=[],
            currency=Currency.USD,
            curve_day_count=DayCountConvention.ACT_365F,
        )
        self.assertEqual(len(curve.nodes), 2)
        self.assertAlmostEqual(
            curve.nodes[1].discount_factor,
            dep_oracle["expected_df"],
            delta=dep_oracle["tolerance"],
        )
        self.assertLess(curve.max_calibration_error_bp(), 1e-9)

    def test_zero_coupon_and_par_instruments_bootstrap(self) -> None:
        val_date = date(2026, 3, 16)
        # 1Y zero-coupon priced at 95.23809523809524 (5% annual yield)
        z1 = BondQuote(
            bond=BondSpec(
                bond_id="ZERO_1Y",
                currency=Currency.USD,
                notional=100.0,
                coupon_rate=0.0,
                frequency=0,
                issue_date=val_date,
                maturity_date=date(2027, 3, 16),
                day_count=DayCountConvention.ACT_365F,
                business_day_convention=BusinessDayConvention.UNADJUSTED,
                calendar_name="NONE",
            ),
            quote_type="CLEAN_PRICE",
            quote_value=100.0 / 1.05,
        )
        # 2Y annual 5% par bond priced at 100 -> flat 5% annual curve implies DF(2) = 1 / 1.05^2
        par2 = BondQuote(
            bond=BondSpec(
                bond_id="PAR_2Y",
                currency=Currency.USD,
                notional=100.0,
                coupon_rate=0.05,
                frequency=1,
                issue_date=val_date,
                maturity_date=date(2028, 3, 16),
                day_count=DayCountConvention.ACT_ACT_ICMA,
                business_day_convention=BusinessDayConvention.UNADJUSTED,
                calendar_name="NONE",
            ),
            quote_type="CLEAN_PRICE",
            quote_value=100.0,
        )
        curve = bootstrap_curve(
            valuation_date=val_date,
            deposits=[],
            bond_quotes=[z1, par2],
            currency=Currency.USD,
            curve_day_count=DayCountConvention.ACT_365F,
        )
        self.assertAlmostEqual(curve.nodes[1].discount_factor, 1.0 / 1.05, places=12)
        self.assertAlmostEqual(curve.nodes[2].discount_factor, 1.0 / (1.05 ** 2), places=12)
        self.assertLess(curve.max_calibration_error_bp(), 1e-8)

    def test_full_usd_fixture_reprices_within_1bp_for_both_interpolations(self) -> None:
        config, deposits, bond_quotes, _portfolio, _scenarios = load_market_fixture(
            FIXTURES_DIR / "valid_curve_and_bonds.json"
        )
        for method in (InterpolationMethod.LOG_LINEAR_DF, InterpolationMethod.LINEAR_ZERO):
            curve = bootstrap_curve(
                valuation_date=config.valuation_date,
                deposits=deposits,
                bond_quotes=bond_quotes,
                currency=config.currency,
                curve_day_count=config.curve_day_count,
                interpolation=method,
            )
            self.assertEqual(len(curve.nodes), 1 + len(deposits) + len(bond_quotes))
            self.assertLess(curve.max_calibration_error_bp(), 1e-6)
            self.assertLess(curve.max_calibration_error_bp(), 1.0)

            # Verify forward rates and zero-rate compounding conversions are finite and positive
            z_cont = curve.zero_rate(5.0, Compounding.CONTINUOUS)
            z_sa = curve.zero_rate(5.0, Compounding.SEMI_ANNUAL)
            self.assertGreater(z_sa, z_cont)  # Discrete > continuous for positive rates
            # Verify round-trip identity: (1 + z_sa/2)^2 == exp(z_cont)
            self.assertAlmostEqual((1.0 + z_sa / 2.0) ** 2, math.exp(z_cont), places=12)
            fwd_2_5 = curve.forward_rate(2.0, 5.0, Compounding.CONTINUOUS)
            self.assertGreater(fwd_2_5, 0.0)

    def test_calibration_error_handling(self) -> None:
        val_date = date(2026, 3, 16)
        with self.assertRaises(ValidationError):
            bootstrap_curve(val_date, [], [])

        dep1 = DepositQuote(
            instrument_id="DEP_A",
            currency=Currency.USD,
            start_date=val_date,
            maturity_date=date(2026, 6, 16),
            rate=0.04,
        )
        dep2 = DepositQuote(
            instrument_id="DEP_B",
            currency=Currency.USD,
            start_date=val_date,
            maturity_date=date(2026, 6, 16),
            rate=0.045,
        )
        with self.assertRaises(CalibrationError):
            bootstrap_curve(val_date, [dep1, dep2], [])

        # Currency mismatch
        eur_dep = DepositQuote(
            instrument_id="EUR_DEP",
            currency=Currency.EUR,
            start_date=val_date,
            maturity_date=date(2026, 6, 16),
            rate=0.03,
        )
        with self.assertRaises(ValidationError):
            bootstrap_curve(val_date, [eur_dep], [], currency=Currency.USD)

        # Negative zero rate rejected when allow_negative_zero_rates=False
        neg_dep = DepositQuote(
            instrument_id="NEG_DEP",
            currency=Currency.USD,
            start_date=val_date,
            maturity_date=date(2026, 6, 16),
            rate=-0.01,
        )
        with self.assertRaises(CalibrationError):
            bootstrap_curve(val_date, [neg_dep], [], allow_negative_zero_rates=False)
        # Allowed explicitly when configured
        neg_curve = bootstrap_curve(val_date, [neg_dep], [], allow_negative_zero_rates=True)
        self.assertGreater(neg_curve.nodes[1].discount_factor, 1.0)


if __name__ == "__main__":
    unittest.main()
