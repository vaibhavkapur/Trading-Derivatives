# Derivatives Margin Engine

Educational and research prototype simulating **Initial Margin (IM)**, **Variation Margin (VM)**, **collateral eligibility and haircuts**, **margin call generation**, and **SQLite double-entry daily settlement journal replay** under transparent, versioned policies.

> **Disclaimer / Scope:** Educational margin methodology only — **not** a replication of a CCP (e.g., CME SPAN, LCH PAIRS, Eurex Prisma) or regulatory capital framework (e.g., ISDA SIMM, Basel SA-CCR). Offline synthetic fixtures only; no live capital, customer data, credentials, or production integrations.

## Quickstart

### Prerequisites
- Python 3.9+ (uses only Python standard library: `decimal`, `sqlite3`, `unittest`, `json`, `hashlib`, `argparse`)
- Zero third-party runtime dependencies (MIT-licensed original code)

### Local Setup & Verification

```bash
cd derivatives-margin-engine

# Optional editable install (or run directly with PYTHONPATH=src)
python3 -m pip install -e . --no-build-isolation

# 1. Run full unit, integration, boundary, failure, and operational test suite
PYTHONPATH=src python3 -m unittest discover -s tests -v

# 2. Verify hand-calculated reference oracles (exact Decimal tolerance 0.00)
PYTHONPATH=src python3 -m derivatives_margin_engine.cli verify-oracle \
  --policy config/default_policy.json \
  --scenarios config/scenarios_reference.json \
  --fixture data/fixtures/reference_hand_calc.json \
  --tolerance 0.00

# 3. Run reproducible end-to-end demo (oracles + 4-day replay + SQLite reconciliation)
PYTHONPATH=src python3 scripts/run_demo.py

# 4. Run full test + synthetic benchmark acceptance collector
PYTHONPATH=src python3 scripts/run_benchmarks_and_acceptance.py
```

## Example Output (`scripts/run_demo.py`)

```text
=== Derivatives Margin Engine Demo Summary ===
[ORACLE PASS] REFERENCE-HAND-CALC-V1: 13 checks matched (tolerance=0.00)
[ORACLE PASS] OFFSET-DIVERSIFICATION-ORACLE-V1: 13 checks matched (tolerance=0.00)

--- Multi-Day Replay & Reconciliation ---
  Day 1 - Baseline Settlement (2026-01-15 rev 1): calls_posted=2, journal_entries=12
    NS-ALPHA: VM=-5000.00, NetIM=271050.00, Col=288000.00, Shortfall=0.00, TotalDue=5000.00
    NS-SHORT-COMM: VM=-15000.00, NetIM=136800.00, Col=150000.00, Shortfall=0.00, TotalDue=15000.00
  Day 2 - Haircut Increase on Corporate Bonds (2026-01-16 rev 1): calls_posted=1, journal_entries=10
    NS-ALPHA: VM=0.00, NetIM=271050.00, Col=273000.00, Shortfall=0.00, TotalDue=0.00
    NS-SHORT-COMM: VM=22500.00, NetIM=134100.00, Col=150000.00, Shortfall=0.00, TotalDue=0.00
  Day 3 - Collateral Expiry (T-Bill matures on 2026-01-19) (2026-01-19 rev 1): calls_posted=3, journal_entries=12
    NS-ALPHA: VM=-18000.00, NetIM=269520.00, Col=175000.00, Shortfall=94520.00, TotalDue=112520.00
    NS-SHORT-COMM: VM=-15000.00, NetIM=135900.00, Col=150000.00, Shortfall=0.00, TotalDue=15000.00
  Day 3 Revision 2 - Settlement Price Correction for ES_MAR26 (2026-01-19 rev 2): calls_posted=1, journal_entries=8
    NS-ALPHA: VM=5000.00, NetIM=270020.00, Col=175000.00, Shortfall=95020.00, TotalDue=0.00
    NS-SHORT-COMM: VM=0.00, NetIM=135900.00, Col=150000.00, Shortfall=0.00, TotalDue=0.00

[RECONCILED=True] runs=4, calls=7, journal_entries=56, currency_totals={'USD': {'total_debit': '1861040.00', 'total_credit': '1861040.00', 'imbalance': '0.00'}}
```

