# Integration Guide — Interest Rate Swaps Engine

## 1. Neighboring Repository Survey

The `Trading-Derivatives/` repository contains seven independent peer project directories:

- `derivatives-pricing-engine/` (`src/derivatives_pricing_engine`)
- `derivatives-margin-engine/` (`src/derivatives_margin_engine`)
- `fixed-income-analytics-engine/` (`src/fixed_income`)
- `interest-rate-swaps-engine/` (this project — `src/irs_engine`)
- `options-hedging-simulator/` (`src/options_hedging_simulator`)
- `real-time-greeks-engine/` (`src/real_time_greeks_engine`)
- `volatility-surface-engine/` (`src/volatility_surface_engine`)

To preserve standalone isolation, this engine implements **zero-coupling Protocol ports and adapters** in `src/irs_engine/adapters.py` rather than importing sibling directories directly. No dependency on proprietary code exists anywhere in the project.

---

## 2. Reusable Adapter Boundaries (`src/irs_engine/adapters.py`)

### Upstream Port: `CurveProviderPort`

Designed to connect with an upstream curve construction module (such as `fixed-income-analytics-engine`):

```python
class CurveProviderPort(Protocol):
    def get_curve(
        self,
        *,
        currency: str,
        role: CurveRole,
        valuation_date: date,
        index_name: str | None = None,
    ) -> CurveSnapshot: ...
```

A deterministic `InMemoryCurveProvider` reference implementation is bundled and tested in `tests/test_scenarios_and_repro.py`.

### Downstream Port: `SwapValuationPort`

Designed for consumption by downstream margin, VaR, or real-time risk engines (such as `derivatives-margin-engine` or `real-time-greeks-engine`):

```python
class SwapValuationPort(Protocol):
    def value_swap(
        self,
        trade: SwapTrade,
        valuation_date: date,
        fixing_history: FixingHistory | None = None,
    ) -> SwapValuationResult: ...

    def compute_risk(
        self,
        trade: SwapTrade,
        valuation_date: date,
        fixing_history: FixingHistory | None = None,
    ) -> SensitivityResult: ...
```

`IRSEngineServiceAdapter` implements `SwapValuationPort` over any `CurveProviderPort`.

---

## 3. Transport Compatibility

Downstream services can integrate in three ways:

1. **In-Process Python Library:** Import `price_swap`, `compute_sensitivities`, `run_deterministic_scenarios`, or `IRSEngineServiceAdapter` directly.
2. **HTTP JSON API (FastAPI):** Call `/v1/schedule`, `/v1/price`, `/v1/par-rate`, `/v1/sensitivities`, `/v1/scenarios`, and `/v1/oracle-check` using versioned schema `1.0.0`.
3. **Batch CLI:** Invoke `python -m irs_engine.cli <command> <bundle.json> --output <out.json>` for file-based replay pipelines.

---

## 4. Optional External Library Integration (`QuantLib`)

`src/irs_engine/domain/oracle.py` includes `check_optional_quantlib_oracle(...)` which probes for `QuantLib` at runtime:
- If `QuantLib` is absent (default offline environment), it returns `{"status": "NOT_RUN", "reason": "..."}` without raising an import crash or hiding the gap.
- Installing the optional `quantlib` extra (`pip install -e ".[quantlib]"`) enables the library hook.
