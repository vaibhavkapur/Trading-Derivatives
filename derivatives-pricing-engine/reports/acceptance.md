# Acceptance and Verification Report — Derivatives Pricing Engine

- **Engine Version:** `0.1.0`
- **Schema / Config Version:** `1.0.0`
- **Verified Date (UTC):** `2026-11-27T12:39:54Z`
- **Measured Environment:** Python `3.9.6` (CPython), NumPy `2.0.2`, SciPy `1.13.1`, pytest `8.4.2`, `macOS-26.5.1-arm64-arm-64bit`
- **Data Provenance:** All inputs and reference benchmarks are 100% synthetic educational fixtures (`data/fixtures/`). No live market quotes, real transactions, customer records, credentials, or proprietary employer/SAAT artifacts exist anywhere in this project.

---

## 1. Project-Specific Acceptance Criteria Matrix

| # | Acceptance Criterion (`plan.md` §5) | Status | Measured Evidence | Artifact / Test Reference |
|---|---|---|---|---|
| AC-1 | Analytic reference error $\le 10^{-8}$ for well-conditioned cases | **PASS** | Max absolute error across all 4 synthetic textbook/Hull reference cases is $\le 10^{-12}$ against independent Python `math.erfc` oracle and fixture targets (`test_reference_hull_fixtures_within_1e_8`). | `tests/test_black_scholes.py`, `data/fixtures/reference_hull_examples.json` |
| AC-2 | Tree/PDE convergence report across at least 3 resolutions | **PASS** | CRR Binomial evaluated across **5 resolutions** (`50, 100, 200, 400, 800`) and Crank-Nicolson PDE evaluated across **3 resolutions** (`100x100, 200x200, 400x400`) for both European and American puts. | `reports/convergence_report.json` |
| AC-3 | MC confidence intervals and runtime reported | **PASS** | Antithetic MC evaluated across `10k, 50k, 200k` paths (`seed=42`) plus a 200-trial 95% CI coverage study (`10,000` paths/trial) with standard error, CI bounds, variance reduction ratio, and runtime in milliseconds. | `reports/mc_coverage_report.json`, `reports/baseline_demo.json` |
| AC-4 | All required test scenarios pass in the supported environment, or unavailable dependencies marked not run | **PASS** | **39 / 39 pytest tests passed in 0.23s** covering domain correctness, integration, boundary/failure behavior, reproducibility, and operational errors. Optional extensions (`QuantLib`, `C++20`) are explicitly marked **Not Run / Out of Scope** in §4 below. | `tests/` (7 modules) |
| AC-5 | README documents setup, demo, validation, assumptions, and known limitations; measured results distinguish actual observations from synthetic fixtures, estimates and targets | **PASS** | Documented in `README.md`, `docs/architecture.md`, `docs/contracts.md`, `docs/runbook.md`, and `docs/adr/0001-architecture-and-conventions.md`. | `README.md`, `docs/` |
| AC-6 | No real transactions, customer data, credentials or proprietary employer/SAAT artifacts | **PASS** | Verified across all source, config, fixture, test, script, and report files. | All project files |

---

## 2. Test and Validation Matrix (`plan.md` §4)

| Test Area | Required Scenario | Automated Evidence | Result |
|---|---|---|---|
| Domain correctness | Put-call parity, intrinsic bounds, zero maturity, volatility limits, finite-difference Greeks | `tests/test_black_scholes.py`, `tests/test_contracts_and_daycount.py`, `tests/test_binomial.py`, `tests/test_monte_carlo.py` | **PASS** |
| Integration and resilience | American put premium vs European price across CRR Binomial & Crank-Nicolson PSOR PDE | `tests/test_integration_and_service.py`, `reports/baseline_demo.json` | **PASS** |
| Boundary and failure behavior | Grid refinement, MC CI coverage, CFL violation, CRR no-arbitrage check, invalid fixtures | `tests/test_pde_fd.py`, `tests/test_monte_carlo.py`, `tests/test_boundary_and_failure.py`, `data/fixtures/invalid_cases.json` | **PASS** |
| Reproducibility | Fixed seed + versioned inputs produce identical `result_id` and `unit_price` on replay | `test_reproducible_replay_envelope`, CLI `price` + `replay` (`abs_unit_price_diff = 0.0`) | **PASS** |
| Operational review | Missing/malformed config, version mismatch, unknown `method_params`, CLI non-zero exit | `test_operational_missing_and_malformed_config`, `test_cli_returns_nonzero_on_invalid_input_and_missing_config` | **PASS** |

---

## 3. Summary of Measured Benchmark Results (Synthetic Fixtures)

> **Note:** All figures below are **actual measured observations** produced by `scripts/run_benchmarks.py` on synthetic contracts (`S=100, K=100, r=0.05, sigma=0.20, T=1.0`, `ACT/365F`), recorded verbatim in `reports/*.json`.

### 3.1 Cross-Method Comparison (`reports/baseline_demo.json`)

**European Call (`q = 0.02`, Black-Scholes reference = `9.227006`):**

| Method | Config | Unit Price | Error vs BS | Runtime (ms) | Notes |
|---|---|---|---|---|---|
| `black_scholes` | Analytic | `9.227006` | `0.000000` | `0.018` | Delta `0.586851`, Gamma `0.018951`, Vega `37.901158` |
| `binomial` | `400` steps | `9.222147` | `-0.004859` | `0.457` | CRR $O(1/N)$ |
| `monte_carlo` | `100,000` paths, `seed=42`, antithetic | `9.241294` | `+0.014288` | `0.848` | 95% CI `[9.177029, 9.305558]`, SE `0.032789`, VRR `1.794x` |
| `pde_fd` | `400x400` Crank-Nicolson + Rannacher | `9.224600` | `-0.002405` | `5.497` | Delta `0.586869`, Gamma `0.018963` |

