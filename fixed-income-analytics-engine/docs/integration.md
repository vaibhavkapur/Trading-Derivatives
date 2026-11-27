# Integration Guide — Fixed Income Analytics Engine

## 1. Repository context and sibling projects

Within `Trading-Derivatives/`, sibling projects are independent Python packages:

- `interest-rate-swaps-engine/` (`src/irs_engine`)
- `derivatives-pricing-engine/` (`src/derivatives_pricing_engine`)
- `derivatives-margin-engine/` (`src/derivatives_margin_engine`)
- `real-time-greeks-engine/` (`src/real_time_greeks_engine`)
- `volatility-surface-engine/` (`src/volatility_surface_engine`)
- `options-hedging-simulator/` (`src/options_hedging_simulator`)

To avoid tight runtime coupling, `fixed-income-analytics-engine` is completely standalone and does **not** import from sibling directories or any proprietary external codebase.

## 2. Reusable adapter boundaries

When sibling engines are implemented, integrate via stable data contracts (JSON payloads or lightweight adapter wrappers) rather than copying internal math:

1. **Discount Curve Provider (`YieldCurve` -> `interest-rate-swaps-engine` / `derivatives-pricing-engine`):**
   - Expose `YieldCurve.discount_factor(t)`, `YieldCurve.discount_factor_on(date)`, `YieldCurve.zero_rate(t, compounding)`, and `YieldCurve.forward_rate(t1, t2, compounding)`.
   - JSON serialization via `YieldCurve.to_dict()` provides `curve_id`, `valuation_date`, `currency`, `day_count`, `interpolation`, and `nodes[]` for language-agnostic exchange.
2. **Schedule & Day-Count Utilities (`daycount.py` / `schedule.py` -> `interest-rate-swaps-engine`):**
   - `year_fraction(...)`, `Calendar.adjust(...)`, and `build_schedule(...)` can be wrapped behind a fixed-leg swap schedule adapter without modifying bond logic.
3. **Risk & Scenario Vectors (`pricing.py` / `scenarios.py` -> `derivatives-margin-engine` / `real-time-greeks-engine`):**
   - `PricingResult.key_rate_dv01` and `ScenarioResult.to_dict()` emit structured rate sensitivity vectors and P&L shocks keyed by deterministic `curve_id`, `bond_id`, and `scenario_id`.

## 3. Dependency and license inventory

| Dependency | Requirement | Version Pin | License | Purpose |
|---|---|---|---|---|
| Python standard library | Required | `>=3.9` | PSF-2.0 | Core math, dates, dataclasses, JSON, CLI, unittest |
| `numpy` | Optional (`[scientific]`) | `1.26.4` | BSD-3-Clause | Optional external comparison scripts |
| `scipy` | Optional (`[scientific]`) | `1.13.1` | BSD-3-Clause | Optional external comparison scripts |
| `pytest` | Optional (`[dev]`) | `8.3.3` | MIT | Optional test runner (unittest works natively) |

## 4. Compatibility guarantees

- All public JSON inputs/outputs include `schema_version: "1.0.0"`. Breaking schema changes must increment the major version.
- No live market data feeds, broker connections, customer data, or credentials are imported or required.
