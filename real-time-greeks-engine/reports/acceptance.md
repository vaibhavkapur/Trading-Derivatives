# Acceptance Evidence Report — Real-Time Greeks Engine

- **Environment:** Python `3.12.15`, `macOS-26.5.1-arm64-arm-64bit`, `pytest 8.4.2`, `fastapi 0.165.0`, `pydantic 2.14.4`
- **Engine Config:** `config/default.json` (`engine_config_hash = e463af0804e45828`)
- **Seed:** `42` (benchmark and default stochastic scenarios)
- **Data Provenance:** 100% synthetic fixtures (`data/fixtures/` and deterministic benchmark generator). Zero real transactions, customer data, credentials, or proprietary artifacts.

---

## 1. Project-Specific Acceptance Criteria (`plan.md` Section 5)

| # | Acceptance Criterion | Status | Measured Evidence | Artifact / Test Reference |
|---|---|---|---|---|
| 1 | Analytic vs finite-difference relative error `<= 1e-4` away from singularities | **PASS** | Max relative error across primary Greeks (`price, delta, gamma, vega, theta, rho`): **`3.801e-8`**. Max across all 9 Greeks including 2nd-order (`vanna, charm, volga`): **`8.600e-7`** (116× tighter than `1e-4`). | `tests/test_pricing_and_units.py::test_analytic_vs_finite_difference_agreement_within_1e4`, `reports/benchmark_results.json` |
| 2 | Aggregate equals full recompute within tolerance | **PASS** | Demo initial: `max_abs_diff = 0.0`. Demo after replay: `max_abs_diff = 4.66e-10`. 200-option book after 1,000 incremental ticks (51 buckets checked): `max_abs_diff = 2.794e-9`, `max_rel_diff = 8.517e-14` (`abs_tol = 1e-8`, `rel_tol = 1e-9`). | `tests/test_aggregation_and_integration.py`, `reports/demo_output.json`, `reports/benchmark_results.json` |
| 3 | Publish `p50` / `p99` update times | **PASS** | 1,000 incremental spot ticks on 200 options / 10 underlyings (20 options recomputed per tick): **`p50 = 0.4474 ms`**, **`p95 = 0.5371 ms`**, **`p99 = 0.8357 ms`**, `mean = 0.5194 ms`. Also exposed live via `GET /metrics` and `/dashboard`. | `reports/benchmark_results.json`, `src/real_time_greeks_engine/api.py` |
| 4 | All required test scenarios pass (unavailable optional deps marked not run) | **PASS** | **22 / 22** automated tests passed in `0.29s`. Optional Redis & C++20 modules explicitly marked **Not Run / Out of Scope** below. | `tests/` (4 test modules) |
| 5 | README documents setup, demo, validation, assumptions, limitations, and distinguishes observations from fixtures/targets | **PASS** | Documented in `README.md`, `docs/architecture.md`, `docs/runbook.md`, and this report. | `README.md` |
| 6 | No real transactions, customer data, credentials, or proprietary artifacts | **PASS** | Verified across all `src/`, `tests/`, `data/fixtures/`, `config/`, `docs/`, and `reports/`. | Repository inspection |

---

## 2. Test & Validation Matrix Coverage (`plan.md` Section 4)

| Test Area | Required Scenario | Test Cases | Outcome |
|---|---|---|---|
| Domain correctness | Finite-difference agreement with analytic Greeks + Hull textbook oracle & put-call parity | `test_hull_textbook_reference_atm_call_and_put_call_parity`, `test_analytic_vs_finite_difference_agreement_within_1e4` (5 parametrized cases), `test_bump_and_revalue_fallback_custom_pricer`, `test_position_scaling_units_and_sign` | **PASS** |
| Integration & resilience | Position sign and multiplier changes | `test_position_sign_and_multiplier_changes_reconcile_exactly`, `test_incremental_aggregation_reconciles_with_full_recompute`, `test_version_aware_cache_hits_and_version_isolation` | **PASS** |
| Boundary & failure behavior | Stale surface, duplicate ticks, and snapshot races | `test_stale_vol_surface_freezes_snapshot_and_recovers_on_refresh`, `test_strict_raise_on_stale_fails_loudly`, `test_duplicate_and_out_of_order_ticks_ignored_without_state_corruption`, `test_snapshot_race_optimistic_conflict_and_multithreaded_consistency`, `test_boundary_flags_near_expiry_expired_low_vol_and_moneyness` | **PASS** |
| Reproducibility | Same versioned inputs/configuration and fixed seed | `test_deterministic_replay_produces_identical_snapshot_id` (`SNAP-000018-28ac8c40ef2a`), `test_parallel_scenarios_match_serial_and_fixed_seed_reproducible` (`max_pv_diff = 0.0`) | **PASS** |
| Operational review | Missing configuration, unsupported inputs, API & dashboard | `test_missing_and_invalid_configuration_fails_loudly`, `test_invalid_fixtures_and_unsupported_american_raise_explicitly`, `test_api_snapshot_reconcile_metrics_and_dashboard`, `test_api_spot_tick_conflict_and_scenarios` | **PASS** |

---

## 3. Explicitly Untested / Optional Capabilities

- **Optional C++20 pricing extension:** Not implemented or tested (pure Python BSM meets latency and accuracy requirements for this prototype).
- **Optional Redis pub/sub / distributed cache:** Not implemented or tested (offline-only environment; adapter boundaries documented in `docs/integration.md`).
- **Live market data / production deployment:** Explicitly out of scope per `plan.md`.

---

## 4. Raw Machine-Readable Artifacts

- [reports/demo_output.json](demo_output.json) — Deterministic demo output (initial snapshot, replay snapshot, 5 stress scenarios, staleness freeze demonstration, diagnostics).
- [reports/benchmark_results.json](benchmark_results.json) — Per-case analytic vs finite-difference errors across 5 contracts × 9 Greeks, 1,000-tick incremental latency percentiles (`p50`/`p95`/`p99`), post-benchmark reconciliation, and 24×200 parallel scenario comparison.