**American Put (`q = 0.00`, European BS reference = `5.573526`):**

| Method | Config | Unit Price | Early Exercise Premium | Runtime (ms) | Notes |
|---|---|---|---|---|---|
| `binomial` | `400` steps | `6.088406` | `+0.514880` | `2.001` | `37,013` early exercise nodes |
| `pde_fd` | `400x400` Crank-Nicolson + Red-Black PSOR | `6.087364` | `+0.513838` | `75.907` | PSOR converged (`max_iter_used = 15`) |

Discrepancy between independent American Binomial and PDE solvers at default `400`-resolution: `0.001042` (`0.017%`).

### 3.2 Tree & PDE Convergence (`reports/convergence_report.json`)

**European Put CRR Binomial (reference: Black-Scholes = `5.573526`):**

| Steps | Price | Abs Error | Empirical Order | Runtime (ms) |
|---:|---:|---:|---:|---:|
| 50 | `5.533634` | `0.039892` | — | `0.114` |
| 100 | `5.553554` | `0.019972` | `0.9981` | `0.117` |
| 200 | `5.563534` | `0.009992` | `0.9991` | `0.219` |
| 400 | `5.568528` | `0.004998` | `0.9995` | `0.451` |
| 800 | `5.571027` | `0.002499` | `0.9998` | `0.976` |

**American Put CRR Binomial (reference: 2000-step CRR = `6.089990`):**

| Steps | Price | Abs Error | Empirical Order | Early Exercise Nodes | Runtime (ms) |
|---:|---:|---:|---:|---:|---:|
| 50 | `6.073728` | `0.016262` | — | 514 | `0.209` |
| 100 | `6.082354` | `0.007636` | `1.0907` | 2,162 | `0.438` |
| 200 | `6.086383` | `0.003607` | `1.0819` | 8,986 | `0.940` |
| 400 | `6.088406` | `0.001584` | `1.1873` | 37,013 | `2.023` |
| 800 | `6.089402` | `0.000588` | `1.4289` | 151,316 | `5.053` |

**European Put Crank-Nicolson PDE (reference: Black-Scholes = `5.573526`):**

| Grid ($M \times N$) | $\Delta S$ | $\Delta t$ | Price | Abs Error | Empirical Order | Runtime (ms) |
|---|---:|---:|---:|---:|---:|---:|
| `100 x 100` | `4.0` | `0.0100` | `5.533640` | `0.039886` | — | `1.257` |
| `200 x 200` | `2.0` | `0.0050` | `5.563611` | `0.009915` | `2.0081` | `2.336` |
| `400 x 400` | `1.0` | `0.0025` | `5.571051` | `0.002475` | `2.0020` | `5.486` |

**American Put Crank-Nicolson + Red-Black PSOR PDE (reference: 800x800 PDE = `6.089586`):**

| Grid ($M \times N$) | $\Delta S$ | $\Delta t$ | Price | Abs Error | Empirical Order | Max PSOR Iters | Runtime (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `100 x 100` | `4.0` | `0.0100` | `6.043422` | `0.046164` | — | 15 | `15.706` |
| `200 x 200` | `2.0` | `0.0050` | `6.078783` | `0.010803` | `2.0953` | 15 | `32.838` |
| `400 x 400` | `1.0` | `0.0025` | `6.087364` | `0.002222` | `2.2813` | 15 | `75.857` |

### 3.3 Monte Carlo Path Sweep & CI Coverage (`reports/mc_coverage_report.json`)

**Path Sweep (`seed = 42`, antithetic = `true`, BS reference = `9.227006`):**

| Paths | Price | Std Error | 95% CI | Variance Reduction Ratio | Runtime (ms) |
|---:|---:|---:|---|---:|---:|
| 10,000 | `9.183628` | `0.103967` | `[8.979856, 9.387400]` | `1.780x` | `0.253` |
| 50,000 | `9.229333` | `0.046340` | `[9.138508, 9.320159]` | `1.793x` | `0.512` |
| 200,000 | `9.250591` | `0.023236` | `[9.205049, 9.296132]` | `1.792x` | `1.861` |

**200-Trial Empirical Coverage Study (`10,000` paths/trial, seeds `10000..10199`, nominal 95% CI):**
- Covered trials: **`185 / 200` (`92.5%`)** (within expected binomial sampling variability for 200 trials at $p=0.95$)
- Mean error: `-0.002333`, RMSE: `0.107736`, Mean CI half-width: `0.202081`
- Mean variance reduction ratio: **`1.800x`**, total runtime: **`21.79 ms`**

---

## 4. Explicitly Untested / Out-of-Scope Capabilities

The following optional or excluded items from `plan.md` were **not run / not implemented**:

- **QuantLib cross-check (optional):** QuantLib is not installed in the local Python environment; independent verification was performed instead via a pure Python `math.erfc` analytic oracle, textbook Hull fixtures, finite-difference Greek checks, and cross-solver (Binomial vs PDE) comparisons.
- **C++20 accelerator (optional):** Not implemented; vectorized NumPy/SciPy solvers meet sub-100ms runtimes across all benchmark resolutions.
- **Calibrated implied-volatility surfaces / local or stochastic volatility / discrete cash dividends / exotic exercise styles (Bermudan, Asian, Barrier) / Longstaff-Schwartz American Monte Carlo:** Explicitly excluded per `plan.md` §1 and rejected with `UNSUPPORTED_FEATURE` where requested.
- **Live market data, execution, customer records, and production security review:** Explicitly out of scope.
