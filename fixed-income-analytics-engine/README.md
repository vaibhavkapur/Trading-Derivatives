# Fixed Income Analytics Engine

Auditable, offline research prototype for fixed-rate and zero-coupon bond cash-flow scheduling, accrued interest, yield-curve bootstrapping, bond pricing, rate sensitivities (duration, convexity, analytic vs numerical DV01, key-rate DV01, Z-spread), and curve-shift scenario P&L attribution.

> **Scope & Disclaimer:** Educational/research prototype using deterministic synthetic offline fixtures only. No live capital, real customer records, live market feeds, or production credentials are included.

---

## Quickstart & Local Setup

Requires **Python 3.9+** with **zero required external runtime dependencies** (pure Python standard library).

```bash
cd fixed-income-analytics-engine

# 1. Run the reproducible end-to-end demo walkthrough
python3 scripts/run_demo.py

# 2. Run the full unit, integration, and boundary test suite
PYTHONPATH=src python3 -m unittest discover -s tests -v

# 3. Run the acceptance verification suite and regenerate raw JSON reports
python3 scripts/run_acceptance.py

# 4. Verify deterministic CLI replay against the saved demo baseline
PYTHONPATH=src python3 -m fixed_income replay \
  --fixture data/fixtures/valid_curve_and_bonds.json \
  --baseline reports/demo_output.json
```

Optional editable install inside a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --no-build-isolation --no-deps -e .
fi-analytics run --fixture data/fixtures/valid_curve_and_bonds.json --output reports/demo_output.json
```

---

## Project Layout

```text
fixed-income-analytics-engine/
├── plan.md                                  # Implementation plan & acceptance criteria
├── pyproject.toml                           # Package metadata & optional scientific/dev pins
├── config/
│   └── default_valuation.json               # Versioned valuation & solver configuration
├── data/fixtures/
│   ├── valid_curve_and_bonds.json           # Synthetic USD deposits, calibration & portfolio bonds
│   ├── boundary_irregular_and_holidays.json # Holiday rolls, leap-year EOM, irregular coupon stubs
│   ├── invalid_cases.json                   # Negative contract validation test cases
│   └── reference_hand_calculated.json       # Independent closed-form analytical oracles
├── src/fixed_income/
│   ├── conventions.py                       # Explicit currencies, day-counts, calendars, enums
│   ├── daycount.py                          # ACT/360, ACT/365F, ACT/ACT_ICMA, 30/360_US, 30E/360
│   ├── errors.py                            # Structured domain exception hierarchy
│   ├── models.py                            # Immutable validated data contracts & stable IDs
│   ├── schedule.py                          # Coupon scheduler (regular & stubs) & accrued engine
│   ├── solvers.py                           # Bracketed Brent & safeguarded Newton-Raphson solvers
│   ├── curve.py                             # Deposit + bond bootstrapper & curve shift transforms
│   ├── pricing.py                           # Clean/dirty price, YTM, Z-spread, duration, convexity, DV01
│   ├── scenarios.py                         # Parallel/twist/butterfly/node shocks & CF attribution
│   ├── diagnostics.py                       # Structured JSON logs, counters & replay fingerprint
│   ├── io.py                                # Isolated JSON config/fixture/report I/O boundary
│   └── cli.py                               # `run` and `replay` CLI commands
├── scripts/
│   ├── run_demo.py                          # Reproducible end-to-end demo script
│   └── run_acceptance.py                    # Acceptance runner emitting machine-readable evidence
├── tests/                                   # 21 unit, integration, boundary, and contract tests
├── docs/
│   ├── architecture.md                      # Component diagram, invariants, and ADR-001
│   ├── contracts.md                         # Input/output schemas, units, and error catalog
│   ├── integration.md                       # Sibling adapter boundaries & dependency licenses
│   └── runbook.md                           # Operator/developer commands, diagnostics & recovery
└── reports/
    ├── acceptance.md                        # Acceptance criteria mapped to measured evidence
    ├── acceptance_results.json              # Machine-readable acceptance metrics & runtime metadata
    ├── baseline_comparison.json             # Hand-calculated closed-form oracle comparisons
    └── demo_output.json                     # Full versioned demo output (curve, pricing, scenarios)
