"""Configuration loading, runtime metadata capture, and replay verification."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
from typing import Any, Dict, Tuple

import matplotlib
import numpy as np
import pandas as pd

from .analytics import summarize_batch
from .contracts import ComparisonConfig, SCHEMA_VERSION, SimulationConfig
from .errors import ConfigurationError, ReplayMismatchError
from .simulator import BatchSimulationResult, simulate_batch


def load_json_file(path: str | Path) -> Dict[str, Any]:
    """Load a JSON file with explicit error handling for missing or malformed files."""
    p = Path(path)
    if not p.exists():
        raise ConfigurationError(f"Configuration file not found: {p}")
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"Malformed JSON in {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration root in {p} must be a JSON object")
    return data


def load_simulation_config(path: str | Path) -> SimulationConfig:
    """Load and validate a SimulationConfig from a JSON file."""
    return SimulationConfig.from_dict(load_json_file(path))


def load_comparison_config(path: str | Path) -> ComparisonConfig:
    """Load and validate a ComparisonConfig from a JSON file."""
    return ComparisonConfig.from_dict(load_json_file(path))


def collect_runtime_metadata() -> Dict[str, Any]:
    """Capture reproducible runtime environment metadata for report provenance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "matplotlib": matplotlib.__version__,
        },
    }


def verify_replay(run_dir: str | Path, *, base_dir: Path | None = None) -> Tuple[bool, Dict[str, Any]]:
    """Reload a saved run's config + seed, re-execute, and assert exact fill identity."""
    out_dir = Path(run_dir)
    cfg_path = out_dir / "config.json"
    summary_path = out_dir / "summary.json"
    if not cfg_path.exists() or not summary_path.exists():
        raise ConfigurationError(
            f"Run directory {out_dir} must contain both config.json and summary.json for replay."
        )

    config = load_simulation_config(cfg_path)
    prior_summary = load_json_file(summary_path)
    expected_digest = prior_summary.get("fills_digest")
    expected_run_id = prior_summary.get("run_id")

    replayed: BatchSimulationResult = simulate_batch(config, base_dir=base_dir)
    replayed_metrics = summarize_batch(replayed)

    if replayed.fills_digest != expected_digest:
        raise ReplayMismatchError(
            f"Replay fills_digest mismatch for {expected_run_id}: "
            f"expected {expected_digest}, got {replayed.fills_digest}"
        )

    prior_metrics = prior_summary.get("metrics", {})
    if abs(float(prior_metrics.get("hedge_error_mean", 0.0)) - replayed_metrics.hedge_error_mean) > 1e-12:
        raise ReplayMismatchError("Replay hedge_error_mean diverged from saved summary.")
    if abs(float(prior_metrics.get("mean_total_cost", 0.0)) - replayed_metrics.mean_total_cost) > 1e-12:
        raise ReplayMismatchError("Replay mean_total_cost diverged from saved summary.")

    report = {
        "run_id": replayed.run_id,
        "config_hash": replayed.config_hash,
        "fills_digest": replayed.fills_digest,
        "matched": True,
        "num_paths": len(replayed.path_results),
        "total_fills": sum(len(r.fills) for r in replayed.path_results),
    }
    return True, report
