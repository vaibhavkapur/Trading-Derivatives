"""Command-line interface for derivatives-margin-engine."""

from __future__ import annotations

import argparse
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence

from derivatives_margin_engine.engine import evaluate_margin_run
from derivatives_margin_engine.errors import MarginEngineError
from derivatives_margin_engine.io_loader import (
    load_json_file,
    load_policy,
    load_scenario_set,
    parse_snapshot_payload,
    write_json_report,
)
from derivatives_margin_engine.journal import MarginJournalStore


def compare_against_oracle(
    run_dict: Dict[str, Any],
    expected_oracle: Dict[str, Any],
    tolerance: Decimal = Decimal("0.00"),
) -> Dict[str, Any]:
    """Compare run results against an independent hand-calculated oracle within tolerance."""
    ns_lookup = {ns["netting_set_id"]: ns for ns in run_dict["netting_set_results"]}
    comparisons: List[Dict[str, Any]] = []
    all_passed = True

    for ns_id, expected_fields in expected_oracle.get("netting_sets", {}).items():
        actual_ns = ns_lookup.get(ns_id)
        if actual_ns is None:
            all_passed = False
            comparisons.append(
                {
                    "netting_set_id": ns_id,
                    "field": "__netting_set__",
                    "passed": False,
                    "error": "Missing netting set in actual result",
                }
            )
            continue

        field_map = {
            "total_mtm": actual_ns["total_mtm"],
            "daily_mtm_movement": actual_ns["daily_mtm_movement"],
            "variation_margin": actual_ns["variation_margin"],
            "gross_im": actual_ns["im_result"]["gross_im"],
            "joint_im": actual_ns["im_result"]["joint_im"],
            "offset_credit": actual_ns["im_result"]["offset_credit"],
            "net_im": actual_ns["im_result"]["net_im"],
            "eligible_collateral_value": actual_ns["eligible_collateral_value"],
            "im_shortfall": actual_ns["im_shortfall"],
            "im_excess": actual_ns["im_excess"],
            "vm_call": actual_ns["vm_call"],
            "vm_credit": actual_ns["vm_credit"],
            "total_call_due": actual_ns["total_call_due"],
        }
        for field_name, exp_val_str in expected_fields.items():
            exp_val = Decimal(str(exp_val_str))
            act_val = Decimal(str(field_map[field_name]))
            diff = abs(act_val - exp_val)
            passed = diff <= tolerance
            if not passed:
                all_passed = False
            comparisons.append(
                {
                    "netting_set_id": ns_id,
                    "field": field_name,
                    "expected": str(exp_val),
                    "actual": str(act_val),
                    "abs_diff": str(diff),
                    "tolerance": str(tolerance),
                    "passed": passed,
                }
            )

    return {
        "all_passed": all_passed,
        "tolerance": str(tolerance),
        "checks_count": len(comparisons),
        "comparisons": comparisons,
    }


