"""FastAPI application isolating HTTP transport from pure IRS domain analytics."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from irs_engine import __schema_version__, __version__
from irs_engine.api.schemas import (
    ScenariosRequest,
    ScheduleRequest,
    SensitivityRequest,
    ValuationRequest,
)
from irs_engine.diagnostics import ENGINE_COUNTERS, collect_runtime_metadata, get_logger
from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import FiniteDifferenceScheme
from irs_engine.domain.errors import IRSEngineError, ValidationError
from irs_engine.domain.oracle import check_optional_quantlib_oracle, run_independent_analytic_oracle
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.scenarios import run_deterministic_scenarios, run_stochastic_scenarios
from irs_engine.domain.schedule import generate_swap_schedules
from irs_engine.domain.sensitivities import compute_sensitivities
from irs_engine.io import (
    parse_curve_snapshot,
    parse_fixing_history,
    parse_trade,
    parse_valuation_config,
    to_plain_dict,
)

logger = get_logger("irs_engine.api")


def create_app(calendar_registry: CalendarRegistry | None = None) -> FastAPI:
    """Create configured FastAPI application instance."""
    calendars = calendar_registry or CalendarRegistry.default()

    api = FastAPI(
        title="Interest Rate Swaps Engine API",
        version=__version__,
        description="Dual-curve vanilla fixed-floating IRS pricing, sensitivities, and scenarios API.",
    )

    @api.exception_handler(IRSEngineError)
    async def handle_domain_error(_request: Request, exc: IRSEngineError) -> JSONResponse:
        ENGINE_COUNTERS.inc(f"errors.{exc.error_code}")
        logger.warning(
            "Domain error during API request",
            extra={"structured_fields": exc.to_dict()},
        )
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine_version": __version__,
            "schema_version": __schema_version__,
            "calendars": calendars.list_names(),
        }

    @api.get("/v1/metrics")
    def metrics() -> dict[str, Any]:
        return {
            "counters": ENGINE_COUNTERS.snapshot(),
            "runtime": collect_runtime_metadata(),
        }

    @api.post("/v1/schedule")
    def build_schedule(req: ScheduleRequest) -> dict[str, Any]:
        ENGINE_COUNTERS.inc("requests.schedule")
        trade = parse_trade(req.trade.model_dump(mode="json"))
        fixed_sched, float_sched = generate_swap_schedules(
            trade,
            calendars.get(trade.fixed_leg.calendar),
            calendars.get(trade.floating_leg.calendar),
        )
        return {
            "trade_id": trade.trade_id,
            "fixed_schedule": to_plain_dict(fixed_sched),
            "floating_schedule": to_plain_dict(float_sched),
            "schema_version": __schema_version__,
        }

    @api.post("/v1/price")
    def price(req: ValuationRequest) -> dict[str, Any]:
        ENGINE_COUNTERS.inc("requests.price")
        trade = parse_trade(req.trade.model_dump(mode="json"))
        disc = parse_curve_snapshot(req.discount_curve.model_dump(mode="json"))
        fwd = parse_curve_snapshot(req.forward_curve.model_dump(mode="json"))
        fixings = parse_fixing_history(
            req.fixing_history.model_dump(mode="json") if req.fixing_history else None
        )
        cfg = parse_valuation_config(req.config.model_dump(mode="json") if req.config else None)
        res = price_swap(
            trade,
            disc,
            fwd,
            fixing_history=fixings,
            calendar_registry=calendars,
            config=cfg,
        )
        ENGINE_COUNTERS.inc("valuations.succeeded")
        logger.info(
            "Swap valued",
            extra={
                "structured_fields": {
                    "trade_id": res.trade_id,
                    "valuation_id": res.valuation_id,
                    "npv": round(res.npv, 6),
                    "par_rate": round(res.par_rate, 8),
                }
            },
        )
        return to_plain_dict(res)

    @api.post("/v1/par-rate")
    def par_rate(req: ValuationRequest) -> dict[str, Any]:
        ENGINE_COUNTERS.inc("requests.par_rate")
        trade = parse_trade(req.trade.model_dump(mode="json"))
        disc = parse_curve_snapshot(req.discount_curve.model_dump(mode="json"))
        fwd = parse_curve_snapshot(req.forward_curve.model_dump(mode="json"))
        fixings = parse_fixing_history(
            req.fixing_history.model_dump(mode="json") if req.fixing_history else None
        )
        cfg = parse_valuation_config(req.config.model_dump(mode="json") if req.config else None)
        res = price_swap(
            trade,
            disc,
            fwd,
            fixing_history=fixings,
            calendar_registry=calendars,
            config=cfg,
        )
        return {
            "valuation_id": res.valuation_id,
            "trade_id": res.trade_id,
            "valuation_date": res.valuation_date.isoformat(),
            "currency": res.currency,
            "par_rate": res.par_rate,
            "par_spread": res.par_spread,
            "annuity": res.annuity,
            "fixed_dv01": res.fixed_dv01,
            "schema_version": __schema_version__,
        }

    @api.post("/v1/sensitivities")
    def sensitivities(req: SensitivityRequest) -> dict[str, Any]:
        ENGINE_COUNTERS.inc("requests.sensitivities")
        trade = parse_trade(req.trade.model_dump(mode="json"))
        disc = parse_curve_snapshot(req.discount_curve.model_dump(mode="json"))
        fwd = parse_curve_snapshot(req.forward_curve.model_dump(mode="json"))
        fixings = parse_fixing_history(
            req.fixing_history.model_dump(mode="json") if req.fixing_history else None
        )
        cfg = parse_valuation_config(req.config.model_dump(mode="json") if req.config else None)
        scheme_enum: FiniteDifferenceScheme | None = None
        if req.scheme is not None:
            try:
                scheme_enum = FiniteDifferenceScheme(req.scheme)
            except ValueError as exc:
                raise ValidationError(
                    f"Invalid finite difference scheme '{req.scheme}'.",
                    details={"scheme": req.scheme},
                ) from exc
        res = compute_sensitivities(
            trade,
            disc,
            fwd,
            fixing_history=fixings,
            calendar_registry=calendars,
            config=cfg,
            bump_bps=req.bump_bps,
            scheme=scheme_enum,
        )
        return to_plain_dict(res)

    @api.post("/v1/scenarios")
    def scenarios(req: ScenariosRequest) -> dict[str, Any]:
        ENGINE_COUNTERS.inc("requests.scenarios")
        trade = parse_trade(req.trade.model_dump(mode="json"))
        disc = parse_curve_snapshot(req.discount_curve.model_dump(mode="json"))
        fwd = parse_curve_snapshot(req.forward_curve.model_dump(mode="json"))
        fixings = parse_fixing_history(
            req.fixing_history.model_dump(mode="json") if req.fixing_history else None
        )
        cfg = parse_valuation_config(req.config.model_dump(mode="json") if req.config else None)

        base = price_swap(
            trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
        )
        det = run_deterministic_scenarios(
            trade,
            disc,
            fwd,
            req.deterministic_scenarios,
            fixing_history=fixings,
            calendar_registry=calendars,
            config=cfg,
        )
        stoch = None
        if req.stochastic is not None:
            stoch = run_stochastic_scenarios(
                trade,
                disc,
                fwd,
                seed=req.stochastic.seed,
                num_paths=req.stochastic.num_paths,
                level_vol_bps=req.stochastic.level_vol_bps,
                slope_vol_bps=req.stochastic.slope_vol_bps,
                curvature_vol_bps=req.stochastic.curvature_vol_bps,
                basis_vol_bps=req.stochastic.basis_vol_bps,
                fixing_history=fixings,
                calendar_registry=calendars,
                config=cfg,
            )

        return {
            "valuation_id": base.valuation_id,
            "trade_id": trade.trade_id,
            "base_npv": base.npv,
            "base_par_rate": base.par_rate,
            "deterministic_scenarios": to_plain_dict(det),
            "stochastic_summary": to_plain_dict(stoch) if stoch is not None else None,
            "schema_version": __schema_version__,
        }

    @api.post("/v1/oracle-check")
    def oracle_check(req: ValuationRequest) -> dict[str, Any]:
        ENGINE_COUNTERS.inc("requests.oracle_check")
        trade = parse_trade(req.trade.model_dump(mode="json"))
        disc = parse_curve_snapshot(req.discount_curve.model_dump(mode="json"))
        fwd = parse_curve_snapshot(req.forward_curve.model_dump(mode="json"))
        fixings = parse_fixing_history(
            req.fixing_history.model_dump(mode="json") if req.fixing_history else None
        )
        cfg = parse_valuation_config(req.config.model_dump(mode="json") if req.config else None)
        analytic = run_independent_analytic_oracle(
            trade, disc, fwd, fixing_history=fixings, calendar_registry=calendars, config=cfg
        )
        ql_status = check_optional_quantlib_oracle(trade, disc, fwd)
        return {
            "analytic_oracle": to_plain_dict(analytic),
            "quantlib_oracle": ql_status,
            "schema_version": __schema_version__,
        }

    return api


app = create_app()
