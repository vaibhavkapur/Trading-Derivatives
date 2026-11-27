#!/usr/bin/env python3
"""Run full unit/integration test suite and synthetic benchmark, saving raw JSON evidence in reports/."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.im import generate_seeded_scenarios
from derivatives_margin_engine.io_loader import load_policy, write_json_report
from derivatives_margin_engine.journal import MarginJournalStore, runtime_environment_metadata
from derivatives_margin_engine.models import (
    CollateralLot,
    NettingSet,
    Position,
    SettlementPrice,
)


def run_test_suite() -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(str(PROJECT_ROOT / "tests"), pattern="test_*.py")
    t0 = time.perf_counter()
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "report_type": "test_suite_results",
        "runtime_environment": runtime_environment_metadata(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "was_successful": result.wasSuccessful(),
        "elapsed_ms": round(elapsed_ms, 2),
    }


def run_synthetic_benchmark() -> dict:
    """Measure deterministic throughput on a synthetic workload (50 netting sets x 20 positions x 250 scenarios)."""
    policy = load_policy(PROJECT_ROOT / "config" / "default_policy.json")
    val_date = date(2026, 1, 15)
    risk_groups = ["EQUITY_INDEX", "RATES", "COMMODITY", "FX"]
    scenarios = generate_seeded_scenarios(
        risk_groups=risk_groups,
        seed=20260115,
        num_scenarios=250,
        horizon_days=5,
        annual_vols={"EQUITY_INDEX": 0.22, "RATES": 0.08, "COMMODITY": 0.30, "FX": 0.12},
    )

    num_ns = 50
    pos_per_ns = 20
    netting_sets = []
    positions = []
    collateral = []
    instruments = {}

    for ns_idx in range(num_ns):
        ns_id = f"NS-BENCH-{ns_idx:03d}"
        netting_sets.append(NettingSet(netting_set_id=ns_id, account_id=f"ACCT-{ns_idx:03d}", base_currency="USD"))
        for p_idx in range(pos_per_ns):
            grp = risk_groups[p_idx % len(risk_groups)]
            inst_id = f"INST_{grp}_{p_idx:02d}"
            instruments[inst_id] = Decimal("100.00") + Decimal(p_idx)
            qty = Decimal(str((p_idx + 1) * (1 if (p_idx + ns_idx) % 2 == 0 else -1)))
            positions.append(
                Position(
                    position_id=f"POS-{ns_idx:03d}-{p_idx:02d}",
                    netting_set_id=ns_id,
                    instrument_id=inst_id,
                    risk_group=grp,
                    quantity=qty,
                    contract_multiplier=Decimal("100"),
                    currency="USD",
                    prior_settlement_price=instruments[inst_id] - Decimal("0.50"),
                )
            )
        collateral.append(
            CollateralLot(
                lot_id=f"COL-BENCH-{ns_idx:03d}-CASH",
                netting_set_id=ns_id,
                asset_class="CASH",
                security_id="USD_CASH",
                currency="USD",
                market_value=Decimal("25000.00"),
            )
        )
        collateral.append(
            CollateralLot(
                lot_id=f"COL-BENCH-{ns_idx:03d}-UST",
                netting_set_id=ns_id,
                asset_class="GOVT_BOND",
                security_id="UST_5Y",
                currency="USD",
                market_value=Decimal("50000.00"),
                maturity_date=date(2028, 6, 15),
            )
        )

    prices = [
        SettlementPrice(
            instrument_id=inst_id,
            valuation_date=val_date,
            price=px,
            currency="USD",
            revision=1,
        )
        for inst_id, px in sorted(instruments.items())
    ]

    t_eval_start = time.perf_counter()
    run_res = evaluate_margin_run(
        valuation_date=val_date,
        policy=policy,
        scenario_set=scenarios,
        netting_sets=netting_sets,
        positions=positions,
        settlement_prices=prices,
        collateral_inventory=collateral,
    )
    eval_ms = (time.perf_counter() - t_eval_start) * 1000.0

    t_db_start = time.perf_counter()
    with MarginJournalStore(":memory:") as store:
        outcome = store.record_run(run_res, prices)
        recon = store.reconcile_journal(raise_on_failure=True)
    db_ms = (time.perf_counter() - t_db_start) * 1000.0

    total_pos_scn = len(positions) * len(scenarios.scenarios)
    return {
        "report_type": "synthetic_benchmark_results",
        "workload_kind": "synthetic_in_memory_fixture",
        "runtime_environment": runtime_environment_metadata(),
        "parameters": {
            "netting_sets": num_ns,
            "positions": len(positions),
            "collateral_lots": len(collateral),
            "scenarios": len(scenarios.scenarios),
            "position_scenario_evaluations": total_pos_scn,
            "seed": scenarios.seed,
        },
        "measurements": {
            "domain_evaluation_ms": round(eval_ms, 2),
            "sqlite_persist_and_reconcile_ms": round(db_ms, 2),
            "total_ms": round(eval_ms + db_ms, 2),
            "position_scenario_evals_per_sec": round(total_pos_scn / (eval_ms / 1000.0), 1),
            "margin_calls_posted": outcome.margin_calls_posted,
            "journal_entries_posted": outcome.journal_entries_posted,
            "reconciled": recon.is_reconciled,
            "run_id": run_res.run_id,
        },
    }


def main() -> int:
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    test_report = run_test_suite()
    write_json_report(reports_dir / "test_results.json", test_report)

    bench_report = run_synthetic_benchmark()
    write_json_report(reports_dir / "benchmark_results.json", bench_report)

    print("\n=== Benchmark Summary (Synthetic Workload) ===")
    print(json.dumps(bench_report["measurements"], indent=2, sort_keys=True))

    return 0 if test_report["was_successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
