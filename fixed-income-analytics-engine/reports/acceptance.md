# Acceptance Report — Fixed Income Analytics Engine

**Engine Version:** `0.1.0`  
**Schema Version:** `1.0.0`  
**Measured Environment:** Python `3.9.6` on `macOS-26.5.1-arm64-arm-64bit`  
**Logical Fingerprint:** `logical_3b68ec80fd9f7edc`  
**Raw Evidence Files:**
- [`reports/acceptance_results.json`](acceptance_results.json)
- [`reports/baseline_comparison.json`](baseline_comparison.json)
- [`reports/demo_output.json`](demo_output.json)

> **Provenance Notice:** All market curves, deposits, bonds, and holiday calendars in these runs are **synthetic offline fixtures** (`data/fixtures/*.json`). Measured numbers below are actual runtime observations produced by `python3 scripts/run_acceptance.py` on those synthetic fixtures, contrasted explicitly against the plan's engineering targets.

---

## 1. Project-specific acceptance criteria

| Criterion | Plan Target | Measured Observation | Evidence / Test | Status |
|---|---|---|---|---|
| Reprice calibration instruments within 1 bp | `<= 1.0 bp` (`CLEAN_PRICE`/`DIRTY_PRICE` % of par bp & `SIMPLE` deposit rate bp) | Max error = `1.45e-10 bp` across 3 synthetic USD deposits + 5 calibration bonds (both `LOG_LINEAR_DF` and `LINEAR_ZERO`) | `reports/acceptance_results.json`, `tests/test_curve_bootstrapping.py` | **PASS** |
| Cash-flow totals reconcile | `<= 1e-9` | Schedule max error = `2.27e-13`; Scenario cash-flow P&L attribution max error = `1.49e-10` across 28 scenario evaluations | `reports/acceptance_results.json`, `tests/test_daycount_and_schedule.py`, `tests/test_scenarios_and_attribution.py` | **PASS** |
| DV01 agrees with finite differences within 0.5% on smooth cases | `<= 0.5%` | Max relative diff = `0.000016%` (`1.62e-5%`) across all 4 portfolio bonds and 5 holiday/irregular boundary bonds (`1 bp` central finite difference) | `reports/acceptance_results.json`, `tests/test_pricing_and_sensitivities.py` | **PASS** |
| All required test scenarios pass | 100% passing test suite | `21 / 21` `unittest` cases passed (`0` failures, `0` errors) in `0.122s` | `tests/test_*.py` | **PASS** |
| Independent reference baseline comparison | Closed-form hand-calculated oracles within `1e-10`–`1e-12` | `10 / 10` oracle metrics passed (`deposit_3m_act360_df`, `zero_5y_*`, `par_2y_*`, plus accrued-interest oracles) | `reports/baseline_comparison.json`, `data/fixtures/reference_hand_calculated.json` | **PASS** |
| Deterministic reproducibility | Matching `logical_fingerprint` across reruns | `logical_3b68ec80fd9f7edc` matched identically across `run_a`, `run_b`, and `fi-analytics replay` | `tests/test_integration_and_contracts.py`, CLI `replay` | **PASS** |
| No live transactions, customer records, credentials, or proprietary artifacts | Zero prohibited data | Verified synthetic offline fixtures only; zero external network or credential dependencies | Repository audit | **PASS** |

---

## 2. Test and validation matrix mapping

| Test Area | Required Scenario | Test Module | Verified Result |
|---|---|---|---|
| Domain correctness | Zero coupon and par instruments | `tests/test_curve_bootstrapping.py`, `tests/test_pricing_and_sensitivities.py` | **PASS** — Closed-form zero-coupon & par bond prices, DFs, duration, convexity, and DV01 match to `< 1e-12`. |
| Integration and resilience | Holiday/day-count boundaries and irregular coupons | `tests/test_daycount_and_schedule.py`, `tests/test_integration_and_contracts.py` | **PASS** — July 4 holiday rolls, leap-year Feb 29 EOM `30/360_US`, short/long front & back stubs all schedule, accrue, and price cleanly. |
| Boundary and failure behavior | Analytic duration vs numerical bumps | `tests/test_pricing_and_sensitivities.py` | **PASS** — Verified `< 0.01%` agreement on 1bp central bumps, quantified convexity divergence (`> 5%`) on 200bp one-sided bumps vs central FD, verified 5-day near-maturity stability, and verified explicit `ValidationError` on invalid bumps/yields/prices. |
| Reproducibility | Versioned inputs/config & replay | `tests/test_integration_and_contracts.py` | **PASS** — Identical `logical_fingerprint` (`logical_3b68ec80fd9f7edc`) across repeated runs and CLI `replay`. |
| Operational review | Missing config, malformed JSON, unsupported conventions | `tests/test_integration_and_contracts.py` | **PASS** — Structured `ConfigurationError`, `ValidationError`, and `UnsupportedConventionError` emitted with non-zero CLI exit code; no silent success. |

---

## 3. Explicitly untested / not-run capabilities

- **Optional QuantLib external cross-check (`NOT_RUN_UNAVAILABLE`):** `QuantLib` is not installed in this offline environment (`optional_packages.QuantLib = "not_installed"`). Accuracy is verified instead against independent closed-form analytical oracles (`data/fixtures/reference_hand_calculated.json`) and central finite-difference numerical bumps.
- **Optional NumPy/SciPy extras (`NOT_RUN_UNAVAILABLE`):** Core algorithms execute in pure Python standard library (`>=3.9`); `numpy`, `scipy`, and `pytest` are optional extras not installed in the validation environment.
- **Out-of-scope instruments/features:** Floating-rate notes, inflation-linked bonds, amortizing/sinking funds, callable/putable option-adjusted spread (OAS), ex-dividend cutoff dates, cubic-spline curve interpolation, and live market feeds are intentionally excluded from v0.1.
