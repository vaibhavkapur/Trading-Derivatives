"""Integration, contract validation, operational failure, and deterministic replay tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fixed_income.cli import execute_market_run, main as cli_main
from fixed_income.curve import bootstrap_curve
from fixed_income.errors import (
    ConfigurationError,
    UnsupportedConventionError,
    ValidationError,
)
from fixed_income.io import load_market_fixture, load_valuation_config
from fixed_income.models import BondSpec
from fixed_income.pricing import price_bond_on_curve
from fixed_income.schedule import build_schedule

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class TestIntegrationAndContracts(unittest.TestCase):
    def test_holiday_daycount_and_irregular_coupon_integration(self) -> None:
        config, deposits, bond_quotes, _, _ = load_market_fixture(
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

        boundary_data = json.loads(
            (FIXTURES_DIR / "boundary_irregular_and_holidays.json").read_text()
        )
        for case in boundary_data["boundary_bonds"]:
            bond = BondSpec.from_dict(case["bond"])
            sched = build_schedule(bond)
            self.assertAlmostEqual(
                sched.total_coupons + sched.total_principal, sched.total_cash_flows, places=10
            )
            pricing = price_bond_on_curve(bond, curve, bump_bp=1.0)
            self.assertGreater(pricing.dirty_price, 0.0)
            self.assertAlmostEqual(
                pricing.clean_price + pricing.accrued_interest, pricing.dirty_price, places=9
            )
            self.assertLess(pricing.dv01_relative_diff_pct, 0.5)

            if case["case_name"] == "holiday_july_4_roll":
                # 2026-07-04 (Sat) -> adjusted payment date must be Monday 2026-07-06
                jul_2026 = [cf for cf in sched.cash_flows if cf.accrual_end.isoformat() == "2026-07-04"]
                self.assertEqual(len(jul_2026), 1)
                self.assertEqual(jul_2026[0].payment_date.isoformat(), "2026-07-06")

            if case["case_name"] == "leap_year_feb_eom_30360":
                # Feb 29, 2028 must appear as an accrual date
                accrual_ends = {cf.accrual_end.isoformat() for cf in sched.cash_flows}
                self.assertIn("2028-02-29", accrual_ends)

    def test_invalid_fixtures_raise_expected_contract_errors(self) -> None:
        invalid_data = json.loads((FIXTURES_DIR / "invalid_cases.json").read_text())
        error_map = {
            "ValidationError": ValidationError,
            "UnsupportedConventionError": UnsupportedConventionError,
        }
        for case in invalid_data["invalid_bonds"]:
            exc_cls = error_map[case["expected_error"]]
            with self.subTest(case=case["case_name"]):
                with self.assertRaises(exc_cls) as ctx:
                    BondSpec.from_dict(case["payload"])
                err_dict = ctx.exception.to_dict()
                self.assertIn("error_code", err_dict)
                self.assertIn("message", err_dict)

    def test_operational_missing_and_malformed_config_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            # Missing file
            with self.assertRaises(ConfigurationError):
                load_valuation_config(tmp / "does_not_exist.json")

            # Malformed JSON
            bad_json = tmp / "bad.json"
            bad_json.write_text("{not valid json", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_valuation_config(bad_json)

            # Missing required valuation_date key
            missing_key = tmp / "missing.json"
            missing_key.write_text(json.dumps({"currency": "USD"}), encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_valuation_config(missing_key)

    def test_deterministic_reproducibility_and_cli_replay(self) -> None:
        fixture = FIXTURES_DIR / "valid_curve_and_bonds.json"
        run1 = execute_market_run(fixture, run_label="repro_1")
        run2 = execute_market_run(fixture, run_label="repro_2")

        self.assertEqual(run1["logical_fingerprint"], run2["logical_fingerprint"])
        self.assertEqual(run1["curve"]["curve_id"], run2["curve"]["curve_id"])
        self.assertGreater(run1["diagnostics"]["event_count"], 0)
        self.assertEqual(run1["diagnostics"]["counters"]["pricing.bonds_priced"], 4)

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "baseline.json"
            rc_run = cli_main(["run", "--fixture", str(fixture), "--output", str(out_path)])
            self.assertEqual(rc_run, 0)
            self.assertTrue(out_path.exists())

            rc_replay = cli_main(["replay", "--fixture", str(fixture), "--baseline", str(out_path)])
            self.assertEqual(rc_replay, 0)


if __name__ == "__main__":
    unittest.main()
