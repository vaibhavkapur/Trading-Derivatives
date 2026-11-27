# Data Contracts — Fixed Income Analytics Engine

**Schema Version:** `1.0.0`

## 1. Units, timestamps, and conventions

| Concept | Representation / Unit | Notes |
|---|---|---|
| Dates | ISO-8601 `YYYY-MM-DD` | Parsed strictly as `datetime.date`. |
| Valuation timestamp | `valuation_date` + `valuation_time_utc` (`HH:MM:SSZ`) | Explicitly recorded in config and output reports. |
| Currency | Enum: `USD`, `EUR`, `GBP`, `JPY` | Cross-currency curve/bond mismatches raise `ValidationError`. |
| Notional | Currency units (float, `> 0`) | e.g., `100.0` or `1000000.0`. |
| Coupon rate, deposit rate, YTM, zero rate | Annual decimal (float) | `0.05` = `5.00%` per annum. |
| Market bond price quotes (`CLEAN_PRICE`, `DIRTY_PRICE`) | Percentage of par (float, `> 0`) | `100.0` = par; converted to currency via `quote * notional / 100.0`. |
| Basis points (`*_bp`) | `1 bp = 0.0001` (rate) or `0.01%` of par (price) | Explicitly labeled with `_bp` suffix. |
| DV01 | Currency units per `+1 bp` yield increase | Positive sign denotes price drop when yields rise (`-dP/dy * 1e-4`). |
| Duration | Years | Macaulay and Modified duration. |
| Convexity | $\text{Years}^2$ | `(1 / P) * d²P/dy²`. |
| Day-count conventions | `ACT/360`, `ACT/365F`, `ACT/ACT_ICMA`, `30/360_US`, `30E/360` | Unrecognized values raise `UnsupportedConventionError`. |
| Business-day conventions | `UNADJUSTED`, `FOLLOWING`, `MODIFIED_FOLLOWING`, `PRECEDING` | Applied to payment dates; accrual dates unadjusted unless `adjust_accrual_dates=true`. |
| Compounding | `SIMPLE`, `ANNUAL`, `SEMI_ANNUAL`, `QUARTERLY`, `MONTHLY`, `CONTINUOUS` | Defaults to bond coupon frequency (`SEMI_ANNUAL` for zero-coupon). |
| Curve interpolation | `LOG_LINEAR_DF`, `LINEAR_ZERO` | `LOG_LINEAR_DF` extrapolates flat forward; `LINEAR_ZERO` extrapolates flat zero. |
| Calendars | `NONE`, `WEEKEND_ONLY`, `US_SYNTHETIC`, `TARGET_SYNTHETIC` | Synthetic deterministic offline calendars; not official exchange schedules. |

## 2. Input schemas

### 2.1 `ValuationConfig` (`config/default_valuation.json`)

```json
{
  "config_version": "1.0.0",
  "valuation_date": "2026-03-16",
  "valuation_time_utc": "21:00:00Z",
  "currency": "USD",
  "curve_day_count": "ACT/365F",
  "interpolation": "LOG_LINEAR_DF",
  "dv01_bump_bp": 1.0,
  "solver_tolerance": 1e-12,
  "solver_max_iterations": 200,
  "repricing_tolerance_bp": 1.0,
  "dv01_agreement_tolerance_pct": 0.5,
  "allow_negative_zero_rates": false
}
```

### 2.2 `DepositQuote`

Required keys: `instrument_id`, `currency`, `start_date`, `maturity_date`, `rate`.  
Optional keys: `day_count` (default `ACT/360`), `business_day_convention` (default `MODIFIED_FOLLOWING`), `calendar_name` (default `US_SYNTHETIC`).

### 2.3 `BondSpec` and `BondQuote`

`BondSpec` required keys: `bond_id`, `currency`, `notional`, `coupon_rate`, `frequency` (`0, 1, 2, 4, 12`), `issue_date`, `maturity_date`.  
Optional keys: `day_count`, `business_day_convention`, `calendar_name`, `yield_compounding`, `first_coupon_date`, `penultimate_coupon_date`, `stub_type` (`NONE`, `SHORT_FIRST`, `LONG_FIRST`, `SHORT_LAST`, `LONG_LAST`), `end_of_month`, `adjust_accrual_dates`.

