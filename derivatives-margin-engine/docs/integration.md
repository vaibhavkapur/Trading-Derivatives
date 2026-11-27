# Integration Guide — Derivatives Margin Engine

## 1. Neighboring Repository & Workspace Path Verification

Verified repository directory layout under `Trading-Derivatives/`:

| Sibling Project Path | Current State | Integration Boundary |
|---|---|---|
| `derivatives-pricing-engine/` | Independent Python package (`src/derivatives_pricing_engine`) | Upstream settlement price provider via `PricingFeedAdapter` |
| `real-time-greeks-engine/` | Independent Python package (`src/real_time_greeks_engine`) | Upstream delta/shock scenario feed via `ScenarioFeedAdapter` |
| `options-hedging-simulator/` | Independent Python package (`src/options_hedging_simulator`) | Upstream hedged position snapshots mapped to `Position` records |
| `volatility-surface-engine/` | Independent Python package (`src/volatility_surface_engine`) | Indirect upstream input to pricing/Greeks scenario generators |
| `fixed-income-analytics-engine/` | Independent Python package (`src/fixed_income`) | Upstream clean/dirty bond valuations mapped to `CollateralLot.market_value` |
| `interest-rate-swaps-engine/` | Independent Python package (`src/irs_engine`) | Upstream swap NPV / DV01 feeds mapped to `SettlementPrice` / `Scenario` |

To preserve clean isolation between sibling projects, **no cross-project runtime imports** exist. Instead, `derivatives-margin-engine` provides independent, runnable adapter interfaces in `src/derivatives_margin_engine/adapters.py`.

## 2. Adapter Boundaries (`adapters.py`)

- `PricingFeedAdapter`: Protocol defining `get_settlement_prices(valuation_date, instrument_ids, revision=1) -> Sequence[SettlementPrice]`.
  - Runnable reference implementation: `StaticPricingAdapter`.
- `ScenarioFeedAdapter`: Protocol defining `build_scenario_set(scenario_set_id) -> ScenarioSet`.
  - Runnable reference implementation: `LinearizedGreeksScenarioAdapter`.

Future sibling implementations should adapt their outputs into canonical `SettlementPrice`, `ScenarioSet`, `Position`, and `CollateralLot` dataclasses (all `Decimal`, explicit ISO currencies/dates) rather than importing internal margin-engine state.

## 3. Dependencies, Licenses, and Provenance

- **Runtime dependencies:** None (Python 3.9+ standard library only — PSF License).
- **Build backend:** `setuptools>=58.0` (MIT License) for optional editable/wheel installation.
- **Optional dev dependency:** `pytest>=7.0` (MIT License); all tests also run directly under stdlib `unittest`.
- **Fixture provenance:** All JSON fixtures in `data/fixtures/` and `config/` are 100% synthetic educational datasets authored specifically for this repository. Zero real transactions, customer records, live credentials, or proprietary employer/SAAT artifacts exist anywhere in source, configs, fixtures, logs, or reports.

## 4. Explicitly Out-of-Scope Production Integrations

- Live exchange/CCP settlement feeds (FIX, CME FIXML, FpML, SWIFT) — **not implemented / disabled**.
- External custody/tri-party collateral settlement gateways — **not implemented / disabled**.
- PostgreSQL multi-node connection pooling — **not tested** (SQLite stdlib adapter implemented and verified).
