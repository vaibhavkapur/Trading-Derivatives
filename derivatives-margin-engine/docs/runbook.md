# Operator & Developer Runbook — Derivatives Margin Engine

## 1. Clean-Checkout Setup

```bash
cd derivatives-margin-engine
python3 --version   # Expect Python >= 3.9
python3 -m compileall -q src tests scripts
```

No external packages or network access are required.

## 2. Demo Walkthrough

Run the reproducible end-to-end demo:

```bash
PYTHONPATH=src python3 scripts/run_demo.py
```

This performs:
1. Independent oracle verification on `data/fixtures/reference_hand_calc.json` and `data/fixtures/reference_offset_oracle.json` with `0.00` Decimal tolerance → writes `reports/reference_baseline.json`.
2. 4-step daily settlement replay (`data/fixtures/multiday_replay.json`) into `reports/demo_journal.sqlite3`:
   - **Day 1 (`2026-01-15` rev 1):** Baseline MTM, VM, IM with cross-group offset, collateral haircuts, and cash/IM margin calls.
   - **Day 2 (`2026-01-16` rev 1):** Corporate bond haircut hike (`10% → 25%`) reducing eligible collateral by `$15,000.00`.
   - **Day 3 (`2026-01-19` rev 1):** T-Bill (`COL-TBILL-EXPIRING`) reaches maturity → marked `EXPIRED` (`0.00` value) → triggers `$94,520.00` IM call + `$18,000.00` VM call.
   - **Day 3 Rev 2 (`2026-01-19` rev 2):** Settlement correction revising `ES_MAR26` from `4950.00` to `4960.00` → posts explicit `$5,000.00` `SETTLEMENT_CORRECTION` credit referencing rev 1.
3. Idempotent rerun verification confirming duplicate margin calls are blocked (`IDEMPOTENT_REPLAY`) and full SQLite double-entry journal reconciliation → writes `reports/demo_replay_report.json`.

## 3. CLI Operations

```bash
# Single-day evaluation with structured JSON logs
PYTHONPATH=src python3 -m derivatives_margin_engine.cli --verbose evaluate \
  --policy config/default_policy.json \
  --scenarios config/scenarios_reference.json \
  --input data/fixtures/reference_hand_calc.json \
  --db reports/single_day.sqlite3

# Multi-day replay with cash settlement clearing
PYTHONPATH=src python3 -m derivatives_margin_engine.cli replay \
  --policy config/default_policy.json \
  --scenarios config/scenarios_reference.json \
  --schedule data/fixtures/multiday_replay.json \
  --db reports/replay.sqlite3 \
  --settle-calls

# Reconcile an existing SQLite journal database
PYTHONPATH=src python3 -m derivatives_margin_engine.cli reconcile \
  --db reports/replay.sqlite3
```

## 4. Validation & Acceptance Commands

```bash
# Full automated test suite
PYTHONPATH=src python3 -m unittest discover -s tests -v

# Collect machine-readable test and synthetic benchmark reports
PYTHONPATH=src python3 scripts/run_benchmarks_and_acceptance.py
```

## 5. Structured Diagnostics & Recovery Procedures

Pass `--verbose` to emit JSON events (`netting_set_evaluated`, `margin_run_completed`) to `stderr`. All errors exit with non-zero status and emit structured JSON `{"error": {"code": ..., "message": ..., "details": ...}}` — **never silent success**.

| Failure Scenario | Observed Error Code | Recovery Procedure |
|---|---|---|
| Missing policy or input file | `MISSING_CONFIG_FILE` / `MISSING_INPUT_FILE` | Verify `--policy`, `--scenarios`, and `--input` file paths; restore missing versioned JSON file and rerun. |
| Malformed JSON or float value | `INVALID_JSON` / `FLOAT_NOT_ALLOWED` | Fix JSON syntax and quote all decimal strings (e.g., `"100.00"`). |
| Double-counted collateral lot | `DUPLICATE_COLLATERAL_LOT` | Remove duplicate `lot_id` assignment across netting sets; split into distinct custody lot IDs if partially allocated. |
| Restart/rerun after crash on same inputs | `DUPLICATE_MARGIN_CALL` (strict mode) | Re-invoke CLI with `--allow-rerun`. If `input_hash`, `policy_hash`, and `scenario_hash` match the committed run, the store skips duplicate postings (`IDEMPOTENT_REPLAY`). |
| Settlement price needs post-publish update | `DUPLICATE_MARGIN_CALL` if reusing rev 1 | Do **not** overwrite rev 1. Publish a new snapshot with `revision = prev + 1`, `is_settlement_correction = true`, and prior settled VM in `settled_cash_adjustment`. |
| Out-of-order settlement correction | `MISSING_PRIOR_REVISION` | Replay missing prior revision (`revision - 1`) first before applying correction revision. |
| Journal reconciliation failure | `JOURNAL_RECONCILIATION_FAILED` (exit code `2`) | Inspect `reconcile --db <path>` output (`errors`, `unreconciled_calls`, `currency_totals`), restore DB from last verified backup, and replay deterministically from versioned inputs. |
