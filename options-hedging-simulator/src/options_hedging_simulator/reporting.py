"""Machine-readable report writers and Matplotlib visualization generators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .analytics import (
    ConvergenceReport,
    batch_paths_dataframe,
    comparison_dataframe,
    summarize_batch,
)
from .contracts import ComparisonConfig
from .io import collect_runtime_metadata
from .simulator import BatchSimulationResult, PathSimulationResult


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")


def plot_sample_path_ledger(result: PathSimulationResult, output_path: Path) -> Path:
    """Plot spot trajectory, target vs actual hedge shares, and portfolio MTM/costs."""
    if not result.ledger_entries:
        return output_path
    times = [e.time_years for e in result.ledger_entries]
    spots = [e.spot for e in result.ledger_entries]
    targets = [e.target_shares for e in result.ledger_entries]
    shares = [e.shares_after for e in result.ledger_entries]
    mtms = [float(e.portfolio_mtm) for e in result.ledger_entries]
    cum_costs = np.cumsum([float(e.spread_cost + e.fee_cost) for e in result.ledger_entries])

    fig, axes = plt.subplots(3, 1, figsize=(9, 8.5), sharex=True)
    axes[0].plot(times, spots, color="#1f77b4", linewidth=1.8)
    axes[0].set_ylabel("Spot Price")
    axes[0].set_title(f"Sample Path Diagnostics: {result.path_id} ({result.policy_name})")
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(times, targets, label="Target Shares", linestyle="--", color="#ff7f0e", linewidth=1.4)
    axes[1].step(times, shares, where="post", label="Held Shares", color="#2ca02c", linewidth=1.6)
    axes[1].set_ylabel("Underlying Shares")
    axes[1].legend(loc="best")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(times, mtms, label="Hedged Portfolio MTM", color="#9467bd", linewidth=1.6)
    axes[2].plot(times, -cum_costs, label="Neg. Cumulative Tx Costs", linestyle=":", color="#d62728", linewidth=1.5)
    axes[2].axhline(0.0, color="black", linewidth=0.8, alpha=0.5)
    axes[2].set_xlabel("Time (Years)")
    axes[2].set_ylabel("Currency")
    axes[2].legend(loc="best")
    axes[2].grid(True, alpha=0.3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def plot_policy_comparison(batches: Dict[str, BatchSimulationResult], output_path: Path) -> Path:
    """Plot hedge error distribution and risk-vs-cost frontier across policies."""
    names = list(batches.keys())
    error_lists = [[float(r.terminal_hedge_error) for r in batches[n].path_results] for n in names]
    metrics_list = [summarize_batch(batches[n]) for n in names]

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    axes[0].boxplot(error_lists, tick_labels=names, showfliers=False, patch_artist=True)
    axes[0].axhline(0.0, color="black", linestyle="--", linewidth=0.9, alpha=0.6)
    axes[0].set_title("Terminal Hedge Error Distribution (Excl. Extreme Fliers)")
    axes[0].set_ylabel("Terminal Hedge Error (Currency)")
    axes[0].tick_params(axis="x", rotation=20)
    axes[0].grid(True, axis="y", alpha=0.3)

    stds = [m.hedge_error_std for m in metrics_list]
    costs = [m.mean_total_cost for m in metrics_list]
    axes[1].scatter(costs, stds, s=80, color="#1f77b4", zorder=3)
    for name, c, s in zip(names, costs, stds):
        axes[1].annotate(name, (c, s), textcoords="offset points", xytext=(6, 4), fontsize=8.5)
    axes[1].set_title("Hedging Frontier: Tracking Error vs. Transaction Cost")
    axes[1].set_xlabel("Mean Transaction Cost (Currency)")
    axes[1].set_ylabel("Hedge Error Std Dev (Currency)")
    axes[1].grid(True, alpha=0.3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def plot_convergence_curve(report: ConvergenceReport, output_path: Path) -> Path:
    """Plot log-log convergence of zero-cost hedge error std vs rebalance steps."""
    steps = np.array([p.num_steps for p in report.points], dtype=np.float64)
    stds = np.array([p.hedge_error_std for p in report.points], dtype=np.float64)
    ref_line = stds[0] * (steps / steps[0]) ** (-0.5)

    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.loglog(steps, stds, marker="o", linewidth=2.0, label=f"Simulated Std (slope={report.empirical_loglog_slope_vs_steps:.3f})")
    ax.loglog(steps, ref_line, linestyle="--", color="#d62728", linewidth=1.5, label="Theoretical O(N^-0.5) reference")
    ax.set_title(f"Zero-Cost Discrete Hedging Convergence ({report.num_paths} GBM paths)")
    ax.set_xlabel("Rebalancing Steps N (log scale)")
    ax.set_ylabel("Terminal Hedge Error Std Dev (log scale)")
    ax.legend(loc="best")
    ax.grid(True, which="both", alpha=0.3)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return output_path


def write_batch_report(
    batch: BatchSimulationResult,
    output_dir: str | Path,
    *,
    generate_plots: bool = True,
) -> Dict[str, Path]:
    """Write versioned JSON, CSV, and plot outputs for a single SimulationConfig batch."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics = summarize_batch(batch)
    metadata = collect_runtime_metadata()

    cfg_file = out / "config.json"
    _write_json(cfg_file, batch.config.to_dict())

    summary_file = out / "summary.json"
    _write_json(
        summary_file,
        {
            "run_id": batch.run_id,
            "config_hash": batch.config_hash,
            "fills_digest": batch.fills_digest,
            "runtime_metadata": metadata,
            "metrics": metrics.to_dict(),
        },
    )

    paths_csv = out / "path_results.csv"
    batch_paths_dataframe(batch).to_csv(paths_csv, index=False)

    artifacts: Dict[str, Path] = {
        "config": cfg_file,
        "summary": summary_file,
        "path_results": paths_csv,
    }

    first_res = batch.path_results[0]
    if first_res.ledger_entries:
        ledger_csv = out / "sample_ledger.csv"
        import pandas as pd

        pd.DataFrame([e.to_dict() for e in first_res.ledger_entries]).to_csv(ledger_csv, index=False)
        artifacts["sample_ledger"] = ledger_csv

        fills_json = out / "sample_fills.json"
        _write_json(fills_json, [f.to_dict() for f in first_res.fills])
        artifacts["sample_fills"] = fills_json

        if generate_plots:
            plot_file = out / "sample_path_diagnostics.png"
            plot_sample_path_ledger(first_res, plot_file)
            artifacts["sample_plot"] = plot_file

    return artifacts


