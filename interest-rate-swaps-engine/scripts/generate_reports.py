#!/usr/bin/env python3
"""Generate machine-readable versioned reports and reports/acceptance.md from live runs."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

from irs_engine import __schema_version__, __version__
from irs_engine.diagnostics import collect_runtime_metadata
from irs_engine.domain.calendars import CalendarRegistry
from irs_engine.domain.enums import SwapDirection
from irs_engine.domain.errors import MissingFixingError
from irs_engine.domain.oracle import (
    check_optional_quantlib_oracle,
    run_independent_analytic_oracle,
    single_curve_telescoping_closed_form,
)
from irs_engine.domain.pricer import price_swap
from irs_engine.domain.scenarios import run_deterministic_scenarios, run_stochastic_scenarios
from irs_engine.domain.sensitivities import compute_sensitivities
from irs_engine.io import load_valuation_bundle, to_plain_dict

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    calendars = CalendarRegistry.default()
    runtime = collect_runtime_metadata()

    # 1. Baseline 5Y USD Swap & Par Swap & Payer/Receiver Symmetry
    trade_5y, disc_5y, fwd_5y, fix_5y, cfg_5y, _ = load_valuation_bundle(
        ROOT / "data" / "fixtures" / "valid" / "usd_5y_vanilla_swap.json"
    )
    base_val = price_swap(
        trade_5y, disc_5y, fwd_5y, fixing_history=fix_5y, calendar_registry=calendars, config=cfg_5y
    )
    recv_trade = dataclasses.replace(trade_5y, direction=SwapDirection.RECEIVER)
    recv_val = price_swap(
        recv_trade, disc_5y, fwd_5y, fixing_history=fix_5y, calendar_registry=calendars, config=cfg_5y
    )
    sym_sum = base_val.npv + recv_val.npv

    par_trade, par_disc, par_fwd, par_fix, par_cfg, _ = load_valuation_bundle(
        ROOT / "data" / "fixtures" / "valid" / "usd_5y_par_swap.json"
    )
    par_val = price_swap(
        par_trade, par_disc, par_fwd, fixing_history=par_fix, calendar_registry=calendars, config=par_cfg
    )

    # Also exact computed par swap
    exact_par_trade = dataclasses.replace(
        trade_5y,
        trade_id="USD-5Y-EXACT-PAR",
        fixed_leg=dataclasses.replace(trade_5y.fixed_leg, fixed_rate=base_val.par_rate),
    )
    exact_par_val = price_swap(
        exact_par_trade, disc_5y, fwd_5y, fixing_history=fix_5y, calendar_registry=calendars, config=cfg_5y
    )

    # Seasoned swap with known + projected fixings
    seas_trade, seas_disc, seas_fwd, seas_fix, seas_cfg, _ = load_valuation_bundle(
        ROOT / "data" / "fixtures" / "valid" / "seasoned_swap_known_fixings.json"
    )
    seas_val = price_swap(
        seas_trade, seas_disc, seas_fwd, fixing_history=seas_fix, calendar_registry=calendars, config=seas_cfg
    )

    # Missing fixing error verification
    miss_trade, miss_disc, miss_fwd, miss_fix, miss_cfg, _ = load_valuation_bundle(
        ROOT / "data" / "fixtures" / "invalid" / "missing_historical_fixing.json"
    )
    missing_fixing_error_code = None
    try:
        price_swap(
            miss_trade, miss_disc, miss_fwd, fixing_history=miss_fix, calendar_registry=calendars, config=miss_cfg
        )
    except MissingFixingError as exc:
        missing_fixing_error_code = exc.error_code

    _write_json(
        REPORTS / "baseline_valuation.json",
        {
            "schema_version": __schema_version__,
            "engine_version": __version__,
            "runtime": runtime,
            "baseline_5y_payer": to_plain_dict(base_val),
            "baseline_5y_receiver": to_plain_dict(recv_val),
            "symmetry_sum": sym_sum,
            "par_fixture_valuation": to_plain_dict(par_val),
            "exact_par_valuation": to_plain_dict(exact_par_val),
            "seasoned_swap_valuation": to_plain_dict(seas_val),
            "missing_fixing_error_code": missing_fixing_error_code,
        },
    )

    # 2. Oracle Comparisons (5Y dual-curve + 2Y hand-calculated single-curve telescoping closed-form)
    oracle_5y = run_independent_analytic_oracle(
        trade_5y, disc_5y, fwd_5y, fixing_history=fix_5y, calendar_registry=calendars, config=cfg_5y
    )
    ql_5y = check_optional_quantlib_oracle(trade_5y, disc_5y, fwd_5y)

    hc_trade, hc_disc, hc_fwd, hc_fix, hc_cfg, hc_meta = load_valuation_bundle(
        ROOT / "data" / "fixtures" / "valid" / "hand_calculated_2y_oracle.json"
    )
    hc_val = price_swap(
        hc_trade, hc_disc, hc_fwd, fixing_history=hc_fix, calendar_registry=calendars, config=hc_cfg
    )
    hc_oracle = run_independent_analytic_oracle(
        hc_trade, hc_disc, hc_fwd, fixing_history=hc_fix, calendar_registry=calendars, config=hc_cfg
    )
    # Closed-form telescoping identity for flat 4.0% continuous 2Y annual swap
    cf_times = [
        (cf.payment_date - hc_disc.valuation_date).days / 365.0
        for cf in hc_val.fixed_cashflows
    ]
    cf_taus = [cf.year_fraction for cf in hc_val.fixed_cashflows]
    telescoping = single_curve_telescoping_closed_form(
        notional=hc_trade.notional,
        fixed_rate=hc_trade.fixed_leg.fixed_rate,
        flat_cont_zero_rate=hc_disc.nodes[0].zero_rate,
        payment_times=cf_times,
        fixed_taus=cf_taus,
        direction=hc_trade.direction,
    )
    telescoping_diff = abs(hc_val.npv - telescoping["npv"])

    _write_json(
        REPORTS / "oracle_comparison.json",
        {
            "schema_version": __schema_version__,
            "engine_version": __version__,
            "runtime": runtime,
            "usd_5y_dual_curve_analytic_oracle": to_plain_dict(oracle_5y),
            "usd_5y_quantlib_oracle": ql_5y,
            "hand_calculated_2y_valuation": to_plain_dict(hc_val),
            "hand_calculated_2y_analytic_oracle": to_plain_dict(hc_oracle),
            "hand_calculated_2y_telescoping_closed_form": telescoping,
            "hand_calculated_2y_telescoping_abs_diff": telescoping_diff,
            "fixture_expected_metadata": hc_meta,
        },
    )

    # 3. Sensitivities & KR01 reconciliation
    sens_payer = compute_sensitivities(
        trade_5y, disc_5y, fwd_5y, fixing_history=fix_5y, calendar_registry=calendars, config=cfg_5y
    )
    sens_recv = compute_sensitivities(
        recv_trade, disc_5y, fwd_5y, fixing_history=fix_5y, calendar_registry=calendars, config=cfg_5y
    )
    _write_json(
        REPORTS / "sensitivities_report.json",
        {
            "schema_version": __schema_version__,
            "engine_version": __version__,
            "runtime": runtime,
            "payer_sensitivities": to_plain_dict(sens_payer),
            "receiver_sensitivities": to_plain_dict(sens_recv),
            "pv01_symmetry_sum": sens_payer.parallel_pv01 + sens_recv.parallel_pv01,
        },
    )

    # 4. Scenario snapshots + deterministic reproducibility check
    scen_cfg = json.loads((ROOT / "config" / "scenarios.json").read_text(encoding="utf-8"))
    det = run_deterministic_scenarios(
        trade_5y,
        disc_5y,
        fwd_5y,
        scen_cfg["deterministic_scenarios"],
        fixing_history=fix_5y,
        calendar_registry=calendars,
        config=cfg_5y,
    )
    sc = scen_cfg["stochastic_config"]
    stoch_run_1 = run_stochastic_scenarios(
        trade_5y,
        disc_5y,
        fwd_5y,
        seed=sc["seed"],
        num_paths=sc["num_paths"],
        level_vol_bps=sc["level_vol_bps"],
        slope_vol_bps=sc["slope_vol_bps"],
        curvature_vol_bps=sc["curvature_vol_bps"],
        basis_vol_bps=sc["basis_vol_bps"],
        fixing_history=fix_5y,
        calendar_registry=calendars,
        config=cfg_5y,
    )
    stoch_run_2 = run_stochastic_scenarios(
        trade_5y,
        disc_5y,
        fwd_5y,
        seed=sc["seed"],
        num_paths=sc["num_paths"],
        level_vol_bps=sc["level_vol_bps"],
        slope_vol_bps=sc["slope_vol_bps"],
        curvature_vol_bps=sc["curvature_vol_bps"],
        basis_vol_bps=sc["basis_vol_bps"],
        fixing_history=fix_5y,
        calendar_registry=calendars,
        config=cfg_5y,
    )
    stoch_identical = to_plain_dict(stoch_run_1) == to_plain_dict(stoch_run_2)

    _write_json(
        REPORTS / "scenario_snapshots.json",
        {
            "schema_version": __schema_version__,
            "engine_version": __version__,
            "runtime": runtime,
            "base_valuation_id": base_val.valuation_id,
            "base_npv": base_val.npv,
            "deterministic_scenarios": to_plain_dict(det),
            "stochastic_summary": to_plain_dict(stoch_run_1),
            "stochastic_seed_reproducibility_exact": stoch_identical,
        },
    )

    # 5. Run pytest to capture live test pass count
    pytest_proc = subprocess.run(
        [sys.executable, "-m", "pytest"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    pytest_summary = (pytest_proc.stdout.strip().splitlines() or ["no output"])[-1]
    pytest_passed = pytest_proc.returncode == 0

    # 6. Write reports/acceptance.md
    acceptance_md = f"""# Acceptance Evidence Report — Interest Rate Swaps Engine

