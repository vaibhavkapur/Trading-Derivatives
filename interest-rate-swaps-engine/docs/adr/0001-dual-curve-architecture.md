# ADR 0001 — Dual-Curve Separation, Pure Domain Core, and Deterministic Conventions

**Status:** Accepted  
**Date:** 2026-11-27  
**Project:** `interest-rate-swaps-engine/`

## Context

Post-2008 interest rate swap valuation requires separating the **discounting curve** (typically OIS/SOFR/ESTR) from the **forward rate projection curve** (e.g., 3M term rate or IBOR/SOFR forward curve). Additionally, the engine must serve both Python library consumers and an HTTP pricing API while remaining deterministic, auditable, and independently verifiable against closed-form references without mandatory heavy C++ dependencies.

## Decisions

1. **Explicit Dual-Curve Role Enforcement:**
   - Every `CurveSnapshot` declares an immutable `role` (`DISCOUNT` or `FORWARD`).
   - `price_swap` rejects curves with mismatched roles, currencies, valuation dates, or mismatched floating index names. Single-curve valuation (for analytical textbook tests) is still possible by supplying two snapshots with identical zero rates and explicit `DISCOUNT`/`FORWARD` roles.

2. **Pure Domain Layer Isolation:**
   - All financial algorithms (`calendars`, `daycount`, `schedule`, `curves`, `pricer`, `sensitivities`, `scenarios`, `oracle`) live under `src/irs_engine/domain/` using only Python 3.12 standard library modules and frozen dataclasses.
   - FastAPI (`src/irs_engine/api/`), JSON I/O (`src/irs_engine/io.py`), and CLI (`src/irs_engine/cli.py`) depend inward on `domain/`, never the reverse.

3. **Default Interpolation & Key-Rate Reconciliation:**
   - `LINEAR_ZERO` (piecewise linear on continuously compounded zero rates with flat extrapolation) is the default curve interpolation because its linear interpolation weights form a partition of unity ($\sum_i w_i(t) = 1$), guaranteeing that the sum of per-pillar key-rate bumps (`KR01`) reconciles with parallel `PV01` to machine-tight first-order accuracy (measured relative error `< 0.0001%` under central finite differences).
   - `LOG_LINEAR_DF` and `CUBIC_SPLINE_ZERO` are also implemented and selectable per curve snapshot.

4. **Fail-Fast Fixing History Policy:**
   - For active floating periods with `fixing_date < valuation_date`, missing historical fixings raise `MissingFixingError` by default (`FixingFallbackPolicy.ERROR`) to prevent silent mispricing. An explicit `PROJECT_FALLBACK` policy is available and visibly tagged as `FALLBACK_PROJECTED` in cashflow outputs and diagnostics.

5. **Independent Reference Verification & Optional QuantLib:**
   - Verification pairs a hand-calculated 2Y single-curve telescoping closed-form fixture with a completely independent direct-summation + 100-iteration bisection oracle.
   - `QuantLib` is treated as an optional cross-check dependency that explicitly reports `NOT_RUN` when not installed.

## Consequences

- **Positive:** Fast offline execution (`41` tests in `~0.33s`), bit-for-bit reproducible `valuation_id` hashes and seeded Monte Carlo scenarios, strict contract enforcement, and clean portability to sibling `Trading-Derivatives` engines.
- **Trade-offs:** Curve bootstrapping from raw market quotes and multi-currency/CSA discounting remain outside this module's scope and must be provided upstream or in a dedicated extension.