## Measured Results vs. Synthetic Fixtures

All measurements below were observed locally on Python 3.9.6 / SQLite 3.51.0 (macOS) against synthetic fixtures (not production market data):

- **Automated Test Suite (`reports/test_results.json`):** 20/20 tests passed in `47 ms` (0 failures, 0 errors, 0 skipped).
- **Reference Oracle Verification (`reports/reference_baseline.json`):** 26/26 assertions across two independent hand-calculated fixtures matched with `0.00` absolute `Decimal` error.
- **Multi-Day Replay & Reconciliation (`reports/demo_replay_report.json`):** 4 settlement runs (including haircut increase, collateral expiry, and rev 1 → rev 2 settlement price correction), 7 margin calls, 56 balanced double-entry journal postings (`1,861,040.00` USD DR = `1,861,040.00` USD CR, `0.00` imbalance), idempotent rerun blocked 7 duplicate calls cleanly.
- **Synthetic Stress Benchmark (`reports/benchmark_results.json`):** 50 netting sets × 20 positions × 250 seeded Monte Carlo scenarios (`250,000` position-scenario evaluations) completed domain evaluation in `400.81 ms` (~`623,733` evals/sec) and SQLite persistence + full reconciliation (100 calls, 400 journal entries) in `3.02 ms`.

## Project Layout & Documentation

- [plan.md](plan.md) — Original project specification and milestones
- [docs/architecture.md](docs/architecture.md) — Component architecture, data flow, invariants, and trade-offs
- [docs/adr/0001-domain-isolation-and-decimal-precision.md](docs/adr/0001-domain-isolation-and-decimal-precision.md) — ADR 0001: pure domain isolation, `Decimal`, and SQLite journal
- [docs/contracts.md](docs/contracts.md) — Canonical input/output schemas, units, timestamps, and error codes
- [docs/integration.md](docs/integration.md) — Adapter protocols, sibling project paths, and dependency provenance
- [docs/runbook.md](docs/runbook.md) — Demo, validation, structured diagnostics, and operational recovery procedures
- [reports/acceptance.md](reports/acceptance.md) — Acceptance criteria & validation matrix mapped to test and benchmark evidence
- [../.github/workflows/derivatives-margin-engine.yml](../.github/workflows/derivatives-margin-engine.yml) — CI validation workflow

## Assumptions & Known Limitations

1. **Linear MTM/Scenario Payoff Model:** Positions are valued linearly as `quantity × contract_multiplier × settlement_price`. Non-linear option convexity (gamma/vega full revaluation) is out of scope unless supplied via an upstream pricing adapter.
2. **Educational IM Offset Methodology:** Scenario-based IM applies transparent intra-group (`alpha_g`) and cross-group (`beta`) diversification offsets over configurable `WORST_CASE`, `HISTORICAL_VAR`, or `EXPECTED_SHORTFALL` aggregations. It is not calibrated to historical CCP margin waterfalls, wrong-way risk, or liquidity/concentration add-ons beyond single asset-class caps.
3. **Static FX Spot Table per Run:** Multi-currency positions and collateral convert via explicit `fx_rates_to_base` in the versioned policy; cross-currency stochastic FX shocks are not jointly simulated unless encoded directly in scenarios.
4. **Single-Node SQLite Persistence:** SQLite provides ACID double-entry journal storage and `UNIQUE` idempotency constraints for educational/local replay; PostgreSQL migration is noted as an untested capability for multi-writer deployments.
5. **No Live Connectivity:** Market data feeds, custody settlement gateways, and messaging buses are explicitly excluded.