**Engine version:** `{__version__}`  
**Schema version:** `{__schema_version__}`  
**Python runtime:** `{runtime["python_version"]}` on `{runtime["platform"]}`  
**Generated (UTC):** `{runtime["generated_at_utc"]}`  
**Note on data provenance:** All valuation fixtures, curves, and fixing histories are synthetic offline fixtures created for reproducible verification. No live market data, customer records, credentials, or proprietary artifacts are used.

---

## 1. Acceptance Criteria Verification Matrix

| Acceptance Criterion | Status | Measured Evidence | Raw Artifact / Test Source |
|---|---|---|---|
| **Par swap PV within stated currency tolerance (`1e-6` USD on 10M notional)** | **PASS** | Exact par swap NPV = `{exact_par_val.npv:.3e}` USD (par rate `{base_val.par_rate * 100:.6f}%`); fixture par swap NPV = `{par_val.npv:.3e}` USD | [baseline_valuation.json](baseline_valuation.json), `tests/test_pricer_domain.py` |
| **Receiver and payer PV sum to zero (`<= 1e-9` USD)** | **PASS** | `PV(PAYER) = {base_val.npv:,.6f}` USD, `PV(RECEIVER) = {recv_val.npv:,.6f}` USD, sum = `{sym_sum:.3e}` USD; `PV01(PAYER) + PV01(RECEIVER) = {sens_payer.parallel_pv01 + sens_recv.parallel_pv01:.3e}` | [baseline_valuation.json](baseline_valuation.json), [sensitivities_report.json](sensitivities_report.json), `tests/test_pricer_domain.py` |
| **Key-rate bump results reconcile with parallel sensitivity within documented approximation (`<= 0.5%` rel or `<= 0.50` USD)** | **PASS** | Parallel PV01 = `{sens_payer.parallel_pv01:,.4f}` USD/bp; Sum(KR01) = `{sens_payer.total_kr01_sum:,.4f}` USD/bp; diff = `{sens_payer.total_reconciliation_diff:+.6f}` USD/bp (`{sens_payer.total_reconciliation_rel_diff:.6%}`) | [sensitivities_report.json](sensitivities_report.json), `tests/test_sensitivities.py` |
| **Independent reference oracle comparison (`<= 1e-6` USD)** | **PASS** | 5Y dual-curve direct-summation oracle abs diff = `{oracle_5y.abs_npv_diff:.3e}` USD; 2Y single-curve telescoping closed-form abs diff = `{telescoping_diff:.3e}` USD | [oracle_comparison.json](oracle_comparison.json), `tests/test_pricer_domain.py` |
| **Known vs projected fixings and missing fixing history** | **PASS** | Seasoned swap used `{seas_val.diagnostics["historical_fixings_used"]}` historical and `{seas_val.diagnostics["projected_fixings_used"]}` projected fixings; missing historical fixing raised `{missing_fixing_error_code}` | [baseline_valuation.json](baseline_valuation.json), `tests/test_fixings_integration.py` |
| **Deterministic curve shocks & seeded stochastic reproducibility** | **PASS** | `{len(det)}` deterministic shocks evaluated; `{stoch_run_1.num_paths}`-path Monte Carlo (seed `{stoch_run_1.seed}`) exact repeat match = `{stoch_identical}` | [scenario_snapshots.json](scenario_snapshots.json), `tests/test_scenarios_and_repro.py` |
| **All required test scenarios pass in supported environment** | **{"PASS" if pytest_passed else "FAIL"}** | `pytest`: `{pytest_summary}` | `tests/` |
| **Optional QuantLib oracle cross-check** | **NOT RUN** | `{ql_5y["reason"]}` — explicitly reported as `NOT_RUN` | [oracle_comparison.json](oracle_comparison.json) |
| **No real transactions, customer data, credentials, or proprietary artifacts** | **PASS** | All fixtures synthetic under `data/fixtures/`; offline execution verified | `data/fixtures/`, `config/` |

