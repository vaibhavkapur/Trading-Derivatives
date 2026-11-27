# ADR 0001 — Domain Isolation, Exact Decimal Arithmetic, and SQLite Double-Entry Journal

- **Status:** Accepted
- **Date:** 2026-11-27
- **Context:** Project 07 (`derivatives-margin-engine`) requires transparent, auditable, reproducible margin calculations (MTM, VM, scenario-based IM, collateral haircuts, shortfalls, margin calls) and daily settlement replay with strict journal reconciliation.

## Decision

1. **Strict `decimal.Decimal` Everywhere (No Binary Floats in Domain Contracts):**
   All prices, quantities, multipliers, haircuts, FX rates, and monetary totals use `decimal.Decimal` with explicit policy-defined `decimal_scale` and `rounding_mode` (`ROUND_HALF_UP` by default). Passing Python `float` values to domain parsers raises `ValidationError("FLOAT_NOT_ALLOWED")`.
2. **Pure Domain Layer Isolated from I/O:**
   `validation.py`, `valuation.py`, `im.py`, `collateral.py`, `calls.py`, and `engine.py` perform zero filesystem, network, or database I/O. All domain inputs and outputs are immutable `@dataclass(frozen=True)` records.
3. **SQLite ACID Double-Entry Journal with Structural Idempotency:**
   Daily settlement replay persists runs, versioned settlement snapshots, collateral allocations, margin calls, and balanced double-entry journal postings in SQLite (`sqlite3`, stdlib). Uniqueness constraints on `margin_calls.idempotency_key` and `collateral_allocations(valuation_date, revision, lot_id)` enforce financial invariants at the storage boundary in addition to domain checks.
4. **Zero Third-Party Runtime Dependencies:**
   Relying exclusively on Python 3.9+ stdlib guarantees clean offline reproducibility, minimal supply-chain risk, and zero licensing friction.

## Consequences

- **Positive:** Exact `0.00` match against hand-calculated reference oracles; deterministic content hashes and IDs across clean runs; atomic rollback on duplicate-call or correction-ordering failures.
- **Negative / Trade-offs:** Pure-Python `Decimal` loops are slower than vectorized C/Rust for institutional-scale (>100k positions × 10k scenarios) runs, and SQLite targets single-process replay rather than concurrent multi-service writes (PostgreSQL would be needed for production concurrency).
