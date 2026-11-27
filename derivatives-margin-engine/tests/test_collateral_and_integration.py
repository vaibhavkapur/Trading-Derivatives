"""Integration tests: haircut change, collateral expiry, boundary rules, and adapters."""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from derivatives_margin_engine.adapters import (
    LinearizedGreeksScenarioAdapter,
    StaticPricingAdapter,
)
from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.io_loader import (
    load_json_file,
    load_policy,
    load_scenario_set,
    parse_snapshot_payload,
)
from derivatives_margin_engine.models import (
    CallType,
    CollateralLot,
    EligibilityStatus,
    NettingSet,
    Position,
    SettlementPrice,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestCollateralAndIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(PROJECT_ROOT / "config" / "default_policy.json")
        self.scenarios = load_scenario_set(PROJECT_ROOT / "config" / "scenarios_reference.json")

    def test_haircut_change_triggers_im_shortfall_and_call(self) -> None:
        val_date = date(2026, 1, 16)
        ns = NettingSet("NS-HC", "ACCT-HC", "USD")
        pos = Position(
            "P1", "NS-HC", "ES_MAR26", "EQUITY_INDEX", Decimal("4"), Decimal("50"), "USD", Decimal("5000.00")
        )
        prices = [SettlementPrice("ES_MAR26", val_date, Decimal("5000.00"), "USD", 1)]
        # Net IM under -10% equity shock = 4 * 50 * 500 = 100,000.00
        # Corp bond MV = 115,000. At 10% haircut => 103,500 (excess 3,500, no call).
        # At 25% haircut => 86,250 (shortfall 13,750 => IM_CALL 13,750).
        col = [
            CollateralLot(
                lot_id="LOT-CORP-1",
                netting_set_id="NS-HC",
                asset_class="CORP_BOND_IG",
                security_id="CORP_2028",
                currency="USD",
                market_value=Decimal("115000.00"),
                maturity_date=date(2028, 1, 15),
            )
        ]

        before = evaluate_margin_run(val_date, self.policy, self.scenarios, [ns], [pos], prices, col)
        ns_before = before.netting_set_results[0]
        self.assertEqual(ns_before.im_result.net_im, Decimal("100000.00"))
        self.assertEqual(ns_before.eligible_collateral_value, Decimal("103500.00"))
        self.assertEqual(ns_before.im_shortfall, Decimal("0.00"))
        self.assertEqual(len(ns_before.margin_calls), 0)

        tighter_policy = self.policy.with_haircut_overrides({"CORP_BOND_IG": "0.25"})
        after = evaluate_margin_run(val_date, tighter_policy, self.scenarios, [ns], [pos], prices, col)
        ns_after = after.netting_set_results[0]
        self.assertEqual(ns_after.eligible_collateral_value, Decimal("86250.00"))
        self.assertEqual(ns_after.im_shortfall, Decimal("13750.00"))
        self.assertEqual(len(ns_after.margin_calls), 1)
        self.assertEqual(ns_after.margin_calls[0].call_type, CallType.IM_CALL)
        self.assertEqual(ns_after.margin_calls[0].amount, Decimal("13750.00"))

    def test_collateral_expiry_transitions_to_ineligible_and_triggers_call(self) -> None:
        ns = NettingSet("NS-EXP", "ACCT-EXP", "USD")
        pos = Position(
            "P1", "NS-EXP", "ES_MAR26", "EQUITY_INDEX", Decimal("4"), Decimal("50"), "USD", Decimal("5000.00")
        )
        col = [
            CollateralLot(
                lot_id="LOT-TBILL",
                netting_set_id="NS-EXP",
                asset_class="GOVT_BOND",
                security_id="UST_JAN19",
                currency="USD",
                market_value=Decimal("105000.00"),
                maturity_date=date(2026, 1, 19),
            )
        ]

        # Day before maturity (residual = 3 days -> 2% haircut -> 102,900 eligible, no shortfall)
        d_pre = date(2026, 1, 16)
        r_pre = evaluate_margin_run(
            d_pre,
            self.policy,
            self.scenarios,
            [ns],
            [pos],
            [SettlementPrice("ES_MAR26", d_pre, Decimal("5000.00"), "USD", 1)],
            col,
        )
        self.assertEqual(r_pre.netting_set_results[0].collateral_valuations[0].status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(r_pre.netting_set_results[0].eligible_collateral_value, Decimal("102900.00"))
        self.assertEqual(r_pre.netting_set_results[0].im_shortfall, Decimal("0.00"))

        # On maturity date (residual = 0 days -> EXPIRED -> 0 eligible -> 100,000 IM shortfall)
        d_exp = date(2026, 1, 19)
        r_exp = evaluate_margin_run(
            d_exp,
            self.policy,
            self.scenarios,
            [ns],
            [pos],
            [SettlementPrice("ES_MAR26", d_exp, Decimal("5000.00"), "USD", 1)],
            col,
        )
        ns_exp = r_exp.netting_set_results[0]
        self.assertEqual(ns_exp.collateral_valuations[0].status, EligibilityStatus.EXPIRED)
        self.assertEqual(ns_exp.eligible_collateral_value, Decimal("0.00"))
        self.assertEqual(ns_exp.im_shortfall, Decimal("100000.00"))
        self.assertEqual(r_exp.diagnostics.ineligible_collateral_lots, 1)

    def test_boundary_cases_fixture(self) -> None:
        payload = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "boundary_cases.json")
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
        ns_res = res.netting_set_results[0]
        by_lot = {c.lot_id: c for c in ns_res.collateral_valuations}
        # 1-day UST: 500 * 0.98 = 490.00 (ELIGIBLE)
        self.assertEqual(by_lot["LOT-1DAY-MAT"].status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(by_lot["LOT-1DAY-MAT"].post_haircut_value_base, Decimal("490.00"))
        # 0-day UST: EXPIRED -> 0.00
        self.assertEqual(by_lot["LOT-0DAY-EXPIRED"].status, EligibilityStatus.EXPIRED)
        self.assertEqual(by_lot["LOT-0DAY-EXPIRED"].post_haircut_value_base, Decimal("0.00"))
        # Crypto: INELIGIBLE_ASSET_CLASS -> 0.00
        self.assertEqual(by_lot["LOT-INELIGIBLE-CRYPTO"].status, EligibilityStatus.INELIGIBLE_ASSET_CLASS)
        self.assertEqual(by_lot["LOT-INELIGIBLE-CRYPTO"].post_haircut_value_base, Decimal("0.00"))
        # EUR Cash: 100 EUR * 1.10 = 110 USD, FX mismatch 8% haircut -> 110 * 0.92 = 101.20
        self.assertEqual(by_lot["LOT-EUR-CASH"].status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(by_lot["LOT-EUR-CASH"].fx_haircut_addon, Decimal("0.08"))
        self.assertEqual(by_lot["LOT-EUR-CASH"].post_haircut_value_base, Decimal("101.20"))
        self.assertEqual(ns_res.eligible_collateral_value, Decimal("591.20"))

    def test_mta_deferral_rounding_increment_and_concentration_cap(self) -> None:
        val_date = date(2026, 1, 15)
        ns = NettingSet("NS-POL", "ACCT-POL", "USD")
        pos = Position("P1", "NS-POL", "ES_MAR26", "EQUITY_INDEX", Decimal("1"), Decimal("50"), "USD", Decimal("5000.00"))
        prices = [SettlementPrice("ES_MAR26", val_date, Decimal("4995.00"), "USD", 1)]
        # VM = -250.00; IM = 24,975.00
        # Collateral: 15,000 CASH + 15,000 EQUITY_ETF (15% hc => 12,750) = 27,750 pre-cap
        # With 50% concentration cap: max per class = 13,875 -> CASH capped to 13,875, ETF=12,750 -> total=26,625
        col = [
            CollateralLot("C1", "NS-POL", "CASH", "USD", "USD", Decimal("15000.00")),
            CollateralLot("C2", "NS-POL", "EQUITY_ETF", "SPY", "USD", Decimal("15000.00")),
        ]
        custom_policy = dataclasses.replace(
            self.policy,
            margin_call_policy=dataclasses.replace(
                self.policy.margin_call_policy,
                minimum_transfer_amount=Decimal("500.00"),
                rounding_increment=Decimal("100.00"),
            ),
            collateral_policy=dataclasses.replace(
                self.policy.collateral_policy,
                max_single_asset_class_concentration=Decimal("0.50"),
            ),
        )
        res = evaluate_margin_run(val_date, custom_policy, self.scenarios, [ns], [pos], prices, col)
        ns_res = res.netting_set_results[0]
        # VM call of 250 < MTA 500 -> deferred to 0
        self.assertEqual(ns_res.vm_call, Decimal("0.00"))
        # Concentration cap applied to CASH lot
        by_lot = {c.lot_id: c for c in ns_res.collateral_valuations}
        self.assertEqual(by_lot["C1"].status, EligibilityStatus.CONCENTRATION_CAPPED)
        self.assertEqual(ns_res.eligible_collateral_value, Decimal("26625.00"))

    def test_independent_pricing_and_scenario_adapters(self) -> None:
        val_date = date(2026, 1, 15)
        pricing_adapter = StaticPricingAdapter(
            quotes={"ES_MAR26": {"price": "5010.00", "currency": "USD"}}
        )
        scenario_adapter = LinearizedGreeksScenarioAdapter(
            shock_table={
                "GREEKS_DN": {"ES_MAR26": "-0.08"},
                "GREEKS_UP": {"ES_MAR26": "0.08"},
            }
        )
        prices = pricing_adapter.get_settlement_prices(val_date, ["ES_MAR26"])
        scn_set = scenario_adapter.build_scenario_set("ADAPTER-SCN-V1")

        ns = NettingSet("NS-ADP", "ACCT-ADP", "USD")
        pos = Position("P1", "NS-ADP", "ES_MAR26", "EQUITY_INDEX", Decimal("2"), Decimal("50"), "USD", Decimal("5000.00"))
        res = evaluate_margin_run(val_date, self.policy, scn_set, [ns], [pos], prices, [])
        ns_res = res.netting_set_results[0]
        self.assertEqual(ns_res.variation_margin, Decimal("1000.00"))
        # 2 * 50 * 5010 * 0.08 = 40,080.00
        self.assertEqual(ns_res.im_result.net_im, Decimal("40080.00"))


if __name__ == "__main__":
    unittest.main()
