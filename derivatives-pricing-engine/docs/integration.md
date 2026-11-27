# Integration Boundaries and Repository Compatibility

## 1. Sibling Projects Surveyed in `Trading-Derivatives`

Sibling projects under `Trading-Derivatives/`:

| Sibling Path | Package | Potential Integration Relationship |
|---|---|---|
| `volatility-surface-engine/` | `src/volatility_surface_engine` | Upstream producer of implied volatility $\sigma(K, T)$ to replace this engine's flat synthetic `MarketSnapshot.volatility` |
| `real-time-greeks-engine/` | `src/real_time_greeks_engine` | Downstream consumer of `PricingResult.greeks` and `PricingEngine.price()` for portfolio sensitivity aggregation |
| `options-hedging-simulator/` | `src/options_hedging_simulator` | Downstream consumer of analytic/PDE prices and Delta along simulated paths |
| `derivatives-margin-engine/` | `src/derivatives_margin_engine` | Downstream consumer of stressed option valuations under margin scenario shocks |
| `fixed-income-analytics-engine/` | `src/fixed_income` | Potential shared upstream provider of discount factors and yield-curve zero rates |
| `interest-rate-swaps-engine/` | `src/irs_engine` | Independent rate-product engine; shares day-count conceptual conventions only |

To preserve clean isolation between sibling projects, `derivatives-pricing-engine` has **zero runtime imports** from sibling directories and runs completely standalone.

## 2. Reusable Adapter Boundaries

Future sibling projects should integrate via **independent runnable adapters** against the versioned public contract rather than copying solver code:

1. **Python Library Interface:**
   - Import `OptionContract`, `MarketSnapshot`, `PricingRequest`, `PricingResult`, `PricingEngine` from `derivatives_pricing_engine`.
   - Maintain contract stability through `SCHEMA_VERSION = "1.0.0"`.
2. **File / Subprocess JSON Envelope Adapter:**
   - Invoke `python -m derivatives_pricing_engine price --input request.json --output envelope.json` for language-agnostic integration (e.g., from a C++20 or Rust downstream engine).
3. **Volatility Surface Adapter (Future `volatility-surface-engine`):**
   - A thin adapter should query the calibrated surface at `(contract.strike, time_to_maturity)` to populate `MarketSnapshot.volatility` along with surface version metadata in `snapshot_id`.
4. **Curve / Discount Factor Adapter (Future `fixed-income-analytics-engine`):**
   - A thin adapter can translate zero-coupon curve discount factors $P(0, T)$ into equivalent continuously compounded zero rates $r = -\ln P(0, T) / T$ for v1.0.0 compatibility.

## 3. External Dependencies and Licenses

| Dependency | Version Range | Pinned Lock Version | License | Purpose |
|---|---|---|---|---|
| `numpy` | `>=1.24,<2.3` | `2.0.2` | BSD-3-Clause | Vectorized tree, Monte Carlo PRNG (`PCG64`), and PDE arrays |
| `scipy` | `>=1.10,<1.16` | `1.13.1` | BSD-3-Clause | High-precision `special.ndtr`/`ndtri`, `linalg.solve_banded`, `interpolate.CubicSpline` |
| `pytest` (dev) | `>=7.4,<9.0` | `8.4.2` | MIT | Automated test suite |

Optional libraries listed in `plan.md` (`QuantLib`, `C++20`) are **not required** for this baseline prototype and are explicitly marked out-of-scope / not run in `reports/acceptance.md`. No proprietary, employer, or SAAT code or credentials exist anywhere in this project.
