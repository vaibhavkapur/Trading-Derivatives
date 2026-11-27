# Operator & Developer Runbook

## 1. Clean Environment Setup

```bash
cd volatility-surface-engine
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/python setup.py develop --no-deps
```

Verify installation:
```bash
.venv/bin/vse validate --input data/fixtures/valid_chain_reference.json
```

## 2. Running Demo, Validation & Acceptance

```bash
# Run full test suite (70 tests)
.venv/bin/pytest

# Run reproducible end-to-end demo
.venv/bin/python scripts/run_demo.py

# Run acceptance benchmark & oracle checks
.venv/bin/python scripts/run_acceptance.py
```

Generated artifacts in `reports/`:
- `reports/demo_results.json` — End-to-end demo summary
- `reports/demo_snapshot.json` — Versioned calibrated surface snapshot
- `reports/demo_surface.html` — Interactive 3D Plotly surface visualization
- `reports/baseline_comparison.json` — Analytical & interpolated oracle comparison
- `reports/acceptance_results.json` — Acceptance criteria metrics
- `reports/runtime_metadata.json` — Python & package versions

## 3. Deterministic Replay & Audit Verification

To verify that a previously generated `SurfaceSnapshot` reproduces identically from the same input snapshot and configuration:

```bash
.venv/bin/vse replay \
  --input data/fixtures/valid_chain_reference.json \
  --snapshot reports/demo_snapshot.json \
  --config config/default_config.json
```

- Exit code `0` with `"replay_match": true`: Exact deterministic match on `surface_id`, `config_hash`, and `is_valid`.
- Exit code `3` with `"replay_match": false`: Configuration, seed, input file, or library versions changed; inspect `config_hash` and `diagnostics.runtime_metadata` in the snapshot JSON.

## 4. Diagnostics & Failure Recovery

| Symptom / Error Code | Likely Cause | Diagnostic Check | Recovery Step |
|---|---|---|---|
| `CONFIGURATION_ERROR` (exit `2`) | Missing config file, bad JSON, unknown key, or out-of-range parameter | Check stderr JSON `message` | Restore from `config/default_config.json` or correct parameter range |
| `VALIDATION_ERROR` (exit `2`) | Schema mismatch, naive timestamp, unsupported `american` style, negative forward | Inspect stderr `details.field` | Fix upstream feed adapter to emit UTC ISO-8601 timestamps and valid positive forwards/DFs |
| `CALIBRATION_ERROR` (exit `2`) | Too few quotes survived cleaning (`< min_slice_quotes`) | Inspect `details.rejection_counts` | Review market-hygiene rejections (`wide_relative_spread`, `crossed_quote`, `near_zero_vega`); do not blindly relax bounds without reviewing quote quality |
| `is_valid: false` / `ARBITRAGE_VIOLATION` | Butterfly (`g(k) < 0` or call convexity `< 0`) or calendar (`w(k, T2) < w(k, T1)`) violation | Inspect `arbitrage_check.violations[]` in snapshot JSON | Inspect affected expiry/strike quotes for stale prices or forward/dividend curve errors; block downstream pricing via `--require-valid` |
| `EXTRAPOLATION_REJECTED` (exit `2`) | Query strike or expiry outside calibrated `[k_min(T), k_max(T)]` or `[T_min, T_max]` | Check stderr `details` (`axis`, bounds) | Restrict query to calibrated domain, supply wider strikes/expiries, or pass `--allow-extrapolation` only with explicit risk sign-off |
| Warning: `Forward parity discrepancy` | Supplied forward disagrees with put-call parity forward by `> max_forward_deviation_rel` | Inspect `diagnostics.forward_estimates` | Verify discount curve, dividend assumptions, or set `prefer_estimated_forward: true` if parity pairs are liquid |
