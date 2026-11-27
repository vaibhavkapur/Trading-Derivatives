"""Domain correctness tests: MTM, VM invariant, price shocks, short positions, and IM offsets."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from derivatives_margin_engine.cli import compare_against_oracle
from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.io_loader import (
    load_json_file,
    load_policy,
    load_scenario_set,
    parse_snapshot_payload,
)
from derivatives_margin_engine.models import (
    AggregationMethod,
    CollateralLot,
    NettingSet,
    Position,
    SUPPORTED_SCHEMA_VERSION,
    Scenario,
    ScenarioSet,
    SettlementPrice,
    Shock,
    ShockType,
    TargetType,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestDomainValuationAndIM(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(PROJECT_ROOT / "config" / "default_policy.json")
        self.scenarios = load_scenario_set(PROJECT_ROOT / "config" / "scenarios_reference.json")

    def test_hand_calculated_reference_oracle_exact(self) -> None:
        payload = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "reference_hand_calc.json")
        val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
        res = evaluate_margin_run(
            valuation_date=val_date,
            policy=self.policy,
            scenario_set=self.scenarios,
            netting_sets=nss,
            positions=pos,
            settlement_prices=prices,
            collateral_inventory=col,
            revision=rev,
            valuation_time_utc=val_time,
            is_settlement_correction=is_corr,
        )
        cmp_res = compare_against_oracle(
            res.to_dict(), payload["expected_oracle"], tolerance=Decimal("0.00")
        )
        self.assertTrue(cmp_res["all_passed"], msg=str(cmp_res))
        self.assertEqual(cmp_res["checks_count"], 13)

    def test_offset_diversification_oracle_exact(self) -> None:
        payload = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "reference_offset_oracle.json")
        val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
        res = evaluate_margin_run(
            valuation_date=val_date,
            policy=self.policy,
            scenario_set=self.scenarios,
            netting_sets=nss,
            positions=pos,
            settlement_prices=prices,
            collateral_inventory=col,
            revision=rev,
            valuation_time_utc=val_time,
            is_settlement_correction=is_corr,
        )
        cmp_res = compare_against_oracle(
            res.to_dict(), payload["expected_oracle"], tolerance=Decimal("0.00")
        )
        self.assertTrue(cmp_res["all_passed"], msg=str(cmp_res))
        ns = res.netting_set_results[0]
        self.assertEqual(ns.im_result.gross_im, Decimal("338000.00"))
        self.assertEqual(ns.im_result.joint_im, Decimal("206000.00"))
        self.assertEqual(ns.im_result.offset_credit, Decimal("66000.00"))
        self.assertEqual(ns.im_result.net_im, Decimal("272000.00"))

    def test_vm_equals_daily_mtm_movement_adjusted_for_settlements(self) -> None:
        val_date = date(2026, 1, 15)
        ns = NettingSet("NS-VM", "ACCT-VM", "USD")
        positions = [
            Position(
                position_id="P-LONG",
                netting_set_id="NS-VM",
                instrument_id="ES_MAR26",
                risk_group="EQUITY_INDEX",
                quantity=Decimal("5"),
                contract_multiplier=Decimal("50"),
                currency="USD",
                prior_settlement_price=Decimal("5000.00"),
                settled_cash_adjustment=Decimal("1000.00"),
            ),
            Position(
                position_id="P-EUR-SHORT",
                netting_set_id="NS-VM",
                instrument_id="FESX_MAR26",
                risk_group="EQUITY_INDEX",
                quantity=Decimal("-10"),
                contract_multiplier=Decimal("10"),
                currency="EUR",
                prior_settlement_price=Decimal("4500.00"),
                settled_cash_adjustment=Decimal("-500.00"),
            ),
        ]
        prices = [
            SettlementPrice("ES_MAR26", val_date, Decimal("5020.00"), "USD", 1),
            SettlementPrice("FESX_MAR26", val_date, Decimal("4480.00"), "EUR", 1),
        ]
        res = evaluate_margin_run(
            valuation_date=val_date,
            policy=self.policy,
            scenario_set=self.scenarios,
            netting_sets=[ns],
            positions=positions,
            settlement_prices=prices,
            collateral_inventory=[],
        )
        ns_res = res.netting_set_results[0]
        # ES: +5 * 50 * 20 = +5000 USD; settled +1000 -> VM +4000
        # FESX: -10 * 10 * (-20) = +2000 EUR = +2200 USD; settled -500 EUR = -550 USD -> VM +2750
        self.assertEqual(ns_res.daily_mtm_movement, Decimal("7200.00"))
        self.assertEqual(ns_res.total_settled_cash_adjustment, Decimal("450.00"))
        self.assertEqual(
            ns_res.variation_margin,
            ns_res.daily_mtm_movement - ns_res.total_settled_cash_adjustment,
        )
        self.assertEqual(ns_res.variation_margin, Decimal("6750.00"))
        self.assertEqual(ns_res.vm_credit, Decimal("6750.00"))
        self.assertEqual(ns_res.vm_call, Decimal("0.00"))

    def test_short_positions_and_asymmetric_upside_price_shocks(self) -> None:
        val_date = date(2026, 1, 15)
        ns = NettingSet("NS-SHORT", "ACCT-S", "USD")
        pos = Position(
            position_id="P-SHORT-CL",
            netting_set_id="NS-SHORT",
            instrument_id="CL_MAR26",
            risk_group="COMMODITY",
            quantity=Decimal("-10"),
            contract_multiplier=Decimal("1000"),
            currency="USD",
            prior_settlement_price=Decimal("70.00"),
        )
        prices = [SettlementPrice("CL_MAR26", val_date, Decimal("72.00"), "USD", 1)]
        scn_set = ScenarioSet(
            schema_version=SUPPORTED_SCHEMA_VERSION,
            scenario_set_id="SHORT-SHOCK-TEST",
            description="Asymmetric upside spike +25% and absolute drop -$100 (floored at 0)",
            scenarios=(
                Scenario(
                    "UP_25_PCT",
                    "Upside shock hurts short",
                    (Shock(TargetType.INSTRUMENT, "CL_MAR26", ShockType.RELATIVE, Decimal("0.25")),),
                ),
                Scenario(
                    "DOWN_100_ABS",
                    "Downside shock floored at 0 benefits short",
                    (Shock(TargetType.INSTRUMENT, "CL_MAR26", ShockType.ABSOLUTE, Decimal("-100.00")),),
                ),
            ),
        )
        res = evaluate_margin_run(
            valuation_date=val_date,
            policy=self.policy,
            scenario_set=scn_set,
            netting_sets=[ns],
            positions=[pos],
            settlement_prices=prices,
            collateral_inventory=[],
        )
        ns_res = res.netting_set_results[0]
        # Short lost 10 * 1000 * (72 - 70) = -20,000 VM
        self.assertEqual(ns_res.variation_margin, Decimal("-20000.00"))
        self.assertEqual(ns_res.vm_call, Decimal("20000.00"))
        # UP_25_PCT: 72 -> 90 (+18) => short loss = 10 * 1000 * 18 = 180,000
        # DOWN_100_ABS: 72 -> 0 (floored) => short gain = 720,000 => loss = 0
        self.assertEqual(ns_res.im_result.net_im, Decimal("180000.00"))
        self.assertEqual(ns_res.im_result.worst_scenario_id, "UP_25_PCT")

    def test_intra_group_partial_offset_for_long_short_spread(self) -> None:
        val_date = date(2026, 1, 15)
        ns = NettingSet("NS-SPREAD", "ACCT-SP", "USD")
        # COMMODITY intra-group offset in default_policy.json is 0.90 (90% offset, 10% basis charge)
        positions = [
            Position("P-L", "NS-SPREAD", "CL_MAR26", "COMMODITY", Decimal("10"), Decimal("1000"), "USD", Decimal("80.00")),
            Position("P-S", "NS-SPREAD", "CL_JUN26", "COMMODITY", Decimal("-10"), Decimal("1000"), "USD", Decimal("80.00")),
        ]
        prices = [
            SettlementPrice("CL_MAR26", val_date, Decimal("80.00"), "USD", 1),
            SettlementPrice("CL_JUN26", val_date, Decimal("80.00"), "USD", 1),
        ]
        scn_set = ScenarioSet(
            schema_version=SUPPORTED_SCHEMA_VERSION,
            scenario_set_id="SPREAD-TEST",
            description="Parallel +10% commodity shock",
            scenarios=(
                Scenario(
                    "COMM_UP_10",
                    "Parallel +10%",
                    (Shock(TargetType.RISK_GROUP, "COMMODITY", ShockType.RELATIVE, Decimal("0.10")),),
                ),
            ),
        )
        res = evaluate_margin_run(
            valuation_date=val_date,
            policy=self.policy,
            scenario_set=scn_set,
            netting_sets=[ns],
            positions=positions,
            settlement_prices=prices,
            collateral_inventory=[],
        )
        # Gross loss on short = 80,000; gross gain on long = 80,000; alpha=0.90 -> residual IM = 8,000
        self.assertEqual(res.netting_set_results[0].im_result.net_im, Decimal("8000.00"))

    def test_var_and_expected_shortfall_aggregation_methods(self) -> None:
        val_date = date(2026, 1, 15)
        ns = NettingSet("NS-AGG", "ACCT-AGG", "USD")
        pos = Position("P1", "NS-AGG", "ES_MAR26", "EQUITY_INDEX", Decimal("1"), Decimal("100"), "USD", Decimal("100.00"))
        prices = [SettlementPrice("ES_MAR26", val_date, Decimal("100.00"), "USD", 1)]
        # 10 scenarios with losses: 100, 200, ..., 1000
        scns = tuple(
            Scenario(
                f"S{i}",
                f"drop {i}%",
                (Shock(TargetType.INSTRUMENT, "ES_MAR26", ShockType.RELATIVE, Decimal(f"-0.{i:02d}")),),
            )
            for i in range(1, 11)
        )
        scn_set = ScenarioSet(SUPPORTED_SCHEMA_VERSION, "AGG-10", "10 losses", scns)

        # At 80% confidence on 10 scenarios: ceil(0.80 * 10) = 8 -> index 7 -> 800; tail = [800, 900, 1000] -> ES = 900
        var_policy = dataclasses.replace(
            self.policy,
            im_policy=dataclasses.replace(
                self.policy.im_policy,
                aggregation_method=AggregationMethod.HISTORICAL_VAR,
                confidence_level=Decimal("0.80"),
            ),
        )
        es_policy = dataclasses.replace(
            self.policy,
            im_policy=dataclasses.replace(
                self.policy.im_policy,
                aggregation_method=AggregationMethod.EXPECTED_SHORTFALL,
                confidence_level=Decimal("0.80"),
            ),
        )
        var_res = evaluate_margin_run(val_date, var_policy, scn_set, [ns], [pos], prices, [])
        es_res = evaluate_margin_run(val_date, es_policy, scn_set, [ns], [pos], prices, [])
        self.assertEqual(var_res.netting_set_results[0].im_result.net_im, Decimal("800.00"))
        self.assertEqual(es_res.netting_set_results[0].im_result.net_im, Decimal("900.00"))


if __name__ == "__main__":
    unittest.main()