def _configure_logging(verbose: bool) -> None:
    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="margin-engine",
        description="Derivatives Margin Engine CLI (educational offline prototype).",
    )
    parser.add_argument("--verbose", action="store_true", help="Emit structured JSON logs to stderr.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_eval = sub.add_parser("evaluate", help="Run a single-day margin evaluation.")
    p_eval.add_argument("--policy", type=Path, required=True)
    p_eval.add_argument("--scenarios", type=Path, required=True)
    p_eval.add_argument("--input", type=Path, required=True)
    p_eval.add_argument("--db", type=Path, default=None, help="Optional SQLite journal DB path.")
    p_eval.add_argument("--allow-rerun", action="store_true", help="Allow idempotent rerun.")
    p_eval.add_argument("--output", type=Path, default=None, help="Optional output JSON file.")

    p_replay = sub.add_parser("replay", help="Replay multi-day settlements and reconcile SQLite journal.")
    p_replay.add_argument("--policy", type=Path, required=True)
    p_replay.add_argument("--scenarios", type=Path, required=True)
    p_replay.add_argument("--schedule", type=Path, required=True)
    p_replay.add_argument("--db", type=Path, required=True)
    p_replay.add_argument("--allow-rerun", action="store_true")
    p_replay.add_argument("--settle-calls", action="store_true")
    p_replay.add_argument("--output", type=Path, default=None)

    p_rec = sub.add_parser("reconcile", help="Reconcile an existing SQLite journal database.")
    p_rec.add_argument("--db", type=Path, required=True)

    p_ora = sub.add_parser("verify-oracle", help="Evaluate fixture and verify against embedded oracle.")
    p_ora.add_argument("--policy", type=Path, required=True)
    p_ora.add_argument("--scenarios", type=Path, required=True)
    p_ora.add_argument("--fixture", type=Path, required=True)
    p_ora.add_argument("--tolerance", type=str, default="0.00")
    p_ora.add_argument("--output", type=Path, default=None)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    try:
        if args.command == "evaluate":
            policy = load_policy(args.policy)
            scenarios = load_scenario_set(args.scenarios)
            payload = load_json_file(args.input, kind="input")
            val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
            result = evaluate_margin_run(
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
            out: Dict[str, Any] = {"run_result": result.to_dict()}
            if args.db is not None:
                args.db.parent.mkdir(parents=True, exist_ok=True)
                with MarginJournalStore(str(args.db)) as store:
                    outcome = store.record_run(
                        result, prices, allow_idempotent_rerun=args.allow_rerun
                    )
                    recon = store.reconcile_journal(raise_on_failure=True)
                    out["persistence"] = {
                        "status": outcome.status,
                        "margin_calls_posted": outcome.margin_calls_posted,
                        "journal_entries_posted": outcome.journal_entries_posted,
                        "duplicate_calls_blocked": outcome.duplicate_calls_blocked,
                    }
                    out["reconciliation"] = recon.to_dict()
            if args.output is not None:
                write_json_report(args.output, out)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0

        if args.command == "replay":
            policy = load_policy(args.policy)
            scenarios = load_scenario_set(args.scenarios)
            schedule = load_json_file(args.schedule, kind="input")
            args.db.parent.mkdir(parents=True, exist_ok=True)
            with MarginJournalStore(str(args.db)) as store:
                days, recon = store.replay_schedule(
                    schedule,
                    policy,
                    scenarios,
                    allow_idempotent_rerun=args.allow_rerun,
                    settle_calls=args.settle_calls,
                )
            out = {
                "fixture_id": schedule.get("fixture_id", "CUSTOM"),
                "days": days,
                "reconciliation": recon.to_dict(),
            }
            if args.output is not None:
                write_json_report(args.output, out)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0

        if args.command == "reconcile":
            if not args.db.exists():
                raise MarginEngineError(
                    "MISSING_DATABASE", f"SQLite journal database not found: {args.db}"
                )
            with MarginJournalStore(str(args.db)) as store:
                recon = store.reconcile_journal(raise_on_failure=False)
            print(json.dumps(recon.to_dict(), indent=2, sort_keys=True))
            return 0 if recon.is_reconciled else 2

        if args.command == "verify-oracle":
            policy = load_policy(args.policy)
            scenarios = load_scenario_set(args.scenarios)
            payload = load_json_file(args.fixture, kind="input")
            val_date, val_time, rev, is_corr, nss, pos, prices, col = parse_snapshot_payload(payload)
            result = evaluate_margin_run(
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
            comparison = compare_against_oracle(
                result.to_dict(),
                payload.get("expected_oracle", {}),
                tolerance=Decimal(args.tolerance),
            )
            out = {
                "fixture_id": payload.get("fixture_id", args.fixture.name),
                "comparison": comparison,
                "run_result": result.to_dict(),
            }
            if args.output is not None:
                write_json_report(args.output, out)
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0 if comparison["all_passed"] else 1

    except MarginEngineError as exc:
        print(json.dumps({"error": exc.to_dict()}, indent=2, sort_keys=True), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
