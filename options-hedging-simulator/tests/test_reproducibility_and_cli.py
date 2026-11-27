"""Reproducibility, replay verification, and CLI operational tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from options_hedging_simulator.cli import main as cli_main
from options_hedging_simulator.errors import ReplayMismatchError
from options_hedging_simulator.io import load_simulation_config, verify_replay
from options_hedging_simulator.reporting import write_batch_report
from options_hedging_simulator.simulator import simulate_batch

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_identical_seed_produces_identical_fills_and_replay_detects_tampering(tmp_path: Path) -> None:
    cfg = load_simulation_config(PROJECT_ROOT / "config" / "baseline_gbm.json")
    batch1 = simulate_batch(cfg)
    batch2 = simulate_batch(cfg)

    assert batch1.fills_digest == batch2.fills_digest
    assert batch1.run_id == batch2.run_id
    for r1, r2 in zip(batch1.path_results, batch2.path_results):
        assert r1.fills == r2.fills
        assert r1.terminal_hedge_error == r2.terminal_hedge_error

    run_dir = tmp_path / "saved_run"
    write_batch_report(batch1, run_dir, generate_plots=False)
    matched, info = verify_replay(run_dir)
    assert matched is True
    assert info["fills_digest"] == batch1.fills_digest

    # Tamper with saved fills_digest -> verify_replay must fail explicitly
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["fills_digest"] = "0" * 64
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ReplayMismatchError):
        verify_replay(run_dir)


def test_cli_end_to_end_and_failure_exit_codes(tmp_path: Path) -> None:
    baseline_cfg = str(PROJECT_ROOT / "config" / "baseline_gbm.json")
    compare_cfg = str(PROJECT_ROOT / "config" / "holdout_comparison.json")

    assert cli_main(["validate", "--config", baseline_cfg]) == 0
    assert cli_main(["validate", "--config", compare_cfg, "--comparison"]) == 0

    run_out = tmp_path / "cli_run"
    assert cli_main(["run", "--config", baseline_cfg, "--output-dir", str(run_out), "--no-plots"]) == 0
    assert (run_out / "summary.json").exists()
    assert (run_out / "path_results.csv").exists()
    assert cli_main(["replay", "--run-dir", str(run_out)]) == 0

    # Missing config file must return non-zero exit code (no silent success)
    assert cli_main(["validate", "--config", str(tmp_path / "missing.json")]) == 1
    assert cli_main(["replay", "--run-dir", str(tmp_path / "empty_dir")]) == 1
