# ADR 0001: Raw SVI Primary Surface with SABR Benchmark and Piecewise-Linear Total-Variance Interpolation

- **Status:** Accepted
- **Date:** 2026-11-27
- **Project:** `volatility-surface-engine/`

## Context

The engine must build an arbitrage-aware implied-volatility surface from timestamped European option chains, support bid-ask weighted calibration, provide deterministic reproducibility for fixed seeds, enforce static butterfly and calendar arbitrage checks, and reject silent extrapolation.

## Decision

1. **Forward-based Black-76 convention**: All pricing, Greeks, and implied-volatility inversions operate in forward/discount-factor space (`F`, `DF`, `K`, `T`) with explicit `PricingConventions` (`USD`, `ACT/365F`, `european`). Deep ITM options are converted to equivalent OTM time value via put-call parity prior to root-finding to prevent IEEE-754 cancellation.
2. **Raw SVI primary slices with Hagan (2002) SABR benchmark**:
   - Each expiry slice is fitted with raw SVI $w(k) = a + b(\rho(k-m) + \sqrt{(k-m)^2 + \sigma^2})$ via bid-ask inverse-variance weighted Trust-Region Reflective least squares with deterministic multi-start (`np.random.default_rng(seed + 1009 * slice_idx)`) and a smooth Durrleman density penalty.
   - Hagan (2002) SABR ($\beta$ configurable, default `0.5`) is calibrated alongside SVI on every slice as an independent parametric benchmark.
3. **Piecewise-linear total-variance interpolation**: Across expiries $T \in [T_j, T_{j+1}]$, total variance is interpolated linearly in $T$ at fixed log-moneyness $k$, and the valid query domain is restricted to the intersection $[\max(k_{\min,j}, k_{\min,j+1}), \min(k_{\max,j}, k_{\max,j+1})]$.
4. **Explicit invalidation over silent projection**: Static arbitrage checks evaluate both Durrleman density $g(k) \ge 0$ and discrete call convexity $\partial^2 C / \partial K^2 \ge 0$ (butterfly) plus $w(k, T_{j+1}) \ge w(k, T_j)$ (calendar). Violations mark `is_valid=False` (or raise `ArbitrageViolationError` in strict mode) rather than silently altering calibrated parameters.

## Consequences

- **Positive**: Closed-form spatial derivatives $w'(k), w''(k)$ make butterfly verification exact and fast (~257 ms for a full 4-expiry surface including SABR benchmark and dense arbitrage grid checks). Determinism is exact (`surface_id` matches across replays).
- **Negative / Trade-off**: Independent per-slice fitting does not automatically repair market-data calendar inversions; an inverted chain will correctly produce an invalid surface rather than an auto-repaired surface. Joint SSVI or constrained multi-slice optimization can be added as a future optional solver while keeping the current `SurfaceSnapshot` contract unchanged.
