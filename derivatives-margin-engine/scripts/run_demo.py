#!/usr/bin/env python3
"""End-to-end reproducible demo script for Derivatives Margin Engine."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from derivatives_margin_engine.cli import compare_against_oracle
from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.io_loader import (
    load_json_file,
    load_policy,
    load_scenario_set,
    parse_snapshot_payload,
    write_json_report,
)
from derivatives_margin_engine.journal import MarginJournalStore, runtime_environment_metadata


def main() -> int:
    policy_path = PROJECT_ROOT / "config" / "default_policy.json"
    scenarios_path = PROJECT_ROOT / "config" / "scenarios_reference.json"
    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    policy = load_policy(policy_path)
    scenarios = load_scenario_set(scenarios_path)

    # 1. Verify both hand-calculated reference fixtures against oracle
    oracle_reports = {}
    for fixture_name in ["reference_hand_calc.json", "reference_offset_oracle.json"]:
        fixture_path = PROJECT_ROOT / "data" / "fixtures" / fixture_name
        payload = load_json_file(fixture_path, kind="input")
        val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
        res = evaluate_margin_run(
            valuation_date=val_date,
            policy=policy,
            scenario_set=scenarios,
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
        assert cmp_res["all_passed"], f"Oracle check failed for {fixture_name}: {cmp_res}"
        oracle_reports[payload["fixture_id"]] = {
            "fixture_file": fixture_name,
            "comparison": cmp_res,
            "run_result": res.to_dict(),
        }

    baseline_out = {
        "report_type": "reference_oracle_baseline",
        "runtime_environment": runtime_environment_metadata(),
        "policy_id": policy.policy_id,
        "scenario_set_id": scenarios.scenario_set_id,
        "oracles": oracle_reports,
    }
    write_json_report(reports_dir / "reference_baseline.json", baseline_out)

    # 2. Run 4-step multi-day replay lifecycle in SQLite (fresh DB)
    replay_db = reports_dir / "demo_journal.sqlite3"
    if replay_db.exists():
        replay_db.unlink()

    schedule = load_json_file(PROJECT_ROOT / "data" / "fixtures" / "multiday_replay.json")
    with MarginJournalStore(str(replay_db)) as store:
        days, recon = store.replay_schedule(
            schedule,
            policy,
            scenarios,
            allow_idempotent_rerun=False,
            settle_calls=True,
        )
        # Verify idempotent rerun blocks duplicate posting cleanly
        rerun_days, recon_after_rerun = store.replay_schedule(
            schedule,
            policy,
            scenarios,
            allow_idempotent_rerun=True,
            settle_calls=False,
        )
        all_calls = store.list_margin_calls()

    assert recon.is_reconciled and recon_after_rerun.is_reconciled
    assert all(d["persist_status"] == "IDEMPOTENT_REPLAY" for d in rerun_days)

    replay_out = {
        "report_type": "multiday_settlement_replay",
        "runtime_environment": runtime_environment_metadata(),
        "fixture_id": schedule["fixture_id"],
        "policy_id": policy.policy_id,
        "scenario_set_id": scenarios.scenario_set_id,
        "days": days,
        "idempotent_rerun_summary": [
            {
                "day_label": d["day_label"],
                "persist_status": d["persist_status"],
                "duplicate_calls_blocked": d["duplicate_calls_blocked"],
            }
            for d in rerun_days
        ],
        "margin_calls": all_calls,
        "reconciliation": recon_after_rerun.to_dict(),
    }
    write_json_report(reports_dir / "demo_replay_report.json", replay_out)

    print("=== Derivatives Margin Engine Demo Summary ===")
    for fid, rep in oracle_reports.items():
        print(
            f"[ORACLE PASS] {fid}: {rep['comparison']['checks_count']} checks matched "
            f"(tolerance={rep['comparison']['tolerance']})"
        )
    print("\n--- Multi-Day Replay & Reconciliation ---")
    for d in days:
        rr = d["run_result"]
        print(
            f"  {d['day_label']} ({rr['valuation_date']} rev {rr['revision']}): "
            f"calls_posted={d['margin_calls_posted']}, journal_entries={d['journal_entries_posted']}"
        )
        for ns in rr["netting_set_results"]:
            print(
                f"    {ns['netting_set_id']}: VM={ns['variation_margin']}, "
                f"NetIM={ns['im_result']['net_im']}, Col={ns['eligible_collateral_value']}, "
                f"Shortfall={ns['im_shortfall']}, TotalDue={ns['total_call_due']}"
            )
    print(
        f"\n[RECONCILED={recon_after_rerun.is_reconciled}] "
        f"runs={recon_after_rerun.total_runs}, calls={recon_after_rerun.total_margin_calls}, "
        f"journal_entries={recon_after_rerun.total_journal_entries}, "
        f"currency_totals={recon_after_rerun.currency_totals}"
    )
    print(f"Reports written to: {reports_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
