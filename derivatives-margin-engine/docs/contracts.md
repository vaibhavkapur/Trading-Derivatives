# Data Contracts — Derivatives Margin Engine

**Schema Version:** `1.0.0` (strictly validated; unsupported schema versions raise `SCHEMA_VERSION_UNSUPPORTED`).

## 1. Conventions & Units

- **Numeric representation:** All decimals MUST be encoded as JSON strings (e.g., `"5000.00"`) or integers. JSON floats are rejected (`FLOAT_NOT_ALLOWED`) to prevent binary precision drift.
- **Currency:** ISO-4217 3-letter codes (`USD`, `EUR`, `GBP` in default policy). Every currency used in netting sets, positions, settlement prices, or collateral must exist in `policy.supported_currencies` and `policy.fx_rates_to_base`.
- **Timestamps & Dates:**
  - `valuation_date` and `maturity_date`: ISO-8601 calendar date `YYYY-MM-DD` (explicit calendar documented in `policy.calendar`, default `US_BUSINESS_DAYS`).
  - `valuation_time_utc`: ISO-8601 UTC time string (e.g., `"21:00:00Z"`).
- **Position Sign Convention:**
  - `quantity > 0`: Long contract position; `quantity < 0`: Short contract position; `quantity == 0`: Flat position.
  - `contract_multiplier > 0`: Units of underlying per contract (e.g., `50` for ES index futures, `1000` for CL/ZN futures).
- **VM Sign Convention:**
  - `daily_mtm_movement = current_mtm - prior_mtm` (positive = participant gain, negative = participant loss).
  - `variation_margin = daily_mtm_movement - settled_cash_adjustment`.
  - Negative `variation_margin` produces a `VM_CALL` (participant pays); positive `variation_margin` produces a `VM_CREDIT` (participant receives).

## 2. Input Schemas

### 2.1 `MarginPolicy` (`config/default_policy.json`)

| Field | Type | Constraints |
|---|---|---|
| `schema_version` | string | Must equal `"1.0.0"` |
| `policy_id` | string | Non-empty policy identifier |
| `base_currency` | string | Must be in `supported_currencies` |
| `calendar` | string | Explicit business calendar label |
| `valuation_time_utc` | string | Default valuation cutoff time |
| `decimal_scale` | int | `0 <= decimal_scale <= 8` |
| `rounding_mode` | string | `ROUND_HALF_UP`, `ROUND_HALF_EVEN`, or `ROUND_CEILING` |
| `supported_currencies` | string[] | Supported ISO currency codes |
| `fx_rates_to_base` | map<ccy, decimal> | Strictly positive rates converting 1 unit of `ccy` into `base_currency` |
| `im_policy` | object | `aggregation_method` (`WORST_CASE` \| `HISTORICAL_VAR` \| `EXPECTED_SHORTFALL`), `confidence_level` in `(0,1]`, `default_intra_group_offset`, `default_cross_group_offset`, `max_cross_group_offset` in `[0,1]`, optional `group_intra_offsets` and `pair_cross_group_offsets` |
| `margin_call_policy` | object | `minimum_transfer_amount >= 0`, `rounding_increment >= 0`, `vm_cash_only` (bool), `allow_im_excess_offset_vm` (bool) |
| `collateral_policy` | object | `min_residual_maturity_days >= 0`, `fx_mismatch_haircut_addon` in `[0,1]`, `max_single_asset_class_concentration` in `(0,1]`, `haircut_schedule[]` (`asset_class`, `eligible`, `min_maturity_days`, `max_maturity_days`, `haircut` in `[0,1]`) |

### 2.2 `ScenarioSet` (`config/scenarios_reference.json`)

| Field | Type | Constraints |
|---|---|---|
| `schema_version` | string | `"1.0.0"` |
| `scenario_set_id` | string | Versioned scenario identifier |
| `seed` | int \| null | Optional RNG seed for deterministic stochastic scenarios |
| `scenarios[]` | array | Non-empty; unique `scenario_id`; each contains `shocks[]` with `target_type` (`INSTRUMENT` \| `RISK_GROUP`), `target_id`, `shock_type` (`RELATIVE` \| `ABSOLUTE`), `value` (decimal) |

*Note on Shock Precedence:* If a scenario defines `INSTRUMENT` shock(s) matching a position's `instrument_id`, those override `RISK_GROUP` shocks for that position.

### 2.3 Snapshot Inputs (`positions`, `settlement_prices`, `collateral_inventory`, `netting_sets`)

