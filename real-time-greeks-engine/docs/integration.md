# Integration Guide — Real-Time Greeks Engine

## 1. Reusable Adapter Boundaries (`src/real_time_greeks_engine/adapters.py`)

The core engine has zero hardcoded external I/O dependencies. Integrations plug in through typed Python `Protocol` interfaces and callable hooks:

1. **`PortfolioFixtureAdapter`** (`load_into(engine) -> PortfolioSnapshot`):
   - Built-in offline implementation: `JsonFixtureAdapter(path)`.
   - Hydrates valuation time, yield curves, vol surfaces, spot ticks, and option positions through the public engine API.
2. **`SnapshotPublisherAdapter`** (`publish(snapshot) -> None`):
   - Built-in implementations: `InMemorySnapshotPublisher` (testing/audit) and `JsonFileSnapshotPublisher(output_dir)` (versioned offline JSON dumps).
3. **Custom Pricing Adapter (`engine.register_custom_pricer(instrument_id, price_fn)`)**:
   - Signature: `price_fn(spot: float, time_to_expiry_years: float, rate: float, vol: float) -> float`.
   - Automatically engages central finite-difference bump-and-revalue fallback and tags results with `FALLBACK_BUMP_AND_REVALUE`.
4. **HTTP / JSON API (`create_app(engine)`)**:
   - FastAPI service for language-agnostic integration (`GET /v1/snapshot`, `GET /v1/alerts`, `GET /v1/reconcile`, `POST /v1/market/{spot,curve,vol}`, `POST /v1/positions`, `POST /v1/scenarios`, `GET /metrics`, `GET /dashboard`).

---

## 2. Neighboring `Trading-Derivatives` Projects Compatibility

This project is completely standalone and does **not** import internal modules from sibling project directories, avoiding tight coupling while keeping data contracts cleanly composable:

- **`volatility-surface-engine/`**: Can publish implied vol grids conforming to `VolSurfaceSnapshot` (`expiries_years`, `strikes`, `vols`, `vol_version`, `as_of_utc`) directly to `POST /v1/market/vol` or `engine.upsert_vol_surface(...)`.
- **`derivatives-pricing-engine/`**: Any exotic or American pricer from a pricing library can be bridged via an independent adapter wrapping its price function into `PriceCallable` and registering it with `engine.register_custom_pricer(...)`.
- **`options-hedging-simulator/`** & **`derivatives-margin-engine/`**: Can consume `PortfolioSnapshot` (`by_underlying`, `by_underlying_expiry`, `totals`, `has_frozen_positions`) over `GET /v1/snapshot` or via `SnapshotPublisherAdapter`.

---

## 3. Optional / Out-of-Scope Production Integrations

The following items listed as *optional* in `plan.md` remain explicitly disabled / outside this offline prototype scope:

- **Redis pub/sub & cache backend:** Not installed or enabled. To add Redis later, implement `SnapshotPublisherAdapter` and an external tick subscriber without modifying core domain code (`pricing.py`, `aggregator.py`, `market_data.py`).
- **C++20 native pricing kernel:** Not built. Pure Python `math.erf` achieves `0.447 ms` p50 per tick on a 200-option book; if sub-50µs latency on 10,000+ options is required, a pybind11/nanobind module can implement the same `analytic_bsm_greeks` signature behind `pricing.py`.
- **Live market feeds / trading credentials:** Strictly excluded by design.
