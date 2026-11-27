# Fixed Income Analytics Engine — Implementation Plan

**Project:** 05 of 67  
**Target repository:** `Trading-Derivatives` (new)  
**Suggested directory:** `projects/fixed-income-analytics-engine/`  
**Status:** Proposed / not implemented by this plan  
**Suggested stack:** Python, QuantLib optional, NumPy, SciPy  
**Scope:** Independently developed educational or research prototype; offline, mock, paper or testnet execution only as applicable.

## 1. Objective and scope

Price bonds and construct auditable yield curves and rate sensitivities.

**Primary inputs:** Bond schedules, day-count calendars, coupon rates, market yields and curve nodes.

**Core deliverables**

- Build coupon cash-flow scheduler and accrued-interest engine.
- Bootstrap discount factors from synthetic deposits and bonds.
- Compute clean/dirty price, YTM, duration, convexity and DV01.
- Add curve shift scenarios and explain cash-flow contributions.

**Out of scope / limitations:** Conventions differ by instrument and market; explicitly document calendars, compounding and interpolation. Live capital, real customer records and production credentials are excluded. Any later production use requires a separate security, operational, legal and risk review as appropriate.

## 2. Architecture and implementation decisions

- **Boundaries:** Use explicit pricing conventions, currency, units, calendar, valuation time, and model parameters. Keep analytics separate from the pricing API; compare against analytic or independent numerical references.
- **Data contracts:** Define input schema, units, timestamp semantics where applicable, identifiers, validation errors and output contract in `docs/contracts.md`. Include valid, invalid and boundary fixtures.
- **Reproducibility:** Pin dependencies and configuration; version inputs and outputs; use fixed seeds for stochastic scenarios and record runtime environment. Establish a simple reference, hand-calculated fixture, or existing library oracle before adding complexity; record deviations and tolerances.
- **Integration:** Document any reusable interfaces from `Trading-Derivatives` or neighboring repositories in `docs/integration.md`. Prefer independent runnable adapters over copied implementations; verify existing paths before importing. No dependency on private employer or SAAT code.
- **Delivery layout:** `src/`, `tests/`, `config/`, `data/fixtures/`, `scripts/`, `docs/`, `reports/`, dependency/build manifest. Include this `plan.md`, a project `README.md`, and documented setup, demo and validation commands. Adapt commands to the selected stack.

## 3. Implementation milestones

### M1 — Foundation and reference fixtures

- [ ] Create scaffold, dependency lock/build configuration, project README and local setup instructions.
- [ ] Define the canonical inputs (bond schedules, day-count calendars, coupon rates, market yields and curve nodes) and expected outputs; include schema, validation and error cases.
- [ ] Build coupon cash-flow scheduler and accrued-interest engine.
- [ ] Build a small deterministic fixture and an independent expected-result example; write architecture notes and the first ADR.

**Exit gate:** Clean setup and a minimal reference scenario run successfully; input and output contracts are documented and tested.

### M2 — Core domain implementation

- [ ] Bootstrap discount factors from synthetic deposits and bonds.
- [ ] Isolate the domain algorithm/state transitions from I/O; expose a typed library or service interface.
- [ ] Implement deterministic unit tests for: Zero coupon and par instruments.
- [ ] Add input validation, error propagation and explicit handling for unsupported cases; capture baseline outputs for comparison.

**Exit gate:** Core behavior passes reference fixtures, including edge cases, with no undocumented assumptions.

### M3 — Integration and evaluation

- [ ] Compute clean/dirty price, YTM, duration, convexity and DV01.
- [ ] Run integration scenarios covering: Holiday/day-count boundaries and irregular coupons.
- [ ] Add structured logs or diagnostic counters, replay or rerun support as appropriate, and stable output identifiers.
- [ ] Compare results against the reference baseline with a documented tolerance or evaluation protocol; retain raw outputs in `reports/`.

**Exit gate:** End-to-end demo runs reproducibly, integration fixtures pass, and discrepancies are explained.

### M4 — Hardening, benchmarking and delivery

- [ ] Add curve shift scenarios and explain cash-flow contributions.
- [ ] Add failure or boundary scenarios for: Analytic duration vs numerical bumps.
- [ ] Run the acceptance checks below, record measured results and configuration, and mark untested capabilities explicitly.
- [ ] Add CI validation, operator/developer runbook, limitation notes, architecture diagram and a short demo walkthrough.

**Exit gate:** All applicable acceptance checks are evidenced; unsupported or untested features are identified; another developer can reproduce the demo from a clean checkout.

## 4. Test and validation matrix

| Test area | Required scenario | Expected evidence |
|---|---|---|
| Domain correctness | Zero coupon and par instruments | Reference comparison and automated assertions |
| Integration and resilience | Holiday/day-count boundaries and irregular coupons | Integration/scenario test report |
| Boundary and failure behavior | Analytic duration vs numerical bumps | Explicit error handling and regression test |
| Reproducibility | Same versioned inputs/configuration and fixed seed where applicable | Matching logical outputs or documented stochastic tolerance |
| Operational review | Missing configuration, unsupported inputs, restart/rerun where relevant | Clear diagnostics, documented recovery steps and no silent success |

## 5. Project-specific acceptance criteria

- [ ] Reprice calibration instruments within 1 bp price/yield convention stated.
- [ ] Cash-flow totals reconcile.
- [ ] DV01 agrees with finite differences within 0.5% on smooth cases.
- [ ] All required test scenarios pass in the supported environment, or any unavailable hardware/data dependency is explicitly marked not run.
- [ ] README documents setup, demo, validation, assumptions, and known limitations; measured results distinguish actual observations from synthetic fixtures, estimates and targets.
- [ ] No real transactions, customer data, credentials or proprietary employer/SAAT artifacts in source, fixtures, logs or reports.

## 6. Required final evidence

| Artifact | Purpose |
|---|---|
| `README.md` | Overview, quickstart, example output and limitations |
| `docs/architecture.md` | Component diagram, data flow, invariants and trade-offs |
| `docs/contracts.md` | Input/output schemas, units, versions and errors |
| `docs/integration.md` | Adapter boundaries, dependencies and compatibility |
| `docs/runbook.md` | Demo, validation, diagnostics and recovery instructions |
| `tests/` | Unit, integration and required failure scenarios |
| `reports/acceptance.md` | Each acceptance criterion mapped to test or benchmark evidence |
| `reports/` raw results | Versioned machine-readable outputs and runtime/configuration metadata |

## 7. Completion checklist

- [ ] M1–M4 completed and reviewed.
- [ ] Project-specific acceptance evidence linked from README.
- [ ] Dependency licenses and fixture provenance documented; original code and no confidential inputs.
- [ ] Reproducible example and CI workflow verified on the stated environment.
- [ ] Any production-only integrations remain disabled or explicitly outside scope.

> Numerical thresholds and technology choices in this plan are proposed engineering targets, not measured results or guarantees. Adjust them only with documented rationale and retain baseline evidence.