`BondQuote` required keys: `bond` (`BondSpec`), `quote_type` (`CLEAN_PRICE` | `DIRTY_PRICE` | `YTM`), `quote_value`.

### 2.4 `ScenarioSpec`

Required keys: `scenario_id`, `name`, `shift_type` (`PARALLEL`, `STEEPENER`, `FLATTENER`, `BUTTERFLY`, `NODE_SHIFTS`).  
Optional keys: `magnitude_bp`, `pivot_years` (default `5.0`), `node_shifts_bp` (required for `NODE_SHIFTS`).

## 3. Output contracts

Every pipeline run (`cli.py` / `scripts/run_demo.py`) outputs a versioned JSON envelope:
- `schema_version`: `"1.0.0"`
- `logical_fingerprint`: deterministic `logical_<16-hex>` SHA-256 hash over logical outputs (excluding wall-clock timestamps)
- `config`: echoed `ValuationConfig`
- `curve`: `curve_id`, `max_calibration_error_bp`, `nodes[]`, `residuals[]`
- `pricing_results[]`: `pricing_id`, `dirty_price`, `clean_price`, `accrued_interest`, `*_pct_par`, `ytm`, `macaulay_duration`, `modified_duration`, `convexity`, `analytic_dv01`, `numerical_yield_dv01`, `dv01_relative_diff_pct`, `curve_parallel_dv01`, `z_spread_bp`, `key_rate_dv01`, `cash_flow_valuations[]`, `accrued_detail`, `solver_iterations`
- `scenario_results[]`: `result_id`, `scenario`, `base_dirty_price`, `shocked_dirty_price`, `total_pnl`, `total_pnl_pct`, `sum_cf_pnl`, `reconciliation_error`, `taylor_duration_pnl`, `taylor_convexity_pnl`, `taylor_residual`, `contributions[]`
- `diagnostics`: structured JSON event list and counters
- `runtime_metadata`: engine version, Python version, platform, optional package versions, UTC timestamp

## 4. Validation error catalog

All domain exceptions inherit from `FixedIncomeError` and serialize via `.to_dict()` to `{"error_code", "error_type", "message", "details"}`:

| Exception | Error Code | Trigger conditions |
|---|---|---|
| `ValidationError` | `FI_VALIDATION_ERROR` | Missing required fields, non-finite numbers, `notional <= 0`, `maturity_date <= issue_date`, `frequency == 0` with `coupon_rate != 0`, currency mismatch, unknown curve node ID in scenario. |
| `UnsupportedConventionError` | `FI_UNSUPPORTED_CONVENTION` | Unknown day-count, calendar, currency, compounding, interpolation, or scenario shift type. |
| `ConfigurationError` | `FI_CONFIGURATION_ERROR` | Missing config/fixture file, malformed JSON, missing `valuation_date`, invalid config parameter bounds. |
| `ScheduleError` | `FI_SCHEDULE_ERROR` | Misaligned dates with `stub_type=NONE`, non-multiple interior coupon span between explicit stub dates. |
| `CalibrationError` | `FI_CALIBRATION_ERROR` | Duplicate calibration maturity dates, negative zero rates when disallowed, non-positive residual PV, repricing error exceeding `repricing_tolerance_bp`. |
| `ConvergenceError` | `FI_CONVERGENCE_ERROR` | Root-finder fails to bracket or exceeds `solver_max_iterations`. |

## 5. Fixture inventory

- `data/fixtures/valid_curve_and_bonds.json`: Canonical synthetic USD curve (3 deposits + 5 bonds) and 4 portfolio bonds (zero-coupon, seasoned semi-annual, 30/360 corporate, 10Y par).
- `data/fixtures/boundary_irregular_and_holidays.json`: July 4th holiday roll, short first stub, long first stub, short last stub, and leap-year Feb 29 EOM 30/360 bond.
- `data/fixtures/invalid_cases.json`: 6 negative contract fixtures mapped to expected exception classes.
- `data/fixtures/reference_hand_calculated.json`: Independent closed-form analytical oracles for deposit DF, 5Y zero-coupon bond, 2Y par bond, and mid-period accrued interest.
