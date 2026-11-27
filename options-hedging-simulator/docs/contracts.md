# Data Contracts & Schema Specification

**Schema Version:** `1.0.0`

## 1. Input Contracts

All input schemas are validated eagerly on construction in `src/options_hedging_simulator/contracts.py`. Invalid inputs raise `ValidationError`, unsupported contract semantics raise `UnsupportedContractError`, and missing/malformed configuration files raise `ConfigurationError`.

### 1.1 `PricingConventions`

| Field | Type | Units / Domain | Default | Notes |
|---|---|---|---|---|
| `day_count` | `str` | `"ACT/365F"`, `"BUS/252"` | `"ACT/365F"` | Year fraction basis |
| `compounding` | `str` | `"continuous"`, `"simple"` | `"continuous"` | Financing & dividend accrual basis |
| `valuation_time_utc` | `str` | ISO-8601 time | `"16:00:00Z"` | Canonical EOD valuation cut |
| `currency` | `str` | `"USD"`, `"EUR"`, `"GBP"`, `"JPY"` | `"USD"` | Must match `contract.currency` |
| `currency_decimals` | `int` | `2` (USD/EUR/GBP), `0` (JPY) | Derived | Minor unit quantization scale |
| `steps_per_year` | `int` | `> 0` | `252` | Nominal annual grid reference |

### 1.2 `OptionContract`

| Field | Type | Units / Constraints | Default |
|---|---|---|---|
| `contract_id` | `str` | Non-empty identifier | Required |
| `underlying` | `str` | Non-empty ticker/symbol | Required |
| `option_type` | `str` | `"call"` or `"put"` | Required |
| `strike` | `float` | Currency per share (`> 0`) | Required |
| `maturity_years` | `float` | Year fraction (`> 0`) | Required |
| `contracts` | `int` | Number of contracts (`> 0`) | `1` |
| `multiplier` | `int` | Shares per contract (`> 0`) | `100` |
| `position_side` | `str` | `"short"` or `"long"` | `"short"` |
| `exercise_style` | `str` | `"european"` only | `"european"` |
| `settlement_type` | `str` | `"cash"` or `"physical"` | `"cash"` |
| `currency` | `str` | Supported currency | `"USD"` |

### 1.3 `MarketParams`

| Field | Type | Units / Constraints | Default |
|---|---|---|---|
| `spot0` | `float` | Currency per share (`> 0`) | Required |
| `risk_free_rate` | `float` | Annualized rate (`[-1.0, 1.0]`) | Required |
| `hedge_volatility` | `float` | Annualized implied vol (`(0, 5.0]`) | Required |
| `dividend_yield` | `float` | Annualized yield (`[0, 1.0]`) | `0.0` |
| `realized_volatility` | `float \| null` | Annualized path vol (`(0, 5.0]`) | `hedge_volatility` |
| `drift` | `float \| null` | Annualized physical drift | `r - q` |

### 1.4 `TransactionCostConfig`

| Field | Type | Units / Constraints | Default |
|---|---|---|---|
| `half_spread_bps` | `float` | Basis points (`[0, 10000)`) | `0.0` |
| `fee_per_share` | `float` | Currency per share (`>= 0`) | `0.0` |
| `fee_per_trade` | `float` | Fixed currency per trade (`>= 0`) | `0.0` |
| `min_fee_per_trade` | `float` | Minimum ticket fee (`>= 0`) | `0.0` |
| `settlement_spread_bps` | `float \| null` | Basis points on expiry unwind | `half_spread_bps` |
| `cash_borrow_spread_bps` | `float` | Additional annualized borrow spread when cash < 0 | `0.0` |
| `share_lot_size` | `float` | Trade rounding lot (`0.0` = fractional shares) | `0.0` |

### 1.5 `PathConfig` & `JumpParams`

