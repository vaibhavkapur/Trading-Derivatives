# ADR-0001: Pricing Conventions, Layer Boundaries, and Numerical Solver Architecture

- **Status:** Accepted
- **Date:** 2026-11-27
- **Project:** `derivatives-pricing-engine/`

## Context

The derivatives pricing engine must price European and American vanilla options using four independent numerical/analytic methodologies (Black-Scholes-Merton, Cox-Ross-Rubinstein binomial tree, seeded antithetic Monte Carlo, and finite-difference PDE) while keeping domain math auditable, deterministic, and strictly separated from I/O, configuration, and serialization.

## Decisions

1. **Strict Layer Separation (`analytics/` vs `service.py` / `cli.py` / `io.py`):**
   - Pure pricing algorithms in `src/derivatives_pricing_engine/analytics/` accept primitive validated numbers and enums (`spot`, `strike`, `time_to_maturity`, `risk_free_rate`, `dividend_yield`, `volatility`, `OptionType`, `ExerciseStyle`) and return immutable dataclasses. They perform zero filesystem I/O and read zero external configuration.
   - `PricingEngine` in `service.py` owns configuration merging, timestamp-to-year-fraction conversion, structured logging, diagnostic counters, deterministic `result_id` hashing, and replay verification.

2. **Explicit Pricing Conventions:**
   - **Timestamps:** All timestamps must be timezone-aware ISO-8601 and are normalized to UTC. Naive timestamps are rejected with `VALIDATION_ERROR`.
   - **Day Count:** Explicit enum (`ACT/365F`, `ACT/360`, `ACT/365.25`, `30/360`), defaulting to `ACT/365F`.
   - **Compounding:** Continuous compounding (`exp(-r * T)`) for both risk-free rate `r` and dividend yield `q`. Unsupported compounding styles raise `UNSUPPORTED_FEATURE`.
   - **Units:** `unit_price`, `intrinsic_value`, and `time_value` are expressed per 1 unit of underlying in contract `currency`; `total_price = unit_price * contract_multiplier`. Greeks use standard mathematical partial derivatives (`vega` per 1.0 absolute vol, `theta` per calendar year, `rho` per 1.0 absolute rate).

3. **Explicit Rejection of Unsupported Features and Instabilities:**
   - American exercise requested on `black_scholes` or `monte_carlo` raises `UNSUPPORTED_FEATURE` (no silent fallback to European pricing).
   - Unsupported exercise styles (`bermudan`, `asian`, `barrier`) raise `UNSUPPORTED_FEATURE`.
   - Explicit finite-difference PDE checks the von Neumann / CFL condition $\Delta\tau (\sigma^2 (M-1)^2 + \max(r, 0)) \le 1$ and raises `NUMERICAL_STABILITY_ERROR` with the minimum required `time_steps` when violated.
   - CRR binomial tree checks $0 < p < 1$ and raises `NUMERICAL_STABILITY_ERROR` if drift dominates volatility at coarse step counts.

4. **PDE Discretization Choice:**
   - Default PDE scheme is Crank-Nicolson ($\theta = 0.5$) with 4 Rannacher implicit half-steps near expiry to damp payoff-kink oscillations, `scipy.linalg.solve_banded` for European tridiagonal systems, and vectorized Red-Black Projected SOR (PSOR) for the American linear complementarity problem.

5. **Reproducible Stochastic Simulation:**
   - Monte Carlo uses `numpy.random.Generator(PCG64(seed))` with antithetic variates enabled by default. Standard errors are computed from paired averages $\frac{1}{2}(V(Z_i) + V(-Z_i))$ so confidence interval coverage remains calibrated under negative antithetic correlation.

## Consequences

- Every engine run is auditable and byte-for-byte reproducible for fixed inputs, configuration, and seeds.
- Any extension to stochastic volatility, local volatility surfaces, or Least-Squares Monte Carlo (Longstaff-Schwartz) can plug into `analytics/` without breaking the `PricingRequest` / `PricingResult` v1.0.0 contract.