```

---

## Example Demo Output (Measured on Synthetic Fixtures)

Running `python3 scripts/run_demo.py` on `data/fixtures/valid_curve_and_bonds.json` (`valuation_date = 2026-03-16`, `USD`, `ACT/365F`, `LOG_LINEAR_DF`) produced:

```text
Valuation Date : 2026-03-16 (21:00:00Z)
Currency       : USD
Curve ID       : curve_c127db2256bef1be
Logical Hash   : logical_3b68ec80fd9f7edc
Max Calib Err  : 0.0000000001 bp

Portfolio Bond Valuations:
  PORT_ZERO_5Y         clean= 78.6473% dirty= 78.6473% ytm=4.8594% modDur=4.8841 conv= 26.238 dv01=  384.12 (fd_diff=0.000005%)
  PORT_SEASONED_4Y     clean=101.5779% dirty=102.4480% ytm=4.7926% modDur=3.4154 conv= 14.039 dv01=  349.90 (fd_diff=0.000003%)
  PORT_CORP_30360_7Y   clean=103.1829% dirty=105.0315% ytm=4.9633% modDur=5.7922 conv= 40.761 dv01=  304.18 (fd_diff=0.000009%)
  PORT_LONG_10Y        clean=100.0000% dirty=100.0000% ytm=5.1000% modDur=7.7579 conv= 73.125 dv01=  775.79 (fd_diff=0.000016%)

Sample Scenario Attribution (PORT_LONG_10Y, Parallel +100bp):
  Total PnL=-75,955.53 (-7.5956%) | Taylor(Dur+Conv)=-75,823.44 | CF Reconcile Err=1.02e-10
```

---

## Measured Acceptance Summary

Full acceptance evidence is recorded in [`reports/acceptance.md`](reports/acceptance.md) and [`reports/acceptance_results.json`](reports/acceptance_results.json).

- **Calibration repricing error:** Measured max `1.45e-10 bp` vs target `<= 1.0 bp` (**PASS**)
- **Cash-flow reconciliation:** Measured schedule error `2.27e-13`, scenario P&L attribution error `1.49e-10` vs target `<= 1e-9` (**PASS**)
- **Analytic vs finite-difference DV01:** Measured max diff `0.000016%` across all portfolio and boundary bonds vs target `<= 0.5%` (**PASS**)
- **Closed-form reference oracles:** `10 / 10` hand-calculated metrics matched within `1e-10`–`1e-12` (**PASS**)
- **Automated tests:** `21 / 21` `unittest` cases passed (`0` failures, `0` errors) (**PASS**)
- **Deterministic replay:** Identical `logical_fingerprint` (`logical_3b68ec80fd9f7edc`) across repeated runs and CLI `replay` (**PASS**)
- **Optional QuantLib cross-check:** Explicitly marked `NOT_RUN_UNAVAILABLE` (QuantLib not installed in offline environment).

---

## Key Assumptions & Limitations

- **Conventions:** Day-count (`ACT/360`, `ACT/365F`, `ACT/ACT_ICMA`, `30/360_US`, `30E/360`), compounding (`SIMPLE`, `ANNUAL`, `SEMI_ANNUAL`, `QUARTERLY`, `MONTHLY`, `CONTINUOUS`), business-day rules, and curve interpolation (`LOG_LINEAR_DF`, `LINEAR_ZERO`) are explicit and validated on every input (see [`docs/contracts.md`](docs/contracts.md)).
- **Synthetic calendars:** Built-in `US_SYNTHETIC` and `TARGET_SYNTHETIC` calendars are deterministic offline subsets, not authoritative exchange holiday schedules.
- **Instrument scope:** Fixed-rate and zero-coupon bullet bonds only (no FRNs, inflation linkers, amortizing schedules, ex-dividend windows, or embedded options/OAS).
- **Documentation links:**
  - Architecture & ADR-001: [`docs/architecture.md`](docs/architecture.md)
  - Data Contracts & Error Catalog: [`docs/contracts.md`](docs/contracts.md)
  - Integration & Dependencies: [`docs/integration.md`](docs/integration.md)
  - Developer/Operator Runbook: [`docs/runbook.md`](docs/runbook.md)
  - Acceptance Evidence: [`reports/acceptance.md`](reports/acceptance.md)
