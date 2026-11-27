# Acceptance Evidence Report — Options Hedging Simulator

**Project:** `options-hedging-simulator/`  
**Schema Version:** `1.0.0`  
**Verification Environment:** Python 3.9.6, macOS (arm64), `numpy==2.0.2`, `pandas==2.3.3`, `matplotlib==3.9.4`, `pytest==8.4.2`  
**Data Provenance:** All market paths and bar fixtures are synthetic educational inputs generated with pinned PCG64 seeds or authored in `data/fixtures/`. No real market vendor data, live transactions, customer records, credentials, or proprietary employer/SAAT artifacts exist anywhere in source, fixtures, logs, or reports.

---

## 1. Acceptance Criteria Mapping

| # | Acceptance Criterion (`plan.md` §5) | Status | Measured Evidence & Artifact Link |
|---|---|---|---|
| 1 | Ledger balances exactly at currency precision | **PASS** | `max_abs_ledger_residual == "0.00"` (USD) and `"0"` (JPY) across all 45 automated tests (`tests/test_ledger_reconciliation.py`) and across every simulated path in `reports/baseline/summary.json`, `reports/holdout_gbm/policy_comparison.csv`, `reports/holdout_stress_jump/policy_comparison.csv`, and `reports/convergence/convergence_summary.json`. Deterministic 3-step fixture matches independent pure-stdlib oracle (`data/fixtures/deterministic_3step_expected.json`, terminal error `-484.02`) at every step to `$0.00`. |
| 2 | Report hedge error quantiles, turnover and costs per policy | **PASS** | Reported in machine-readable JSON/CSV (`reports/holdout_gbm/policy_comparison.csv`, `reports/holdout_gbm/comparison_summary.json`, `reports/holdout_stress_jump/policy_comparison.csv`) and plotted (`policy_comparison.png`). Includes mean, std, RMSE, MAE, min, p05, p25, p50, p75, p95, max, CVaR 5%, spread cost, fee cost, total cost, gross shares traded, gross notional traded, and turnover multiple. |
| 3 | Replay identical seed produces identical fills | **PASS** | `tests/test_reproducibility_and_cli.py::test_identical_seed_produces_identical_fills_and_replay_detects_tampering` verifies bit-identical `FillRecord` sequences and SHA-256 `fills_digest`; tampering raises `ReplayMismatchError`. End-to-end demo verified `verify_replay("reports/baseline") == True` over `14,476` fills (`run_id=baseline_gbm_daily_atm_call_6d89dd6a_s202601`). |
| 4 | All required test scenarios pass in the supported environment | **PASS** | `pytest -v`: **45 passed, 0 failed** in `11.06s` covering contracts/validation, BSM & finite-difference oracle, path generation, exact ledger reconciliation, cash/physical settlement, zero-cost convergence, gap shocks, stale prices, expiry settlement, replay, and CLI exit codes. |
| 5 | README documents setup, demo, validation, assumptions, and known limitations | **PASS** | Documented in `README.md`, `docs/architecture.md`, `docs/contracts.md`, `docs/integration.md`, `docs/runbook.md`, and `docs/adr/0001-architecture-and-ledger-precision.md`. Measured outputs are clearly labeled as synthetic simulation observations. |
| 6 | No real transactions, customer data, credentials or proprietary artifacts | **PASS** | Verified across all files in `src/`, `tests/`, `config/`, `data/fixtures/`, `docs/`, and `reports/`. Offline synthetic execution only. |

---

## 2. Test & Validation Matrix Evidence (`plan.md` §4)

| Test Area | Required Scenario | Automated Test / Benchmark Evidence | Observed Result |
|---|---|---|---|
| Domain correctness | Self-financing cash and inventory reconciliation | `tests/test_ledger_reconciliation.py` (14 tests) + `tests/test_pricing_and_oracle.py` (14 tests) | Exact `0.00` USD / `0` JPY ledger residual; exact step-by-step match to pure-stdlib oracle (`-484.02`); analytic Greeks match finite-difference oracle within `1e-5` (delta/gamma) and `1e-3` (vega/theta). |
| Integration and resilience | Zero-cost convergence as hedge interval shrinks | `tests/test_convergence_integration.py` + `reports/convergence/convergence_summary.json` | 300 GBM paths (`seed=424242`), $N \in \{12, 24, 63, 126, 252\}$: std decreases monotonically from `\$1,037.70` (`22.49%` of premium) to `\$221.53` (`4.80%` of premium). Empirical log-log slope = `-0.4900` vs. theoretical `-0.5000`. |
| Boundary and failure behavior | Gap shocks, stale prices and expiry settlement | `tests/test_boundary_and_failure.py` (4 tests) + `reports/holdout_stress_jump/policy_comparison.csv` | `-18%` gap shock logs shock counter and produces negative gamma loss with exact `0.00` residual; stale bars raise `StalePriceError` under `error` and increment counters under `skip`/`use_last`; ATM pin and OTM/ITM cash & physical settlements reconcile to `0.00` and flat inventory (`0.0` shares). |
| Reproducibility | Same versioned inputs/configuration and fixed seed | `tests/test_paths.py`, `tests/test_reproducibility_and_cli.py`, `reports/baseline/summary.json` | Identical PCG64 seed yields identical paths, fills, `fills_digest`, and metrics; `replay` command verifies `14,476` fills with zero divergence. |
| Operational review | Missing configuration, unsupported inputs, restart/rerun | `tests/test_contracts_and_validation.py`, `tests/test_reproducibility_and_cli.py` | Missing/malformed config raises `ConfigurationError` (CLI exit code `1`); American exercise and `CHF` currency raise `UnsupportedContractError`; no silent success on failure paths. |

