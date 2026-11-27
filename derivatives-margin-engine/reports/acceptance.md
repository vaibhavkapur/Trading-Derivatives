# Acceptance & Validation Report — Derivatives Margin Engine

**Date Executed:** 2026-11-27  
**Environment:** Python `3.9.6`, SQLite `3.51.0`, macOS (`Darwin-arm64`)  
**Policy & Scenario Versions:** `EDU-DEFAULT-V1` (`schema_version=1.0.0`), `REF-SCENARIOS-V1` (`schema_version=1.0.0`)

## 1. Test & Validation Matrix Evidence

| Test Area | Required Scenario | Test / Script Evidence | Measured Result |
|---|---|---|---|
| Domain correctness | Price shocks and short positions | `tests/test_domain_valuation_and_im.py` (`test_hand_calculated_reference_oracle_exact`, `test_offset_diversification_oracle_exact`, `test_short_positions_and_asymmetric_upside_price_shocks`, `test_intra_group_partial_offset_for_long_short_spread`, `test_var_and_expected_shortfall_aggregation_methods`), `reports/reference_baseline.json` | **PASS** — 26/26 hand-calculated oracle checks matched with `0.00` tolerance; short-position upside shock loss (`180,000.00`) and 90% intra-group basis offset (`8,000.00`) verified. |
| Integration and resilience | Haircut change and collateral expiry | `tests/test_collateral_and_integration.py` (`test_haircut_change_triggers_im_shortfall_and_call`, `test_collateral_expiry_transitions_to_ineligible_and_triggers_call`, `test_boundary_cases_fixture`, `test_mta_deferral_rounding_increment_and_concentration_cap`), `reports/demo_replay_report.json` | **PASS** — Corporate bond haircut hike (`10%→25%`) triggered `$13,750.00` IM call in unit integration test and `$15,000.00` collateral reduction in Day 2 replay; T-Bill expiry (`0` residual days) marked `EXPIRED` and triggered `$94,520.00` IM shortfall on Day 3. |
| Boundary and failure behavior | Duplicate margin calls and settlement correction | `tests/test_failure_boundary_and_journal.py` (`test_duplicate_margin_call_rejected_and_idempotent_rerun_safe`, `test_multiday_replay_settlement_correction_and_full_reconciliation`, `test_settlement_correction_without_prior_revision_rejected`, `test_invalid_cases_fixture_raises_expected_error_codes`) | **PASS** — Strict duplicate call raised `DUPLICATE_MARGIN_CALL`; idempotent rerun blocked duplicate postings (`IDEMPOTENT_REPLAY`); Day 3 rev 2 correction posted `$5,000.00` `SETTLEMENT_CORRECTION` credit with immutable rev 1 & rev 2 snapshots; missing prior revision rejected (`MISSING_PRIOR_REVISION`). |
| Reproducibility | Same versioned inputs/configuration and fixed seed | `tests/test_reproducibility_and_ops.py` (`test_seeded_stochastic_scenarios_are_bit_for_bit_reproducible`), `reports/benchmark_results.json` | **PASS** — Identical `run_id`, `scenario_hash`, and serialized outputs across repeated runs with `seed=4242` (`100` MC scenarios); distinct hash with `seed=9999`. |
| Operational review | Missing config, unsupported inputs, restart/rerun | `tests/test_reproducibility_and_ops.py` (`test_cli_missing_config_fails_loudly_with_diagnostics`, `test_cli_restart_rerun_and_reconcile_on_sqlite_file`), `tests/test_failure_boundary_and_journal.py` (`test_float_inputs_rejected_explicitly`) | **PASS** — Missing config exited non-zero with `MISSING_CONFIG_FILE`; unsupported currency, missing price, negative multiplier, and floats rejected loudly; SQLite restart/rerun with `--allow-rerun` reconciled cleanly. |

## 2. Project-Specific Acceptance Criteria

| Acceptance Criterion | Status | Evidence |
|---|---|---|
| VM equals daily MTM movement adjusted for settlements | **PASS** | Asserted in `valuation.py` (`value_netting_set_positions`) and verified by `test_vm_equals_daily_mtm_movement_adjusted_for_settlements` (`daily_mtm=7200.00 - settled=450.00 == VM 6750.00`) and all multi-day replay runs. |
| No double-counted collateral | **PASS** | Enforced at domain validation (`DUPLICATE_COLLATERAL_LOT` in `test_invalid_cases_fixture_raises_expected_error_codes`) and SQLite `PRIMARY KEY (valuation_date, revision, lot_id)` + reconciliation check (`duplicate_collateral_violations=[]`). |
| All margin calls reconcile to journal | **PASS** | `MarginJournalStore.reconcile_journal()` confirms `unreconciled_calls=[]` and `imbalance="0.00"` across 7 calls / 56 journal entries (`1,861,040.00` USD DR = CR) in `reports/demo_replay_report.json` and 100 calls / 400 entries in `reports/benchmark_results.json`; tamper detection verified by `test_reconciliation_detects_tampered_journal_imbalance`. |
| All required test scenarios pass in supported environment | **PASS** | `reports/test_results.json`: `20/20` tests passed in `47.2 ms`, `0` failures, `0` errors, `0` skipped. |
| README documents setup, demo, validation, assumptions, and limitations | **PASS** | Documented in `README.md` and `docs/runbook.md`, distinguishing actual measured runs from synthetic fixtures. |
| No real transactions, customer data, credentials, or proprietary artifacts | **PASS** | All source code and fixtures are original synthetic educational data with zero external credentials or proprietary references. |

## 3. Synthetic Benchmark Summary (`reports/benchmark_results.json`)

> **Note:** Measured on a synthetic in-memory fixture (`50` netting sets × `20` positions × `250` seeded MC scenarios, `seed=20260115`), not production data.

- **Position-scenario evaluations:** `250,000`
- **Domain evaluation latency:** `400.81 ms` (`623,732.8` position-scenario evals/sec)
- **SQLite persistence + reconciliation latency:** `3.02 ms` (`100` margin calls, `400` journal entries, `reconciled=true`)
- **Total wall-clock time:** `403.83 ms`

## 4. Explicitly Untested / Out-of-Scope Capabilities

- **PostgreSQL backend:** Not run (no local PostgreSQL service configured in sandbox; SQLite 3.51.0 stdlib backend is implemented and verified).
- **Non-linear option revaluation (gamma/vega full pricing):** Out of scope for linear payoff engine; interface defined via `adapters.py`.
- **Live market data, custody gateways, and CCP regulatory capital parity:** Explicitly excluded per project scope.

## 5. Raw Machine-Readable Artifacts

- `reports/test_results.json` — Automated test suite pass/fail counts and runtime metadata
- `reports/reference_baseline.json` — Hand-calculated oracle comparisons (`0.00` tolerance)
- `reports/demo_replay_report.json` — Multi-day replay results, idempotent rerun summary, and journal reconciliation
- `reports/benchmark_results.json` — Synthetic stress benchmark latency/throughput and environment metadata