| Field | Type | Units / Constraints | Default |
|---|---|---|---|
| `model` | `str` | `"gbm"`, `"merton_jump"`, `"historical_bars"` | `"gbm"` |
| `num_paths` | `int` | `> 0` | `100` |
| `num_steps` | `int` | `> 0` | `63` |
| `seed` | `int` | `>= 0` (PCG64 seed) | `42` |
| `jump_params` | `object \| null` | `{jump_intensity, jump_mean, jump_std, deterministic_shocks}` | `null` |
| `bars_path` | `str \| null` | Required when `model == "historical_bars"` | `null` |
| `stale_price_policy` | `str` | `"skip"`, `"error"`, `"use_last"` | `"skip"` |
| `max_staleness_seconds` | `float` | Seconds (`> 0`) | `86400.0` |

### 1.6 Historical Bar CSV Schema

Required columns:
- `timestamp_utc`: Strictly increasing ISO-8601 UTC timestamps (`YYYY-MM-DDTHH:MM:SSZ`).
- `spot` or `close`: Strictly positive finite price.

Optional columns:
- `time_years`: Explicit year fraction from $t = 0$. If omitted, derived from elapsed seconds using `day_count`.
- `is_stale`: Boolean flag (`true`/`false`/`1`/`0`).
- `staleness_seconds`: Non-negative age of quote in seconds; rows exceeding `max_staleness_seconds` are automatically marked stale.
- `gap_shock`: Optional diagnostic shock return marker.

## 2. Output Contracts

Every CLI `run` writes a versioned directory containing:
- `config.json`: Canonical serialized `SimulationConfig` (input provenance for replay).
- `summary.json`: `{run_id, config_hash, fills_digest, runtime_metadata, metrics}`.
- `path_results.csv`: Per-path terminal hedge error, spread/fee costs, turnover, rebalance counts, exercise flag, and `ledger_residual` (exact currency string).
- `sample_ledger.csv`: Step-by-step self-financing ledger rows for path 0.
- `sample_fills.json`: Structured fill log for path 0.
- `sample_path_diagnostics.png`: 3-panel spot, delta tracking, and MTM/cost chart (unless `--no-plots`).

Every CLI `compare` writes:
- `comparison_config.json`, `comparison_summary.json`, `policy_comparison.csv`, and `policy_comparison.png`.

## 3. Error Hierarchy

| Error Class | Code / Trigger |
|---|---|
| `ValidationError` | Invalid field type, NaN/Inf, negative price/strike/fee, non-monotonic bar timestamps, currency mismatch |
| `UnsupportedContractError` | Non-European exercise style (e.g., `american`), unsupported currency (`CHF`), unsupported day-count or compounding |
| `ConfigurationError` | Missing file, malformed JSON, missing top-level config sections |
| `StalePriceError` | Stale bar encountered under `stale_price_policy="error"` or at step `0` |
| `LedgerImbalanceError` | Step or cumulative cash/inventory accounting invariant violation |
| `ReplayMismatchError` | Re-running a saved config + seed produces a different `fills_digest` or summary metrics |

## 4. Fixture Inventory (`data/fixtures/`)

| Fixture | Category | Provenance | Purpose |
|---|---|---|---|
| `deterministic_3step_bars.csv` | Valid / Reference | Synthetic educational | 3-step deterministic path ($S = 100, 105, 96, 108$) |
| `deterministic_3step_expected.json` | Oracle | Independent pure-stdlib calculation | Exact cent-level expected step ledger and terminal error (`-484.02`) |
| `historical_permitted_bars_sample.csv` | Valid | Synthetic permitted OHLCV bars | 22-day daily bar ingestion smoke fixture |
| `stale_price_bars.csv` | Boundary | Synthetic educational | Stale bar (`172800s`) at step 2 for `skip`/`use_last`/`error` tests |
| `gap_shock_bars.csv` | Boundary / Stress | Synthetic educational | Instantaneous `-18%` gap drop at step 2 |
| `invalid_contract_fixture.json` | Invalid | Synthetic negative cases | American exercise, negative strike, unsupported `CHF` currency |