---

## 3. Measured Holdout Benchmark Summary (Synthetic Fixtures)

> All figures below are **measured outputs from synthetic seeded simulations**, not live trading results or profitability claims.

### 3.1 Holdout GBM Policy Comparison (`config/holdout_comparison.json`, 500 paths, `seed=900101`)
Short 10 ATM European calls (`K=100`, `T=0.25y`, multiplier `100`, `r=5%`, `q=1%`, `sigma=20%`, cash settled, `2.5 bps` half-spread, `\$0.005/sh` fee, `\$1.00` min fee):

| Policy | Mean Error | Std Dev | p05 | p95 | Turnover (x Notional) | Mean Tx Cost | Max Residual |
|---|---:|---:|---:|---:|---:|---:|---:|
| `static_initial` | -\$339.25 | \$3,104.03 | -\$5,629.60 | \$3,549.88 | 1.12x | \$34.64 | \$0.00 |
| `periodic_weekly` | -\$146.51 | \$877.96 | -\$1,630.99 | \$1,189.59 | 2.23x | \$75.49 | \$0.00 |
| `threshold_band_10` | -\$169.01 | \$664.68 | -\$1,254.69 | \$949.38 | 2.46x | \$79.41 | \$0.00 |
| `threshold_band_05` | -\$173.34 | \$498.56 | -\$1,019.86 | \$527.52 | 3.01x | \$104.02 | \$0.00 |
| `threshold_band_02` | -\$203.78 | \$433.49 | -\$940.55 | \$476.48 | 3.43x | \$130.46 | \$0.00 |
| `periodic_daily` | -\$223.68 | \$421.13 | -\$941.28 | \$425.38 | 3.59x | \$153.08 | \$0.00 |

### 3.2 Holdout Stressed Merton Jump + Gap Shock (`config/stress_jump_comparison.json`, 500 paths, `seed=900202`)
Short 10 ATM European puts (`K=100`, `T=0.25y`, physical settlement, realized vol `22%` vs. hedge vol `20%`, Merton jumps `lambda=4, mu_J=-4%, sigma_J=6%` + `-8%` gap shock at step 30):

| Policy | Mean Error | Std Dev | p05 | CVaR 5% | Mean Tx Cost | Gap Events | Max Residual |
|---|---:|---:|---:|---:|---:|---:|---:|
| `static_initial` | -\$2,173.65 | \$4,689.32 | -\$11,116.41 | -\$13,718.40 | \$52.08 | 1,006 | \$0.00 |
| `periodic_weekly` | -\$2,407.71 | \$1,944.61 | -\$5,696.84 | -\$8,055.55 | \$105.71 | 1,006 | \$0.00 |
| `threshold_band_05` | -\$2,423.98 | \$1,536.51 | -\$5,168.63 | -\$7,020.36 | \$154.42 | 1,006 | \$0.00 |
| `periodic_daily` | -\$2,503.89 | \$1,540.60 | -\$5,288.54 | -\$7,101.28 | \$210.83 | 1,006 | \$0.00 |

*Observation:* Even daily or tight threshold delta hedging incurs substantial negative mean hedge error and tail losses under jumps, discrete gaps, and realized-vs-implied volatility misspecification—confirming that discrete delta hedging manages local diffusion variance but does not remove gap or model risk.

---

## 4. Explicitly Untested / Out-of-Scope Capabilities

- **Not implemented / fail-fast (`UnsupportedContractError`):** American/Bermudan early exercise, exotic/path-dependent payoffs, currencies outside `{USD, EUR, GBP, JPY}`, and non-constant local/stochastic volatility surfaces (plug-in via `PricingModel` protocol).
- **Not run in this environment:** GitHub Actions remote runner execution of `../.github/workflows/options-hedging-simulator.yml` (workflow file is authored and its exact command sequence was verified locally on Python 3.9.6).
- **Excluded by scope:** Live market data feeds, broker execution APIs, real capital, customer records, and cross-repo production deployment.
