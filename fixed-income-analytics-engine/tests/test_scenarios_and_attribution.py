"""Unit tests for curve shift scenarios and cash-flow P&L attribution."""

from __future__ import annotations

import unittest
from pathlib import Path

from fixed_income.curve import bootstrap_curve
from fixed_income.errors import UnsupportedConventionError, ValidationError
from fixed_income.io import load_market_fixture
from fixed_income.models import ScenarioSpec
from fixed_income.scenarios import default_scenarios, run_scenario, run_scenario_suite

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "data" / "fixtures"


class TestScenariosAndAttribution(unittest.TestCase):
    def setUp(self) -> None:
        config, deposits, bond_quotes, portfolio, _ = load_market_fixture(
            FIXTURES_DIR / "valid_curve_and_bonds.json"
        )
        self.config = config
        self.portfolio = portfolio
        self.curve = bootstrap_curve(
            config.valuation_date,
            deposits,
            bond_quotes,
            currency=config.currency,
            curve_day_count=config.curve_day_count,
            interpolation=config.interpolation,
        )

    def test_scenario_suite_cash_flow_reconciliation_and_convexity(self) -> None:
        scenarios = default_scenarios(pivot_years=5.0)
        results = run_scenario_suite(self.portfolio, self.curve, scenarios)
        self.assertEqual(len(results), len(self.portfolio) * len(scenarios))

        for res in results:
            # Cash-flow PV changes must reconcile identically to total scenario P&L
            self.assertLess(res.reconciliation_error, 1e-9)
            if abs(res.total_pnl) > 1e-6:
                share_sum = sum(c.pnl_share_pct for c in res.contributions)
                self.assertAlmostEqual(share_sum, 100.0, places=6)

        # Positive convexity check on 10Y bond: gain from -100bp exceeds loss from +100bp
        long_bond = self.portfolio[-1]
        up_100 = run_scenario(long_bond, self.curve, scenarios[2])  # +100bp
        dn_100 = run_scenario(long_bond, self.curve, scenarios[3])  # -100bp
        self.assertLess(up_100.total_pnl, 0.0)
        self.assertGreater(dn_100.total_pnl, 0.0)
        self.assertGreater(abs(dn_100.total_pnl), abs(up_100.total_pnl))
        self.assertGreater(up_100.taylor_convexity_pnl, 0.0)

        # On small +10bp parallel shift, Taylor (duration + convexity) residual is tiny
        up_10 = run_scenario(long_bond, self.curve, scenarios[0])
        self.assertLess(abs(up_10.taylor_residual) / abs(up_10.total_pnl), 0.005)

    def test_node_shift_and_invalid_scenario_errors(self) -> None:
        long_bond = self.portfolio[-1]
        node_scen = ScenarioSpec(
            scenario_id="node_10y_up_25bp",
            name="10Y Node +25bp",
            shift_type="NODE_SHIFTS",
            node_shifts_bp={"USD_BOND_10Y_PAR": 25.0},
        )
        res = run_scenario(long_bond, self.curve, node_scen)
        self.assertLess(res.total_pnl, 0.0)
        self.assertLess(res.reconciliation_error, 1e-9)
        # Final cash flow dominates P&L share for a 10Y node bump
        self.assertGreater(res.contributions[-1].pnl_share_pct, 80.0)

        # Unknown node ID raises ValidationError
        bad_node = ScenarioSpec(
            scenario_id="bad_node",
            name="Bad Node",
            shift_type="NODE_SHIFTS",
            node_shifts_bp={"NON_EXISTENT_NODE": 10.0},
        )
        with self.assertRaises(ValidationError):
            run_scenario(long_bond, self.curve, bad_node)

        # Unsupported shift type raises UnsupportedConventionError
        bad_type = ScenarioSpec(
            scenario_id="bad_type",
            name="Bad Type",
            shift_type="QUADRATIC_WAVE",
            magnitude_bp=10.0,
        )
        with self.assertRaises(UnsupportedConventionError):
            run_scenario(long_bond, self.curve, bad_type)


if __name__ == "__main__":
    unittest.main()
