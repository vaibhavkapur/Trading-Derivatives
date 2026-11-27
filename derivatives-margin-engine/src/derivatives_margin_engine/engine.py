"""Pure domain margin run orchestrator (isolated from file/database I/O)."""

from __future__ import annotations

from datetime import date
import json
import logging
from typing import Any, Dict, List, Optional, Sequence

from derivatives_margin_engine.calls import generate_margin_calls
from derivatives_margin_engine.collateral import value_collateral_inventory
from derivatives_margin_engine.im import compute_initial_margin
from derivatives_margin_engine.models import (
    CollateralLot,
    MarginPolicy,
    NettingSet,
    NettingSetMarginResult,
    Position,
    RunDiagnostics,
    RunResult,
    SUPPORTED_SCHEMA_VERSION,
    ScenarioSet,
    SettlementPrice,
    content_hash,
)
from derivatives_margin_engine.validation import validate_inputs
from derivatives_margin_engine.valuation import value_netting_set_positions

logger = logging.getLogger("derivatives_margin_engine")


def _log_event(event: str, payload: Dict[str, Any]) -> None:
    if logger.isEnabledFor(logging.INFO):
        record = {"event": event, **payload}
        logger.info(json.dumps(record, sort_keys=True))


def evaluate_margin_run(
    valuation_date: date,
    policy: MarginPolicy,
    scenario_set: ScenarioSet,
    netting_sets: Sequence[NettingSet],
    positions: Sequence[Position],
    settlement_prices: Sequence[SettlementPrice],
    collateral_inventory: Sequence[CollateralLot],
    revision: int = 1,
    valuation_time_utc: Optional[str] = None,
    is_settlement_correction: bool = False,
) -> RunResult:
    """Execute a complete, deterministic margin calculation across all netting sets.

    Pure domain function: no disk, network, or database side effects.
    """
    price_map = validate_inputs(
        valuation_date=valuation_date,
        revision=revision,
        policy=policy,
        scenario_set=scenario_set,
        netting_sets=netting_sets,
        positions=positions,
        settlement_prices=settlement_prices,
        collateral_inventory=collateral_inventory,
    )

    val_time = valuation_time_utc or policy.valuation_time_utc
    policy_hash = content_hash(policy)
    scenario_hash = content_hash(scenario_set)
    input_hash = content_hash(
        {
            "valuation_date": valuation_date,
            "valuation_time_utc": val_time,
            "revision": revision,
            "is_settlement_correction": is_settlement_correction,
            "netting_sets": sorted(netting_sets, key=lambda x: x.netting_set_id),
            "positions": sorted(positions, key=lambda x: x.position_id),
            "settlement_prices": sorted(settlement_prices, key=lambda x: x.instrument_id),
            "collateral_inventory": sorted(collateral_inventory, key=lambda x: x.lot_id),
        }
    )
    run_id_digest = content_hash(
        {
            "valuation_date": valuation_date,
            "revision": revision,
            "policy_hash": policy_hash,
            "scenario_hash": scenario_hash,
            "input_hash": input_hash,
        }
    )[:16].upper()
    run_id = f"RUN-{valuation_date.strftime('%Y%m%d')}-R{revision}-{run_id_digest}"

    positions_by_ns: Dict[str, List[Position]] = {ns.netting_set_id: [] for ns in netting_sets}
    for pos in sorted(positions, key=lambda p: p.position_id):
        positions_by_ns[pos.netting_set_id].append(pos)

    lots_by_ns: Dict[str, List[CollateralLot]] = {ns.netting_set_id: [] for ns in netting_sets}
    for lot in sorted(collateral_inventory, key=lambda c: c.lot_id):
        lots_by_ns[lot.netting_set_id].append(lot)

    ns_results: List[NettingSetMarginResult] = []
    total_positions = 0
    total_scenarios = 0
    total_lots = 0
    total_ineligible = 0
    total_calls = 0

    for ns in sorted(netting_sets, key=lambda x: x.netting_set_id):
        ns_positions = positions_by_ns[ns.netting_set_id]
        ns_lots = lots_by_ns[ns.netting_set_id]

        (
            pos_vals,
            total_mtm,
            daily_mtm_movement,
            total_settled_adj,
            variation_margin,
        ) = value_netting_set_positions(ns, ns_positions, price_map, policy)

        im_res = compute_initial_margin(ns, ns_positions, price_map, scenario_set, policy)

        col_vals, gross_col, elig_col, inelig_cnt = value_collateral_inventory(
            ns, ns_lots, valuation_date, policy
        )

        (
            im_shortfall,
            im_excess,
            vm_call,
            vm_credit,
            total_call_due,
            calls,
        ) = generate_margin_calls(
            netting_set=ns,
            valuation_date=valuation_date,
            revision=revision,
            net_im=im_res.net_im,
            eligible_collateral_value=elig_col,
            variation_margin=variation_margin,
            policy=policy,
            is_settlement_correction=is_settlement_correction,
        )

        total_positions += len(pos_vals)
        total_scenarios += len(scenario_set.scenarios)
        total_lots += len(col_vals)
        total_ineligible += inelig_cnt
        total_calls += len(calls)

        ns_results.append(
            NettingSetMarginResult(
                netting_set_id=ns.netting_set_id,
                account_id=ns.account_id,
                base_currency=ns.base_currency,
                valuation_date=valuation_date,
                revision=revision,
                position_valuations=pos_vals,
                total_mtm=total_mtm,
                daily_mtm_movement=daily_mtm_movement,
                total_settled_cash_adjustment=total_settled_adj,
                variation_margin=variation_margin,
                im_result=im_res,
                collateral_valuations=col_vals,
                gross_collateral_value=gross_col,
                eligible_collateral_value=elig_col,
                im_shortfall=im_shortfall,
                im_excess=im_excess,
                vm_call=vm_call,
                vm_credit=vm_credit,
                total_call_due=total_call_due,
                margin_calls=calls,
            )
        )

        _log_event(
            "netting_set_evaluated",
            {
                "run_id": run_id,
                "valuation_date": valuation_date.isoformat(),
                "revision": revision,
                "netting_set_id": ns.netting_set_id,
                "variation_margin": str(variation_margin),
                "net_im": str(im_res.net_im),
                "eligible_collateral": str(elig_col),
                "im_shortfall": str(im_shortfall),
                "total_call_due": str(total_call_due),
                "calls_issued": len(calls),
            },
        )

    diagnostics = RunDiagnostics(
        positions_valued=total_positions,
        scenarios_evaluated=total_scenarios,
        collateral_lots_valued=total_lots,
        ineligible_collateral_lots=total_ineligible,
        margin_calls_issued=total_calls,
        duplicate_calls_blocked=0,
        journal_entries_posted=0,
    )

    result = RunResult(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        run_id=run_id,
        valuation_date=valuation_date,
        valuation_time_utc=val_time,
        revision=revision,
        policy_id=policy.policy_id,
        policy_hash=policy_hash,
        scenario_set_id=scenario_set.scenario_set_id,
        scenario_hash=scenario_hash,
        input_hash=input_hash,
        seed=scenario_set.seed,
        netting_set_results=tuple(ns_results),
        diagnostics=diagnostics,
        is_settlement_correction=is_settlement_correction,
    )

    _log_event(
        "margin_run_completed",
        {
            "run_id": run_id,
            "valuation_date": valuation_date.isoformat(),
            "revision": revision,
            "netting_sets": len(ns_results),
            "margin_calls_issued": total_calls,
            "ineligible_collateral_lots": total_ineligible,
        },
    )
    return result
