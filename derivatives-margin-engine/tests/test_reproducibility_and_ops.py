"""Reproducibility (fixed seed) and operational review (CLI, missing config, restart/rerun) tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from derivatives_margin_engine.cli import main as cli_main
from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.im import generate_seeded_scenarios
from derivatives_margin_engine.io_loader import load_policy
from derivatives_margin_engine.models import NettingSet, Position, SettlementPrice

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class TestReproducibilityAndOps(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(PROJECT_ROOT / "config" / "default_policy.json")

    def test_seeded_stochastic_scenarios_are_bit_for_bit_reproducible(self) -> None:
        val_date = date(2026, 1, 15)
        ns = NettingSet("NS-SEED", "ACCT-SEED", "USD")
        positions = [
            Position("P1", "NS-SEED", "ES_MAR26", "EQUITY_INDEX", Decimal("5"), Decimal("50"), "USD", Decimal("5000.00")),
            Position("P2", "NS-SEED", "ZN_MAR26", "RATES", Decimal("-10"), Decimal("1000"), "USD", Decimal("110.00")),
        ]
        prices = [
            SettlementPrice("ES_MAR26", val_date, Decimal("5000.00"), "USD", 1),
            SettlementPrice("ZN_MAR26", val_date, Decimal("110.00"), "USD", 1),
        ]

        scn_a = generate_seeded_scenarios(["EQUITY_INDEX", "RATES"], seed=4242, num_scenarios=100)
        scn_b = generate_seeded_scenarios(["EQUITY_INDEX", "RATES"], seed=4242, num_scenarios=100)
        scn_other = generate_seeded_scenarios(["EQUITY_INDEX", "RATES"], seed=9999, num_scenarios=100)

        res_a = evaluate_margin_run(val_date, self.policy, scn_a, [ns], positions, prices, [])
        res_b = evaluate_margin_run(val_date, self.policy, scn_b, [ns], positions, prices, [])
        res_other = evaluate_margin_run(val_date, self.policy, scn_other, [ns], positions, prices, [])

        self.assertEqual(res_a.run_id, res_b.run_id)
        self.assertEqual(res_a.scenario_hash, res_b.scenario_hash)
        self.assertEqual(res_a.to_dict(), res_b.to_dict())
        self.assertNotEqual(res_a.scenario_hash, res_other.scenario_hash)

    def test_cli_missing_config_fails_loudly_with_diagnostics(self) -> None:
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            code = cli_main(
                [
                    "evaluate",
                    "--policy",
                    str(PROJECT_ROOT / "config" / "does_not_exist.json"),
                    "--scenarios",
                    str(PROJECT_ROOT / "config" / "scenarios_reference.json"),
                    "--input",
                    str(PROJECT_ROOT / "data" / "fixtures" / "reference_hand_calc.json"),
                ]
            )
        self.assertNotEqual(code, 0)
        err_payload = json.loads(stderr_buf.getvalue())
        self.assertEqual(err_payload["error"]["code"], "MISSING_CONFIG_FILE")

    def test_cli_restart_rerun_and_reconcile_on_sqlite_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "ops_test.sqlite3"
            out_path = Path(tmpdir) / "replay_out.json"
            base_args = [
                "replay",
                "--policy",
                str(PROJECT_ROOT / "config" / "default_policy.json"),
                "--scenarios",
                str(PROJECT_ROOT / "config" / "scenarios_reference.json"),
                "--schedule",
                str(PROJECT_ROOT / "data" / "fixtures" / "multiday_replay.json"),
                "--db",
                str(db_path),
                "--output",
                str(out_path),
            ]
            # First run commits
            with patch("sys.stdout", io.StringIO()):
                code1 = cli_main(base_args)
            self.assertEqual(code1, 0)
            self.assertTrue(out_path.exists())

            # Second run without --allow-rerun fails loudly (duplicate call protection)
            err_buf = io.StringIO()
            with patch("sys.stderr", err_buf):
                code_dup = cli_main(base_args)
            self.assertEqual(code_dup, 1)
            self.assertEqual(json.loads(err_buf.getvalue())["error"]["code"], "DUPLICATE_MARGIN_CALL")

            # Restart/rerun with --allow-rerun succeeds idempotently
            out_buf = io.StringIO()
            with patch("sys.stdout", out_buf):
                code2 = cli_main(base_args + ["--allow-rerun"])
            self.assertEqual(code2, 0)
            rerun_payload = json.loads(out_buf.getvalue())
            self.assertTrue(rerun_payload["reconciliation"]["is_reconciled"])
            self.assertTrue(
                all(d["persist_status"] == "IDEMPOTENT_REPLAY" for d in rerun_payload["days"])
            )

            # Standalone reconcile subcommand confirms DB integrity
            rec_buf = io.StringIO()
            with patch("sys.stdout", rec_buf):
                code_rec = cli_main(["reconcile", "--db", str(db_path)])
            self.assertEqual(code_rec, 0)
            self.assertTrue(json.loads(rec_buf.getvalue())["is_reconciled"])


if __name__ == "__main__":
    unittest.main()
