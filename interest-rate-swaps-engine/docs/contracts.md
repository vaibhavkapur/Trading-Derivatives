# Data Contracts — Interest Rate Swaps Engine

**Schema version:** `1.0.0`  
**Encoding:** UTF-8 JSON

---

## 1. Conventions, Units, and Timestamp Semantics

| Quantity | Unit / Format | Example |
|---|---|---|
| **Dates** (`valuation_date`, `effective_date`, `maturity_date`, `fixing_date`, `payment_date`) | ISO-8601 calendar date `YYYY-MM-DD` (timezone-naive business date in the leg's calendar) | `"2026-01-05"` |
| **Audit timestamps** (`generated_at_utc`, structured log `timestamp_utc`) | ISO-8601 UTC timestamp with offset | `"2026-11-27T12:30:00+00:00"` |
| **Rates & Spreads** (`fixed_rate`, `zero_rate`, `spread`, `par_rate`, historical fixing `rate`) | Decimal annual rate (NOT percent) | `0.0425` (= 4.25%) |
| **Curve Shocks & Bumps** (`bump_bps`, `*_shift_bps`, `par_rate_change_bps`) | Basis points (`1 bp = 0.0001 = 1e-4`) | `1.0`, `+100.0` |
| **Notional, PV, NPV, PnL** | Trade currency units (floating-point) | `10000000.0` (10M USD) |
| **PV01 / KR01 / Fixed DV01** | Currency units per `+1 bp` zero-rate / coupon bump | `4246.0981` USD/bp |
| **Tenor (`tenor_years`)** | Strictly positive year fraction on curve's `day_count` | `0.25`, `5.0`, `10.0` |
| **Supported Currencies** | Single-currency swaps only: `"USD"`, `"EUR"`, `"GBP"` | `"USD"` |

### Sign Convention

- **`PAYER`**: Pays fixed coupon, receives floating coupon. $\text{NPV} = \text{PV}_{\text{float}} - \text{PV}_{\text{fixed}}$.
- **`RECEIVER`**: Receives fixed coupon, pays floating coupon. $\text{NPV} = \text{PV}_{\text{fixed}} - \text{PV}_{\text{float}}$.

---

## 2. Canonical Input Schemas

### 2.1 `SwapTrade`

```json
{
  "schema_version": "1.0.0",
  "trade_id": "USD-5Y-IRS-001",
  "currency": "USD",
  "notional": 10000000.0,
  "direction": "PAYER",
  "effective_date": "2026-01-05",
  "maturity_date": "2031-01-05",
  "fixed_leg": {
    "fixed_rate": 0.0425,
    "frequency": "SEMI_ANNUAL",
    "day_count": "30/360",
    "business_day_convention": "MODIFIED_FOLLOWING",
    "calendar": "USD_NY",
    "stub_type": "FRONT_SHORT",
    "end_of_month": false,
    "payment_lag_days": 0
  },
  "floating_leg": {
    "index_name": "USD-SOFR-3M",
    "frequency": "QUARTERLY",
    "day_count": "ACT/360",
    "spread": 0.0,
    "business_day_convention": "MODIFIED_FOLLOWING",
    "calendar": "USD_NY",
    "stub_type": "FRONT_SHORT",
    "end_of_month": false,
    "fixing_lag_days": 2,
    "payment_lag_days": 0
  }
}
```

### 2.2 `CurveSnapshot`

```json
{
  "schema_version": "1.0.0",
  "curve_id": "USD-OIS-DISC-20260105",
  "role": "DISCOUNT",
  "currency": "USD",
  "valuation_date": "2026-01-05",
  "day_count": "ACT/365F",
  "compounding": "CONTINUOUS",
  "interpolation": "LINEAR_ZERO",
  "index_name": null,
  "nodes": [
    { "tenor_years": 0.25, "zero_rate": 0.0410, "label": "3M" },
    { "tenor_years": 5.00, "zero_rate": 0.0442, "label": "5Y" }
  ]
}
```

- `role`: `"DISCOUNT"` or `"FORWARD"` (strictly enforced).
- `nodes`: Non-empty list with strictly increasing `tenor_years > 0` and realistic `zero_rate` in `(-0.50, +1.00)`.
- `compounding`: `"CONTINUOUS"`, `"ANNUAL"`, `"SIMPLE"`.
- `interpolation`: `"LINEAR_ZERO"`, `"LOG_LINEAR_DF"`, `"CUBIC_SPLINE_ZERO"`.

### 2.3 `FixingHistory`

```json
{
  "schema_version": "1.0.0",
  "entries": [
    { "index_name": "USD-SOFR-3M", "fixing_date": "2025-12-31", "rate": 0.0428 }
  ]
}
```

---

## 3. Canonical Output Contracts & Identifiers

### Deterministic `valuation_id`

Every `SwapValuationResult` and `SensitivityResult` includes a deterministic identifier:

`valuation_id = "val_" + sha256(canonical_json(engine_version, schema_version, trade, discount_curve, forward_curve, fixing_history, config))[:16]`

Re-running the same inputs on any supported machine produces the exact same `valuation_id` and numerical outputs.

### Key Output Payloads

- **`SwapValuationResult` (`POST /v1/price`):** `valuation_id`, `trade_id`, `valuation_date`, `currency`, `direction`, `notional`, `discount_curve_id`, `forward_curve_id`, `fixed_leg_pv`, `floating_leg_pv`, `npv`, `annuity`, `fixed_dv01`, `par_rate`, `par_spread`, `fixed_cashflows[]`, `floating_cashflows[]`, `diagnostics`, `schema_version`.
- **`SensitivityResult` (`POST /v1/sensitivities`):** `base_npv`, `par_rate`, `discount_pv01`, `forward_pv01`, `parallel_pv01`, `parallel_convexity`, `discount_kr01[]`, `forward_kr01[]`, `discount_kr01_sum`, `forward_kr01_sum`, `total_kr01_sum`, reconciliation diffs, and `reconciliation_within_tolerance`.
- **`ScenarioSnapshot` & `StochasticSummary` (`POST /v1/scenarios`):** Per-scenario `npv`, `pnl`, `par_rate`, `par_rate_change_bps`, plus Monte Carlo distribution statistics (`seed`, `num_paths`, `mean_pnl`, `std_pnl`, percentiles, `var_95`, `expected_shortfall_95`).

---

## 4. Machine-Readable Error Envelope

All domain validation and runtime errors return structured JSON (HTTP `422` or `500` in FastAPI, exit code `1` + stderr JSON in CLI):

```json
{
  "error_code": "MISSING_FIXING",
  "message": "Missing required historical fixing for index 'USD-SOFR-3M' on 2026-01-05 (before valuation_date 2026-02-10).",
  "details": {
    "index_name": "USD-SOFR-3M",
    "fixing_date": "2026-01-05",
    "valuation_date": "2026-02-10",
    "policy": "ERROR"
  }
}
```

| `error_code` | HTTP Status | Trigger |
|---|---:|---|
| `VALIDATION_ERROR` | 422 | Invalid dates, non-positive notional, unsupported currency, index mismatch, bad enum |
| `UNSUPPORTED_CONVENTION` | 422 | Unsupported day-count or compounding rule |
| `CALENDAR_ERROR` | 422 | Unknown calendar name or malformed calendar file |
| `SCHEDULE_ERROR` | 422 | Unaligned dates under `StubType.NONE` or degenerate adjusted accrual period |
| `CURVE_ERROR` | 422 | Wrong curve role, currency/valuation-date mismatch, non-monotonic pillars, non-positive DF |
| `MISSING_FIXING` | 422 | Required historical fixing absent from `FixingHistory` under `ERROR` policy |
| `CONFIGURATION_ERROR` | 500 | Missing or malformed JSON configuration file |

---

## 5. Fixture Inventory (`data/fixtures/`)

- **Valid:** `usd_5y_vanilla_swap.json`, `usd_5y_par_swap.json`, `seasoned_swap_known_fixings.json`, `stub_front_short_swap.json`, `stub_back_long_swap.json`, `hand_calculated_2y_oracle.json`
- **Invalid:** `missing_historical_fixing.json`, `currency_mismatch.json`, `invalid_dates.json`, `non_monotonic_curve.json`
- **Boundary:** `negative_rates_eur_swap.json`, `single_period_swap.json`, `eom_leap_year_swap.json`