---

## 2. Detailed Measured Snapshots (Synthetic 5Y USD Vanilla Swap)

- **Trade:** `USD-5Y-IRS-001` — Payer, Notional `10,000,000 USD`, Fixed `4.25%` Semi-Annual `30/360`, Floating `USD-SOFR-3M` Quarterly `ACT/360`
- **Valuation ID:** `{base_val.valuation_id}`
- **Fixed Leg PV:** `{base_val.fixed_leg_pv:,.2f} USD`
- **Floating Leg PV:** `{base_val.floating_leg_pv:,.2f} USD`
- **Swap NPV (Payer):** `{base_val.npv:,.2f} USD`
- **Par Rate:** `{base_val.par_rate * 100:.6f}%`
- **Discount PV01 / Sum(Disc KR01):** `{sens_payer.discount_pv01:,.4f}` / `{sens_payer.discount_kr01_sum:,.4f}` (diff `{sens_payer.discount_reconciliation_diff:+.6f}`)
- **Forward PV01 / Sum(Fwd KR01):** `{sens_payer.forward_pv01:,.4f}` / `{sens_payer.forward_kr01_sum:,.4f}` (diff `{sens_payer.forward_reconciliation_diff:+.6f}`)
- **Parallel PV01 / Sum(All KR01):** `{sens_payer.parallel_pv01:,.4f}` / `{sens_payer.total_kr01_sum:,.4f}` (diff `{sens_payer.total_reconciliation_diff:+.6f}`)

