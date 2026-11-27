#!/usr/bin/env python3
"""End-to-end deterministic demo script generating reproducible baseline report artifacts."""

from __future__ import annotations

import json
import platform
import sys
from datetime import timedelta
from pathlib import Path

from real_time_greeks_engine.adapters import JsonFixtureAdapter
from real_time_greeks_engine.config import load_config
from real_time_greeks_engine.engine import RealTimeGreeksEngine
from real_time_greeks_engine.models import ScenarioShock

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    cfg = load_config(ROOT / "config" / "default.json")
    engine = RealTimeGreeksEngine(cfg)

    base_snap = JsonFixtureAdapter(ROOT / "data" / "fixtures" / "valid_portfolio.json").load_into(engine)
    base_recon = engine.reconcile()

    # Replay deterministic stream (spot updates, duplicate tick, position sign/multiplier change, vol shift)
    events = json.loads((ROOT / "data" / "fixtures" / "replay_events.json").read_text(encoding="utf-8"))["events"]
    after_replay_snap = engine.replay_events(events)
    after_replay_recon = engine.reconcile()

    # Parallel deterministic scenarios
    shocks = [
        ScenarioShock(scenario_id="SPOT_DOWN_5PCT", spot_shock_pct=-0.05),
        ScenarioShock(scenario_id="SPOT_UP_5PCT", spot_shock_pct=0.05),
        ScenarioShock(scenario_id="VOL_UP_3PTS", vol_shift_abs=0.03),
        ScenarioShock(scenario_id="RATES_UP_50BPS", rate_shift_bps=50.0),
        ScenarioShock(scenario_id="THETA_1D_CARRY", time_step_days=1.0),
    ]
    scenario_results = engine.run_scenarios(shocks)

    # Trigger staleness freeze check by advancing valuation time past spot TTL
    stale_time = after_replay_snap.valuation_time_utc + timedelta(seconds=120)
    stale_snap = engine.set_valuation_time(stale_time)

    report = {
        "metadata": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "engine_id": cfg.engine_id,
            "engine_config_hash": cfg.config_hash(),
        },
        "initial_snapshot": {
            "snapshot_id": base_snap.snapshot_id,
            "totals": base_snap.totals.model_dump(mode="json"),
            "by_underlying": {k: v.model_dump(mode="json") for k, v in base_snap.by_underlying.items()},
            "reconciliation_passed": base_recon.passed,
            "reconciliation_max_abs_diff": base_recon.max_abs_diff,
        },
        "after_replay": {
            "snapshot_id": after_replay_snap.snapshot_id,
            "totals": after_replay_snap.totals.model_dump(mode="json"),
            "by_underlying": {k: v.model_dump(mode="json") for k, v in after_replay_snap.by_underlying.items()},
            "alerts": [a.model_dump(mode="json") for a in after_replay_snap.alerts],
            "reconciliation_passed": after_replay_recon.passed,
            "reconciliation_max_abs_diff": after_replay_recon.max_abs_diff,
        },
        "scenarios": [
            {
                "scenario_id": r.scenario_id,
                "base_pv": round(r.base_pv, 6),
                "scenario_pv": round(r.scenario_pv, 6),
                "pnl": round(r.pnl, 6),
                "elapsed_ms": r.elapsed_ms,
            }
            for r in scenario_results
        ],
        "staleness_freeze_demo": {
            "snapshot_id": stale_snap.snapshot_id,
            "has_frozen_positions": stale_snap.has_frozen_positions,
            "frozen_count": stale_snap.totals.frozen_count,
            "alert_codes": sorted({a.code for a in stale_snap.alerts}),
        },
        "diagnostics": engine.diagnostics(),
    }

    out_path = ROOT / "reports" / "demo_output.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote deterministic demo report to {out_path}")
    print(json.dumps(report["after_replay"]["totals"], indent=2))
    return 0 if (base_recon.passed and after_replay_recon.passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
