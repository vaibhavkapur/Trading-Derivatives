"""Boundary, failure, and SQLite journal reconciliation tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import unittest

from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.errors import (
    DuplicateCollateralError,
    DuplicateMarginCallError,
    ReconciliationError,
    SettlementCorrectionError,
    ValidationError,
)
from derivatives_margin_engine.io_loader import (
    load_json_file,
    load_policy,
    load_scenario_set,
    parse_snapshot_payload,
)
from derivatives_margin_engine.journal import MarginJournalStore
from derivatives_margin_engine.models import (
    CallType,
    NettingSet,
    Position,
    SettlementPrice,
    to_decimal,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestFailureBoundaryAndJournal(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(PROJECT_ROOT / "config" / "default_policy.json")
        self.scenarios = load_scenario_set(PROJECT_ROOT / "config" / "scenarios_reference.json")

    def test_invalid_cases_fixture_raises_expected_error_codes(self) -> None:
        invalid_data = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "invalid_cases.json")
        for case in invalid_data["cases"]:
            with self.subTest(case_id=case["case_id"]):
                val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(
                    case["payload"]
                )
                with self.assertRaises(ValidationError) as ctx:
                    evaluate_margin_run(
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
                self.assertEqual(ctx.exception.code, case["expected_error_code"])
                if case["case_id"] == "ERR_DOUBLE_COUNTED_COLLATERAL":
                    self.assertIsInstance(ctx.exception, DuplicateCollateralError)

    def test_float_inputs_rejected_explicitly(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            to_decimal(0.1, "price")
        self.assertEqual(ctx.exception.code, "FLOAT_NOT_ALLOWED")

    def test_duplicate_margin_call_rejected_and_idempotent_rerun_safe(self) -> None:
        payload = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "reference_hand_calc.json")
        val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
        run_res = evaluate_margin_run(
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

        with MarginJournalStore(":memory:") as store:
            first = store.record_run(run_res, prices, allow_idempotent_rerun=False)
            self.assertEqual(first.status, "COMMITTED")
            self.assertEqual(first.margin_calls_posted, 2)  # IM_CALL + VM_CALL

            # Strict mode rejects duplicate margin call explicitly
            with self.assertRaises(DuplicateMarginCallError) as ctx:
                store.record_run(run_res, prices, allow_idempotent_rerun=False)
            self.assertEqual(ctx.exception.code, "DUPLICATE_MARGIN_CALL")

            # Idempotent rerun returns cleanly without posting duplicate entries
            rerun = store.record_run(run_res, prices, allow_idempotent_rerun=True)
            self.assertEqual(rerun.status, "IDEMPOTENT_REPLAY")
            self.assertEqual(rerun.margin_calls_posted, 0)
            self.assertEqual(rerun.duplicate_calls_blocked, 2)

            # Mutated input on the SAME revision without incrementing revision MUST fail even with allow_rerun
            mutated_prices = [
                SettlementPrice("ES_MAR26", val_date, Decimal("4970.00"), "USD", 1),
                SettlementPrice("ZN_MAR26", val_date, Decimal("110.50"), "USD", 1),
            ]
            mutated_run = evaluate_margin_run(
                valuation_date=val_date,
                policy=self.policy,
                scenario_set=self.scenarios,
                netting_sets=nss,
                positions=pos,
                settlement_prices=mutated_prices,
                collateral_inventory=col,
                revision=1,
            )
            with self.assertRaises(DuplicateMarginCallError):
                store.record_run(mutated_run, mutated_prices, allow_idempotent_rerun=True)

            recon = store.reconcile_journal(raise_on_failure=True)
            self.assertTrue(recon.is_reconciled)
            self.assertEqual(recon.total_margin_calls, 2)

    def test_multiday_replay_settlement_correction_and_full_reconciliation(self) -> None:
        schedule = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "multiday_replay.json")
        with MarginJournalStore(":memory:") as store:
            days, recon = store.replay_schedule(
                schedule,
                self.policy,
                self.scenarios,
                allow_idempotent_rerun=False,
                settle_calls=True,
            )
            self.assertTrue(recon.is_reconciled)
            self.assertEqual(len(days), 4)
            self.assertEqual(recon.total_runs, 4)
            self.assertEqual(len(recon.unreconciled_calls), 0)
            self.assertEqual(len(recon.duplicate_collateral_violations), 0)

            # Verify Day 4 entry is Day 3 Revision 2 settlement correction (+5000 credit on NS-ALPHA)
            corr_day = days[3]["run_result"]
            self.assertTrue(corr_day["is_settlement_correction"])
            self.assertEqual(corr_day["revision"], 2)
            ns_alpha = next(
                n for n in corr_day["netting_set_results"] if n["netting_set_id"] == "NS-ALPHA"
            )
            self.assertEqual(Decimal(ns_alpha["variation_margin"]), Decimal("5000.00"))
            self.assertEqual(len(ns_alpha["margin_calls"]), 1)
            self.assertEqual(ns_alpha["margin_calls"][0]["call_type"], CallType.SETTLEMENT_CORRECTION.value)
            self.assertEqual(Decimal(ns_alpha["margin_calls"][0]["amount"]), Decimal("5000.00"))

            # Verify immutable settlement snapshots preserved both rev 1 (4950) and rev 2 (4960)
            snaps = store.list_settlement_snapshots()
            es_jan19 = [
                s for s in snaps if s["valuation_date"] == "2026-01-19" and s["instrument_id"] == "ES_MAR26"
            ]
            self.assertEqual([(s["revision"], s["price"]) for s in es_jan19], [(1, "4950.00"), (2, "4960.00")])

    def test_settlement_correction_without_prior_revision_rejected(self) -> None:
        val_date = date(2026, 1, 20)
        ns = NettingSet("NS-1", "ACCT-1", "USD")
        pos = Position("P1", "NS-1", "ES_MAR26", "EQUITY_INDEX", Decimal("1"), Decimal("50"), "USD", Decimal("5000.00"))
        prices = [SettlementPrice("ES_MAR26", val_date, Decimal("5010.00"), "USD", 2)]
        res = evaluate_margin_run(
            val_date,
            self.policy,
            self.scenarios,
            [ns],
            [pos],
            prices,
            [],
            revision=2,
            is_settlement_correction=True,
        )
        with MarginJournalStore(":memory:") as store:
            with self.assertRaises(SettlementCorrectionError) as ctx:
                store.record_run(res, prices)
            self.assertEqual(ctx.exception.code, "MISSING_PRIOR_REVISION")

    def test_reconciliation_detects_tampered_journal_imbalance(self) -> None:
        payload = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "reference_hand_calc.json")
        val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
        run_res = evaluate_margin_run(
            val_date, self.policy, self.scenarios, nss, pos, prices, col, rev, val_time, is_corr
        )
        with MarginJournalStore(":memory:") as store:
            store.record_run(run_res, prices)
            # Deliberately corrupt a debit entry to verify reconciliation catches it
            store._conn.execute(
                "UPDATE journal_entries SET debit = '999999.00' WHERE rowid = 1"
            )
            store._conn.commit()
            with self.assertRaises(ReconciliationError) as ctx:
                store.reconcile_journal(raise_on_failure=True)
            self.assertEqual(ctx.exception.code, "JOURNAL_RECONCILIATION_FAILED")


if __name__ == "__main__":
    unittest.main()