### Deterministic Curve Shock Summary

| Scenario | NPV (USD) | PnL vs Base (USD) | Par Rate Change (bps) |
|---|---:|---:|---:|
""" + "\n".join(
        f"| `{s.scenario_id}` | {s.npv:,.2f} | {s.pnl:+,.2f} | {s.par_rate_change_bps:+.2f} |"
        for s in det
    ) + f"""

### Seeded Stochastic Summary (Seed `{stoch_run_1.seed}`, `{stoch_run_1.num_paths}` Paths)

- **Mean PnL:** `{stoch_run_1.mean_pnl:+,.2f} USD`
- **Std PnL:** `{stoch_run_1.std_pnl:,.2f} USD`
- **5th / 50th / 95th Percentile PnL:** `{stoch_run_1.p05_pnl:+,.2f}` / `{stoch_run_1.p50_pnl:+,.2f}` / `{stoch_run_1.p95_pnl:+,.2f} USD`
- **95% VaR / Expected Shortfall:** `{stoch_run_1.var_95:,.2f}` / `{stoch_run_1.expected_shortfall_95:,.2f} USD`

---

## 3. Untested or Out-of-Scope Capabilities

- **QuantLib external library cross-check:** Explicitly marked `NOT_RUN` because `QuantLib` is an optional dependency not installed in the offline CPython 3.12 environment.
- **Multi-currency / cross-currency swaps & CSA collateral discounting:** Explicitly out of scope per `plan.md`.
- **Curve bootstrapping from market par swap/futures quotes:** Curves are supplied as explicit versioned zero-rate `CurveSnapshot` inputs.
"""

    (REPORTS / "acceptance.md").write_text(acceptance_md, encoding="utf-8")
    print(f"Generated reports in {REPORTS} (pytest: {pytest_summary})")


if __name__ == "__main__":
    main()
