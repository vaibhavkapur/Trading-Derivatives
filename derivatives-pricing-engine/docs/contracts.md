# Data Contracts and Error Semantics

**Schema Version:** `1.0.0`  
**Config Version:** `1.0.0`  
**Engine Version:** `0.1.0`

## 1. Input Schemas

### 1.1 `OptionContract`

| Field | Type | Required | Units / Format | Validation Rules |
|---|---|---|---|---|
| `contract_id` | `string` | Yes | Identifier | Non-empty string |
| `underlying` | `string` | Yes | Ticker / synthetic ID | Non-empty string (no proprietary/real customer identifiers) |
| `currency` | `string` | Yes | ISO-4217 | 3 uppercase ASCII letters (e.g., `USD`, `EUR`) |
| `option_type` | `string` | Yes | Enum | `"call"` or `"put"` (otherwise `UNSUPPORTED_FEATURE`) |
| `exercise_style` | `string` | Yes | Enum | `"european"` or `"american"` (others such as `"bermudan"`, `"asian"` raise `UNSUPPORTED_FEATURE`) |
| `strike` | `number` | Yes | Currency per 1 unit | Finite float $> 0$ |
| `expiry_timestamp` | `string` | Yes | ISO-8601 UTC / offset | Must include timezone (`Z` or `±HH:MM`); normalized to UTC |
| `contract_multiplier` | `number` | No (default `1.0`) | Units per contract | Finite float $> 0$ |
| `day_count` | `string` | No (default `"ACT/365F"`) | Enum | `"ACT/365F"`, `"ACT/360"`, `"ACT/365.25"`, `"30/360"` |
| `calendar` | `string` | No (default `"NONE_365"`) | Enum | `"NONE_365"`, `"US_NYSE"`, `"TARGET"` |

### 1.2 `MarketSnapshot`

| Field | Type | Required | Units / Format | Validation Rules |
|---|---|---|---|---|
| `snapshot_id` | `string` | Yes | Identifier | Non-empty string |
| `valuation_timestamp` | `string` | Yes | ISO-8601 UTC / offset | Must include timezone; must satisfy `valuation_timestamp <= expiry_timestamp` |
| `spot` | `number` | Yes | Currency per 1 unit | Finite float $> 0$ |
| `risk_free_rate` | `number` | Yes | Annualized continuous decimal | Finite float in `[-0.5, 2.0]` (e.g., `0.05` = 5% p.a.) |
| `dividend_yield` | `number` | Yes | Annualized continuous decimal | Finite float in `[0.0, 2.0]` |
| `volatility` | `number` | Yes | Annualized lognormal decimal | Finite float in `[0.0, 10.0]` (flat synthetic input, not a calibrated surface) |
| `rate_compounding` | `string` | No (default `"continuous"`) | Enum | `"continuous"` only |

### 1.3 `PricingRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `schema_version` | `string` | No (default `"1.0.0"`) | Must equal `"1.0.0"` |
| `request_id` | `string` | No (auto-generated) | Deterministic `req-<sha256[:16]>` if omitted |
| `contract` | `OptionContract` | Yes | Contract specification |
| `market` | `MarketSnapshot` | Yes | Point-in-time market snapshot |
| `method` | `string` | Yes | `"black_scholes"`, `"binomial"`, `"monte_carlo"`, `"pde_fd"` |
| `method_params` | `object` | No (default `{}`) | Method-specific overrides; unknown keys raise `VALIDATION_ERROR` |

#### Supported `method_params`

- **`black_scholes`:** `{}` (no parameters).
- **`binomial`:** `steps` (int $\ge 1$, default `400`).
- **`monte_carlo`:** `paths` (int $\ge 2$, even if `antithetic=true`, default `100000`), `seed` (int, default `42`), `antithetic` (bool, default `true`), `confidence_level` (float in `(0, 1)`, default `0.95`).
- **`pde_fd`:** `spot_steps` (int $\ge 10$, default `400`), `time_steps` (int $\ge 2$, default `400`), `scheme` (`"crank_nicolson"`, `"implicit"`, `"explicit"`, default `"crank_nicolson"`), `s_max_multiplier` (float $\ge 2.0$, default `4.0`), `rannacher_steps` (int, default `4`), `psor_omega` (float in `(0, 2)`, default `1.2`), `psor_tol` (float $> 0$, default `1e-10`), `psor_max_iter` (int $\ge 1$, default `10000`).

