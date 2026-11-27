"""Unit tests for SVI and SABR slice calibration, bid-ask weighting, and determinism."""

from __future__ import annotations

import numpy as np
import pytest

from volatility_surface_engine import (
    CalibrationError,
    SABRConfig,
    SABRParameters,
    SVIConfig,
    SVIParameters,
    fit_sabr_slice,
    fit_svi_slice,
    sabr_implied_vol,
    svi_implied_vol,
)


def test_svi_exact_synthetic_smile_recovery() -> None:
    t_years = 0.5
    true_params = SVIParameters(a=0.015, b=0.085, rho=-0.35, m=0.01, sigma=0.20)
    k = np.linspace(-0.25, 0.25, 13)
    true_iv = svi_implied_vol(k, t_years, true_params)
    weights = np.ones_like(k)

    fit_params, w_rmse, u_rmse, max_err, converged, nfev = fit_svi_slice(
        k, true_iv, weights, t_years, SVIConfig(), seed=42
    )
    assert converged
    assert nfev > 0
    assert w_rmse < 1e-4
    assert u_rmse < 1e-4
    assert max_err < 2e-4
    # Fitted curve matches true curve everywhere
    fit_iv = svi_implied_vol(k, t_years, fit_params)
    assert float(np.max(np.abs(fit_iv - true_iv))) < 2e-4


def test_svi_bid_ask_weighting_downweights_wide_spread_outlier() -> None:
    t_years = 0.25
    true_params = SVIParameters(a=0.008, b=0.05, rho=-0.40, m=0.0, sigma=0.18)
    k = np.linspace(-0.20, 0.20, 11)
    true_iv = svi_implied_vol(k, t_years, true_params)

    # Corrupt one wing quote with a +3% vol outlier
    noisy_iv = true_iv.copy()
    noisy_iv[0] += 0.03

    equal_w = np.ones_like(k)
    tight_core_w = np.full_like(k, 1000.0)
    tight_core_w[0] = 1.0  # Very wide bid-ask spread at outlier

    p_eq, _, _, _, _, _ = fit_svi_slice(k, noisy_iv, equal_w, t_years, SVIConfig(), seed=42)
    p_wt, _, _, _, _, _ = fit_svi_slice(k, noisy_iv, tight_core_w, t_years, SVIConfig(), seed=42)

    # Weighted fit must stay significantly closer to true IV on the clean interior strikes
    err_eq = float(np.sqrt(np.mean((svi_implied_vol(k[1:], t_years, p_eq) - true_iv[1:]) ** 2)))
    err_wt = float(np.sqrt(np.mean((svi_implied_vol(k[1:], t_years, p_wt) - true_iv[1:]) ** 2)))
    assert err_wt < err_eq * 0.25


def test_sabr_exact_synthetic_smile_recovery() -> None:
    fwd = 100.0
    t_years = 0.5
    true_sabr = SABRParameters(alpha=2.2, beta=0.5, rho=-0.35, nu=0.45)
    strikes = np.linspace(80.0, 125.0, 13)
    true_iv = sabr_implied_vol(fwd, strikes, t_years, true_sabr)
    weights = np.ones_like(strikes)

    fit_params, w_rmse, u_rmse, max_err, converged, _ = fit_sabr_slice(
        fwd, strikes, true_iv, weights, t_years, SABRConfig(beta=0.5), seed=42
    )
    assert converged
    assert w_rmse < 1e-4
    assert u_rmse < 1e-4
    assert max_err < 2e-4
    assert abs(fit_params.rho - true_sabr.rho) < 1e-2


def test_deterministic_calibration_for_fixed_seed() -> None:
    t_years = 0.5
    true_params = SVIParameters(a=0.015, b=0.085, rho=-0.35, m=0.01, sigma=0.20)
    k = np.linspace(-0.25, 0.25, 11)
    iv = svi_implied_vol(k, t_years, true_params)
    w = np.linspace(1.0, 5.0, 11)

    res1 = fit_svi_slice(k, iv, w, t_years, SVIConfig(), seed=101)
    res2 = fit_svi_slice(k, iv, w, t_years, SVIConfig(), seed=101)
    assert res1 == res2


def test_insufficient_quotes_raises_calibration_error() -> None:
    k = np.array([-0.1, 0.0, 0.1])
    iv = np.array([0.22, 0.20, 0.21])
    w = np.ones(3)
    with pytest.raises(CalibrationError, match="Insufficient quotes"):
        fit_svi_slice(k, iv, w, 0.5, SVIConfig(min_slice_quotes=5), seed=42)
