"""Static arbitrage verification: butterfly (Durrleman + call convexity) and calendar variance checks."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import numpy as np

from volatility_surface_engine.black76 import black76_price
from volatility_surface_engine.models import (
    ArbitrageCheckResult,
    ArbitrageConfig,
    ArbitrageViolation,
    SliceCalibrationResult,
)
from volatility_surface_engine.svi import durrleman_density, svi_derivatives, svi_total_variance


def interpolate_slice_variance_and_derivs(
    slices: List[SliceCalibrationResult],
    k_vals: np.ndarray,
    time_to_expiry: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """Evaluate piecewise-linear total variance w(k, T) and spatial derivatives (w, w', w''), plus (F(T), DF(T))."""
    ordered = sorted(slices, key=lambda s: s.time_to_expiry_years)
    k_arr = np.asarray(k_vals, dtype=float)

    # Exact match or single slice
    for sl in ordered:
        if abs(time_to_expiry - sl.time_to_expiry_years) <= 1e-12:
            w, dw, d2w = svi_derivatives(k_arr, sl.svi_params)
            return w, dw, d2w, sl.forward, sl.discount_factor

    # Find bracketing slices
    for i in range(len(ordered) - 1):
        s0 = ordered[i]
        s1 = ordered[i + 1]
        if s0.time_to_expiry_years <= time_to_expiry <= s1.time_to_expiry_years:
            lam = (time_to_expiry - s0.time_to_expiry_years) / (s1.time_to_expiry_years - s0.time_to_expiry_years)
            w0, dw0, d2w0 = svi_derivatives(k_arr, s0.svi_params)
            w1, dw1, d2w1 = svi_derivatives(k_arr, s1.svi_params)
            w = (1.0 - lam) * w0 + lam * w1
            dw = (1.0 - lam) * dw0 + lam * dw1
            d2w = (1.0 - lam) * d2w0 + lam * d2w1
            fwd = math.exp((1.0 - lam) * math.log(s0.forward) + lam * math.log(s1.forward))
            df = math.exp((1.0 - lam) * math.log(s0.discount_factor) + lam * math.log(s1.discount_factor))
            return w, dw, d2w, fwd, df

    raise ValueError(f"Time {time_to_expiry} lies outside calibrated expiry range.")


def check_surface_arbitrage(
    slices: List[SliceCalibrationResult],
    config: ArbitrageConfig,
) -> ArbitrageCheckResult:
    """Validate butterfly and calendar no-arbitrage conditions across the calibrated domain."""
    if not slices:
        return ArbitrageCheckResult(
            is_valid=False,
            butterfly_violation_count=0,
            calendar_violation_count=0,
            min_butterfly_density=float("nan"),
            min_call_convexity=float("nan"),
            min_calendar_variance_diff=float("nan"),
            k_grid_points=config.k_grid_points,
            t_grid_points=config.t_grid_points,
            violations=[],
        )

    ordered = sorted(slices, key=lambda s: s.time_to_expiry_years)
    violations: List[ArbitrageViolation] = []
    butterfly_count = 0
    calendar_count = 0

    min_density = float("inf")
    min_convexity = float("inf")
    min_cal_diff = float("inf")

    # 1. Butterfly checks on every calibrated expiry slice AND intermediate maturities
    eval_times: List[Tuple[float, Optional[str], float, float]] = [
        (s.time_to_expiry_years, s.expiry_timestamp, s.k_min, s.k_max) for s in ordered
    ]

    if len(ordered) >= 2:
        t_min = ordered[0].time_to_expiry_years
        t_max = ordered[-1].time_to_expiry_years
        t_grid = np.linspace(t_min, t_max, config.t_grid_points)
        for t_val in t_grid:
            # Skip points identical to calibrated slices
            if any(abs(float(t_val) - s.time_to_expiry_years) <= 1e-12 for s in ordered):
                continue
            # Find bracketing slices to determine valid k-domain
            for i in range(len(ordered) - 1):
                if ordered[i].time_to_expiry_years <= t_val <= ordered[i + 1].time_to_expiry_years:
                    k_lo = max(ordered[i].k_min, ordered[i + 1].k_min)
                    k_hi = min(ordered[i].k_max, ordered[i + 1].k_max)
                    if k_hi > k_lo:
                        eval_times.append((float(t_val), None, k_lo, k_hi))
                    break

    for t_val, exp_ts, k_lo, k_hi in eval_times:
        if k_hi <= k_lo:
            continue
        k_grid = np.linspace(k_lo, k_hi, config.k_grid_points)
        w, dw, d2w, fwd, df = interpolate_slice_variance_and_derivs(ordered, k_grid, t_val)

        # 1a. Positivity & Durrleman density check
        g_vals = durrleman_density(w, dw, d2w, k_grid)
        # Also treat non-positive total variance as butterfly/density failure
        effective_g = np.where(w <= 0.0, -np.abs(w) - 1.0, g_vals)
        local_min_g = float(np.min(effective_g))
        if local_min_g < min_density:
            min_density = local_min_g

        bad_g_idx = np.where(effective_g < config.butterfly_density_tol)[0]
        if len(bad_g_idx) > 0:
            worst = int(bad_g_idx[int(np.argmin(effective_g[bad_g_idx]))])
            butterfly_count += len(bad_g_idx)
            k_bad = float(k_grid[worst])
            violations.append(
                ArbitrageViolation(
                    kind="butterfly_density",
                    expiry_timestamp=exp_ts,
                    time_to_expiry_years=t_val,
                    k=k_bad,
                    strike=fwd * math.exp(k_bad),
                    magnitude=float(effective_g[worst]),
                    threshold=config.butterfly_density_tol,
                    detail=(
                        f"Durrleman density g(k)={effective_g[worst]:.6e} < {config.butterfly_density_tol:.6e} "
                        f"at T={t_val:.6f}, k={k_bad:.4f} (w={w[worst]:.6e}; {len(bad_g_idx)} grid points violated)."
                    ),
                )
            )

        # 1b. Discrete Black-76 call butterfly convexity d^2 C / dK^2 >= 0 on uniform strike grid
        strikes = np.linspace(fwd * math.exp(k_lo), fwd * math.exp(k_hi), config.k_grid_points)
        dk = float(strikes[1] - strikes[0])
        k_for_strikes = np.log(strikes / fwd)
        w_s, _, _, _, _ = interpolate_slice_variance_and_derivs(ordered, k_for_strikes, t_val)
        vols = np.sqrt(np.maximum(w_s, 1e-16) / t_val)
        call_prices = np.array(
            [black76_price(fwd, float(K), t_val, float(v), df, "call") for K, v in zip(strikes, vols)],
            dtype=float,
        )
        second_diff = (call_prices[:-2] - 2.0 * call_prices[1:-1] + call_prices[2:]) / (dk * dk)
        # Scale-invariant dimensionless density: F * (d^2 C / dK^2) / DF
        norm_convexity = (fwd / df) * second_diff
        local_min_conv = float(np.min(norm_convexity))
        if local_min_conv < min_convexity:
            min_convexity = local_min_conv

        bad_c_idx = np.where(norm_convexity < config.butterfly_call_convexity_tol)[0]
        if len(bad_c_idx) > 0:
            worst = int(bad_c_idx[int(np.argmin(norm_convexity[bad_c_idx]))])
            butterfly_count += len(bad_c_idx)
            k_strike = float(strikes[worst + 1])
            violations.append(
                ArbitrageViolation(
                    kind="butterfly_call_convexity",
                    expiry_timestamp=exp_ts,
                    time_to_expiry_years=t_val,
                    k=math.log(k_strike / fwd),
                    strike=k_strike,
                    magnitude=float(norm_convexity[worst]),
                    threshold=config.butterfly_call_convexity_tol,
                    detail=(
                        f"Normalized call butterfly convexity {norm_convexity[worst]:.6e} < "
                        f"{config.butterfly_call_convexity_tol:.6e} at T={t_val:.6f}, K={k_strike:.4f} "
                        f"({len(bad_c_idx)} strike triplets violated)."
                    ),
                )
            )

    # 2. Calendar arbitrage checks: w(k, T_{i+1}) >= w(k, T_i) on overlapping log-moneyness domain
    if len(ordered) >= 2:
        for i in range(len(ordered) - 1):
            s0 = ordered[i]
            s1 = ordered[i + 1]
            k_lo = max(s0.k_min, s1.k_min)
            k_hi = min(s0.k_max, s1.k_max)
            if k_hi <= k_lo:
                continue
            k_grid = np.linspace(k_lo, k_hi, config.k_grid_points)
            w0 = svi_total_variance(k_grid, s0.svi_params)
            w1 = svi_total_variance(k_grid, s1.svi_params)
            diff = w1 - w0
            local_min_diff = float(np.min(diff))
            if local_min_diff < min_cal_diff:
                min_cal_diff = local_min_diff

            bad_cal = np.where(diff < config.calendar_variance_tol)[0]
            if len(bad_cal) > 0:
                worst = int(bad_cal[int(np.argmin(diff[bad_cal]))])
                calendar_count += len(bad_cal)
                k_bad = float(k_grid[worst])
                violations.append(
                    ArbitrageViolation(
                        kind="calendar_variance",
                        expiry_timestamp=s1.expiry_timestamp,
                        time_to_expiry_years=s1.time_to_expiry_years,
                        k=k_bad,
                        strike=s1.forward * math.exp(k_bad),
                        magnitude=float(diff[worst]),
                        threshold=config.calendar_variance_tol,
                        detail=(
                            f"Calendar total-variance decrease w(k, T={s1.time_to_expiry_years:.4f}) - "
                            f"w(k, T={s0.time_to_expiry_years:.4f}) = {diff[worst]:.6e} < "
                            f"{config.calendar_variance_tol:.6e} at k={k_bad:.4f} ({len(bad_cal)} points violated)."
                        ),
                    )
                )
    else:
        min_cal_diff = 0.0

    is_valid = butterfly_count == 0 and calendar_count == 0
    return ArbitrageCheckResult(
        is_valid=is_valid,
        butterfly_violation_count=butterfly_count,
        calendar_violation_count=calendar_count,
        min_butterfly_density=0.0 if not math.isfinite(min_density) else min_density,
        min_call_convexity=0.0 if not math.isfinite(min_convexity) else min_convexity,
        min_calendar_variance_diff=0.0 if not math.isfinite(min_cal_diff) else min_cal_diff,
        k_grid_points=config.k_grid_points,
        t_grid_points=config.t_grid_points,
        violations=violations,
    )
