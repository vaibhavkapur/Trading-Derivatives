# Data Contracts & Schemas (`schema_version = "1.0.0"`)

## 1. Units, Conventions, and Timestamp Semantics

Every input snapshot and output surface carries an explicit `PricingConventions` block:

| Field | Default | Allowed / Semantics |
|---|---|---|
| `currency` | `"USD"` | 3-letter ISO-4217 currency code |
| `day_count` | `"ACT/365F"` | `"ACT/365F"`, `"ACT/365.25"`, `"ACT/360"` |
| `exercise_style` | `"european"` | `"european"` only (American/exotic styles raise `ValidationError`) |
| `price_unit` | `"currency_per_unit"` | Option bid/ask/mid and strikes in currency per 1 unit of underlying |
| `vol_unit` | `"annualized_decimal"` | Implied volatility as decimal (e.g., `0.25` = 25% annualized) |
| `time_unit` | `"year_fraction"` | Time to expiry $T$ in years according to `day_count` |

**Timestamp semantics:** All timestamps (`valuation_timestamp`, `expiry_timestamp`) MUST be ISO-8601 strings with explicit timezone offset or `Z` (UTC). Naive timestamps are rejected with `ValidationError`. If `time_to_expiry_years` is provided on an expiry, it must agree with `(expiry_timestamp - valuation_timestamp) / day_count` within 1 calendar day.

## 2. Input Contract: `OptionChainSnapshot`

```json
{
  "schema_version": "1.0.0",
  "snapshot_id": "ref-synth-eqx-20260115",
  "underlying": "SYNTH-EQX",
  "valuation_timestamp": "2026-01-15T16:00:00Z",
  "spot": 100.0,
  "conventions": {
    "currency": "USD",
    "day_count": "ACT/365F",
    "exercise_style": "european",
    "price_unit": "currency_per_unit",
    "vol_unit": "annualized_decimal",
    "time_unit": "year_fraction"
  },
  "expiries": [
    {
      "expiry_timestamp": "2026-02-14T16:00:00Z",
      "time_to_expiry_years": 0.0821917808,
      "forward": 100.24687959,
      "discount_factor": 0.9963082014
    }
  ],
  "quotes": [
    {
      "quote_id": "ref-synth-eqx-20260115-q0001",
      "expiry_timestamp": "2026-02-14T16:00:00Z",
      "strike": 78.0723,
      "option_type": "put",
      "bid": 0.020575,
      "ask": 0.020775,
      "volume": 342,
      "open_interest": 2218
    }
  ]
}
```

### Schema Validation vs. Quote Cleaning
- **Fail-fast schema errors (`ValidationError`)**: Missing required fields, unsupported `schema_version`, invalid/naive timestamps, unsupported `exercise_style` or `option_type`, duplicate `quote_id` or `expiry_timestamp`, quote referencing unknown expiry, non-positive `spot` / `forward`, `discount_factor` outside `(0, 1.5]`.
- **Market-hygiene rejections (recorded in `diagnostics.rejection_counts`)**: `non_finite_quote`, `invalid_strike`, `expired_or_short_expiry`, `crossed_quote`, `non_positive_bid`, `non_positive_mid`, `wide_absolute_spread`, `wide_relative_spread`, `extreme_moneyness`, `intrinsic_bound_violation`, `otm_preference_filtered`, `iv_inversion_failed`, `near_zero_vega`.

## 3. Configuration Contract: `CalibrationConfig`

Stored in [config/default_config.json](../config/default_config.json). Unknown keys or invalid parameter ranges raise `ConfigurationError`. Every configuration produces a deterministic 16-character SHA-256 `config_hash` stored on every `SurfaceSnapshot`.

## 4. Output Contracts

### 4.1 `SurfaceSnapshot`
Top-level fields:
- `surface_id`: Deterministic content identifier (`vse-<12-hex>`) derived from `input_snapshot_id`, `config_hash`, calibrated SVI slices, and `is_valid`.
- `schema_version`: `"1.0.0"`
- `input_snapshot_id`, `underlying`, `valuation_timestamp`, `conventions`, `config_hash`, `interpolation_method`
- `is_valid`: Boolean (`true` iff `butterfly_violation_count == 0` and `calendar_violation_count == 0`).
- `slices[]`: Per-expiry `SliceCalibrationResult` containing `svi_params`, `sabr_params`, domain `[k_min, k_max]`, bid-ask weighted and unweighted IV RMSE, max abs error, convergence flags, and cleaned `fitted_quotes`.
- `arbitrage_check`: `ArbitrageCheckResult` with violation counts, minimum Durrleman density, minimum normalized call convexity, minimum calendar total-variance difference, grid sizes, and itemized `violations[]`.
- `diagnostics`: Quote acceptance/rejection counts, `forward_estimates[]`, overall SVI/SABR weighted RMSE, `seed`, `runtime_metadata`, and `warnings[]`.

### 4.2 `SurfaceQueryResult`
Fields: `underlying`, `expiry_years`, `strike`, `forward`, `log_moneyness`, `implied_vol`, `total_variance`, `model`, `extrapolated`, `surface_id`, `surface_valid`.

## 5. Error Taxonomy

| Exception | Error Code | Trigger | CLI Exit Code |
|---|---|---|---|
| `ConfigurationError` | `CONFIGURATION_ERROR` | Missing/malformed config, unknown key, out-of-range setting | `2` |
| `ValidationError` | `VALIDATION_ERROR` | Schema violation, bad timestamp, unsupported option/exercise type | `2` |
| `InputBoundsError` | `INPUT_BOUNDS_ERROR` | Option price outside no-arbitrage or solver bounds, near-zero vega | `2` |
| `CalibrationError` | `CALIBRATION_ERROR` | Insufficient clean quotes for slice/surface calibration | `2` |
| `ArbitrageViolationError` | `ARBITRAGE_VIOLATION` | Static arbitrage violation in strict mode or `require_valid_surface=True` | `2` |
| `ExtrapolationError` | `EXTRAPOLATION_REJECTED` | Query outside calibrated strike or maturity bounds without opt-in | `2` |

## 6. Deterministic Fixture Catalog (`data/fixtures/`)

All fixtures are synthetic and deterministically generated via [scripts/generate_fixtures.py](../scripts/generate_fixtures.py):

| Fixture | Type | Purpose |
|---|---|---|
| `valid_chain_reference.json` | Valid baseline | 4-expiry (30d/90d/180d/365d), 11-strike arbitrage-free raw SVI chain with matched call/put parity pairs |
| `expected_reference.json` | Independent oracle | Exact analytic SVI & 60d interpolated total-variance/IV expectations and tolerances |
| `edge_cases_chain.json` | Edge cases | Sparse/missing strikes plus 7 injected anomalies (crossed, wide rel/abs spread, zero bid, intrinsic violation, near-zero vega, ultra-short expiry) |
| `heldout_smile_chain.json` | Integration / evaluation | 17-strike synthetic chain split into 13 noisy training strikes (8 bps IV noise) and 4 clean held-out interior strikes per expiry |
| `arbitrage_violation_chain.json` | Failure / boundary | Inverted term structure (30d total variance > 90d total variance) triggering calendar arbitrage detection |
