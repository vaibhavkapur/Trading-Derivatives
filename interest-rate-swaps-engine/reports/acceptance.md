# Acceptance Evidence Report — Interest Rate Swaps Engine

**Engine version:** `0.1.0`  
**Schema version:** `1.0.0`  
**Python runtime:** `3.12.15` on `macOS-26.5.1-arm64-arm-64bit`  
**Generated (UTC):** `2026-11-27T12:43:27.757354+00:00`  
**Note on data provenance:** All valuation fixtures, curves, and fixing histories are synthetic offline fixtures created for reproducible verification. No live market data, customer records, credentials, or proprietary artifacts are used.

---

## 1. Acceptance Criteria Verification Matrix

| Acceptance Criterion | Status | Measured Evidence | Raw Artifact / Test Source |
|---|---|---|---|
| **Par swap PV within stated currency tolerance (`1e-6` USD on 10M notional)** | **PASS** | Exact par swap NPV = `-2.328e-10` USD (par rate `4.649331%`); fixture par swap NPV = `1.863e-09` USD | [baseline_valuation.json](baseline_valuation.json), `tests/test_pricer_domain.py` |
| **Receiver and payer PV sum to zero (`<= 1e-9` USD)** | **PASS** | `PV(PAYER) = 177,599.342399` USD, `PV(RECEIVER) = -177,599.342399` USD, sum = `0.000e+00` USD; `PV01(PAYER) + PV01(RECEIVER) = 0.000e+00` | [baseline_valuation.json](baseline_valuation.json), [sensitivities_report.json](sensitivities_report.json), `tests/test_pricer_domain.py` |
| **Key-rate bump results reconcile with parallel sensitivity within documented approximation (`<= 0.5%` rel or `<= 0.50` USD)** | **PASS** | Parallel PV01 = `4,246.0981` USD/bp; Sum(KR01) = `4,246.0979` USD/bp; diff = `-0.000172` USD/bp (`0.000004%`) | [sensitivities_report.json](sensitivities_report.json), `tests/test_sensitivities.py` |
| **Independent reference oracle comparison (`<= 1e-6` USD)** | **PASS** | 5Y dual-curve direct-summation oracle abs diff = `0.000e+00` USD; 2Y single-curve telescoping closed-form abs diff = `7.276e-11` USD | [oracle_comparison.json](oracle_comparison.json), `tests/test_pricer_domain.py` |
| **Known vs projected fixings and missing fixing history** | **PASS** | Seasoned swap used `1` historical and `9` projected fixings; missing historical fixing raised `MISSING_FIXING` | [baseline_valuation.json](baseline_valuation.json), `tests/test_fixings_integration.py` |
| **Deterministic curve shocks & seeded stochastic reproducibility** | **PASS** | `7` deterministic shocks evaluated; `250`-path Monte Carlo (seed `42`) exact repeat match = `True` | [scenario_snapshots.json](scenario_snapshots.json), `tests/test_scenarios_and_repro.py` |
| **All required test scenarios pass in supported environment** | **PASS** | `pytest`: `41 passed, 1 warning in 0.32s` | `tests/` |
| **Optional QuantLib oracle cross-check** | **NOT RUN** | `Optional dependency QuantLib is not installed in the offline Python environment.` — explicitly reported as `NOT_RUN` | [oracle_comparison.json](oracle_comparison.json) |
| **No real transactions, customer data, credentials, or proprietary artifacts** | **PASS** | All fixtures synthetic under `data/fixtures/`; offline execution verified | `data/fixtures/`, `config/` |

---

## 2. Detailed Measured Snapshots (Synthetic 5Y USD Vanilla Swap)

- **Trade:** `USD-5Y-IRS-001` — Payer, Notional `10,000,000 USD`, Fixed `4.25%` Semi-Annual `30/360`, Floating `USD-SOFR-3M` Quarterly `ACT/360`
- **Valuation ID:** `val_115da881c42aab8e`
- **Fixed Leg PV:** `1,890,152.32 USD`
- **Floating Leg PV:** `2,067,751.66 USD`
- **Swap NPV (Payer):** `177,599.34 USD`
- **Par Rate:** `4.649331%`
- **Discount PV01 / Sum(Disc KR01):** `-30.4515` / `-30.4515` (diff `+0.000001`)
- **Forward PV01 / Sum(Fwd KR01):** `4,276.5495` / `4,276.5495` (diff `+0.000005`)
- **Parallel PV01 / Sum(All KR01):** `4,246.0981` / `4,246.0979` (diff `-0.000172`)

### Deterministic Curve Shock Summary

| Scenario | NPV (USD) | PnL vs Base (USD) | Par Rate Change (bps) |
|---|---:|---:|---:|
| `parallel_up_100bp` | 591,542.56 | +413,943.22 | +96.65 |
| `parallel_down_100bp` | -258,033.71 | -435,633.06 | -96.42 |
| `bear_steepener` | 276,187.74 | +98,588.40 | +22.26 |
| `bull_flattener` | 77,966.03 | -99,633.32 | -22.43 |
| `butterfly_belly_up` | 393,460.25 | +215,860.91 | +49.16 |
| `basis_widening_25bp` | 284,546.54 | +106,947.20 | +24.05 |
| `discount_only_up_50bp` | 176,086.10 | -1,513.24 | +0.19 |

### Seeded Stochastic Summary (Seed `42`, `250` Paths)

- **Mean PnL:** `+8,053.96 USD`
- **Std PnL:** `287,240.85 USD`
- **5th / 50th / 95th Percentile PnL:** `-451,814.02` / `-4,820.21` / `+461,994.51 USD`
- **95% VaR / Expected Shortfall:** `451,814.02` / `588,475.12 USD`

---

## 3. Untested or Out-of-Scope Capabilities

- **QuantLib external library cross-check:** Explicitly marked `NOT_RUN` because `QuantLib` is an optional dependency not installed in the offline CPython 3.12 environment.
- **Multi-currency / cross-currency swaps & CSA collateral discounting:** Explicitly out of scope per `plan.md`.
- **Curve bootstrapping from market par swap/futures quotes:** Curves are supplied as explicit versioned zero-rate `CurveSnapshot` inputs.
