"""Unit tests for day-count conventions, calendars, scheduling, and accrued interest."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from fixed_income.conventions import (
    BusinessDayConvention,
    Currency,
    DayCountConvention,
    StubType,
    resolve_calendar,
)
from fixed_income.daycount import year_fraction
from fixed_income.errors import ScheduleError, UnsupportedConventionError, ValidationError
from fixed_income.models import BondSpec
from fixed_income.schedule import build_schedule, compute_accrued_interest

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class TestDayCountAndCalendar(unittest.TestCase):
    def test_day_count_conventions(self) -> None:
        d1 = date(2026, 3, 16)
        d2 = date(2026, 6, 16)  # 92 actual days
        self.assertAlmostEqual(year_fraction(d1, d2, DayCountConvention.ACT_360), 92.0 / 360.0, places=12)
        self.assertAlmostEqual(year_fraction(d1, d2, DayCountConvention.ACT_365F), 92.0 / 365.0, places=12)
        self.assertAlmostEqual(year_fraction(d1, d2, DayCountConvention.THIRTY_360_US), 90.0 / 360.0, places=12)
        self.assertAlmostEqual(year_fraction(d1, d2, DayCountConvention.THIRTY_E_360), 90.0 / 360.0, places=12)

    def test_30_360_us_feb_eom_and_leap_year(self) -> None:
        # Feb 28 2027 (non-leap) to Feb 29 2028 (leap): both Feb EOM -> exactly 1.0 year
        yf = year_fraction(date(2027, 2, 28), date(2028, 2, 29), DayCountConvention.THIRTY_360_US)
        self.assertAlmostEqual(yf, 1.0, places=12)

        # ACT/ACT ISDA fallback over full leap year 2028 = 366/366 = 1.0
        yf_isda = year_fraction(date(2028, 1, 1), date(2029, 1, 1), DayCountConvention.ACT_ACT_ICMA)
        self.assertAlmostEqual(yf_isda, 1.0, places=12)

    def test_calendar_business_day_adjustments(self) -> None:
        cal = resolve_calendar("US_SYNTHETIC")
        # 2026-07-04 is Saturday; observed US holiday is Friday 2026-07-03
        sat_jul4 = date(2026, 7, 4)
        self.assertEqual(cal.adjust(sat_jul4, BusinessDayConvention.UNADJUSTED), sat_jul4)
        self.assertEqual(cal.adjust(sat_jul4, BusinessDayConvention.FOLLOWING), date(2026, 7, 6))
        self.assertEqual(cal.adjust(sat_jul4, BusinessDayConvention.MODIFIED_FOLLOWING), date(2026, 7, 6))
        self.assertEqual(cal.adjust(sat_jul4, BusinessDayConvention.PRECEDING), date(2026, 7, 2))

        # Month-end Saturday rolls backward under Modified Following
        # 2026-10-31 is Saturday
        sat_oct31 = date(2026, 10, 31)
        self.assertEqual(cal.adjust(sat_oct31, BusinessDayConvention.FOLLOWING), date(2026, 11, 2))
        self.assertEqual(cal.adjust(sat_oct31, BusinessDayConvention.MODIFIED_FOLLOWING), date(2026, 10, 30))

    def test_invalid_date_order_and_calendar(self) -> None:
        with self.assertRaises(ValidationError):
            year_fraction(date(2026, 6, 1), date(2026, 3, 1), DayCountConvention.ACT_365F)
        with self.assertRaises(UnsupportedConventionError):
            resolve_calendar("UNKNOWN_CAL")


class TestScheduleAndAccruedInterest(unittest.TestCase):
    def test_regular_par_bond_schedule_reconciliation(self) -> None:
        bond = BondSpec(
            bond_id="REG_2Y",
            currency=Currency.USD,
            notional=1000.0,
            coupon_rate=0.05,
            frequency=2,
            issue_date=date(2026, 3, 16),
            maturity_date=date(2028, 3, 16),
            day_count=DayCountConvention.ACT_ACT_ICMA,
            business_day_convention=BusinessDayConvention.UNADJUSTED,
            calendar_name="NONE",
        )
        sched = build_schedule(bond)
        self.assertEqual(len(sched.cash_flows), 4)
        self.assertAlmostEqual(sched.total_coupons, 100.0, places=12)
        self.assertAlmostEqual(sched.total_principal, 1000.0, places=12)
        self.assertAlmostEqual(sched.total_cash_flows, 1100.0, places=12)
        self.assertAlmostEqual(
            sum(cf.total_amount for cf in sched.cash_flows), sched.total_cash_flows, places=12
        )

    def test_zero_coupon_schedule_and_accrued(self) -> None:
        bond = BondSpec(
            bond_id="ZERO_5Y",
            currency=Currency.USD,
            notional=100.0,
            coupon_rate=0.0,
            frequency=0,
            issue_date=date(2026, 3, 16),
            maturity_date=date(2031, 3, 16),
        )
        sched = build_schedule(bond)
        self.assertEqual(len(sched.cash_flows), 1)
        self.assertEqual(sched.total_coupons, 0.0)
        self.assertEqual(sched.total_principal, 100.0)
        acc = compute_accrued_interest(bond, date(2028, 7, 1), schedule=sched)
        self.assertEqual(acc.accrued_interest, 0.0)

    def test_mid_period_accrued_matches_hand_calculated_oracles(self) -> None:
        oracles = json.loads((FIXTURES_DIR / "reference_hand_calculated.json").read_text())["oracles"]
        oracle = oracles["mid_period_accrued_30360_and_icma"]
        val_date = date.fromisoformat(oracle["valuation_date"])

        bond_30360 = BondSpec(
            bond_id="ACC_30360",
            currency=Currency.USD,
            notional=1000.0,
            coupon_rate=0.06,
            frequency=2,
            issue_date=date(2026, 1, 15),
            maturity_date=date(2028, 1, 15),
            day_count=DayCountConvention.THIRTY_360_US,
            business_day_convention=BusinessDayConvention.UNADJUSTED,
            calendar_name="NONE",
        )
        bond_icma = BondSpec(
            bond_id="ACC_ICMA",
            currency=Currency.USD,
            notional=1000.0,
            coupon_rate=0.06,
            frequency=2,
            issue_date=date(2026, 1, 15),
            maturity_date=date(2028, 1, 15),
            day_count=DayCountConvention.ACT_ACT_ICMA,
            business_day_convention=BusinessDayConvention.UNADJUSTED,
            calendar_name="NONE",
        )
        acc_30360 = compute_accrued_interest(bond_30360, val_date)
        acc_icma = compute_accrued_interest(bond_icma, val_date)

        self.assertAlmostEqual(acc_30360.accrued_interest, oracle["expected_accrued_30360"], places=12)
        self.assertAlmostEqual(acc_icma.accrued_interest, oracle["expected_accrued_icma"], places=12)

        # On coupon date -> zero accrued
        acc_on_cpn = compute_accrued_interest(bond_icma, date(2026, 7, 15))
        self.assertEqual(acc_on_cpn.accrued_interest, 0.0)
        self.assertTrue(acc_on_cpn.is_on_coupon_date)

    def test_irregular_stubs_short_and_long(self) -> None:
        # Short first stub (3 months) + 4 regular semi-annual periods
        short_first = BondSpec(
            bond_id="SF",
            currency=Currency.USD,
            notional=1000.0,
            coupon_rate=0.06,
            frequency=2,
            issue_date=date(2026, 3, 15),
            first_coupon_date=date(2026, 6, 15),
            maturity_date=date(2028, 6, 15),
            day_count=DayCountConvention.ACT_ACT_ICMA,
            stub_type=StubType.SHORT_FIRST,
        )
        sf_sched = build_schedule(short_first)
        self.assertEqual(len(sf_sched.cash_flows), 5)
        self.assertTrue(sf_sched.cash_flows[0].is_stub)
        self.assertEqual(sf_sched.cash_flows[0].stub_kind, "SHORT_FIRST")
        self.assertLess(sf_sched.cash_flows[0].coupon_amount, 30.0)
        self.assertAlmostEqual(sf_sched.cash_flows[1].coupon_amount, 30.0, places=12)

        # Long first stub (9 months) -> coupon > regular 30.0
        long_first = BondSpec(
            bond_id="LF",
            currency=Currency.USD,
            notional=1000.0,
            coupon_rate=0.06,
            frequency=2,
            issue_date=date(2025, 9, 15),
            first_coupon_date=date(2026, 6, 15),
            maturity_date=date(2028, 6, 15),
            day_count=DayCountConvention.ACT_ACT_ICMA,
            stub_type=StubType.LONG_FIRST,
        )
        lf_sched = build_schedule(long_first)
        self.assertEqual(lf_sched.cash_flows[0].stub_kind, "LONG_FIRST")
        self.assertGreater(lf_sched.cash_flows[0].coupon_amount, 30.0)
        # Accrued interest inside long first stub must be strictly positive and < full stub coupon
        acc_lf = compute_accrued_interest(long_first, date(2026, 3, 16), schedule=lf_sched)
        self.assertGreater(acc_lf.accrued_interest, 0.0)
        self.assertLess(acc_lf.accrued_interest, lf_sched.cash_flows[0].coupon_amount)

        # StubType.NONE rejects misaligned issue/maturity
        misaligned = BondSpec(
            bond_id="NO_STUB",
            currency=Currency.USD,
            notional=1000.0,
            coupon_rate=0.05,
            frequency=2,
            issue_date=date(2026, 4, 1),
            maturity_date=date(2028, 6, 15),
            stub_type=StubType.NONE,
        )
        with self.assertRaises(ScheduleError):
            build_schedule(misaligned)


if __name__ == "__main__":
    unittest.main()
