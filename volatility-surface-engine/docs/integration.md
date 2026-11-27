# Integration, Adapter Boundaries & Provenance

## 1. Repository & Neighboring Project Interfaces

This project lives in `Trading-Derivatives/volatility-surface-engine/` and is designed to operate independently with zero runtime coupling to sibling projects.

### Verified Sibling Interfaces in `Trading-Derivatives`
Inspected sibling packages in this repository:
- `derivatives-pricing-engine/src/derivatives_pricing_engine/` (`contracts.py`, `service.py`, `analytics/`)
- `real-time-greeks-engine/src/real_time_greeks_engine/` (`models.py`, `engine.py`, `api.py`)
- `options-hedging-simulator/src/options_hedging_simulator/` (`pricing.py`, `simulator.py`)

**Recommended Adapter Boundary (No Direct Code Copying):**
Downstream pricers (such as `derivatives-pricing-engine`, `options-hedging-simulator`, or `real-time-greeks-engine`) should consume calibrated implied volatilities via one of two stable boundaries rather than importing internal solver modules:

1. **In-process Python adapter**: Load a persisted `SurfaceSnapshot` via `load_surface_snapshot(path)` and query `surface.query(expiry_years=T, strike=K, require_valid_surface=True).implied_vol` to supply volatility inputs to Black-Scholes, binomial tree, Monte Carlo, or PDE pricers.
2. **File/CLI contract adapter**: Consume the versioned JSON `SurfaceSnapshot` (`schema_version = "1.0.0"`) produced by `vse calibrate --output <snapshot.json>` or invoke `vse query --snapshot <snapshot.json> --require-valid`.

```python
# Example independent runnable adapter (no cross-package import coupling required)
from volatility_surface_engine import load_surface_snapshot

def make_vol_provider(snapshot_path: str):
    surface = load_surface_snapshot(snapshot_path)
    def get_vol(strike: float, expiry_years: float) -> float:
        res = surface.query(
            expiry_years=expiry_years,
            strike=strike,
            model="svi",
            allow_extrapolation=False,
            require_valid_surface=True,
        )
        return res.implied_vol
    return get_vol
```

## 2. Dependency Licenses & Compatibility

All dependencies are open-source, pinned in [requirements-lock.txt](../requirements-lock.txt), and verified on Python `3.9.6`:

| Package | Pinned Version | License | Purpose |
|---|---|---|---|
| `numpy` | `2.0.2` | BSD-3-Clause | Vectorized array math & deterministic `Generator` RNG |
| `scipy` | `1.13.1` | BSD-3-Clause | `ndtr`, `brentq` root-finding, `least_squares` TRF calibration |
| `polars` | `1.36.1` | MIT | Optional tabular export of cleaned quotes and slice summaries |
| `plotly` | `7.1.0` | MIT | Optional interactive 3D surface HTML export |
| `pytest` | `8.4.2` | MIT | Automated unit, integration, and boundary test runner |

## 3. Fixture Provenance & Data Compliance

- **100% synthetic data**: Every fixture in `data/fixtures/` is deterministically generated from analytic raw SVI surfaces via [scripts/generate_fixtures.py](../scripts/generate_fixtures.py) with documented seeds (`42`, `77`, `99`, `123`, `456`).
- **No confidential or production artifacts**: No live transactions, customer records, broker credentials, or proprietary employer/SAAT code exist anywhere in source, configs, fixtures, logs, or reports.
- **Production boundaries**: Live market-data adapters, OMS/EMS connectivity, and real-time streaming ingestion are explicitly out of scope and disabled/absent.
