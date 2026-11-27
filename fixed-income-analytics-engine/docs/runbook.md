# Developer & Operator Runbook — Fixed Income Analytics Engine

## 1. Local setup

Requires Python `>= 3.9`. Zero third-party packages are required for core execution or tests.

```bash
cd fixed-income-analytics-engine

# Optional: editable install in a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install --no-build-isolation --no-deps -e .
```

You can also run directly without installation using `PYTHONPATH=src`.

## 2. Standard commands

### Run the end-to-end demo walkthrough
```bash
python3 scripts/run_demo.py
```
Generates `reports/demo_output.json` and prints bootstrapped curve nodes, bond valuations, DV01 finite-difference agreement, and a sample scenario attribution.

### Run the CLI directly
```bash
PYTHONPATH=src python3 -m fixed_income run \
  --fixture data/fixtures/valid_curve_and_bonds.json \
  --output reports/demo_output.json \
  --label manual_run
```

### Verify deterministic replay against a saved report
```bash
PYTHONPATH=src python3 -m fixed_income replay \
  --fixture data/fixtures/valid_curve_and_bonds.json \
  --baseline reports/demo_output.json
```
Exits `0` with `"status": "MATCH"` if the logical fingerprint matches bit-for-bit, or `2` with `"status": "MISMATCH"` otherwise.

### Run unit, integration, and boundary tests
```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

### Run full acceptance verification and regenerate reports
```bash
python3 scripts/run_acceptance.py
```
Regenerates:
- `reports/acceptance_results.json`
- `reports/baseline_comparison.json`
- `reports/demo_output.json`

## 3. Diagnostics and structured logs

Every JSON report contains a `diagnostics` block with:
- `counters`: `inputs.deposits`, `inputs.calibration_bonds`, `inputs.portfolio_bonds`, `calibration.nodes`, `pricing.bonds_priced`, `pricing.solver_iterations`, `scenarios.evaluations`, `events.info`.
- `events[]`: structured UTC-timestamped event records (`pipeline.start`, `curve.bootstrapped`, `bond.priced`, `pipeline.complete`).

CLI errors emit structured JSON to `stderr` with `error_code`, `error_type`, `message`, and `details`, and return exit code `1` (never silent success).

## 4. Failure modes and recovery steps

| Symptom / Error Code | Likely Cause | Recovery Action |
|---|---|---|
| `FI_CONFIGURATION_ERROR` | Missing or malformed config/fixture JSON, or missing `valuation_date`. | Verify file path and JSON syntax against `docs/contracts.md`; compare with `config/default_valuation.json`. |
| `FI_VALIDATION_ERROR` | Invalid dates (`maturity <= issue`), negative notional, currency mismatch, or unknown scenario node ID. | Inspect `details` in stderr output and correct the offending instrument or scenario definition. |
| `FI_UNSUPPORTED_CONVENTION` | Unrecognized day-count, calendar, compounding, or interpolation string. | Select a supported value documented in `docs/contracts.md`. |
| `FI_CALIBRATION_ERROR` | Duplicate calibration maturity dates, negative zero rates with `allow_negative_zero_rates=false`, or inconsistent quotes causing repricing error `> repricing_tolerance_bp`. | Deduplicate maturities, verify quote units (`CLEAN_PRICE` is % of par, rates are decimals), or enable `allow_negative_zero_rates` explicitly if testing negative-rate curves. |
| `FI_CONVERGENCE_ERROR` | Extreme distressed price or non-bracketed solver state. | Check price units and increase `solver_max_iterations` if needed; inspect `details` for bracket endpoints. |
| Replay `MISMATCH` (exit code `2`) | Input fixture, config, or engine code changed relative to baseline report. | Diff the fixture/config against `baseline["config"]` and regenerate the baseline after verifying intentional changes. |

## 5. Known limitations

- Synthetic holiday calendars (`US_SYNTHETIC`, `TARGET_SYNTHETIC`) are deterministic offline subsets and must not be used for live settlement.
- Fixed-rate and zero-coupon bullet bonds only; floating-rate notes, inflation linkers, amortizing schedules, ex-dividend cutoff windows, and embedded options (callable/putable OAS) are out of scope.
- Curve interpolation supports `LOG_LINEAR_DF` and `LINEAR_ZERO` only (no cubic spline / monotone convex).
- Optional QuantLib cross-check is not executed in offline environments without QuantLib installed; independent closed-form analytical oracles are verified instead.