- **NettingSet:** `netting_set_id` (unique), `account_id`, `base_currency`, optional `cross_group_offset_override` in `[0, 1]`.
- **Position:** `position_id` (unique), `netting_set_id` (must exist), `instrument_id`, `risk_group`, `quantity`, `contract_multiplier > 0`, `currency`, `prior_settlement_price`, optional `settled_cash_adjustment` (default `"0.00"`), optional `allow_negative_price` (default `false`).
- **SettlementPrice:** `instrument_id` (unique per snapshot), `valuation_date` (must match run `valuation_date`), `price`, `currency` (must match position currency), `revision >= 1`.
- **CollateralLot:** `lot_id` (**globally unique across all netting sets**), `netting_set_id` (must exist), `asset_class`, `security_id`, `currency`, `market_value >= 0`, `maturity_date` (ISO date or `null` for cash/perpetual).

## 3. Output Contract (`RunResult`)

`RunResult.to_dict()` emits deterministic JSON containing:
- Run provenance: `schema_version`, `run_id` (`RUN-YYYYMMDD-R<rev>-<16-char SHA256>`), `valuation_date`, `valuation_time_utc`, `revision`, `is_settlement_correction`, `policy_id`, `policy_hash`, `scenario_set_id`, `scenario_hash`, `input_hash`, `seed`.
- `netting_set_results[]`: per-netting-set `position_valuations[]`, `total_mtm`, `daily_mtm_movement`, `total_settled_cash_adjustment`, `variation_margin`, `im_result` (group standalone IMs, `gross_im`, `joint_im`, `diversification_benefit`, `applied_cross_group_offset`, `offset_credit`, `net_im`, `worst_scenario_id`, `scenario_losses`), `collateral_valuations[]`, `gross_collateral_value`, `eligible_collateral_value`, `im_shortfall`, `im_excess`, `vm_call`, `vm_credit`, `total_call_due`, and `margin_calls[]`.
- `diagnostics`: counters (`positions_valued`, `scenarios_evaluated`, `collateral_lots_valued`, `ineligible_collateral_lots`, `margin_calls_issued`, `duplicate_calls_blocked`, `journal_entries_posted`).

## 4. Validation & Domain Error Codes

| Error Code | Exception Class | Trigger |
|---|---|---|
| `MISSING_CONFIG_FILE` / `MISSING_INPUT_FILE` | `ValidationError` | Policy, scenario, or input JSON file missing |
| `INVALID_JSON` | `ValidationError` | Malformed JSON or non-object root |
| `SCHEMA_VERSION_UNSUPPORTED` | `ValidationError` | `schema_version != "1.0.0"` |
| `FLOAT_NOT_ALLOWED` / `INVALID_DECIMAL` | `ValidationError` | Binary float or unparseable decimal |
| `MISSING_CONFIG_FIELD` / `MISSING_INPUT_FIELD` | `ValidationError` | Required contract field omitted |
| `INVALID_CONFIG` / `INVALID_HAIRCUT` | `ValidationError` | Out-of-bounds offset, confidence level, scale, or haircut |
| `UNSUPPORTED_CURRENCY` / `CURRENCY_MISMATCH` | `ValidationError` | Currency absent from policy or position/price mismatch |
| `UNKNOWN_NETTING_SET` | `ValidationError` | Position or collateral references undefined netting set |
| `INVALID_POSITION` / `INVALID_PRICE` | `ValidationError` | Non-positive multiplier or disallowed negative price |
| `MISSING_SETTLEMENT_PRICE` / `PRICE_DATE_MISMATCH` | `ValidationError` | Missing or wrong-date settlement price |
| `DUPLICATE_COLLATERAL_LOT` | `DuplicateCollateralError` | Same `lot_id` allocated more than once |
| `DUPLICATE_MARGIN_CALL` | `DuplicateMarginCallError` | Duplicate call `idempotency_key` in strict mode or conflicting same-revision payload |
| `INVALID_SETTLEMENT_CORRECTION` / `MISSING_PRIOR_REVISION` | `SettlementCorrectionError` | Correction with `revision < 2` or missing revision `revision - 1` |
| `JOURNAL_RECONCILIATION_FAILED` | `ReconciliationError` | Unbalanced DR/CR, unreconciled call, orphan entry, or duplicate collateral row |

## 5. Reference Fixtures

- Valid hand-calculated oracles: `data/fixtures/reference_hand_calc.json`, `data/fixtures/reference_offset_oracle.json`
- Multi-day lifecycle (haircut hike, expiry, settlement correction): `data/fixtures/multiday_replay.json`
- Boundary cases (flat position, 1-day vs 0-day maturity, ineligible asset, EUR FX mismatch): `data/fixtures/boundary_cases.json`
- Invalid cases (double-counted collateral, missing price, unsupported currency, negative multiplier): `data/fixtures/invalid_cases.json`
