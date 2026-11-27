"""Hagan (2002) SABR implied volatility formula and bid-ask weighted benchmark calibration."""

from __future__ import annotations

import math
from typing import Tuple

import numpy as np
from scipy.optimize import least_squares

from volatility_surface_engine.models import CalibrationError, SABRConfig, SABRParameters


def sabr_implied_vol_scalar(
    forward: float,
    strike: float,
    time_to_expiry: float,
    params: SABRParameters,
) -> float:
    """Compute Hagan (2002) lognormal implied volatility for a single strike."""
    alpha = params.alpha
    beta = params.beta
    rho = params.rho
    nu = params.nu

    if alpha <= 0.0 or forward <= 0.0 or strike <= 0.0 or time_to_expiry <= 0.0:
        return 1e-8

    one_minus_beta = 1.0 - beta
    if abs(forward - strike) / forward < 1e-7:
        f_pow = forward ** one_minus_beta
        term1 = ((one_minus_beta ** 2) / 24.0) * (alpha * alpha) / (f_pow * f_pow)
        term2 = 0.25 * (rho * beta * nu * alpha) / f_pow
        term3 = ((2.0 - 3.0 * rho * rho) / 24.0) * (nu * nu)
        return max((alpha / f_pow) * (1.0 + (term1 + term2 + term3) * time_to_expiry), 1e-8)

    log_fk = math.log(forward / strike)
    fk_beta = (forward * strike) ** (0.5 * one_minus_beta)
    z = (nu / alpha) * fk_beta * log_fk

    if abs(z) < 1e-7:
        z_over_xz = 1.0
    else:
        disc = max(1.0 - 2.0 * rho * z + z * z, 1e-16)
        num = math.sqrt(disc) + z - rho
        den = max(1.0 - rho, 1e-12)
        if num <= 0.0:
            return 1e-8
        xz = math.log(num / den)
        z_over_xz = 1.0 if abs(xz) < 1e-12 else (z / xz)

    log2 = log_fk * log_fk
    denom = fk_beta * (1.0 + ((one_minus_beta ** 2) / 24.0) * log2 + ((one_minus_beta ** 4) / 1920.0) * log2 * log2)
    t1 = ((one_minus_beta ** 2) / 24.0) * (alpha * alpha) / (fk_beta * fk_beta)
    t2 = 0.25 * (rho * beta * nu * alpha) / fk_beta
    t3 = ((2.0 - 3.0 * rho * rho) / 24.0) * (nu * nu)
    vol = (alpha / denom) * z_over_xz * (1.0 + (t1 + t2 + t3) * time_to_expiry)
    return max(vol, 1e-8)


def sabr_implied_vol(
    forward: float,
    strikes: np.ndarray,
    time_to_expiry: float,
    params: SABRParameters,
) -> np.ndarray:
    """Vectorized Hagan (2002) SABR implied volatility over an array of strikes."""
    k_arr = np.asarray(strikes, dtype=float)
    return np.array(
        [sabr_implied_vol_scalar(forward, float(k), time_to_expiry, params) for k in k_arr],
        dtype=float,
    )


def fit_sabr_slice(
    forward: float,
    strikes: np.ndarray,
    iv_vals: np.ndarray,
    weights: np.ndarray,
    time_to_expiry: float,
    config: SABRConfig,
    seed: int = 42,
) -> Tuple[SABRParameters, float, float, float, bool, int]:
    """Calibrate SABR (alpha, rho, nu) with fixed beta using bid-ask weighted least squares.

    Returns:
        (params, weighted_rmse_iv, unweighted_rmse_iv, max_abs_err_iv, converged, total_nfev)
    """
    k_arr = np.asarray(strikes, dtype=float)
    iv_arr = np.asarray(iv_vals, dtype=float)
    w_arr = np.asarray(weights, dtype=float)

    if len(k_arr) < config.min_slice_quotes:
        raise CalibrationError(
            f"Insufficient quotes ({len(k_arr)}) for SABR calibration; minimum is {config.min_slice_quotes}.",
            details={"quote_count": int(len(k_arr)), "min_slice_quotes": config.min_slice_quotes},
        )

    norm_w = w_arr / np.sum(w_arr)
    sqrt_w = np.sqrt(norm_w)
    beta = float(config.beta)

    # Initial alpha from ATM implied volatility
    atm_idx = int(np.argmin(np.abs(k_arr - forward)))
    atm_iv = float(iv_arr[atm_idx])
    alpha0 = max(atm_iv * (forward ** (1.0 - beta)), 1e-4)

    lower = np.array([1e-5, -0.995, 1e-4], dtype=float)
    upper = np.array([max(10.0 * alpha0, 5.0 * (forward ** (1.0 - beta))), 0.995, 5.0], dtype=float)

    def _residuals(x: np.ndarray) -> np.ndarray:
        p = SABRParameters(alpha=float(x[0]), beta=beta, rho=float(x[1]), nu=float(x[2]))
        iv_pred = sabr_implied_vol(forward, k_arr, time_to_expiry, p)
        return sqrt_w * (iv_pred - iv_arr)

    rng = np.random.default_rng(seed)
    starts = [
        np.array([alpha0, -0.3, 0.4], dtype=float),
        np.array([alpha0, 0.0, 0.3], dtype=float),
    ]
    for _ in range(max(0, config.num_restarts - len(starts))):
        cand_alpha = float(np.clip(alpha0 * math.exp(rng.normal(0.0, 0.25)), lower[0] * 2.0, upper[0] * 0.8))
        cand_rho = float(rng.uniform(-0.85, 0.85))
        cand_nu = float(np.clip(0.5 * math.exp(rng.normal(0.0, 0.6)), 0.02, 3.0))
        starts.append(np.array([cand_alpha, cand_rho, cand_nu], dtype=float))

    best_cost = float("inf")
    best_x = starts[0]
    best_converged = False
    total_nfev = 0

    for x0 in starts[: config.num_restarts]:
        x0_clipped = np.clip(x0, lower + 1e-7, upper - 1e-7)
        res = least_squares(
            _residuals,
            x0=x0_clipped,
            bounds=(lower, upper),
            method="trf",
            ftol=1e-11,
            xtol=1e-11,
            gtol=1e-11,
            max_nfev=config.max_nfev,
        )
        total_nfev += int(res.nfev)
        if np.isfinite(res.cost) and res.cost < best_cost:
            best_cost = float(res.cost)
            best_x = res.x.copy()
            best_converged = bool(res.success)

    best_params = SABRParameters(alpha=float(best_x[0]), beta=beta, rho=float(best_x[1]), nu=float(best_x[2]))
    iv_fit = sabr_implied_vol(forward, k_arr, time_to_expiry, best_params)
    errors = iv_fit - iv_arr
    weighted_rmse = float(np.sqrt(np.sum(norm_w * (errors ** 2))))
    unweighted_rmse = float(np.sqrt(np.mean(errors ** 2)))
    max_abs_err = float(np.max(np.abs(errors)))
    return best_params, weighted_rmse, unweighted_rmse, max_abs_err, best_converged, total_nfev
