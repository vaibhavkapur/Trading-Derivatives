"""Pricing service orchestration, structured logging, metrics, and replay support."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .analytics.binomial import price_binomial
from .analytics.black_scholes import price_black_scholes
from .analytics.day_count import year_fraction
from .analytics.monte_carlo import price_monte_carlo
from .analytics.pde_fd import price_pde_fd
from .config import EngineConfig, load_config
from .contracts import (
    ExerciseStyle,
    MarketSnapshot,
    OptionContract,
    PricingMethod,
    PricingRequest,
    PricingResult,
    compute_result_id,
)
from .errors import PricingEngineError, ValidationError
from .io import collect_runtime_environment
from .version import ENGINE_VERSION, SCHEMA_VERSION

logger = logging.getLogger("derivatives_pricing_engine")

_ALLOWED_METHOD_PARAMS: dict[PricingMethod, set[str]] = {
    PricingMethod.BLACK_SCHOLES: set(),
    PricingMethod.BINOMIAL: {"steps"},
    PricingMethod.MONTE_CARLO: {"paths", "seed", "antithetic", "confidence_level"},
    PricingMethod.PDE_FD: {
        "spot_steps",
        "time_steps",
        "scheme",
        "s_max_multiplier",
        "rannacher_steps",
        "psor_omega",
        "psor_tol",
        "psor_max_iter",
    },
}


@dataclass
class EngineMetrics:
    """In-memory diagnostic counters for observability and operational audits."""

    requests_total: int = 0
    requests_succeeded: int = 0
    requests_failed: int = 0
    errors_by_code: dict[str, int] = field(default_factory=dict)
    requests_by_method: dict[str, int] = field(default_factory=dict)
    total_runtime_ms: float = 0.0

    def record_success(self, method: str, runtime_ms: float) -> None:
        self.requests_total += 1
        self.requests_succeeded += 1
        self.requests_by_method[method] = self.requests_by_method.get(method, 0) + 1
        self.total_runtime_ms += runtime_ms

    def record_failure(self, method: str, error_code: str, runtime_ms: float) -> None:
        self.requests_total += 1
        self.requests_failed += 1
        self.requests_by_method[method] = self.requests_by_method.get(method, 0) + 1
        self.errors_by_code[error_code] = self.errors_by_code.get(error_code, 0) + 1
        self.total_runtime_ms += runtime_ms

    def snapshot(self) -> dict[str, Any]:
        return {
            "requests_total": self.requests_total,
            "requests_succeeded": self.requests_succeeded,
            "requests_failed": self.requests_failed,
            "errors_by_code": dict(self.errors_by_code),
            "requests_by_method": dict(self.requests_by_method),
            "total_runtime_ms": round(self.total_runtime_ms, 6),
        }


class PricingEngine:
    """Typed pricing service isolating I/O and configuration from domain analytics."""

    def __init__(self, config: Optional[EngineConfig] = None) -> None:
        self.config = config if config is not None else load_config()
        self.metrics = EngineMetrics()
        self._env = collect_runtime_environment()

    def _resolve_params(self, method: PricingMethod, overrides: dict[str, Any]) -> dict[str, Any]:
        allowed = _ALLOWED_METHOD_PARAMS[method]
        unknown = sorted(set(overrides.keys()) - allowed)
        if unknown:
            raise ValidationError(
                f"Unknown method_params for '{method.value}': {unknown}",
                details={"method": method.value, "unknown_params": unknown, "allowed_params": sorted(allowed)},
            )
        defaults = self.config.method_defaults(method.value)
        merged = {k: v for k, v in defaults.items() if k in allowed}
        merged.update(overrides)
        return merged

    def price(self, request: PricingRequest) -> PricingResult:
        """Execute a validated PricingRequest and return a versioned PricingResult."""
        t0 = time.perf_counter()
        method = request.method
        try:
            effective_params = self._resolve_params(method, request.method_params)
            tau = year_fraction(
                request.market.valuation_timestamp,
                request.contract.expiry_timestamp,
                request.contract.day_count,
            )

            contract = request.contract
            market = request.market
            greeks: dict[str, Optional[float]] = {
                "delta": None,
                "gamma": None,
                "vega": None,
                "theta": None,
                "rho": None,
            }
            diagnostics: dict[str, Any]

            if method == PricingMethod.BLACK_SCHOLES:
                bs = price_black_scholes(
                    spot=market.spot,
                    strike=contract.strike,
                    time_to_maturity=tau,
                    risk_free_rate=market.risk_free_rate,
                    dividend_yield=market.dividend_yield,
                    volatility=market.volatility,
                    option_type=contract.option_type,
                    exercise_style=contract.exercise_style,
                )
                unit_price = bs.price
                intrinsic = bs.intrinsic
                time_value = bs.time_value
                df = bs.discount_factor
                greeks = {
                    "delta": bs.delta,
                    "gamma": bs.gamma,
                    "vega": bs.vega,
                    "theta": bs.theta,
                    "rho": bs.rho,
                }
                diagnostics = {
                    "regime": bs.regime,
                    "forward": bs.forward,
                    "dividend_discount_factor": bs.dividend_discount_factor,
                    "d1": bs.d1,
                    "d2": bs.d2,
                }
            elif method == PricingMethod.BINOMIAL:
                bn = price_binomial(
                    spot=market.spot,
                    strike=contract.strike,
                    time_to_maturity=tau,
                    risk_free_rate=market.risk_free_rate,
                    dividend_yield=market.dividend_yield,
                    volatility=market.volatility,
                    option_type=contract.option_type,
                    exercise_style=contract.exercise_style,
                    **effective_params,
                )
                unit_price = bn.price
                intrinsic = bn.intrinsic
                time_value = bn.time_value
                df = bn.discount_factor
                greeks["delta"] = bn.delta
                greeks["gamma"] = bn.gamma
                greeks["theta"] = bn.theta
                diagnostics = {
                    "regime": bn.regime,
                    "steps": bn.steps,
                    "up_factor": bn.up_factor,
                    "down_factor": bn.down_factor,
                    "risk_neutral_prob": bn.risk_neutral_prob,
                    "early_exercise_nodes": bn.early_exercise_nodes,
                    "solver_runtime_ms": bn.runtime_ms,
                }
            elif method == PricingMethod.MONTE_CARLO:
                mc = price_monte_carlo(
                    spot=market.spot,
                    strike=contract.strike,
                    time_to_maturity=tau,
                    risk_free_rate=market.risk_free_rate,
                    dividend_yield=market.dividend_yield,
                    volatility=market.volatility,
                    option_type=contract.option_type,
                    exercise_style=contract.exercise_style,
                    **effective_params,
                )
                unit_price = mc.price
                intrinsic = mc.intrinsic
                time_value = mc.time_value
                df = mc.discount_factor
                diagnostics = {
                    "regime": mc.regime,
                    "paths": mc.paths,
                    "seed": mc.seed,
                    "antithetic": mc.antithetic,
                    "confidence_level": mc.confidence_level,
                    "standard_error": mc.standard_error,
                    "ci_low": mc.ci_low,
                    "ci_high": mc.ci_high,
                    "ci_half_width": mc.ci_half_width,
                    "variance_reduction_ratio": mc.variance_reduction_ratio,
                    "solver_runtime_ms": mc.runtime_ms,
                }
            elif method == PricingMethod.PDE_FD:
                pde = price_pde_fd(
                    spot=market.spot,
                    strike=contract.strike,
                    time_to_maturity=tau,
                    risk_free_rate=market.risk_free_rate,
                    dividend_yield=market.dividend_yield,
                    volatility=market.volatility,
                    option_type=contract.option_type,
                    exercise_style=contract.exercise_style,
                    **effective_params,
                )
                unit_price = pde.price
                intrinsic = pde.intrinsic
                time_value = pde.time_value
                df = pde.discount_factor
                greeks["delta"] = pde.delta
                greeks["gamma"] = pde.gamma
                diagnostics = {
                    "regime": pde.regime,
                    "scheme": pde.scheme,
                    "spot_steps": pde.spot_steps,
                    "time_steps": pde.time_steps,
                    "s_max": pde.s_max,
                    "grid_ds": pde.grid_ds,
                    "grid_dt": pde.grid_dt,
                    "cfl_number": pde.cfl_number,
                    "is_stable": pde.is_stable,
                    "rannacher_steps": pde.rannacher_steps,
                    "psor_max_iterations_used": pde.psor_max_iterations_used,
                    "psor_converged": pde.psor_converged,
                    "solver_runtime_ms": pde.runtime_ms,
                }
            else:
                raise ValidationError(f"Unhandled pricing method: {method}")

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            diagnostics["service_runtime_ms"] = elapsed_ms

            result_id = compute_result_id(request, effective_params)
            result = PricingResult(
                result_id=result_id,
                request_id=request.request_id,
                schema_version=SCHEMA_VERSION,
                engine_version=ENGINE_VERSION,
                contract_id=contract.contract_id,
                snapshot_id=market.snapshot_id,
                method=method.value,
                currency=contract.currency,
                unit_price=unit_price,
                total_price=unit_price * contract.contract_multiplier,
                intrinsic_value=intrinsic,
                time_value=time_value,
                time_to_maturity=tau,
                discount_factor=df,
                greeks=greeks,
                diagnostics=diagnostics,
                metadata={
                    "config_version": self.config.config_version,
                    "day_count": contract.day_count.value,
                    "calendar": contract.calendar.value,
                    "rate_compounding": market.rate_compounding.value,
                    "exercise_style": contract.exercise_style.value,
                    "option_type": contract.option_type.value,
                    "contract_multiplier": contract.contract_multiplier,
                    "effective_params": effective_params,
                    "environment": dict(self._env),
                },
            )
            self.metrics.record_success(method.value, elapsed_ms)
            logger.info(
                "pricing_succeeded",
                extra={
                    "request_id": request.request_id,
                    "result_id": result_id,
                    "method": method.value,
                    "unit_price": unit_price,
                    "runtime_ms": round(elapsed_ms, 4),
                },
            )
            return result

        except PricingEngineError as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.metrics.record_failure(method.value, exc.error_code, elapsed_ms)
            logger.warning(
                "pricing_failed",
                extra={
                    "request_id": request.request_id,
                    "method": method.value,
                    "error_code": exc.error_code,
                    "error_message": exc.message,
                },
            )
            raise

    def compare_methods(
        self,
        contract: OptionContract,
        market: MarketSnapshot,
        methods: Optional[Sequence[PricingMethod]] = None,
        method_params_by_method: Optional[dict[PricingMethod, dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Run compatible pricing engines on one contract/market pair and summarize differences."""
        if methods is None:
            if contract.exercise_style == ExerciseStyle.EUROPEAN:
                methods = (
                    PricingMethod.BLACK_SCHOLES,
                    PricingMethod.BINOMIAL,
                    PricingMethod.MONTE_CARLO,
                    PricingMethod.PDE_FD,
                )
            else:
                methods = (PricingMethod.BINOMIAL, PricingMethod.PDE_FD)

        params_map = method_params_by_method or {}
        results: dict[str, dict[str, Any]] = {}
        for m in methods:
            req = PricingRequest(
                contract=contract,
                market=market,
                method=m,
                method_params=params_map.get(m, {}),
            )
            res = self.price(req)
            results[m.value] = res.to_dict()

        # Always compute European Black-Scholes benchmark for early-exercise premium / reference check
        euro_contract = OptionContract(
            contract_id=f"{contract.contract_id}-EURO-REF",
            underlying=contract.underlying,
            currency=contract.currency,
            option_type=contract.option_type,
            exercise_style=ExerciseStyle.EUROPEAN,
            strike=contract.strike,
            expiry_timestamp=contract.expiry_timestamp,
            contract_multiplier=contract.contract_multiplier,
            day_count=contract.day_count,
            calendar=contract.calendar,
        )
        euro_ref = self.price(
            PricingRequest(
                contract=euro_contract,
                market=market,
                method=PricingMethod.BLACK_SCHOLES,
            )
        )

        diffs_vs_euro_bs: dict[str, float] = {}
        for m_name, res_dict in results.items():
            diffs_vs_euro_bs[m_name] = res_dict["unit_price"] - euro_ref.unit_price

        return {
            "contract": contract.to_dict(),
            "market": market.to_dict(),
            "european_black_scholes_reference": euro_ref.unit_price,
            "results": results,
            "diff_vs_european_bs": diffs_vs_euro_bs,
        }

    def replay_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Replay a saved {'request': ..., 'result': ...} envelope and verify reproducibility."""
        if not isinstance(envelope, dict) or "request" not in envelope:
            raise ValidationError("Replay envelope must be a JSON object containing a 'request' key.")
        req = PricingRequest.from_dict(envelope["request"])
        fresh = self.price(req)
        prior = envelope.get("result")

        matches_id: Optional[bool] = None
        price_diff: Optional[float] = None
        if isinstance(prior, dict):
            matches_id = prior.get("result_id") == fresh.result_id
            if isinstance(prior.get("unit_price"), (int, float)):
                price_diff = abs(float(prior["unit_price"]) - fresh.unit_price)

        return {
            "request": req.to_dict(),
            "replayed_result": fresh.to_dict(),
            "prior_result_id": prior.get("result_id") if isinstance(prior, dict) else None,
            "result_id_matches": matches_id,
            "abs_unit_price_diff": price_diff,
            "exact_reproducible": bool(matches_id and price_diff == 0.0) if prior is not None else None,
        }
