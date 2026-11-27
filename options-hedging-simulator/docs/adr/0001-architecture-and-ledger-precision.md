# ADR-0001: Pricing Isolation, Exact Minor-Unit Ledger Accounting, and Reproducible Simulation

- **Status:** Accepted
- **Date:** 2026-11-27
- **Project:** `options-hedging-simulator/`

## Context

Discrete options hedging simulators often suffer from three recurring engineering defects:
1. Mixing floating-point cash arithmetic with option analytics leads to silent penny/cent drift over long rebalancing horizons, making ledger reconciliation ambiguous.
2. Coupling simulation state transitions directly to a single pricing implementation or file I/O prevents plugging in alternative pricing oracles and complicates deterministic unit testing.
3. Unpinned random streams or non-canonical serialization prevent exact fill-level replay across runs.

## Decisions

1. **Exact Currency-Precision `Decimal` Ledger (`ledger.py`)**:
   - Every monetary flow component (`financing_interest`, `dividend_cash`, `trade_mid_cash`, `spread_cost`, `fee_cost`, `option_cash_flow`) is quantized to the contract currency's minor unit (`Decimal("0.01")` for USD/EUR/GBP, `Decimal("1")` for JPY) using `ROUND_HALF_EVEN`.
   - Every step enforces the exact accounting identity `cash_after == cash_before + financing + dividend + trade_mid_cash - spread_cost - fee_cost + option_cash_flow` with zero tolerance (`Decimal("0.00")`), and full cumulative reconciliation is audited at expiry alongside flat terminal inventory (`shares_after == 0.0`).

2. **Pricing API Separation & Independent Numerical Oracle (`pricing.py`)**:
   - The simulator depends only on the `PricingModel` protocol (`price_and_greeks -> OptionQuote`).
   - `BlackScholesPricer` implements closed-form BSM with explicit `ACT/365F` / continuous-compounding conventions and expiry limits, cross-checked against an independent bump-and-revalue `finite_difference_greeks` oracle and a pure-stdlib deterministic 3-step fixture.

3. **Explicit Cash vs. Physical Settlement Mechanics (`simulator.py`)**:
   - Both `cash` and `physical` settlement modes are modeled explicitly. Under physical assignment/exercise of an ITM option, the contracted share block is delivered/received at strike `K` and only the residual share mismatch is unwound at spot `S_T`, accurately reflecting lower settlement spread drag compared to full cash unwind.

4. **Deterministic Seeded Replay (`paths.py`, `io.py`)**:
   - Stochastic path generation explicitly instantiates `np.random.default_rng(np.random.PCG64(seed))`.
   - Every batch run computes a SHA-256 `fills_digest` over all canonicalized `FillRecord` entries and persists `config.json` + `summary.json`, enabling exact fill-by-fill verification via `verify_replay` / `cli replay`.

## Consequences

- **Positive:** Zero reconciliation drift at currency precision; deterministic fill replay; clean swap-in boundary for `derivatives-pricing-engine` adapters; transparent separation of spread, fee, financing, and gamma-tracking P&L.
- **Trade-offs:** Sub-cent per-step rounding introduces negligible ($O(\text{cent} \cdot \sqrt{N})$) rounding noise on very small contracts (e.g., 1 share), though negligible on standard 100-multiplier option blocks. Only European exercise is currently supported; American/Bermudan styles fail fast with `UnsupportedContractError`.
