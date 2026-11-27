# Architecture — Real-Time Greeks Engine

## 1. Component Overview

The engine separates **pure numerical analytics** (`pricing.py`, `market_data.py`), **stateful incremental aggregation and caching** (`aggregator.py`, `cache.py`), **concurrency/lifecycle orchestration** (`engine.py`), and **I/O / delivery surfaces** (`adapters.py`, `api.py`, `cli.py`).

```mermaid
flowchart LR
    subgraph Inputs["Versioned Inputs"]
        POS["Option Positions"]
        TICKS["Spot Ticks (seq, tick_id)"]
        CURVE["Yield Curve Snapshots"]
        VOL["Vol Surface Snapshots"]
    end

    subgraph Engine["RealTimeGreeksEngine (RLock + Monotonic Snapshot Seq)"]
        MD["MarketDataStore\n(Dedup, Seq Check, TTL Staleness)"]
        CACHE["VersionAwareGreeksCache\n(Composite Version LRU)"]
        PRICER["Pure Pricing Analytics\n(Analytic BSM / Bump-and-Revalue)"]
        AGG["IncrementalGreeksAggregator\n(O(1) Underlying & Expiry Buckets)"]
        SNAP["Immutable PortfolioSnapshot\n(Atomic Reference Swap)"]
        SCEN["Parallel Scenario Runner\n(ThreadPoolExecutor on Cloned State)"]
    end

    subgraph Outputs["API, Diagnostics & Reconciliation"]
        API["FastAPI Service\n(/v1/snapshot, /v1/alerts, /v1/reconcile)"]
        DASH["HTML Reconciliation Dashboard\n(/dashboard)"]
        RECON["Full Recompute Oracle\n(Uncached Scratch Verification)"]
        PUB["SnapshotPublisherAdapter\n(InMemory / JSON File)"]
    end

    POS --> Engine
    TICKS --> MD
    CURVE --> MD
    VOL --> MD

    MD --> CACHE
    CACHE -->|miss| PRICER
    PRICER --> CACHE
    CACHE --> AGG
    AGG --> SNAP
    SNAP --> SCEN

    SNAP --> API
    SNAP --> DASH
    SNAP --> PUB
    Engine --> RECON
    RECON --> API
    RECON --> DASH
```

---

## 2. Data Flow & State Transitions

1. **Ingress Validation:** All positions, spot ticks, yield curves, and volatility surfaces pass strict Pydantic v2 validation (finite numbers, positive strike/spot/multiplier, strictly increasing curve tenors and vol grid axes, timezone-aware UTC timestamps).
2. **Tick Deduplication & Sequencing:** `MarketDataStore.ingest_spot_tick` rejects any previously seen `tick_id` (`DUPLICATE_TICK`) or non-increasing `sequence_no` per underlying (`OUT_OF_ORDER_TICK`), emitting an operational `StaleDataAlert` without mutating market state.
3. **Targeted Incremental Recomputation:**
   - A spot tick or vol surface update for underlying `U` recomputes **only** positions indexed in `_positions_by_underlying[U]`.
   - A yield curve update for currency `C` recomputes **only** positions indexed in `_positions_by_currency[C]`.
   - A position upsert or removal updates **only** that single `position_id`.
4. **Staleness Evaluation & Freeze-on-Stale:** Before pricing a position, `check_staleness_for_position` compares `(valuation_time_utc - as_of_utc)` against spot, curve, and vol TTLs.
   - If stale and `strict_raise_on_stale=True`: raises `StaleMarketDataError`.
   - If stale and `freeze_on_stale=True`: retains the last reliable `PositionGreeksResult` (when quantity/multiplier unchanged), sets `is_frozen=True`, appends `STALE_MARKET_DATA` + `FROZEN_SNAPSHOT`, and records a `CRITICAL` `StaleDataAlert`.
   - When fresh market data arrives, staleness alerts for that resource clear and live valuation resumes automatically.
5. **Version-Aware Caching:** Non-stale valuations check `VersionAwareGreeksCache` keyed by `(position_id, position_version, instrument_fingerprint, quantity, market_version_key, valuation_time_iso, pricing_method)`. Any version bump automatically misses without risking stale reads.
6. **O(1) Incremental Aggregation:** `IncrementalGreeksAggregator.upsert` subtracts the position's prior `PositionGreeks` from its `underlying` bucket, `underlying|expiry_date` bucket, and portfolio totals, then adds the updated `PositionGreeks` (cleaning up empty buckets when `position_count == 0`).
7. **Atomic Snapshot Publication:** After mutation completes under `self._lock`, `snapshot_seq` increments and `self._latest_snapshot` is atomically swapped to a newly constructed frozen `PortfolioSnapshot` with a deterministic SHA-256 `snapshot_id`.

---

## 3. Core Invariants

1. **Snapshot Atomicity:** Readers calling `get_snapshot()` receive a frozen `PortfolioSnapshot` where `sum(positions.pv) == sum(by_underlying.pv) == sum(by_underlying_expiry.pv) == totals.pv` at all times, even under concurrent writes.
2. **Incremental Equivalence:** For any sequence of position upserts, sign flips, multiplier changes, removals, and market ticks, `reconcile()` verifies incremental buckets match a clean uncached full recompute within `abs_tolerance = 1e-8` and `rel_tolerance = 1e-9`.
3. **No Silent Unsupported Behavior:** Unsupported exercise styles (e.g., `AMERICAN` without a custom pricer), missing/invalid configuration files, and optimistic concurrency mismatches (`expected_snapshot_seq`) raise explicit domain exceptions.
4. **Determinism & Reproducibility:** Given identical configuration, `valuation_time_utc`, inputs, and random seed, replaying events produces the exact same `snapshot_id` and logical numeric outputs.

---

## 4. Key Trade-offs

| Decision | Chosen Approach | Trade-off / Rationale |
|---|---|---|
| Core math runtime | Pure Python `math.erf` / `math.exp` | Zero C-extension build/ABI fragility, identical IEEE-754 results across platforms, `~0.45 ms` p50 per 20-option underlying tick. Optional C++20 kernel deferred to future scale-out. |
| Scenario parallelism | `ThreadPoolExecutor` over cloned state | Decouples scenario execution from the live write lock with zero serialization overhead; CPython GIL limits CPU speedup on pure-Python BSM (`47.7 ms` vs `46.2 ms` for 24×200). Use `ProcessPoolExecutor` or native C++20 if scaling to thousands of heavy shocks. |
| Snapshot concurrency | `RLock` + atomic immutable snapshot swap + optional `expected_snapshot_seq` | Simple, provably race-free readers; optimistic concurrency protects multi-writer workflows from lost updates. |
| Floating-point drift in incremental updates | `float64` add/subtract + on-demand `reconcile()` | Fast $O(1)$ updates; observed drift after 1,000 ticks on 200 options is `2.79e-9` abs (`8.52e-14` rel). Periodic reconciliation catches any unexpected divergence. |
