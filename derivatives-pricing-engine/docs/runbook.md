# Operator and Developer Runbook

## 1. Clean Environment Setup

From `derivatives-pricing-engine/`:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
```

Verify the runtime environment:

```bash
PYTHONPATH=src .venv/bin/python -c "import derivatives_pricing_engine as dpe; print(dpe.ENGINE_VERSION)"
```

## 2. Running the Demo

Run the reproducible end-to-end European and American pricing comparison demo:

```bash
.venv/bin/python scripts/run_demo.py
```

Or via the CLI module directly:

```bash
PYTHONPATH=src .venv/bin/python -m derivatives_pricing_engine demo --output reports/baseline_demo.json
```

Price a single fixture file and save a replayable envelope:

```bash
PYTHONPATH=src .venv/bin/python -m derivatives_pricing_engine price \
  --input data/fixtures/valid_european_call.json \
  --output reports/single_envelope.json
```

## 3. Running Validation and Benchmarks

### Automated Test Suite

```bash
PYTHONPATH=src .venv/bin/pytest
```

### Convergence and Monte Carlo Coverage Benchmarks

```bash
.venv/bin/python scripts/run_benchmarks.py
```

This writes machine-readable, environment-stamped JSON outputs to:
- `reports/baseline_demo.json`
- `reports/convergence_report.json`
- `reports/mc_coverage_report.json`

## 4. Reproducibility & Replay Verification

To verify that a previously saved pricing envelope reproduces identically:

```bash
PYTHONPATH=src .venv/bin/python -m derivatives_pricing_engine replay \
  --input reports/single_envelope.json
```

Expected output fields:
- `"result_id_matches": true`
- `"abs_unit_price_diff": 0.0`
- `"exact_reproducible": true`

Exit code `0` confirms exact reproduction; exit code `2` signals a reproducibility drift.

## 5. Diagnostics and Failure Recovery

All CLI failures emit structured JSON to `stderr` and exit with a non-zero status code (never silent success).

| Error Code | Likely Cause | Diagnostic Inspection | Recovery Action |
|---|---|---|---|
| `CONFIGURATION_ERROR` | Missing or corrupt `--config` JSON, or `config_version` mismatch | Check `details.path` and `config_version` | Restore `config/default.json` or pass a valid v1.0.0 config file |
| `VALIDATION_ERROR` | Negative spot/strike/vol, naive timestamp, expired contract, odd MC antithetic paths, or typo in `method_params` | Inspect `details.field` / `details.unknown_params` | Correct the input payload per `docs/contracts.md` and rerun |
| `UNSUPPORTED_FEATURE` | Unsupported exercise style (e.g., Bermudan) or American option routed to `black_scholes` / `monte_carlo` | Inspect `details.exercise_style` and `details.method` | Route American options to `binomial` or `pde_fd`; unsupported exotic styles are out of scope |
| `NUMERICAL_STABILITY_ERROR` | Explicit PDE CFL violation (`cfl_number > 1`) or CRR $p \notin (0, 1)$ or PSOR non-convergence | Inspect `details.min_required_time_steps`, `details.min_recommended_steps`, or `details.psor_max_iter` | Switch PDE `scheme` to `"crank_nicolson"` or `"implicit"`, or increase `time_steps` / `steps` above the reported minimum |