## 2. Output Schema (`PricingResult`)

| Field | Type | Units / Semantics |
|---|---|---|
| `result_id` | `string` | Deterministic `res-<sha256[:16]>` over canonical request + effective params + engine version |
| `request_id` | `string` | Request correlation identifier |
| `schema_version` | `string` | `"1.0.0"` |
| `engine_version` | `string` | `"0.1.0"` |
| `contract_id` | `string` | Contract identifier |
| `snapshot_id` | `string` | Market snapshot identifier |
| `method` | `string` | Executed solver name |
| `currency` | `string` | Contract currency |
| `unit_price` | `number` | Present value per 1 unit of underlying |
| `total_price` | `number` | `unit_price * contract_multiplier` |
| `intrinsic_value` | `number` | Immediate exercise payoff per 1 unit |
| `time_value` | `number` | `unit_price - intrinsic_value` |
| `time_to_maturity` | `number` | Year fraction $T$ under `day_count` |
| `discount_factor` | `number` | $\exp(-r T)$ |
| `greeks` | `object` | `delta` ($\partial V/\partial S$), `gamma` ($\partial^2 V/\partial S^2$), `vega` ($\partial V/\partial \sigma$ per 1.0 vol), `theta` ($\partial V/\partial t$ per year), `rho` ($\partial V/\partial r$ per 1.0 rate); `null` where not computed by solver |
| `diagnostics` | `object` | Solver-specific metrics (`regime`, `steps`, `early_exercise_nodes`, `standard_error`, `ci_low`, `ci_high`, `variance_reduction_ratio`, `cfl_number`, `psor_converged`, `solver_runtime_ms`, `service_runtime_ms`) |
| `metadata` | `object` | Effective conventions, config version, effective parameters, and runtime environment (`python_version`, `numpy_version`, `scipy_version`, `platform`) |

## 3. Error Codes

All domain and operational failures raise typed subclasses of `PricingEngineError` and return a structured JSON error envelope on CLI failure with non-zero exit code:

| Error Code | Exception Class | Trigger Conditions |
|---|---|---|
| `VALIDATION_ERROR` | `ValidationError` | Missing fields, non-finite/out-of-range numbers, naive timestamps, expired contracts ($T < 0$), odd `paths` with `antithetic=true`, unknown `method_params` |
| `UNSUPPORTED_FEATURE` | `UnsupportedFeatureError` | Unsupported exercise style (e.g., Bermudan/Asian), unsupported method or PDE scheme, American option on `black_scholes` or `monte_carlo` |
| `NUMERICAL_STABILITY_ERROR` | `NumericalStabilityError` | Explicit PDE CFL violation ($\text{CFL} > 1$), CRR risk-neutral probability $p \notin (0, 1)$, PSOR non-convergence |
| `CONFIGURATION_ERROR` | `ConfigurationError` | Missing config file, malformed JSON, missing required sections, mismatched `config_version` |

## 4. Fixture Inventory

All fixtures in `data/fixtures/` are 100% synthetic (no live market quotes, customer records, or proprietary data):

- `data/fixtures/valid_european_call.json` — Canonical 1Y ATM European call (`USD`, `ACT/365F`, $q = 2\%$).
- `data/fixtures/valid_american_put.json` — Canonical 1Y ATM American put (`USD`, `ACT/365F`, $q = 0\%$).
- `data/fixtures/reference_hull_examples.json` — Independent double-precision analytic reference benchmark cases.
- `data/fixtures/boundary_cases.json` — Zero maturity ($T=0$), zero volatility ($\sigma=0$), and negative interest rate (`ACT/360`, `TARGET`) cases.
- `data/fixtures/invalid_cases.json` — Negative cases exercising every error code (`VALIDATION_ERROR`, `UNSUPPORTED_FEATURE`, `NUMERICAL_STABILITY_ERROR`).
