# ADR 0001: Canonical Greeks Units, Fallback Pricing, and Freeze-on-Stale Semantics

- **Status:** Accepted
- **Date:** 2026-11-27
- **Project:** Real-Time Greeks Engine (`real-time-greeks-engine/`)

## Context

Option risk engines frequently suffer from silent unit mismatches (e.g., annualized vs daily theta, 100% vs 1% vega, per-share vs dollar gamma), stale market surfaces leaking into live risk aggregates, and discontinuous near-expiry sensitivities causing erroneous hedges.

## Decisions

1. **Explicit, Published Unit Contract (`GreeksUnitSpec`):**
   - `delta`: unitless $\partial V / \partial S$; `delta_shares = delta * qty * multiplier`; `dollar_delta = delta_shares * S`.
   - `gamma`: $\partial^2 V / \partial S^2$; `gamma_shares = gamma * qty * multiplier`; `dollar_gamma_1pct = gamma_shares * S^2 * 0.01`.
   - `vega`: currency per `+1%` (`+0.01` absolute) implied volatility shift (`0.01 * dV/dsigma`).
   - `theta`: currency per `+1` calendar day forward (`(1 / days_per_year) * dV/dt`), aligned to the instrument's day-count basis (`ACT_365F` = 365, `ACT_360` = 360).
   - `rho`: currency per `+1%` (`+0.01` absolute) continuous zero-rate shift (`0.01 * dV/dr`).
   - `vanna`, `charm`, `volga`: consistently scaled to `+1%` vol and `+1` calendar day.
   - Every `PortfolioSnapshot` embeds `unit_spec` so consumers never have to guess conventions.

2. **Analytic BSM Primary + Central Finite-Difference Fallback:**
   - Standard European options use closed-form Black-Scholes-Merton (`ANALYTIC_BSM`).
   - Custom instruments/pricers (registered via `register_custom_pricer`) or explicit fallback requests use central finite-difference `bump_and_revalue_greeks` with identical unit scaling and attach `FALLBACK_BUMP_AND_REVALUE`.
   - Unsupported exercise styles (e.g., `AMERICAN` without a custom pricer) fail loudly with `UnsupportedInstrumentError`.

3. **Explicit Reliability Flags & Freeze-on-Stale:**
   - Options with `T <= 0` or `T < min_reliable_tte_days` (`1.0` day) receive `NEAR_EXPIRY`; low vol (`sigma * sqrt(T) < 1e-3`) receives `LOW_VOL`; deep moneyness (`|ln(S/K)| > 0.75`) receives `EXTREME_MONEYNESS`.
   - When spot, curve, or vol surface age exceeds its TTL, the engine emits a `CRITICAL` `StaleDataAlert` and freezes the position's last reliable Greeks (`is_frozen=True`, `FROZEN_SNAPSHOT`) by default (`freeze_on_stale=True`), or raises `StaleMarketDataError` when `strict_raise_on_stale=True`.

## Consequences

- Analytic and bump-and-revalue implementations share an exact unit oracle, verified automatically to `<= 1e-4` relative error (measured `<= 8.60e-7`).
- Downstream risk consumers can filter or gate hedges using explicit `flags`, `is_frozen`, and `alerts` rather than relying on fragile numerical heuristics.
- Operators must refresh stale market feeds to unfreeze snapshots (documented in `docs/runbook.md`).