def write_comparison_report(
    comparison_config: ComparisonConfig,
    batches: Dict[str, BatchSimulationResult],
    output_dir: str | Path,
    *,
    generate_plots: bool = True,
) -> Dict[str, Path]:
    """Write versioned outputs comparing static, periodic, and threshold policies."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metadata = collect_runtime_metadata()
    df = comparison_dataframe(batches)

    cfg_file = out / "comparison_config.json"
    _write_json(cfg_file, comparison_config.to_dict())

    table_csv = out / "policy_comparison.csv"
    df.to_csv(table_csv, index=False)

    summary_json = out / "comparison_summary.json"
    _write_json(
        summary_json,
        {
            "scenario_name": comparison_config.scenario_name,
            "config_hash": comparison_config.config_hash(),
            "runtime_metadata": metadata,
            "policies": {name: summarize_batch(b).to_dict() for name, b in batches.items()},
        },
    )

    artifacts: Dict[str, Path] = {
        "config": cfg_file,
        "table_csv": table_csv,
        "summary_json": summary_json,
    }

    if generate_plots:
        plot_file = out / "policy_comparison.png"
        plot_policy_comparison(batches, plot_file)
        artifacts["comparison_plot"] = plot_file

    return artifacts


def write_convergence_report(
    report: ConvergenceReport,
    output_dir: str | Path,
    *,
    generate_plots: bool = True,
) -> Dict[str, Path]:
    """Write machine-readable JSON/CSV and log-log plot for a convergence study."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    metadata = collect_runtime_metadata()

    json_file = out / "convergence_summary.json"
    payload = report.to_dict()
    payload["runtime_metadata"] = metadata
    _write_json(json_file, payload)

    import pandas as pd

    csv_file = out / "convergence_points.csv"
    pd.DataFrame(payload["points"]).to_csv(csv_file, index=False)

    artifacts: Dict[str, Path] = {
        "summary_json": json_file,
        "points_csv": csv_file,
    }
    if generate_plots:
        plot_file = out / "convergence_curve.png"
        plot_convergence_curve(report, plot_file)
        artifacts["convergence_plot"] = plot_file
    return artifacts
