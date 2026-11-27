# Data Contracts — Real-Time Greeks Engine

## 1. Conventions & Timestamp Semantics

- **Timestamps:** All timestamps (`expiry_utc`, `updated_at_utc`, `event_time_utc`, `received_time_utc`, `as_of_utc`, `valuation_time_utc`, `computed_at_utc`, `detected_at_utc`) **must be timezone-aware ISO-8601** and are normalized to UTC (`Z` / `+00:00`). Naive datetimes are rejected with a validation error.
- **Identifiers:** `underlying` and `currency` are normalized to uppercase (`USD`, `ACME`, `SPX_SYN`). `position_id`, `instrument_id`, `tick_id`, `curve_version`, `vol_version`, and `snapshot_id` are opaque non-empty strings.
- **Day Count:** `ACT_365F` (`365.0` days/year, default) or `ACT_360` (`360.0` days/year). Year fraction is `delta_seconds / (days_per_year * 86400.0)`.
- **Rates & Dividends:** Continuously compounded annualized decimals (`0.05` = 5%).
- **Implied Volatility:** Annualized decimal in `(0.0, 5.0]` (`0.20` = 20% vol).

---

## 2. Canonical Unit Definitions (`GreeksUnitSpec`)

| Metric | Scope | Formula / Unit |
|---|---|---|
| `price` | Unit | Option premium in instrument currency per 1 unit of underlying |
| `pv` | Position | `price * quantity * contract_multiplier` (currency) |
| `delta` | Unit | $\partial V / \partial S$ (unitless) |
| `delta_shares` | Position | `delta * quantity * contract_multiplier` (underlying shares/units) |
| `dollar_delta` | Position | `delta_shares * spot` (currency per 100% spot move) |
| `gamma` | Unit | $\partial^2 V / \partial S^2$ (per 1 currency unit move in spot) |
| `gamma_shares` | Position | `gamma * quantity * contract_multiplier` |
| `dollar_gamma_1pct` | Position | `gamma_shares * spot^2 * 0.01` (change in `dollar_delta` for 1% spot move) |
| `vega` | Unit / Position | `0.01 * dV/dsigma` (currency per `+1%` / `+0.01` abs vol move; scaled by `qty * mult` at position level) |
| `theta` | Unit / Position | `(1 / days_per_year) * dV/dt` (currency decay per `+1` calendar day forward; scaled at position level) |
| `rho` | Unit / Position | `0.01 * dV/dr` (currency per `+1%` / `+0.01` abs continuous rate move; scaled at position level) |
| `vanna` | Unit / Position | `0.01 * dDelta/dsigma` (per `+1%` vol move) |
| `charm` | Unit / Position | `(1 / days_per_year) * dDelta/dt` (per `+1` calendar day) |
| `volga` | Unit / Position | `0.0001 * d2V/dsigma2` (change in 1%-vega per `+1%` vol move) |

---

## 3. Input Schemas

### `OptionPosition`
- `position_id` (str, min 1), `account_id` (str, min 1)
- `quantity` (finite float; `>0` long, `<0` short, `0` flat)
- `version` (int `>= 1`)
- `updated_at_utc` (aware datetime)
- `instrument` (`OptionInstrument`): `instrument_id`, `underlying`, `option_type` (`CALL` | `PUT`), `strike` (`>0`), `expiry_utc`, `currency` (3-char ISO), `contract_multiplier` (`>0`, default `100.0`), `exercise_style` (`EUROPEAN` | `AMERICAN`), `day_count` (`ACT_365F` | `ACT_360`).

### `SpotTick`
- `tick_id` (str, min 1), `underlying` (str, min 1), `spot` (finite float `> 0`)
- `bid`, `ask` (optional finite floats `> 0`, invariant `bid <= ask`)
- `sequence_no` (int `>= 1`, must strictly increase per underlying)
- `event_time_utc`, `received_time_utc` (aware datetimes)
- `source` (str, default `"SYNTHETIC_FEED"`)

### `YieldCurveSnapshot`
- `curve_version` (str, min 1), `currency` (3-char), `as_of_utc` (aware datetime), `ttl_seconds` (optional `> 0`)
- `points`: non-empty list of `{tenor_years > 0, zero_rate}` with strictly increasing `tenor_years`
- `dividend_yields`: mapping `underlying -> continuous dividend yield`

### `VolSurfaceSnapshot`
- `vol_version` (str, min 1), `underlying` (str, min 1), `as_of_utc` (aware datetime), `ttl_seconds` (optional `> 0`)
- `expiries_years`: strictly increasing positive floats
- `strikes`: strictly increasing positive floats
- `vols`: 2D matrix `[len(expiries_years)][len(strikes)]`, every entry in `(0.0, 5.0]`

---

## 4. Output Schemas

- **`PortfolioSnapshot`**: `snapshot_id` (`SNAP-{seq:06d}-{sha256[:12]}`), `snapshot_seq`, `valuation_time_utc`, `engine_config_hash`, `unit_spec`, `market_versions`, `totals`, `by_underlying`, `by_underlying_expiry`, `positions`, `alerts`, `has_frozen_positions`.
- **`ReconciliationReport`**: `reconciliation_id`, `snapshot_seq`, `valuation_time_utc`, `passed`, `abs_tolerance`, `rel_tolerance`, `max_abs_diff`, `max_rel_diff`, `total_buckets_checked`, `mismatched_buckets`.
- **`ScenarioResult`**: `scenario_id`, `shock`, `base_pv`, `scenario_pv`, `pnl`, `totals`, `by_underlying`, `elapsed_ms`.

---

## 5. Error & HTTP Status Contract

| Domain Exception | `error_code` | HTTP Status | Trigger |
|---|---|---|---|
| `ValidationError` / Pydantic | `VALIDATION_ERROR` | `422` | Negative spot/strike/multiplier, NaN/Inf, naive timestamp, invalid grid |
| `UnsupportedInstrumentError` | `UNSUPPORTED_INSTRUMENT` | `400` | `AMERICAN` exercise without custom pricer adapter |
| `MissingMarketDataError` | `MISSING_MARKET_DATA` | `404` | Missing spot, curve, or vol surface on explicit query |
| `SnapshotVersionConflictError` | `SNAPSHOT_VERSION_CONFLICT` | `409` | Optimistic write `expected_snapshot_seq != current snapshot_seq` |
| `StaleMarketDataError` | `STALE_MARKET_DATA` | `503` | Market data older than TTL when `strict_raise_on_stale=True` |
| `ConfigurationError` | `CONFIGURATION_ERROR` | `500` (startup) | Missing or invalid JSON config file |

---

## 6. Fixture Inventory (`data/fixtures/`)

- `valid_portfolio.json`: 5 European option positions across 3 synthetic underlyings (`ACME`, `SPX_SYN`, `TECH_SYN`), 1 USD yield curve, 3 vol surfaces, 3 spot ticks.
- `invalid_and_boundary.json`: Negative spot, zero strike, negative multiplier, unsupported `AMERICAN` exercise, near-expiry (6h), expired ITM, extreme OTM (`K=300`), and stale ACME vol surface (`age = 300s > 60s`).
- `replay_events.json`: Deterministic event sequence exercising spot tick update, duplicate tick rejection, position sign flip + multiplier change, and vol surface refresh.
