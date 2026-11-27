"""Raw SVI total-variance parameterization, Durrleman density, and bid-ask weighted calibration."""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np
from scipy.optimize import least_squares

from volatility_surface_engine.models import CalibrationError, SVIConfig, SVIParameters


def svi_total_variance(k: np.ndarray, params: SVIParameters) -> np.ndarray:
    """Evaluate raw SVI total implied variance w(k)."""
    k_arr = np.asarray(k, dtype=float)
    d = k_arr - params.m
    return params.a + params.b * (params.rho * d + np.sqrt(d * d + params.sigma * params.sigma))


def svi_derivatives(k: np.ndarray, params: SVIParameters) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return analytic (w(k), w'(k), w''(k)) for raw SVI."""
    k_arr = np.asarray(k, dtype=float)
    d = k_arr - params.m
    r = np.sqrt(d * d + params.sigma * params.sigma)
    w = params.a + params.b * (params.rho * d + r)
    dw = params.b * (params.rho + d / r)
    d2w = params.b * (params.sigma * params.sigma) / (r * r * r)
    return w, dw, d2w


def svi_implied_vol(k: np.ndarray, time_to_expiry: float, params: SVIParameters) -> np.ndarray:
    """Evaluate annualized implied volatility sqrt(w(k) / T)."""
    w = svi_total_variance(k, params)
    return np.sqrt(np.maximum(w, 1e-16) / time_to_expiry)


def durrleman_density(w: np.ndarray, dw: np.ndarray, d2w: np.ndarray, k: np.ndarray) -> np.ndarray:
    """Compute Durrleman's local density function g(k); g(k) >= 0 implies no butterfly arbitrage."""
    k_arr = np.asarray(k, dtype=float)
    w_safe = np.maximum(w, 1e-12)
    term1 = (1.0 - (k_arr * dw) / (2.0 * w_safe)) ** 2
    term2 = 0.25 * (dw * dw) * (1.0 / w_safe + 0.25)
    term3 = 0.5 * d2w
    return term1 - term2 + term3


def svi_minimum_variance(params: SVIParameters) -> float:
    """Global minimum of raw SVI total variance over all real k."""
    return params.a + params.b * params.sigma * math.sqrt(max(0.0, 1.0 - params.rho * params.rho))


def fit_svi_slice(
    k_vals: np.ndarray,
    iv_vals: np.ndarray,
    weights: np.ndarray,
    time_to_expiry: float,
    config: SVIConfig,
    seed: int = 42,
) -> Tuple[SVIParameters, float, float, float, bool, int]:
    """Fit raw SVI parameters to a single expiry slice with bid-ask weighting.

    Returns:
        (params, weighted_rmse_iv, unweighted_rmse_iv, max_abs_err_iv, converged, total_nfev)
    """
    k_arr = np.asarray(k_vals, dtype=float)
    iv_arr = np.asarray(iv_vals, dtype=float)
    w_arr = np.asarray(weights, dtype=float)

    if len(k_arr) < config.min_slice_quotes:
        raise CalibrationError(
            f"Insufficient quotes ({len(k_arr)}) for SVI slice calibration; minimum required is {config.min_slice_quotes}.",
            details={"quote_count": int(len(k_arr)), "min_slice_quotes": config.min_slice_quotes},
        )
    if not np.all(np.isfinite(k_arr)) or not np.all(np.isfinite(iv_arr)) or not np.all(np.isfinite(w_arr)):
        raise CalibrationError("Non-finite inputs supplied to SVI calibration.")
    if np.any(w_arr <= 0.0):
        raise CalibrationError("Calibration weights must be strictly positive.")

    norm_w = w_arr / np.sum(w_arr)
    sqrt_w = np.sqrt(norm_w)
    total_var = (iv_arr ** 2) * time_to_expiry

    k_min = float(np.min(k_arr))
    k_max = float(np.max(k_arr))
    k_span = max(k_max - k_min, 0.2)
    penalty_grid = np.linspace(k_min - 0.1 * k_span, k_max + 0.1 * k_span, 41)

    # Parameter bounds: [a, b, rho, m, sigma]
    max_w = float(np.max(total_var))
    lower = np.array([-max(2.0 * max_w, 0.5), 1e-6, -0.995, k_min - 0.5 * k_span, 1e-4], dtype=float)
    upper = np.array([max(4.0 * max_w, 2.0), 1.99, 0.995, k_max + 0.5 * k_span, max(2.0 * k_span, 1.5)], dtype=float)

    # Heuristic initial guess anchored at minimum observed variance
    min_idx = int(np.argmin(total_var))
    w_min_obs = float(total_var[min_idx])
    m0 = float(np.clip(k_arr[min_idx], lower[3] + 1e-4, upper[3] - 1e-4))
    sigma0 = float(np.clip(0.25 * k_span, 0.02, 0.5))

    # Estimate wing slope from endpoints
    left_slope = abs(total_var[0] - w_min_obs) / max(abs(k_arr[0] - m0), 0.05)
    right_slope = abs(total_var[-1] - w_min_obs) / max(abs(k_arr[-1] - m0), 0.05)
    b0 = float(np.clip(0.5 * (left_slope + right_slope), 0.01, 1.0))
    rho0 = float(np.clip((right_slope - left_slope) / max(right_slope + left_slope, 1e-6), -0.8, 0.8))
    a0 = float(np.clip(w_min_obs - b0 * sigma0 * math.sqrt(max(1e-4, 1.0 - rho0 * rho0)), lower[0] + 1e-6, upper[0] - 1e-6))

    base_x0 = np.array([a0, b0, rho0, m0, sigma0], dtype=float)

    penalty_scale = math.sqrt(max(config.butterfly_penalty_weight, 0.0))

    def _residuals(x: np.ndarray) -> np.ndarray:
        p = SVIParameters(a=float(x[0]), b=float(x[1]), rho=float(x[2]), m=float(x[3]), sigma=float(x[4]))
        w_pred = svi_total_variance(k_arr, p)

        # Data residuals in implied volatility space (bid-ask weighted)
        w_pos = np.maximum(w_pred, 1e-12)
        iv_pred = np.sqrt(w_pos / time_to_expiry)
        data_res = sqrt_w * (iv_pred - iv_arr)

        # Positivity & Lee wing bound penalties
        w_global_min = svi_minimum_variance(p)
        pos_pen = 50.0 * max(0.0, 1e-7 - w_global_min)
        lee_pen = 50.0 * max(0.0, p.b * (1.0 + abs(p.rho)) - 1.98)
        neg_w_pen = 50.0 * np.maximum(0.0, 1e-7 - w_pred)

        if penalty_scale > 0.0:
            w_g, dw_g, d2w_g = svi_derivatives(penalty_grid, p)
            g_vals = durrleman_density(w_g, dw_g, d2w_g, penalty_grid)
            bf_pen = penalty_scale * np.maximum(0.0, -g_vals)
            return np.concatenate([data_res, neg_w_pen, np.array([pos_pen, lee_pen]), bf_pen])

        return np.concatenate([data_res, neg_w_pen, np.array([pos_pen, lee_pen])])

    rng = np.random.default_rng(seed)
    starts = [base_x0]
    for _ in range(config.num_restarts - 1):
        cand_rho = float(rng.uniform(-0.85, 0.85))
        cand_b = float(np.clip(b0 * math.exp(rng.normal(0.0, 0.6)), 0.005, 1.4 / (1.0 + abs(cand_rho))))
        cand_sigma = float(np.clip(sigma0 * math.exp(rng.normal(0.0, 0.5)), 0.01, upper[4] * 0.8))
        cand_m = float(np.clip(m0 + rng.normal(0.0, 0.25 * k_span), lower[3] + 1e-4, upper[3] - 1e-4))
        cand_a = float(
            np.clip(
                w_min_obs - cand_b * cand_sigma * math.sqrt(max(1e-4, 1.0 - cand_rho * cand_rho))
                + rng.normal(0.0, 0.1 * max(w_min_obs, 1e-4)),
                lower[0] + 1e-6,
                upper[0] - 1e-6,
            )
        )
        starts.append(np.array([cand_a, cand_b, cand_rho, cand_m, cand_sigma], dtype=float))

    best_cost = float("inf")
    best_x: np.ndarray = base_x0
    best_converged = False
    total_nfev = 0

    for x0 in starts:
        x0_clipped = np.clip(x0, lower + 1e-8, upper - 1e-8)
        res = least_squares(
            _residuals,
            x0=x0_clipped,
            bounds=(lower, upper),
            method="trf",
            ftol=1e-12,
            xtol=1e-12,
            gtol=1e-12,
            max_nfev=config.max_nfev,
        )
        total_nfev += int(res.nfev)
        if np.isfinite(res.cost) and res.cost < best_cost:
            best_cost = float(res.cost)
            best_x = res.x.copy()
            best_converged = bool(res.success)

    best_params = SVIParameters(
        a=float(best_x[0]),
        b=float(best_x[1]),
        rho=float(best_x[2]),
        m=float(best_x[3]),
        sigma=float(best_x[4]),
    )
    iv_fit = svi_implied_vol(k_arr, time_to_expiry, best_params)
    errors = iv_fit - iv_arr
    weighted_rmse = float(np.sqrt(np.sum(norm_w * (errors ** 2))))
    unweighted_rmse = float(np.sqrt(np.mean(errors ** 2)))
    max_abs_err = float(np.max(np.abs(errors)))
    return best_params, weighted_rmse, unweighted_rmse, max_abs_err, best_converged, total_nfev
