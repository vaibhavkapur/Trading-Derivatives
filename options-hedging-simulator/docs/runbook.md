# Operator & Developer Runbook

## 1. Local Environment Setup

From `options-hedging-simulator/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-lock.txt
pip install -e ".[dev]"
```

(If running on older `pip < 22` without `wheel`, add `src/` to `PYTHONPATH`: `export PYTHONPATH="$PWD/src"`.)

## 2. Verification & Test Suite

Run all unit, reconciliation, convergence, boundary, and CLI tests:

```bash
.venv/bin/pytest -v
```

Expected: all tests pass with `0` failures and `0` warnings/errors.

## 3. Running Demos & CLI Commands

### 3.1 Full End-to-End Benchmark & Demo Script

```bash
.venv/bin/python scripts/run_demo.py
```

Generates versioned outputs and plots under `reports/`:
- `reports/baseline/` (250 GBM paths, daily periodic hedging + replay check)
- `reports/holdout_gbm/` (500 holdout GBM paths across 6 static/periodic/threshold policies)
- `reports/holdout_stress_jump/` (500 holdout Merton jump + -8% deterministic gap shock paths, physical settlement)
- `reports/convergence/` (zero-cost convergence study across `N = 12, 24, 63, 126, 252`)

### 3.2 CLI Subcommands

```bash
# Validate configs
.venv/bin/python -m options_hedging_simulator validate --config config/baseline_gbm.json
.venv/bin/python -m options_hedging_simulator validate --config config/holdout_comparison.json --comparison

# Single-policy run
.venv/bin/python -m options_hedging_simulator run \
  --config config/baseline_gbm.json \
  --output-dir reports/baseline

# Replay and verify exact fill-by-fill reproducibility
.venv/bin/python -m options_hedging_simulator replay --run-dir reports/baseline

# Multi-policy comparison
.venv/bin/python -m options_hedging_simulator compare \
  --config config/holdout_comparison.json \
  --output-dir reports/holdout_gbm

# Zero-cost convergence study
.venv/bin/python -m options_hedging_simulator convergence \
  --config config/convergence_study.json \
  --steps 12,24,63,126,252 \
  --output-dir reports/convergence
```

## 4. Diagnostics & Recovery Procedures

| Symptom / Error | Likely Cause | Diagnostic Check | Recovery |
|---|---|---|---|
| Exit code `1`: `ConfigurationError: Configuration file not found` | Incorrect `--config` path or missing file | Verify relative path from working directory | Pass absolute or valid relative path; run `validate` first |
| Exit code `1`: `UnsupportedContractError` | `exercise_style != "european"` or unsupported currency/day-count | Inspect error field tag (`[UNSUPPORTED_CONTRACT]`) | Switch to supported contract terms or implement a custom `PricingModel` adapter |
| Exit code `1`: `StalePriceError` | Stale bar encountered under `stale_price_policy="error"` or at step `0` | Read `step` and `staleness_seconds` from error message | Clean source bar file or set `stale_price_policy` to `"skip"` / `"use_last"` for resilient backtests |
| Exit code `1`: `ReplayMismatchError` | `config.json` or `summary.json` modified, or dependency/RNG version changed | Compare `runtime_metadata` in `summary.json` against local `pip freeze` | Restore locked environment (`requirements-lock.txt`) and re-run from unmodified `config.json` |
| Exit code `1`: `LedgerImbalanceError` | Accounting invariant failure (internal bug or corrupted negative cost) | Inspect step ledger entries | Do not ignore; file regression test reproducing the exact seed and step |
| Headless plotting failure | Interactive GUI backend requested on server/CI | Check `MPLBACKEND` env var | `reporting.py` forces `Agg`; pass `--no-plots` if PNG output is not needed |
