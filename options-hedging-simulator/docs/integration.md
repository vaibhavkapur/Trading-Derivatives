# Integration & Adapter Boundaries

## 1. Repository Context (`Trading-Derivatives`)

Sibling project directories under `Trading-Derivatives/`:
- `derivatives-pricing-engine/` (`src/derivatives_pricing_engine`)
- `derivatives-margin-engine/` (`src/derivatives_margin_engine`)
- `fixed-income-analytics-engine/` (`src/fixed_income`)
- `interest-rate-swaps-engine/` (`src/irs_engine`)
- `volatility-surface-engine/` (`src/volatility_surface_engine`)
- `real-time-greeks-engine/` (`src/real_time_greeks_engine`)

This project (`options-hedging-simulator/`) is completely self-contained and runnable without cross-project imports, avoiding fragile coupling while defining clean adapter protocols for optional integration.

## 2. Reusable Adapter Boundaries

### 2.1 External Pricing Adapter (`PricingModel` Protocol)

`options_hedging_simulator.pricing.PricingModel` defines the single method required by `simulate_path` and `simulate_batch`:

```python
class PricingModel(Protocol):
    def price_and_greeks(
        self,
        contract: OptionContract,
        *,
        spot: float,
        time_to_expiry: float,
        rate: float,
        dividend_yield: float,
        volatility: float,
    ) -> OptionQuote: ...
```

To integrate with `derivatives-pricing-engine/` (e.g., for local-volatility, binomial, or Monte Carlo pricing) or `volatility-surface-engine/`:
1. Implement a thin adapter class in your integration layer wrapping the external pricer and mapping its return structure to `OptionQuote`.
2. Pass `pricer=MyExternalPricerAdapter()` into `simulate_path(...)`, `simulate_batch(...)`, or `compare_policies(...)`.
3. Cross-check adapter Greeks against `finite_difference_greeks(...)` before running benchmarks.

### 2.2 Margin & Collateral Downstream Feed

Each `PathSimulationResult` exposes:
- `ledger_entries`: Step-by-step cash balance, underlying inventory (`shares_after`), and mark-to-market (`portfolio_mtm`).
- `fills`: Structured trade records (`FillRecord.to_dict()`).

These outputs can be exported as JSON/CSV and fed via an independent adapter into `derivatives-margin-engine/` to evaluate initial/variation margin calls alongside discrete hedge cash flows.

## 3. Dependency & License Provenance

All runtime and development dependencies are standard open-source scientific Python packages pinned in `requirements-lock.txt`:

| Package | Version (Locked) | License | Role |
|---|---|---|---|
| `numpy` | `2.0.2` | BSD-3-Clause | PCG64 RNG and vectorized path generation / quantile analytics |
| `pandas` | `2.3.3` | BSD-3-Clause | Historical bar CSV ingestion and tabular report exports |
| `matplotlib` | `3.9.4` | PSF-based (Matplotlib) | Headless (`Agg`) diagnostic, frontier, and convergence plots |
| `pytest` (dev) | `8.4.2` | MIT | Unit, integration, boundary, and CLI test execution |

No proprietary, employer, SAAT, or live-brokerage libraries or credentials are used or referenced.

## 4. Explicitly Out-of-Scope Production Integrations

- Live market data feeds, broker order-routing APIs, real execution venues, and production risk/margin systems are intentionally excluded.
- Any future production deployment requires separate security, operational, legal, model-risk, and execution-venue reviews.
